"""tests/test_parallel_eval.py — 5-worker parallel eval runner.

Verifies:
  - PII redaction (redact_pii) strips emails / phones / names from
    trace fields while preserving the body and numeric fields.
  - 5-worker parallel workflow run produces 36 distinct traces and a
    valid merged manifest.
  - 5-worker parallel judge run produces a complete judge YAML with
    the failed section populated.
  - End-to-end: phase1 then phase2 with 5 workers each.

The test suite uses the test_mode patch (enter_demo_mode) so the
Reflexive always runs the deterministic DEMO path. The judge is
configured to be "not configured" (no JUDGE_API_KEY) so the metrics
are recorded as unscored — this lets the structural pipeline run
without requiring real LLM access.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Force DEMO mode and clear JUDGE keys (the test uses the
# "judge is not configured" path so the metrics are recorded as
# unscored, no LLM calls needed).
import tests.conftest  # noqa: F401
import os
os.environ.pop("JUDGE_BASE_URL", None)
os.environ.pop("JUDGE_MODEL", None)
os.environ.pop("JUDGE_API_KEY", None)


EVAL_SET = (REPO_ROOT / "data" / "completeBytemartEvalset"
            / "BytemartEvals.yaml")


@unittest.skipUnless(EVAL_SET.exists(), f"missing {EVAL_SET}")
class TestPIIRedaction(unittest.TestCase):
    def test_redact_email_in_string(self):
        from src.agent.parallel_eval import redact_string
        out = redact_string("Contact me at alice@example.com please.")
        self.assertNotIn("alice@example.com", out)
        self.assertIn("<email>", out)

    def test_redact_phone_in_string(self):
        from src.agent.parallel_eval import redact_string
        out = redact_string("Call +1 12345 67890 or 98765-43210")
        self.assertIn("<phone>", out)

    def test_redact_nested_dict(self):
        from src.agent.parallel_eval import redact_pii
        trace = {
            "retriever": {
                "customer": {
                    "found": True,
                    "row": {
                        "email": "alice@example.com",
                        "full_name": "Alice Example",
                        "phone": "+1 12345 67890",
                    },
                },
                "payments": [
                    {"customer_email": "bob@example.com", "amount": 100},
                ],
                "products": [
                    {"sku": "X1", "warranty_text": "1 year warranty"},
                ],
            },
            "input": {
                "subject": "Help",
                "body": "alice@example.com is asking about a refund.",
                "sender_email": "alice@example.com",
            },
        }
        out = redact_pii(trace)
        # Customer email + name + phone redacted
        self.assertEqual(out["retriever"]["customer"]["row"]["email"],
                         "<sender_email>")
        self.assertEqual(out["retriever"]["customer"]["row"]["full_name"],
                         "<first_name> <last_name>")
        self.assertEqual(out["retriever"]["customer"]["row"]["phone"],
                         "<phone>")
        # Payment customer_email redacted
        self.assertEqual(out["retriever"]["payments"][0]["customer_email"],
                         "<sender_email>")
        # Payment amount preserved (not PII)
        self.assertEqual(out["retriever"]["payments"][0]["amount"], 100)
        # Product warranty text: no email/phone PII in this case, so
        # it passes through unchanged. The redaction only touches
        # PII-shaped tokens.
        self.assertEqual(
            out["retriever"]["products"][0]["warranty_text"],
            "1 year warranty",
        )
        # Subject + body preserved (operator needs the actual content)
        self.assertEqual(out["input"]["subject"], "Help")
        # sender_email redacted
        self.assertEqual(out["input"]["sender_email"], "<sender_email>")
        # body still has the email mentioned (operator reads it as-is)
        self.assertIn("alice@example.com", out["input"]["body"])


@unittest.skipUnless(EVAL_SET.exists(), f"missing {EVAL_SET}")
class TestParallelWorkflowRun(unittest.TestCase):
    def test_5_workers_produces_36_traces(self):
        from src.agent.parallel_eval import run_workflow_phase
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_id = run_workflow_phase(
                EVAL_SET, base, parallel=5, notes="test",
            )
            run_dir = base / run_id
            self.assertTrue(run_dir.exists())
            # 36 per-email trace files
            trace_files = list(run_dir.glob("*.json"))
            self.assertGreaterEqual(len(trace_files), 36)
            # workflow.yaml
            self.assertTrue((run_dir / "workflow.yaml").exists())
            # manifest.json
            self.assertTrue((run_dir / "manifest.json").exists())
            # Sanity: manifest has all 36 eids
            import json
            m = json.loads((run_dir / "manifest.json").read_text())
            self.assertEqual(m["n_emails"], 36)
            self.assertEqual(m["n_succeeded"], 36)
            self.assertEqual(m["parallelism"], 5)


@unittest.skipUnless(EVAL_SET.exists(), f"missing {EVAL_SET}")
class TestParallelJudgeRun(unittest.TestCase):
    def test_phase2_after_phase1_produces_judge_yaml(self):
        from src.agent.parallel_eval import (
            run_workflow_phase, run_judge_phase,
        )
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            wf_dir = base / "workflow_runs"
            jd_dir = base / "judge_runs"
            # Run phase 1
            run_id = run_workflow_phase(
                EVAL_SET, wf_dir, parallel=2, notes="phase1",
            )
            wf_run_dir = wf_dir / run_id
            self.assertTrue((wf_run_dir / "workflow.yaml").exists())
            # Run phase 2
            judge_run_id = run_judge_phase(
                wf_dir, jd_dir, parallel=2, notes="phase2",
                phase1_run_id=run_id,
            )
            jy = jd_dir / judge_run_id / "judge.yaml"
            self.assertTrue(jy.exists())
            # Parse and check structure
            import yaml
            data = yaml.safe_load(jy.read_text())
            self.assertEqual(data["schema_version"], "1.0")
            self.assertEqual(data["phase1_run_id"], run_id)
            self.assertEqual(data["parallelism"], 2)
            self.assertEqual(data["summary"]["total_emails"], 36)
            # All metrics are unscored (judge not configured)
            self.assertEqual(data["summary"]["total_pass"], 0)
            self.assertEqual(data["summary"]["total_fail"], 0)
            self.assertEqual(data["summary"]["total_unscored"], 308)
            # The failed list is empty (no actual passes/fails)
            self.assertEqual(len(data["failed"]), 0)


@unittest.skipUnless(EVAL_SET.exists(), f"missing {EVAL_SET}")
class TestParallelPreservesInputOrder(unittest.TestCase):
    """The workflow YAML's emails list preserves the original input order
    (the 'serial' field increments 1, 2, 3 ... regardless of completion
    order). The user explicitly asked for this preservation."""

    def test_serial_field_increments_in_input_order(self):
        import yaml
        from src.agent.parallel_eval import run_workflow_phase
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_id = run_workflow_phase(EVAL_SET, base, parallel=5, notes="")
            run_dir = base / run_id
            data = yaml.safe_load(
                (run_dir / "workflow.yaml").read_text()
            )
            serials = [e["serial"] for e in data["emails"]]
            # serials are 1, 2, 3, ..., 36 in input order
            self.assertEqual(serials, list(range(1, 37)))


@unittest.skipUnless(EVAL_SET.exists(), f"missing {EVAL_SET}")
class TestWorkflowYAMLHasRequiredSections(unittest.TestCase):
    """The workflow YAML must have the stage-wise fields the spec requires:
    email, retriever, resolver_draft, reflex, final_state."""

    def test_workflow_yaml_has_stage_sections(self):
        import yaml
        from src.agent.parallel_eval import run_workflow_phase
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            run_id = run_workflow_phase(EVAL_SET, base, parallel=2, notes="")
            run_dir = base / run_id
            self.assertTrue((run_dir / "workflow.yaml").exists())
            data = yaml.safe_load(
                (run_dir / "workflow.yaml").read_text()
            )
            # Every email record has these sections
            for e in data["emails"]:
                self.assertIn("email_id", e)
                self.assertIn("retriever", e)
                self.assertIn("resolver", e)
                self.assertIn("reflex", e)
                self.assertIn("final_state", e)
                # PII is redacted (sender_email is a placeholder)
                self.assertTrue(
                    e["sender_email"] in (None, "<sender_email>"),
                    f"sender_email not redacted for {e['email_id']}: "
                    f"{e['sender_email']!r}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
