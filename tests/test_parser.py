"""tests/test_parser.py — unit tests for src/parser.py.

Maps 1:1 to src/parser.py. No DB, no OpenAI, no LLM.

Prerequisite for T-002 / T-003 input validation in src/tools.py.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class ExtractOrderId(unittest.TestCase):
    """Tests for src.parser.extract_order_id."""

    def test_typical(self):
        from src.parser import extract_order_id
        self.assertEqual(extract_order_id("Order BM123244 damaged"), "BM123244")

    def test_lowercase_preserved(self):
        from src.parser import extract_order_id
        self.assertEqual(extract_order_id("order bm728349 arrived"), "bm728349")

    def test_no_order_id_returns_none(self):
        from src.parser import extract_order_id
        self.assertIsNone(extract_order_id("no order id here"))

    def test_pincode_returns_none(self):
        """6-digit numbers are pincodes, NOT order ids (8-char regex)."""
        from src.parser import extract_order_id
        self.assertIsNone(extract_order_id("pincode 400001"))

    def test_empty_returns_none(self):
        from src.parser import extract_order_id
        self.assertIsNone(extract_order_id(""))


class ExtractTxnId(unittest.TestCase):
    """Tests for src.parser.extract_txn_id."""

    def test_typical(self):
        from src.parser import extract_txn_id
        self.assertEqual(extract_txn_id("txn 72384982 charged twice"), "72384982")

    def test_pincode_returns_none(self):
        from src.parser import extract_txn_id
        self.assertIsNone(extract_txn_id("pincode 400001"))

    def test_no_txn_returns_none(self):
        from src.parser import extract_txn_id
        self.assertIsNone(extract_txn_id("nothing"))

    def test_empty_returns_none(self):
        from src.parser import extract_txn_id
        self.assertIsNone(extract_txn_id(""))


class Validators(unittest.TestCase):
    """Tests for is_valid_order_id and is_valid_txn_id (used by T-002, T-003 input validation)."""

    def test_order_id_format(self):
        from src.parser import is_valid_order_id
        self.assertTrue(is_valid_order_id("BM123244"))
        self.assertTrue(is_valid_order_id("bm123244"))        # case-insensitive accepted
        self.assertTrue(is_valid_order_id("ab000000"))        # 2 letters + 6 digits
        self.assertFalse(is_valid_order_id("BAD"))           # not the right format
        self.assertFalse(is_valid_order_id("BM12324"))        # 5 digits, too short
        self.assertFalse(is_valid_order_id("BM1232445"))     # 7 digits, too long
        self.assertFalse(is_valid_order_id("123456"))        # no letters
        self.assertFalse(is_valid_order_id(""))              # empty

    def test_txn_id_format(self):
        from src.parser import is_valid_txn_id
        self.assertTrue(is_valid_txn_id("72384982"))         # golden E28
        self.assertTrue(is_valid_txn_id("00000000"))         # 8-digit numeric
        self.assertFalse(is_valid_txn_id("400001"))         # 6 digits, too short
        self.assertFalse(is_valid_txn_id("A0000000"))       # not pure digits
        self.assertFalse(is_valid_txn_id("7238498"))         # 7 digits
        self.assertFalse(is_valid_txn_id(""))                # empty


class MultiExtractor(unittest.TestCase):
    """Tests for the plural extractors (all matching IDs in document order)."""

    def test_multiple_order_ids_deduped(self):
        from src.parser import extract_order_ids
        ids = extract_order_ids("Order BM123244 and BM293482, confirmed by BM123244 again")
        self.assertEqual(ids, ["BM123244", "BM293482"])

    def test_multiple_txn_ids_deduped(self):
        from src.parser import extract_txn_ids
        ids = extract_txn_ids("txn 72384982 then 72384982 again; new 11112222")
        self.assertEqual(ids, ["72384982", "11112222"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
