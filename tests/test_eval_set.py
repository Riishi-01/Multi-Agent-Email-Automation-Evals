"""tests/test_eval_set.py — RED tests for src/agent/eval_set.py.

eval_set.run_eval_set:
  - Loads BytemartEvals.yaml through a defensive normalizer
    (coerces last_updated date -> str, transaction_id int -> str,
     amount float -> Decimal-as-str).
  - Loops 36 rows through Workflow.run().
  - Writes per-email traces + manifest.json + run.jsonl.
  - Returns the manifest path.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


EVAL_YAML = REPO_ROOT / "data/completeBytemartEvalset/BytemartEvals.yaml"


class TestNormalizer(unittest.TestCase):
    def setUp(self):
        if not EVAL_YAML.exists():
            self.skipTest(f"{EVAL_YAML} not present")
        from src.agent.eval_set import normalize_eval_row
        self.normalize = normalize_eval_row

    def test_last_updated_date_coerced_to_str(self):
        row = {"email_id": "X", "last_updated": date(2026, 9, 6)}
        out = self.normalize(row)
        self.assertIsInstance(out["last_updated"], str)
        self.assertEqual(out["last_updated"], "2026-09-06")

    def test_last_updated_already_str_kept(self):
        row = {"email_id": "X", "last_updated": "2026-09-06"}
        out = self.normalize(row)
        self.assertEqual(out["last_updated"], "2026-09-06")

    def test_transaction_id_int_coerced_to_str(self):
        row = {"email_id": "X",
               "action_sequence": [{"tool": "lookup_payment_by_transaction_id",
                                    "args": {"transaction_id": 72384982}}]}
        out = self.normalize(row)
        self.assertIsInstance(out["action_sequence"][0]["args"]["transaction_id"], str)
        self.assertEqual(out["action_sequence"][0]["args"]["transaction_id"], "72384982")

    def test_amount_float_coerced_to_str_decimal(self):
        row = {"email_id": "X",
               "action_sequence": [{"tool": "lookup_payments_for_order",
                                    "args": {"amount": 5499.0}}]}
        out = self.normalize(row)
        amt = out["action_sequence"][0]["args"]["amount"]
        self.assertIsInstance(amt, str)
        self.assertEqual(Decimal(amt), Decimal("5499.0"))


class TestEvalSetLoading(unittest.TestCase):
    def setUp(self):
        if not EVAL_YAML.exists():
            self.skipTest(f"{EVAL_YAML} not present")

    def test_load_returns_36_rows(self):
        from src.agent.eval_set import load_eval_set
        rows = load_eval_set(EVAL_YAML)
        self.assertEqual(len(rows), 36)

    def test_each_row_has_email_id_and_action_sequence(self):
        from src.agent.eval_set import load_eval_set
        rows = load_eval_set(EVAL_YAML)
        for r in rows:
            self.assertIn("email_id", r)
            self.assertIn("action_sequence", r)
            self.assertIsInstance(r["action_sequence"], list)

    def test_last_updated_is_str_on_every_row(self):
        from src.agent.eval_set import load_eval_set
        rows = load_eval_set(EVAL_YAML)
        for r in rows:
            self.assertIsInstance(r["last_updated"], str,
                                  f"{r['email_id']} last_updated type")

    def test_e28_transaction_id_is_str(self):
        from src.agent.eval_set import load_eval_set
        rows = load_eval_set(EVAL_YAML)
        e28 = next(r for r in rows if r["email_id"] == "E28")
        txn = e28["action_sequence"][1]["args"]["transaction_id"]
        self.assertIsInstance(txn, str)
        self.assertEqual(txn, "72384982")


class TestRunEvalSet(unittest.TestCase):
    """End-to-end: run_eval_set loops 36 rows and writes traces + manifest."""

    def setUp(self):
        if not EVAL_YAML.exists():
            self.skipTest(f"{EVAL_YAML} not present")

    def test_runs_all_36_emails(self):
        from src.agent.eval_set import run_eval_set
        trace_dir = Path("/tmp/test_eval_set_traces")
        if trace_dir.exists():
            for p in trace_dir.glob("*"):
                if p.is_file():
                    p.unlink()
        manifest_path = run_eval_set(EVAL_YAML, trace_dir=trace_dir,
                                     log_path=None)
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["n_emails"], 36)
        # 36 trace files
        traces = list(trace_dir.glob("*.json"))
        # manifest.json also matches the glob
        self.assertGreaterEqual(len(traces), 37)  # 36 + manifest
        # Check one trace has schema_version 1.2 (Phase 5F: added
        # reflexive + final_action fields)
        e1_trace = json.loads((trace_dir / "E1.json").read_text())
        self.assertEqual(e1_trace["schema_version"], "1.2")

    def test_manifest_records_decision_distribution(self):
        from src.agent.eval_set import run_eval_set
        trace_dir = Path("/tmp/test_eval_set_manifest")
        if trace_dir.exists():
            for p in trace_dir.glob("*"):
                if p.is_file():
                    p.unlink()
        manifest_path = run_eval_set(EVAL_YAML, trace_dir=trace_dir)
        manifest = json.loads(manifest_path.read_text())
        self.assertIn("decisions", manifest)
        for k in ("auto_send", "hilt_refund", "hilt_other", "escalate"):
            self.assertIn(k, manifest["decisions"])
            self.assertIsInstance(manifest["decisions"][k], int)
        # Total must equal 36
        self.assertEqual(sum(manifest["decisions"].values()), 36)

    def test_manifest_records_timing_and_cost_totals(self):
        from src.agent.eval_set import run_eval_set
        trace_dir = Path("/tmp/test_eval_set_timing")
        if trace_dir.exists():
            for p in trace_dir.glob("*"):
                if p.is_file():
                    p.unlink()
        manifest_path = run_eval_set(EVAL_YAML, trace_dir=trace_dir)
        manifest = json.loads(manifest_path.read_text())
        self.assertIn("timing_ms_total", manifest)
        self.assertGreaterEqual(manifest["timing_ms_total"], 0)


class TestDeferredFlag(unittest.TestCase):
    def test_deferred_is_false_in_phase_4d(self):
        from src.agent import eval_set
        self.assertFalse(eval_set.DEFERRED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
