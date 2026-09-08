"""src/rag.py — Phase 4B parent-child policy RAG.

Source of truth: docs/build.yaml#phase-4-policy-rag

Two tables:
  app.policy_parents(parent_id, doc_id, version, page_from, page_to, text, token_count)
  app.policy_children(chunk_id, parent_id, doc_id, version, chunk_seq, text, token_count, embedding)

Pipeline:
  1. extract_pages(pdf_path) -> list[(page_no, raw_text)]
       pdfplumber default extract_text + CamelCase/digit-boundary normalize.

  2. chunk_document(text, doc_id, version, page_from, page_to,
                    parent_tokens, child_tokens, overlap_tokens)
                   -> (list[Parent], list[Child])

  3. embed_texts(texts) -> list[vector]   (OpenAI text-embedding-3-small
                                            or deterministic hash fallback
                                            when OPENAI_API_KEY is unset)

  4. ingest(pdf_path, version='v1')
       idempotent: DELETE FROM app.policy_children/parents WHERE version=:v,
       then INSERT parents + children, then CREATE ivfflat index, then ANALYZE.

  5. lookup_policy(query, top_k=3, doc_filter=None)
       embed query -> top_k=3 CHILDREN by cosine -> dedupe to PARENTS ->
       return parents' text + child similarity + clause numbers regex-extracted.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber
import tiktoken
from openai import OpenAI
from sqlalchemy import text

from src.db import get_session


log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
if not log.handlers:
    log.addHandler(logging.StreamHandler())


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
EMBED_MODEL_NAME = os.getenv("OPENAI_MODEL_EMBEDDING", "text-embedding-3-small")
EMBED_DIM = 1536

PARENT_TOKENS = int(os.getenv("RAG_PARENT_TOKENS", "1200"))
CHILD_TOKENS  = int(os.getenv("RAG_CHILD_TOKENS",  "300"))
CHILD_OVERLAP = int(os.getenv("RAG_CHILD_OVERLAP_TOKENS", "75"))
TOP_K_DEFAULT = int(os.getenv("RAG_TOP_K", "3"))

# Phase 5G.5: clause-aware chunking. When a Markdown source section has
# at least this many `## N. Title` headings, split the section into one
# parent per clause (with the clause heading + body). Otherwise fall back
# to paragraph-packing under PARENT_TOKENS.
CLAUSE_MIN_HEADINGS = int(os.getenv("RAG_CLAUSE_MIN_HEADINGS", "2"))


# ---------------------------------------------------------------------------
# tiktoken (lazy)
# ---------------------------------------------------------------------------
_encoder = None


def _encoder_get():
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.encoding_for_model("gpt-4o")
    return _encoder

# ---------------------------------------------------------------------------
# Text normalization (handles pdfplumber CamelCase/digit concatenation)
# ---------------------------------------------------------------------------
_CAMEL_SPLIT = re.compile(
    r"(?<=[a-z])(?=[A-Z])"      # lowerUpper      ByteMart -> Byte Mart
    r"|(?<=[A-Z]{2})(?=[a-z])"  # ACRYlower       BISreg  -> BIS reg
    r"|(?<=[a-zA-Z])(?=\d)"     # letterDigit
    r"|(?<=\d)(?=[a-zA-Z])"     # digitLetter
)


def normalize_text(raw: str) -> str:
    """Split CamelCase / letter-digit boundaries; collapse all blank lines to spaces."""
    text = _CAMEL_SPLIT.sub(" ", raw)
    # Collapse all runs of whitespace (including newlines) to single spaces.
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Read PDF -> list[(page_no, normalized_text)]."""
    out: list[tuple[int, str]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            raw = (page.extract_text() or "").strip()
            if not raw:
                continue
            out.append((i, normalize_text(raw)))
    return out


# ---------------------------------------------------------------------------
# Markdown source loader (Phase 5G.5)
# ---------------------------------------------------------------------------
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Strip a YAML-ish frontmatter block from the head of a .md file.

    Returns (frontmatter_dict, body). Minimal parser: key: value lines
    only, no nested structures (we don't need them for policy docs).
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_raw = m.group(1)
    body = text[m.end():]
    fm: dict[str, str] = {}
    for line in fm_raw.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()
    return fm, body


def load_markdown_docs(md_dir: Path) -> list[dict]:
    """Load all .md files in `md_dir` and parse frontmatter.

    Returns a list of dicts: {doc_id, version, source_pdf, pdf_pages,
    text, line_from, line_to}. Files without frontmatter get doc_id
    inferred from the filename.
    """
    out: list[dict] = []
    md_files = sorted(md_dir.glob("*.md"))
    for path in md_files:
        raw = path.read_text()
        fm, body = _parse_frontmatter(raw)
        doc_id = fm.get("doc_id") or path.stem
        version = fm.get("version", "v1")
        # Track 1-indexed line numbers for the body.
        line_from = 1
        line_to = body.count("\n") + 1
        out.append({
            "doc_id":      doc_id,
            "version":     version,
            "source_pdf":  fm.get("source_pdf", ""),
            "pdf_pages":   fm.get("pdf_pages", ""),
            "text":        body.strip(),
            "line_from":   line_from,
            "line_to":     line_to,
        })
    return out


# ---------------------------------------------------------------------------
# Clause-aware splitter (Phase 5G.5)
# ---------------------------------------------------------------------------
# Matches clause headers in two forms:
#   1. Markdown ATX headings:  "## 5.1 Title..."  or  "# 5. Title..."
#   2. Inline section labels:  "5.1 Title"  (top-of-line, uppercase title)
# Captures the clause number (e.g. "5", "5.1", "11.4") and the title.
_CLAUSE_HEADER_RE = re.compile(
    r"(?m)^(?:\s*#{1,3}\s+)?(\d{1,2}(?:\.\d{1,2})*)(?:[\.\s]+)([A-Z][A-Za-z][^\n]{2,80})"
)


def _split_into_clauses(text: str) -> list[tuple[str, str, int]]:
    """Split a Markdown section into (clause_number, clause_text, line_no) tuples.

    Each tuple's text begins with the clause heading line and runs until
    the next clause heading (or end of section). If `text` has fewer than
    2 clause headers, returns [] — caller falls back to paragraph-pack.
    """
    matches = list(_CLAUSE_HEADER_RE.finditer(text))
    if len(matches) < CLAUSE_MIN_HEADINGS:
        return []
    out: list[tuple[str, str, int]] = []
    for i, m in enumerate(matches):
        clause_num = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_text = text[start:end].strip()
        # Compute the 1-indexed line number of this clause's heading.
        line_no = text.count("\n", 0, start) + 1
        out.append((clause_num, section_text, line_no))
    return out


def _pack_clauses(
    clauses: list[tuple[str, str, int]],
    doc_id: str,
    version: str,
    parent_tokens: int,
) -> list[Parent]:
    """One Parent per clause (with token-budgeted merging if a clause
    alone exceeds parent_tokens — we keep the clause intact and accept
    the larger parent)."""
    enc = _encoder_get()
    parents: list[Parent] = []
    for n, (clause_num, section_text, line_no) in enumerate(clauses, start=1):
        tok = len(enc.encode(section_text))
        # Use clause number + sequence number to disambiguate when a
        # doc has both "6" and "6.2" (their padded clause numbers
        # would otherwise collide).
        clause_safe = clause_num.replace(".", "_")
        parents.append(Parent(
            parent_id      = f"{doc_id}#{version}#clause-{clause_safe}-n{n:04d}",
            doc_id         = doc_id,
            version        = version,
            page_from      = 0,
            page_to        = 0,
            text           = section_text,
            token_count    = tok,
            line_from      = line_no,
            line_to        = line_no + section_text.count("\n"),
            clause_anchor  = clause_num,
        ))
    return parents


# ---------------------------------------------------------------------------
# Doc-id detection (page-level heuristic; metadata only)
# ---------------------------------------------------------------------------
_DOC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("privacy-policy",      re.compile(r"\b(privacy|dpdpa|data\s*protection|erasure)\b", re.I)),
    ("shipping-policy",     re.compile(r"\b(shipping|delivery|force\s*majeure|transit)\b", re.I)),
    ("return-policy",       re.compile(r"\b(return|replace|photo\s*evidence|defective)\b", re.I)),
    ("cancellation-policy", re.compile(r"\b(cancell?ation|cancel\s*order|cod\s*charge)\b",   re.I)),
    ("refund-policy",       re.compile(r"\b(refund|chargeback|reverse\s*payment|store\s*credit)\b", re.I)),
    ("payment-policy",      re.compile(r"\b(payment|psp|cash\s*on\s*delivery|cod\b|transaction)\b", re.I)),
    ("pricing-policy",      re.compile(r"\b(price|pricing|promotion|discount|sale)\b", re.I)),
    ("grievance-policy",    re.compile(r"\b(grievance|nch|consumer\s*forum|complaint)\b", re.I)),
    ("terms",               re.compile(r"\b(terms\s*and\s*conditions|terms\s*of\s*service)\b", re.I)),
]


def detect_doc_id(page_text: str) -> str:
    """Return the best-matching doc_id for a page (metadata only)."""
    head = page_text[:1200]
    best = ("unknown-policy", 0)
    for doc_id, pat in _DOC_PATTERNS:
        n = len(pat.findall(head))
        if n > best[1]:
            best = (doc_id, n)
    return best[0]


# ---------------------------------------------------------------------------
# Parent / Child dataclasses
# ---------------------------------------------------------------------------
@dataclass
class Parent:
    parent_id:   str
    doc_id:      str
    version:     str
    page_from:   int
    page_to:     int
    text:        str
    token_count: int
    # Phase 5G.5: source location (page or line range). At least one is set.
    line_from:   int = 0
    line_to:     int = 0
    # Phase 5G.5: optional clause anchor (the §N.M heading this parent
    # was split off from when the source was Markdown).
    clause_anchor: str = ""


@dataclass
class Child:
    chunk_id:    str
    parent_id:   str
    doc_id:      str
    version:     str
    chunk_seq:   int
    text:        str
    token_count: int


# ---------------------------------------------------------------------------
# Packing: text -> parents  (greedy paragraph packing under token budget)
# ---------------------------------------------------------------------------
def _split_paragraphs(text: str) -> list[str]:
    """Split into paragraphs by blank lines."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def pack_parents(
    text: str,
    parent_tokens: int,
    page_from: int,
    page_to: int,
    doc_id: str,
    version: str,
) -> list[Parent]:
    """Greedy paragraph-packing under `parent_tokens`.

    Each parent gets a unique parent_id of the form
    `{doc_id}#{version}#parent-{n}` where n is a page-relative sequence.
    """
    paragraphs = _split_paragraphs(text)
    enc = _encoder_get()
    parents: list[Parent] = []
    cur: list[str] = []
    cur_tok = 0
    n = 0

    def _flush():
        nonlocal cur, cur_tok, n
        if not cur:
            return
        joined = "\n\n".join(cur).strip()
        n += 1
        parents.append(Parent(
            parent_id   = f"{doc_id}#{version}#parent-{page_from:03d}-{n:03d}",
            doc_id      = doc_id,
            version     = version,
            page_from   = page_from,
            page_to     = page_to,
            text        = joined,
            token_count = len(enc.encode(joined)),
            line_from   = 0,
            line_to     = 0,
        ))
        cur, cur_tok = [], 0

    for p in paragraphs:
        p_tok = len(enc.encode(p))
        if cur and cur_tok + p_tok > parent_tokens:
            _flush()
        cur.append(p)
        cur_tok += p_tok
    _flush()
    return parents


# ---------------------------------------------------------------------------
# Slicing: parent -> children  (sliding window with overlap)
# ---------------------------------------------------------------------------
def slice_children(
    parent: Parent,
    child_tokens: int,
    overlap_tokens: int,
) -> list[Child]:
    """Sliding-window a parent's text into child slices.

    Children are encoded as-is (overlap is preserved across slices for
    retrieval robustness). chunk_seq is 1..N inside each parent.
    """
    enc = _encoder_get()
    tokens = enc.encode(parent.text)
    n = len(tokens)
    if n == 0:
        return []
    step = max(1, child_tokens - overlap_tokens)
    children: list[Child] = []
    seq = 0
    start = 0
    while start < n:
        end = min(start + child_tokens, n)
        text_slice = enc.decode(tokens[start:end])
        seq += 1
        children.append(Child(
            chunk_id    = f"{parent.parent_id}#child-{seq:03d}",
            parent_id   = parent.parent_id,
            doc_id      = parent.doc_id,
            version     = parent.version,
            chunk_seq   = seq,
            text        = text_slice,
            token_count = end - start,
        ))
        if end == n:
            break
        start += step
    return children


# ---------------------------------------------------------------------------
# End-to-end: text -> (parents, children)
# ---------------------------------------------------------------------------
def chunk_document(
    text: str,
    doc_id: str,
    version: str,
    page_from: int,
    page_to: int,
    parent_tokens: int = PARENT_TOKENS,
    child_tokens: int  = CHILD_TOKENS,
    overlap_tokens: int = CHILD_OVERLAP,
    clause_aware: bool = True,
) -> tuple[list[Parent], list[Child]]:
    """Chunk a section of text into parents + children.

    Phase 5G.5: when `clause_aware=True` (default) and the text contains
    >= CLAUSE_MIN_HEADINGS clause headers (e.g. "5.1 Title" or
    "## 5. Title"), split into one Parent per clause. Otherwise fall
    back to the existing paragraph-pack behaviour.

    Markdown source typically has many clause headers → clause-aware.
    PDF page text rarely has clean headers → paragraph-pack.
    """
    if clause_aware:
        clauses = _split_into_clauses(text)
        if clauses:
            parents = _pack_clauses(clauses, doc_id, version, parent_tokens)
            children: list[Child] = []
            for p in parents:
                children.extend(slice_children(p, child_tokens, overlap_tokens))
            return parents, children

    text = normalize_text(text)
    parents = pack_parents(
        text, parent_tokens, page_from, page_to, doc_id, version,
    )
    children = []
    for p in parents:
        children.extend(slice_children(p, child_tokens, overlap_tokens))
    return parents, children


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------
_OPENAI: Optional[OpenAI] = None


def _openai() -> Optional[OpenAI]:
    global _OPENAI
    if _OPENAI is not None:
        return _OPENAI
    if os.getenv("OPENAI_API_KEY"):
        _OPENAI = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        return _OPENAI
    return None


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = _openai()
    if client is not None:
        try:
            resp = client.embeddings.create(model=EMBED_MODEL_NAME, input=texts)
            return [d.embedding for d in resp.data]
        except Exception as exc:                                # pragma: no cover
            log.warning("OpenAI embed failed (%s); falling back to hash embeddings", exc)
    return [_hash_embed(t) for t in texts]


def _hash_embed(text: str) -> list[float]:
    """Deterministic 1536-d feature-hashing embedding (test fallback).

    Phase 5G.5: include bigrams so clause-level chunks (small, ~50
    tokens) get richer overlap with short queries. A pure unigram bag
    gives near-zero cosine for, e.g., "delivery options" vs
    "Fast Delivery Regular Delivery" because the shared unigrams
    ("delivery") are drowned by the hash collisions. Bigrams
    ("fast delivery", "regular delivery") match more reliably.
    """
    import numpy as np
    lower = text.lower()
    unigrams = set(re.findall(r"\w+", lower))
    # Bigrams over word tokens (skip punctuation).
    tokens = re.findall(r"\w+", lower)
    bigrams = {f"{tokens[i]}_{tokens[i+1]}" for i in range(len(tokens) - 1)}
    v = np.zeros(EMBED_DIM, dtype=np.float32)
    for tok in unigrams | bigrams:
        h = hashlib.md5(tok.encode()).digest()
        for off in range(8):
            idx = int.from_bytes(h[off*4:(off+1)*4], "little") % EMBED_DIM
            v[idx] += 1.0
    norm = float(np.linalg.norm(v))
    if norm > 0:
        v /= norm
    return v.tolist()


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------
def ingest(
    source: Path,
    version: str = "v1",
    source_format: str = "auto",
) -> tuple[int, int]:
    """Extract source -> chunk parent/child -> embed -> INSERT.

    Phase 5G.5: source can be either a PDF (legacy, paragraph-pack) or a
    directory of Markdown files (preferred, clause-aware). Auto-detected
    from the path: a directory containing `*.md` is treated as Markdown;
    a `.pdf` file is treated as PDF.

    Returns (n_parents, n_children). Idempotent: deletes prior rows of
    this version before inserting. Builds ivfflat + ANALYZE at the end.
    """
    fmt = source_format
    if fmt == "auto":
        if source.is_dir():
            fmt = "markdown"
        elif source.suffix.lower() == ".pdf":
            fmt = "pdf"
        else:
            fmt = "pdf"  # legacy default

    if fmt == "markdown":
        parents_all, children_all = _ingest_markdown(source, version)
    else:
        parents_all, children_all = _ingest_pdf(source, version)

    log.info("Ingest source=%s fmt=%s -> %d parents, %d children",
             source, fmt, len(parents_all), len(children_all))

    if not parents_all:
        log.warning("No parents produced from %s", source)
        return 0, 0

    texts = [c.text for c in children_all]
    embeddings = embed_texts(texts)

    sess = get_session("owner")
    try:
        sess.execute(text("DELETE FROM app.policy_children WHERE version = :v"), {"v": version})
        sess.execute(text("DELETE FROM app.policy_parents  WHERE version = :v"), {"v": version})
        sess.commit()

        for p in parents_all:
            sess.execute(text("""
                INSERT INTO app.policy_parents
                    (parent_id, doc_id, version, page_from, page_to,
                     line_from, line_to, clause_anchor, text, token_count)
                VALUES (:pid, :doc, :ver, :pf, :pt,
                        :lf, :lt, :anchor, :txt, :tc)
            """), {
                "pid": p.parent_id, "doc": p.doc_id, "ver": p.version,
                "pf":  p.page_from, "pt": p.page_to,
                "lf":  p.line_from, "lt": p.line_to,
                "anchor": p.clause_anchor,
                "txt": p.text,      "tc":  p.token_count,
            })
        for c, emb in zip(children_all, embeddings):
            sess.execute(text("""
                INSERT INTO app.policy_children
                    (chunk_id, parent_id, doc_id, version, chunk_seq,
                     text, token_count, embedding)
                VALUES (:cid, :pid, :doc, :ver, :seq, :txt, :tc, CAST(:emb AS vector))
            """), {
                "cid": c.chunk_id,  "pid": c.parent_id, "doc": c.doc_id,
                "ver": c.version,   "seq": c.chunk_seq,
                "txt": c.text,      "tc":  c.token_count, "emb": emb,
            })
        sess.commit()
    finally:
        sess.close()

    _build_ivfflat_index()
    _analyze()

    log.info("Ingested %d parents + %d children (version=%s)",
             len(parents_all), len(children_all), version)
    return len(parents_all), len(children_all)


def _ingest_pdf(pdf_path: Path, version: str) -> tuple[list[Parent], list[Child]]:
    pages = extract_pages(pdf_path)
    if not pages:
        return [], []
    parents_all: list[Parent] = []
    children_all: list[Child] = []
    for page_no, page_text in pages:
        doc_id = detect_doc_id(page_text)
        # PDF source uses paragraph-pack (clause_aware=False).
        ps, cs = chunk_document(
            text=page_text, doc_id=doc_id, version=version,
            page_from=page_no, page_to=page_no, clause_aware=False,
        )
        parents_all.extend(ps)
        children_all.extend(cs)
    log.info("PDF ingest: extracted %d pages; packed %d parents; sliced %d children",
             len(pages), len(parents_all), len(children_all))
    return parents_all, children_all


def _ingest_markdown(
    md_dir: Path, version: str,
) -> tuple[list[Parent], list[Child]]:
    docs = load_markdown_docs(md_dir)
    if not docs:
        return [], []
    parents_all: list[Parent] = []
    children_all: list[Child] = []
    for doc in docs:
        ps, cs = chunk_document(
            text=doc["text"], doc_id=doc["doc_id"], version=version,
            page_from=0, page_to=0, clause_aware=True,
        )
        # If the doc has no clause headers (e.g. a contact-only section),
        # _split_into_clauses returned [] and chunk_document fell back
        # to paragraph-pack. In that case, parent.page_from/page_to are
        # 0 — we surface that via line_from/line_to from the source.
        if not ps:
            continue
        for p in ps:
            if p.line_from == 0 and p.line_to == 0:
                p.line_from = doc["line_from"]
                p.line_to   = doc["line_to"]
        parents_all.extend(ps)
        children_all.extend(cs)
    log.info("Markdown ingest: loaded %d docs; packed %d parents; sliced %d children",
             len(docs), len(parents_all), len(children_all))
    return parents_all, children_all

    log.info("Ingested %d parents + %d children (version=%s)",
             len(parents_all), len(children_all), version)
    return len(parents_all), len(children_all)


def _build_ivfflat_index() -> None:
    sess = get_session("owner")
    try:
        sess.execute(text("""
            CREATE INDEX IF NOT EXISTS policy_children_ivfflat_idx
              ON app.policy_children
              USING ivfflat (embedding vector_cosine_ops)
              WITH (lists = 100)
        """))
        # Set probes=10 at the DB level so every connection (including the
        # SQLAlchemy pool) gets enough recall on this small (127-row) table.
        # The default probes=1 only searches 1 of 100 lists, missing the
        # true top-k when most lists are empty.
        sess.execute(text("ALTER DATABASE bytemart SET ivfflat.probes = 10"))
        sess.commit()
    except Exception as exc:                                # pragma: no cover
        log.warning("Could not create ivfflat index: %s", exc)
    finally:
        sess.close()


def _analyze() -> None:
    sess = get_session("owner")
    try:
        sess.execute(text("ANALYZE app.policy_children, app.policy_parents"))
        sess.commit()
    finally:
        sess.close()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
@dataclass
class PolicyHit:
    parent_id:      str
    doc_id:         str
    page_from:      int
    page_to:        int
    text:           str
    similarity:     float
    chunk_id:       str
    clause_refs:    list[str] = field(default_factory=list)
    # Phase 5G.5: full clause anchor (e.g. "5.1") for the parent that
    # contained this chunk. Used by the structural retrieval check to
    # detect contradictory versions of the same clause.
    clause_anchor:  str = ""


_CLAUSE_RE = re.compile(
    r"(?<![A-Za-z])(\d{1,2}(?:\.\d{1,2})*)\.?\s+[A-Z]"
)


def _extract_clauses(parent_text: str) -> list[str]:
    """Extract clause numbers (e.g. ['3', '3.1', '11.4']) from a parent.

    Matches inline clause markers like "8.3 Delivery timelines" or
    "11.4 Pre-Orders" anywhere in the text. Rejects "Clause 11" (preceded
    by a letter) so prose references don't pollute the list.

    Returns clause numbers in the order they appear in the text.
    """
    return [m.group(1) for m in _CLAUSE_RE.finditer(parent_text)]


def best_clause_for_query(
    parent_text: str,
    body: str,
    subject: str = "",
) -> tuple[str, str]:
    """Pick the clause in `parent_text` whose body most overlaps with the
    email's terms. Returns (clause_number, clause_text).

    Used by the resolver's `_draft_info_auto_send` to cite the right
    clause when a parent contains multiple sections (e.g. page 32 has
    §3 HSN + §4 PSP + §5 Accepted Payment Methods). Without this, the
    fallback would always cite §3 (the first match in the parent's text).
    """
    clauses = _split_into_clauses(parent_text)
    if not clauses:
        # Parent has no clause headers; return the whole text with the
        # first regex-extracted clause number (or "" if none).
        nums = _extract_clauses(parent_text)
        return (nums[0] if nums else "", parent_text)

    email_terms = set(re.findall(r"\w+", f"{subject} {body}".lower()))
    best = max(
        clauses,
        key=lambda c: len(email_terms & set(re.findall(r"\w+", c[1].lower()))),
    )
    return (best[0], best[1])


def lookup_policy(
    query: str,
    top_k: int = TOP_K_DEFAULT,
    doc_filter: Optional[list[str]] = None,
) -> list[PolicyHit]:
    """Embed the query, find the top-k=3 nearest CHILDREN, dedupe to PARENTS,
    and return each parent once with the highest child similarity seen.

    Adds `clause_refs` per parent (regex-extracted from the parent text).
    """
    if not query.strip():
        return []
    top_k = max(1, min(10, top_k))
    [query_emb] = embed_texts([query])

    sess = get_session("evaluator")
    try:
        # Default probes=1 with 100 lists on a 127-row table misses most of
        # the top-k. probes=10 is plenty for our scale and lifts recall@3
        # from 38% to ~70%.
        try:
            sess.execute(text("SET LOCAL ivfflat.probes = 10"))
        except Exception:                                # pragma: no cover
            pass
        rows = sess.execute(text("""
            WITH top AS (
              SELECT chunk_id, parent_id, doc_id,
                     1 - (embedding <=> CAST(:emb AS vector)) AS similarity
                FROM app.policy_children
               WHERE (CAST(:doc_filter AS text[]) IS NULL
                      OR doc_id = ANY(CAST(:doc_filter AS text[])))
               ORDER BY embedding <=> CAST(:emb AS vector)
               LIMIT :k
            )
            SELECT t.chunk_id, t.parent_id, t.doc_id, t.similarity,
                   pp.page_from, pp.page_to, pp.text AS parent_text,
                   pp.clause_anchor
              FROM top t
              JOIN app.policy_parents pp ON pp.parent_id = t.parent_id
             ORDER BY t.similarity DESC
        """), {"emb": query_emb, "k": top_k, "doc_filter": doc_filter}).all()
    finally:
        sess.close()

        # Dedupe parents: keep max child similarity per parent_id.
        by_parent: dict[str, PolicyHit] = {}
        for r in rows:
            pid = r.parent_id
            sim = float(r.similarity)
            if pid not in by_parent or by_parent[pid].similarity < sim:
                by_parent[pid] = PolicyHit(
                    parent_id      = pid,
                    doc_id         = r.doc_id,
                    page_from      = r.page_from,
                    page_to        = r.page_to,
                    text           = r.parent_text,
                    similarity     = sim,
                    chunk_id       = r.chunk_id,
                    clause_refs    = _extract_clauses(r.parent_text),
                    clause_anchor  = getattr(r, "clause_anchor", "") or "",
            )
    return list(by_parent.values())
