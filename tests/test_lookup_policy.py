"""tests/test_lookup_policy.py — RED tests for src/rag.lookup_policy.

The function takes a query, embeds it, finds the top-k=3 nearest CHILDREN,
dedupes to PARENTS, and returns each parent once with the highest child
similarity seen. Each hit carries clause_refs extracted from the parent text.

DB-backed (requires app.policy_children + app.policy_parents to be
populated by scripts/ingest_policies_parent_child.py).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class _Ingested(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from sqlalchemy import text as _t
            from src.db import get_session
            s = get_session("evaluator")
            n_parents  = s.execute(_t("SELECT COUNT(*) FROM app.policy_parents")).scalar()
            n_children = s.execute(_t("SELECT COUNT(*) FROM app.policy_children")).scalar()
            s.close()
            cls._ok = (n_parents or 0) > 0 and (n_children or 0) > 0
        except Exception as exc:
            print(f"[skip] DB not reachable: {exc}")
            cls._ok = False

    def setUp(self):
        if not getattr(self, "_ok", False):
            self.skipTest("policy_parents/policy_children not populated; "
                          "run scripts/ingest_policies_parent_child.py first")


class TestLookupPolicy(_Ingested):
    def test_returns_at_least_one_hit(self):
        from src.rag import lookup_policy
        hits = lookup_policy("force majeure shipping delay", top_k=3)
        self.assertGreaterEqual(len(hits), 1)

    def test_similarity_in_range(self):
        from src.rag import lookup_policy
        hits = lookup_policy("refund timelines", top_k=3)
        for h in hits:
            self.assertGreaterEqual(h.similarity, 0.0)
            self.assertLessEqual(h.similarity, 1.0)

    def test_top_k_dedupes_to_parents(self):
        from src.rag import lookup_policy
        hits = lookup_policy("DPDPA data principal rights erasure", top_k=3)
        # Each parent appears at most once in the hits.
        ids = [h.parent_id for h in hits]
        self.assertEqual(len(ids), len(set(ids)),
                         f"duplicate parent_ids in hits: {ids}")

    def test_hits_have_clause_refs(self):
        from src.rag import lookup_policy
        hits = lookup_policy("14 day return window defective on arrival", top_k=3)
        # At least one hit should have clause_refs parsed (the Returns section
        # has clause numbers like 3, 3.1, 11).
        any_with_clauses = any(len(h.clause_refs) > 0 for h in hits)
        self.assertTrue(any_with_clauses,
                        f"no clause_refs parsed from any hit; sample={hits[:1]}")

    def test_doc_filter_narrows(self):
        from src.rag import lookup_policy
        hits = lookup_policy("refund", top_k=5, doc_filter=["refund-policy"])
        # Every hit must come from the filtered doc_id (or be empty if none match).
        for h in hits:
            self.assertEqual(h.doc_id, "refund-policy")

    def test_empty_query_returns_empty(self):
        from src.rag import lookup_policy
        self.assertEqual(lookup_policy("", top_k=3), [])
        self.assertEqual(lookup_policy("   ", top_k=3), [])

    def test_top_k_clamped(self):
        from src.rag import lookup_policy
        # top_k is clamped to [1, 10]; requesting 100 returns at most 10 parents.
        hits = lookup_policy("warranty", top_k=100)
        self.assertLessEqual(len(hits), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
