#!/usr/bin/env python3
"""scripts/pdf_to_markdown.py — Phase 5G.5: convert policy PDF to Markdown.

Reads data/policies/bytemart-policy-pack.pdf via pymupdf4llm, detects
doc_id per page (reusing src.rag._DOC_PATTERNS), groups consecutive
same-doc pages, and writes one .md file per detected doc into
data/policies/markdown/.

Each .md file has YAML frontmatter:
    ---
    doc_id: <detected-doc-id>
    version: v1
    source_pdf: bytemart-policy-pack.pdf
    pdf_pages: [1, 2, 3]    # 1-indexed PDF pages that compose this doc
    ---

The body is the concatenated pymupdf4llm output for those pages. pymupdf4llm
already detects "# N. Title" headers, so clause boundaries are preserved as
markdown headings (which the chunker can later split on).

Idempotent: re-running overwrites the .md files.

Usage:
    python scripts/pdf_to_markdown.py [--pdf <path>] [--out <dir>]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pymupdf4llm

from src.rag import _DOC_PATTERNS


PDF_PATH_DEFAULT = REPO_ROOT / "data" / "policies" / "bytemart-policy-pack.pdf"
OUT_DIR_DEFAULT  = REPO_ROOT / "data" / "policies" / "markdown"


# ---------------------------------------------------------------------------
# Doc-id detection (per page) — same heuristic as src.rag.detect_doc_id,
# exposed here as a function (not a method) so the script is standalone.
# ---------------------------------------------------------------------------
def detect_doc_id_for_page(page_md: str) -> str:
    head = page_md[:1500]
    best = ("unknown-policy", 0)
    for doc_id, pat in _DOC_PATTERNS:
        n = len(pat.findall(head))
        if n > best[1]:
            best = (doc_id, n)
    return best[0]


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------
def frontmatter(doc_id: str, version: str, pdf_path: Path,
                pdf_pages: list[int]) -> str:
    pages_csv = ",".join(str(p) for p in pdf_pages)
    return (
        "---\n"
        f"doc_id: {doc_id}\n"
        f"version: {version}\n"
        f"source_pdf: {pdf_path.name}\n"
        f"pdf_pages: [{pages_csv}]\n"
        "---\n\n"
    )


# ---------------------------------------------------------------------------
# Body cleanup
# ---------------------------------------------------------------------------
# pymupdf4llm emits "=== Document parser messages ===" / "Using Tesseract for
# OCR processing." prefixes. Strip those.
_NOISE_LINES = re.compile(
    r"^(=== Document parser messages ===|Using Tesseract for OCR processing\.).*$",
    re.MULTILINE,
)


def clean_page_md(page_md: str) -> str:
    cleaned = _NOISE_LINES.sub("", page_md).strip()
    # Ensure each page ends with a single newline so concatenation is clean.
    return cleaned + "\n\n"


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------
def convert(pdf_path: Path, out_dir: Path, version: str = "v1") -> int:
    if not pdf_path.exists():
        print(f"!! {pdf_path} not found")
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)

    # Convert each page individually so we can detect doc_id per page.
    # pymupdf4llm uses 0-indexed pages; we normalize to 1-indexed for frontmatter.
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    page_count = doc.page_count
    doc.close()

    print(f"PDF: {pdf_path} ({page_count} pages)")

    page_md_list: list[tuple[int, str]] = []
    for pg0 in range(page_count):
        try:
            md = pymupdf4llm.to_markdown(str(pdf_path), pages=[pg0])
        except Exception as exc:
            print(f"  !! page {pg0}: pymupdf4llm error: {exc}")
            continue
        page_md_list.append((pg0 + 1, md))

    # Detect doc_id for each page.
    page_doc: list[tuple[int, str, str]] = []  # (pdf_page_1idx, doc_id, md)
    for pdf_pg1, md in page_md_list:
        d = detect_doc_id_for_page(md)
        page_doc.append((pdf_pg1, d, md))

    # Group consecutive same-doc pages.
    groups: list[tuple[str, list[tuple[int, str]]]] = []
    cur_doc: Optional[str] = None
    cur_pages: list[tuple[int, str]] = []
    for pdf_pg1, d, md in page_doc:
        if d != cur_doc:
            if cur_doc is not None and cur_pages:
                groups.append((cur_doc, cur_pages))
            cur_doc = d
            cur_pages = [(pdf_pg1, md)]
        else:
            cur_pages.append((pdf_pg1, md))
    if cur_doc is not None and cur_pages:
        groups.append((cur_doc, cur_pages))

    # Merge all groups that share the same doc_id (the same policy is
    # often split across non-contiguous PDF pages because the source
    # was concatenated from multiple smaller documents).
    merged: dict[str, list[tuple[int, str]]] = {}
    for doc_id, pages in groups:
        merged.setdefault(doc_id, []).extend(pages)

    # Sort each merged list by PDF page number so the resulting .md is
    # in reading order.
    for doc_id in merged:
        merged[doc_id].sort(key=lambda p: p[0])

    # Write one .md file per unique doc_id.
    written = 0
    for doc_id, pages in sorted(merged.items()):
        pdf_pages_1idx = [p[0] for p in pages]
        body_parts = [clean_page_md(p[1]) for p in pages]
        body = "".join(body_parts)
        # Prepend a top-level heading with the doc title for readability.
        title = doc_id.replace("-", " ").title()
        content = f"# {title}\n\n{body}"
        out_path = out_dir / f"{doc_id}.md"
        out_path.write_text(
            frontmatter(doc_id, version, pdf_path, pdf_pages_1idx)
            + content
        )
        print(f"  [OK] {doc_id:22s} <- PDF pages {pdf_pages_1idx} "
              f"({len(body)} chars)")
        written += 1

    print(f"\nWrote {written} .md files to {out_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert policy PDF to per-doc Markdown files."
    )
    ap.add_argument("--pdf", default=str(PDF_PATH_DEFAULT),
                    help="Path to the policy PDF.")
    ap.add_argument("--out", default=str(OUT_DIR_DEFAULT),
                    help="Output directory for .md files.")
    ap.add_argument("--version", default="v1",
                    help="Schema version (written into frontmatter).")
    args = ap.parse_args()
    return convert(Path(args.pdf), Path(args.out), version=args.version)


if __name__ == "__main__":
    sys.exit(main())
