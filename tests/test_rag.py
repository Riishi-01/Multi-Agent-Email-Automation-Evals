"""tests/test_rag.py — unit tests for src/rag.py (Phase 4B parent-child RAG).

Layers:
  - Pure-function tests (no I/O): _hash_embed determinism, normalize_text,
    detect_doc_id, _extract_clauses.
  - DB-backed live tests (requires `scripts/setup_db.py` +
    `scripts/ingest_policies_parent_child.py` to have populated the
    parent/child tables): lookup_policy returns hits with parent-level
    text + clause_refs.

Phase 4B re-runnable; gracefully skips DB tests when unreachable.
"""
from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class HashEmbedder(unittest.TestCase):
    """Tests for src.rag._hash_embed (deterministic fallback when OPENAI_API_KEY missing)."""

    def test_determinism(self):
        from src.rag import _hash_embed
        a = _hash_embed("refund policy")
        b = _hash_embed("refund policy")
        self.assertEqual(a, b)

    def test_vector_length_is_embedding_dim(self):
        from src.rag import _hash_embed, EMBED_DIM
        v = _hash_embed("anything")
        self.assertEqual(len(v), EMBED_DIM)

    def test_unit_norm(self):
        from src.rag import _hash_embed
        v = _hash_embed("anything")
        norm = math.sqrt(sum(x * x for x in v))
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_overlapping_vocab_has_higher_cosine(self):
        from src.rag import _hash_embed as e
        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b)))
        s_refund   = e("refund")
        s_policy   = e("refund policy")           # shares 1 token
        s_unrelated = e("shipping delivery")
        self.assertGreater(cos(s_refund, s_policy), cos(s_refund, s_unrelated))

    def test_empty_string_safe(self):
        from src.rag import _hash_embed
        v = _hash_embed("")
        self.assertEqual(len(v), 1536)


class EmbedTexts(unittest.TestCase):
    """Tests for src.rag.embed_texts (real OpenAI if key set, else hash fallback)."""

    def test_deterministic_for_same_input(self):
        from src.rag import embed_texts
        a = embed_texts(["foo bar", "bar baz"])
        b = embed_texts(["foo bar", "bar baz"])
        self.assertEqual(a, b)

    def test_empty_input_returns_empty(self):
        from src.rag import embed_texts
        self.assertEqual(embed_texts([]), [])


class NormalizeText(unittest.TestCase):
    """Tests for src.rag.normalize_text (CamelCase / digit split)."""

    def test_splits_byte_mart(self):
        from src.rag import normalize_text
        self.assertIn("Byte Mart", normalize_text("ByteMart"))

    def test_splits_acronym_then_lower(self):
        from src.rag import normalize_text
        self.assertIn("DPDPA", normalize_text("DPDPA"))

    def test_collapses_whitespace(self):
        from src.rag import normalize_text
        self.assertNotIn("\n", normalize_text("a\nb\nc"))


class DocIdDetector(unittest.TestCase):
    """Tests for src.rag.detect_doc_id (heuristic policy-name detection)."""

    def test_privacy_keyword(self):
        from src.rag import detect_doc_id
        self.assertEqual(detect_doc_id("This document covers DPDPA and privacy ..."),
                         "privacy-policy")

    def test_refund_keyword(self):
        from src.rag import detect_doc_id
        self.assertEqual(detect_doc_id("Refund terms include 14-day window"),
                         "refund-policy")

    def test_shipping_keyword(self):
        from src.rag import detect_doc_id
        self.assertEqual(detect_doc_id("Force majeure shipping delay in transit"),
                         "shipping-policy")

    def test_unknown_falls_back(self):
        from src.rag import detect_doc_id
        self.assertEqual(detect_doc_id("Random content with no policy keywords"),
                         "unknown-policy")


class ExtractClauses(unittest.TestCase):
    """Tests for src.rag._extract_clauses (regex from parent text)."""

    def test_extracts_top_level_clause(self):
        from src.rag import _extract_clauses
        cs = _extract_clauses("3. Fast Delivery\n3.1 Charge: ₹100")
        self.assertIn("3", cs)
        self.assertIn("3.1", cs)

    def test_extracts_sub_clause(self):
        from src.rag import _extract_clauses
        cs = _extract_clauses("11.4 Pre-Orders During Sales")
        self.assertIn("11.4", cs)

    def test_skips_non_clause_lines(self):
        from src.rag import _extract_clauses
        cs = _extract_clauses("This is a regular paragraph without a clause.")
        self.assertEqual(cs, [])


class _DBAware(unittest.TestCase):
    """Base: skip live-DB tests if parent-child tables aren't populated."""
    @classmethod
    def setUpClass(cls):
        try:
            from src.db import get_session
            from sqlalchemy import text
            s = get_session("evaluator")
            n = s.execute(text("SELECT count(*) FROM app.policy_children")).scalar()
            s.close()
            cls._n = n or 0
            cls._skip = cls._n == 0
        except Exception as exc:
            print(f"[skip] DB not reachable: {exc}")
            cls._skip = True

    def setUp(self):
        if self._skip:
            self.skipTest("policy_children not populated; "
                          "run scripts/ingest_policies_parent_child.py")


class SearchLive(_DBAware):
    """End-to-end src.rag.lookup_policy() against the populated pgvector tables."""

    def test_search_returns_top_k(self):
        from src.rag import lookup_policy
        hits = lookup_policy("force majeure shipping delay", top_k=3)
        self.assertGreaterEqual(len(hits), 1)
        for h in hits:
            self.assertGreaterEqual(h.similarity, 0.0)
            self.assertLessEqual(h.similarity,    1.0)
            self.assertTrue(h.doc_id)
            self.assertTrue(h.text)
            self.assertTrue(h.parent_id)

    def test_doc_filter_narrows(self):
        from src.rag import lookup_policy
        hits = lookup_policy("refund", top_k=5, doc_filter=["refund-policy"])
        for h in hits:
            self.assertEqual(h.doc_id, "refund-policy")

    def test_empty_query_returns_empty(self):
        from src.rag import lookup_policy
        self.assertEqual(lookup_policy("",    top_k=3), [])
        self.assertEqual(lookup_policy("   ", top_k=3), [])

    def test_clause_refs_present_on_returns_hit(self):
        from src.rag import lookup_policy
        hits = lookup_policy("14 day return reporting window defective", top_k=3)
        # The hit should carry extracted clause_refs (e.g. '3.1' for the
        # 14-day Reporting Window in the Return, Refund and Replacement Policy).
        any_with_clauses = any(h.clause_refs for h in hits)
        self.assertTrue(any_with_clauses,
                        f"no clause_refs parsed from any hit; hits={hits[:1]}")


class ExtractPages(unittest.TestCase):
    """Tests for src.rag.extract_pages (PDF → normalized page text)."""

    @classmethod
    def setUpClass(cls):
        cls._pdf = REPO_ROOT / "data" / "policies" / "bytemart-policy-pack.pdf"
        cls._skip = not cls._pdf.exists()

    def setUp(self):
        if self._skip:
            self.skipTest("PDF not at data/policies/bytemart-policy-pack.pdf")

    def test_extracts_40_pages(self):
        from src.rag import extract_pages
        pages = extract_pages(self._pdf)
        self.assertEqual(len(pages), 40)

    def test_first_page_has_normalized_text(self):
        from src.rag import extract_pages
        pages = extract_pages(self._pdf)
        # Page 1 is the cover/title; should have non-empty normalized text.
        self.assertTrue(pages[0][1])
        # CamelCase normalization: Byte Mart is split, not ByteMart.
        self.assertIn("Byte Mart", pages[0][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
