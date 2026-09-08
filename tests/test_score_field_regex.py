"""tests/test_score_field_regex.py — RED tests for the field/regex checks.

Three field/regex checks:
  - no_pii_echo:        no synthetic PII (emails, phones, etc.) in the draft
                        that wasn't already in the inbound email.
  - no_fabricated_amounts: every ₹ amount in the draft appears in the
                        retriever's gathered context (orders + payments).
  - cites_policy_clause: the draft cites a policy_id + clause number
                        extracted from retrieved policy parents.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class TestNoPiiEcho(unittest.TestCase):
    def test_passes_when_draft_has_no_new_emails(self):
        from src.agent.score import check_no_pii_echo
        trace = {
            "retriever": {"customer": {"found": True, "row": {"email": "a@b.com"}}},
            "resolver": {"draft": "Hi, thanks for your message."},
            "input": {"body": "I want a refund."},
        }
        r = check_no_pii_echo(trace, {})
        self.assertTrue(r.passed)

    def test_fails_when_draft_echoes_a_different_email(self):
        from src.agent.score import check_no_pii_echo
        trace = {
            "retriever": {"customer": {"found": True, "row": {"email": "a@b.com"}}},
            "resolver": {"draft": "Forwarding to evil@phisher.com ..."},
            "input": {"body": "I want a refund."},
        }
        r = check_no_pii_echo(trace, {})
        self.assertFalse(r.passed)

    def test_fails_when_draft_reveals_phone(self):
        from src.agent.score import check_no_pii_echo
        trace = {
            "retriever": {"customer": {"found": True, "row": {"email": "a@b.com"}}},
            "resolver": {"draft": "Call me at +91 98765 43210"},
            "input": {"body": "I want a refund."},
        }
        r = check_no_pii_echo(trace, {})
        self.assertFalse(r.passed)


class TestNoFabricatedAmounts(unittest.TestCase):
    def test_passes_when_amount_in_draft_is_in_context(self):
        from src.agent.score import check_no_fabricated_amounts
        trace = {
            "retriever": {"order": {"row": {"total_amount": 54990, "items": [{"sku": "X"}]}},
                         "payments": [{"amount": 54990}]},
            "resolver": {"draft": "Your bill is ₹54,990."},
            "input": {"body": "Why was I charged twice?"},
        }
        r = check_no_fabricated_amounts(trace, {})
        self.assertTrue(r.passed)

    def test_fails_when_draft_invents_amount(self):
        from src.agent.score import check_no_fabricated_amounts
        trace = {
            "retriever": {"order": {"row": {"total_amount": 54990}},
                         "payments": [{"amount": 54990}]},
            "resolver": {"draft": "We'll refund ₹1,23,456 as goodwill."},
            "input": {"body": "..."},
        }
        r = check_no_fabricated_amounts(trace, {})
        self.assertFalse(r.passed)

    def test_ignores_round_numbers_like_100_express_delivery(self):
        """Surrogate fees (100 ₹) may appear in the order's items; tolerated."""
        from src.agent.score import check_no_fabricated_amounts
        trace = {
            "retriever": {
                "order": {"row": {"total_amount": 55090,
                                   "items": [
                                       {"sku": "PS5-DISC-001", "unit_price": 54990},
                                       {"sku": "EXP-DEL-001", "unit_price": 100}]}},
                "payments": [{"amount": 55090}],
            },
            "resolver": {"draft": "Your bill: PS5 ₹54,990 + Express ₹100."},
            "input": {"body": "..."},
        }
        r = check_no_fabricated_amounts(trace, {})
        self.assertTrue(r.passed)


class TestCitesPolicyClause(unittest.TestCase):
    def test_passes_when_draft_cites_clause_in_parents(self):
        from src.agent.score import check_cites_policy_clause
        trace = {
            "retriever": {"policies": [
                {"parent_id": "p1", "doc_id": "returns",
                 "clause_refs": ["3.1"],
                 "text": "3.1 Reporting Window: fourteen (14) calendar days."},
            ]},
            "resolver": {"draft": "Per the returns policy §3.1, you have 14 days."},
            "input": {"body": "..."},
        }
        r = check_cites_policy_clause(trace, {})
        self.assertTrue(r.passed)

    def test_fails_when_draft_cites_unknown_clause(self):
        from src.agent.score import check_cites_policy_clause
        trace = {
            "retriever": {"policies": [
                {"parent_id": "p1", "doc_id": "returns",
                 "clause_refs": ["3.1"],
                 "text": "3.1 Reporting Window"},
            ]},
            "resolver": {"draft": "Per returns §99, you owe nothing."},
            "input": {"body": "..."},
        }
        r = check_cites_policy_clause(trace, {})
        self.assertFalse(r.passed)

    def test_fails_when_draft_does_not_cite_policy_at_all(self):
        from src.agent.score import check_cites_policy_clause
        trace = {
            "retriever": {"policies": [
                {"parent_id": "p1", "doc_id": "refund",
                 "clause_refs": ["3"],
                 "text": "Refund Timelines"},
            ]},
            "resolver": {"draft": "We'll process your refund."},
            "input": {"body": "..."},
        }
        r = check_cites_policy_clause(trace, {})
        self.assertFalse(r.passed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
