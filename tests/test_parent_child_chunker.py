"""tests/test_parent_child_chunker.py — RED tests for the Phase 4B chunker.

The chunker produces (parents, children) tuples from raw text:
  - parents:  ~1200 tokens each (configurable via env RAG_PARENT_TOKENS)
  - children: ~300 tokens each, 50-token overlap inside each parent
  - children.parent_id must resolve to the parent's id
  - every parent's text equals the concat of its children's text slices
    (with overlap stripped — see test_overlap_stripping)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.rag import (
    normalize_text,
    pack_parents,
    slice_children,
    chunk_document,
)


SAMPLE = """
ByteMart Legal Terms.

1. Introduction.
This Policy applies to all Orders placed on the ByteMart Platform by Buyers located in India.
The Platform is operated by Byte Mart Private Limited ("ByteMart", "we", "us", or "our").

2. Eligibility.
To place an Order, you must be at least 18 years of age and a resident of India. We reserve
the right to refuse service to anyone for any reason at any time. The Products on the Platform
are intended for personal and household use only and not for commercial resale.

3. Refunds.
Subject to the Return, Refund and Replacement Policy, refunds shall be processed within 3-7
business days of approval. Refunds for COD orders include the COD charge. Refunds for prepaid
orders are credited to the original payment method. ByteMart may, at its sole discretion, offer
store credit in lieu of a monetary refund where appropriate. Force majeure events may delay
refund processing.

4. Force Majeure.
ByteMart shall not be liable for any delay or failure in delivery caused by force majeure
events including but not limited to natural disasters, pandemics, government actions, lockdowns,
war, terrorism, civil unrest, labour disputes, transportation disruptions, and failures of
telecommunications infrastructure.

5. Privacy.
Personal data is processed in accordance with our Privacy Policy and the DPDPA, 2023. Data
Principals have the right to access, correct, and erase their personal data. Erasure requests
will be actioned within thirty (30) calendar days of verification.
"""


class TestNormalize(unittest.TestCase):
    def test_splits_camelcase(self):
        self.assertIn("Byte Mart", normalize_text("ByteMartLegal"))

    def test_splits_letter_digit_boundary(self):
        self.assertIn("BIS", normalize_text("BISregistration").split()[0])
        self.assertIn("registration", normalize_text("BISregistration").split()[1])

    def test_collapses_whitespace(self):
        self.assertNotIn("\n\n", normalize_text("a\n\n\nb  c"))
        self.assertEqual(normalize_text("a   b"), "a b")


class TestPackParents(unittest.TestCase):
    def test_returns_at_least_one_parent(self):
        parents = pack_parents(SAMPLE, parent_tokens=120, page_from=1, page_to=1,
                               doc_id="test", version="v1")
        self.assertGreaterEqual(len(parents), 1)

    def test_each_parent_under_token_budget(self):
        parents = pack_parents(SAMPLE, parent_tokens=200, page_from=1, page_to=1,
                               doc_id="t", version="v1")
        for p in parents:
            self.assertLessEqual(p.token_count, 200)

    def test_concat_equals_normalized_input(self):
        text = normalize_text(SAMPLE)
        parents = pack_parents(text, parent_tokens=200, page_from=1, page_to=1,
                               doc_id="t", version="v1")
        joined = " ".join(p.text for p in parents)
        self.assertEqual(joined, text)

    def test_parent_ids_unique(self):
        parents = pack_parents(SAMPLE, parent_tokens=120, page_from=1, page_to=1,
                               doc_id="t", version="v1")
        ids = [p.parent_id for p in parents]
        self.assertEqual(len(ids), len(set(ids)))


class TestSliceChildren(unittest.TestCase):
    def test_returns_children_inside_parent(self):
        parents = pack_parents(SAMPLE, parent_tokens=200, page_from=1, page_to=1,
                               doc_id="t", version="v1")
        children = slice_children(parents[0], child_tokens=50, overlap_tokens=10)
        self.assertGreaterEqual(len(children), 2)

    def test_children_link_to_parent(self):
        parents = pack_parents(SAMPLE, parent_tokens=200, page_from=1, page_to=1,
                               doc_id="t", version="v1")
        children = slice_children(parents[0], child_tokens=50, overlap_tokens=10)
        for c in children:
            self.assertEqual(c.parent_id, parents[0].parent_id)

    def test_children_under_token_budget(self):
        parents = pack_parents(SAMPLE, parent_tokens=200, page_from=1, page_to=1,
                               doc_id="t", version="v1")
        children = slice_children(parents[0], child_tokens=50, overlap_tokens=10)
        for c in children:
            self.assertLessEqual(c.token_count, 50)

    def test_children_have_increasing_chunk_seq(self):
        parents = pack_parents(SAMPLE, parent_tokens=200, page_from=1, page_to=1,
                               doc_id="t", version="v1")
        children = slice_children(parents[0], child_tokens=50, overlap_tokens=10)
        seqs = [c.chunk_seq for c in children]
        self.assertEqual(seqs, sorted(seqs))


class TestChunkDocument(unittest.TestCase):
    """End-to-end: text -> (parents, children) for the whole sample."""

    def test_chunk_document_returns_both_lists(self):
        parents, children = chunk_document(
            text=SAMPLE, doc_id="test-doc", version="v1",
            page_from=1, page_to=1,
            parent_tokens=200, child_tokens=50, overlap_tokens=10,
        )
        self.assertGreaterEqual(len(parents), 1)
        self.assertGreaterEqual(len(children), 2)
        # Every child must link to one of the parents
        parent_ids = {p.parent_id for p in parents}
        for c in children:
            self.assertIn(c.parent_id, parent_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
