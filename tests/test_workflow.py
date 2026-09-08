"""tests/test_workflow.py — workflow.py orchestration tests (Phase 4C)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class WorkflowHappyPath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path("/tmp/test_workflow_traces")
        self.tmp.mkdir(exist_ok=True)
        for p in self.tmp.glob("*.json"):
            p.unlink()
        for p in self.tmp.glob("*.json.tmp"):
            p.unlink()
        from src.agent.workflow import Workflow
        from src.agent.types import Email
        self.Email = Email
        self.Workflow = Workflow

    def _new(self) -> "Workflow":
        return self.Workflow(trace_dir=self.tmp)

    def test_auto_send_emits_sent(self):
        w = self._new()
        trace = w.run("T_auto",
                      self.Email("PS5 delivery",
                                 "Hi, I would like to know the expected "
                                 "delivery date for my PS5 order.",
                                 "sanya.delhi@gmail.com"))
        self.assertEqual(trace.outcome, "sent")
        self.assertEqual(trace.resolver["action"], "auto_send")
        self.assertIsNone(trace.error)
        self.assertTrue((self.tmp / "T_auto.json").exists())
        self.assertFalse((self.tmp / "T_auto.json.tmp").exists())

    def test_hilt_refund_emits_pending(self):
        w = self._new()
        trace = w.run("T_hilt_refund",
                      self.Email("Refund request",
                                 "My PS5 arrived damaged. I want a refund.",
                                 "rishit1@gmail.com"))
        self.assertEqual(trace.outcome, "pending")
        self.assertEqual(trace.resolver["action"], "hilt_refund")
        self.assertIn("[PENDING APPROVAL]", trace.resolver["draft"])
        self.assertIsNotNone(trace.resolver["hilt_reason"])

    def test_hilt_other_emits_pending(self):
        w = self._new()
        trace = w.run("T_hilt_other",
                      self.Email("Address change",
                                 "Change the delivery address.",
                                 "sneha.mumbai86@gmail.com"))
        self.assertEqual(trace.outcome, "pending")
        self.assertEqual(trace.resolver["action"], "hilt_other")
        self.assertIn("[PENDING APPROVAL]", trace.resolver["draft"])
        self.assertIsNotNone(trace.resolver["hilt_reason"])

    def test_escalate_only_via_resolver_directly(self):
        """escalate is reachable only when the resolver is called with NO
        ctx and NO sender."""
        from src.agent.resolver_agent.agent_resolver import ResolverAgent
        agent = ResolverAgent()
        out = agent.run(
            subject="Escalate",
            body="Want your manager.",
            sender_email="",
            context={"customer": {"found": False, "row": None},
                     "order": {"found": False, "row": None},
                     "payments": [], "products": [], "policies": []},
        )
        self.assertEqual(out.action, "escalate")
        self.assertIsNone(out.draft)
        self.assertEqual(out.intent, "escalation")

    def test_trace_file_schema_v11(self):
        w = self._new()
        trace = w.run("T_schema",
                      self.Email("S", "Any body.", "alice@example.com"))
        path = self.tmp / "T_schema.json"
        self.assertTrue(path.exists())
        loaded = json.loads(path.read_text())
        for k in ("schema_version", "email_id", "captured_at", "input",
                  "retriever", "resolver", "outcome", "timing_ms"):
            self.assertIn(k, loaded, f"trace JSON missing field: {k}")
        # Phase 5F: schema_version is 1.2 (adds reflexive + final_action).
        self.assertEqual(loaded["schema_version"], "1.2")
        self.assertEqual(loaded["email_id"], "T_schema")
        self.assertEqual(loaded["input"]["sender_email"], "alice@example.com")
        # Phase 5F: new fields are present
        self.assertIn("reflexive", loaded)
        self.assertIn("final_action", loaded)
        # Retriever now has tool_calls[] (audit log)
        self.assertIn("tool_calls", loaded["retriever"])
        # Resolver has hilt_reason slot
        self.assertIn("hilt_reason", loaded["resolver"])

    def test_timings_non_negative(self):
        w = self._new()
        trace = w.run("T_time",
                      self.Email("S", "Body.", "x@example.com"))
        self.assertGreaterEqual(trace.timing_ms["retriever"], 0)
        self.assertGreaterEqual(trace.timing_ms["resolver"], 0)
        self.assertGreaterEqual(trace.timing_ms["total"],
                                 trace.timing_ms["resolver"])

    def test_retriever_logged_lookup_customer_first(self):
        w = self._new()
        # Body carries an order ID so the email is NOT info-only;
        # Phase 5G fix skips lookup_customer for info-only emails.
        trace = w.run("T_audit",
                      self.Email("Order inquiry",
                                 "My order id is BM200001.",
                                 "alice@example.com"))
        d = json.loads((self.tmp / "T_audit.json").read_text())
        calls = d["retriever"]["tool_calls"]
        self.assertGreaterEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "lookup_customer")
        self.assertEqual(calls[0]["args"]["email"], "alice@example.com")


class WorkflowErrorPath(unittest.TestCase):
    def test_resolver_exception_produces_error_trace(self):
        from src.agent.workflow import Workflow
        from src.agent.types import Email

        class _Boom:
            def run(self, **_): raise RuntimeError("kaboom")

        tmp = Path("/tmp/test_workflow_err")
        tmp.mkdir(exist_ok=True)
        for p in tmp.glob("*.json"):
            p.unlink()
        w = Workflow(trace_dir=tmp, resolver=_Boom(), retriever=None)
        # When retriever=None the workflow falls back to a stub context; the
        # resolver still raises, which the workflow must catch.
        trace = None
        try:
            trace = w.run("T_err", Email("S", "Body", "a@b.com"))
        except Exception:
            self.fail("Workflow.run must not propagate agent exceptions")
        self.assertIsNotNone(trace)
        self.assertEqual(trace.outcome, "error")
        self.assertIn("kaboom", trace.error or "")
        self.assertTrue((tmp / "T_err.json").exists())


class AddNewAgentPattern(unittest.TestCase):
    """Demonstrates the architectural promise: a 4th agent plugs in
    without touching workflow.py substantively."""

    def test_mock_observer_agent_runs_after_resolver(self):
        from src.agent.workflow import Workflow
        from src.agent.types import Email
        from src.agent.resolver_agent.agent_resolver import ResolverAgent

        observed = {}

        class Summarizer:
            def run(self, *, email, trace):
                observed["summary"] = trace.to_dict().get("resolver", {}).get("action")
                return {"summary": observed["summary"]}

        from src.agent.workflow import Workflow as _W

        class Extended(_W):
            def run(self, email_id, email):
                trace = super().run(email_id, email)
                summary = Summarizer().run(email=email, trace=trace)
                trace_dict = trace.to_dict()
                trace_dict["summarizer"] = summary
                (self.trace_dir / f"{trace.email_id}.json").write_text(
                    json.dumps(trace_dict, default=str, indent=2))
                return trace

        tmp = Path("/tmp/test_workflow_ext")
        tmp.mkdir(exist_ok=True)
        for p in tmp.glob("*.json"):
            p.unlink()
        w = Extended(trace_dir=tmp, resolver=ResolverAgent())
        w.run("T_ext", Email("S", "Hi", "alice@example.com"))
        loaded = json.loads((tmp / "T_ext.json").read_text())
        self.assertIn("summarizer", loaded)
        self.assertIn("summary", loaded["summarizer"])


# ---------------------------------------------------------------------------
# Phase 5 audit fixes: error-path resilience
# ---------------------------------------------------------------------------
class WorkflowErrorPath(unittest.TestCase):
    """When a downstream agent raises, the trace must (1) preserve
    whatever data the retriever already produced, (2) record real
    per-agent timing, (3) classify outcome as 'error'."""

    def _setup(self):
        import tempfile
        from src.agent.workflow import Workflow
        from src.agent.types import Email
        self.tmp = Path(tempfile.mkdtemp())
        for p in self.tmp.glob("*.json"):
            p.unlink()
        for p in self.tmp.glob("*.json.tmp"):
            p.unlink()
        self.Workflow = Workflow
        self.Email = Email

    def setUp(self):
        self._setup()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_retriever_data_preserved_when_resolver_raises(self):
        """Bug fix: trace.retriever must NOT be reset to {} when only
        the resolver raised. The retriever's data is the most valuable
        part of the trace (the resolver's draft is lost either way)."""
        from src.agent.retriever_agent.agent_retriever import RetrieverContext

        class _GoodR:
            def run(self, *, subject, body, sender_email):
                ctx = RetrieverContext(_sender_email=sender_email)
                ctx.tool_calls = [
                    {"tool": "lookup_customer",
                     "args": {"email": sender_email},
                     "result_keys": [], "duration_ms": 5},
                ]
                return ctx

        class _BoomRes:
            def run(self, **_): raise RuntimeError("resolver kaboom")

        w = self.Workflow(trace_dir=self.tmp,
                          retriever=_GoodR(),
                          resolver=_BoomRes())
        trace = w.run("T_partial",
                      self.Email("S", "B", "alice@example.com"))

        # Outcome is error
        self.assertEqual(trace.outcome, "error")
        self.assertIn("resolver kaboom", trace.error or "")
        # Retriever data is preserved
        self.assertNotEqual(trace.retriever, {})
        self.assertEqual(trace.retriever.get("_sender_email"),
                         "alice@example.com")
        self.assertEqual(len(trace.retriever.get("tool_calls", [])), 1)
        self.assertEqual(
            trace.retriever["tool_calls"][0]["tool"],
            "lookup_customer")
        # Resolver is empty (it never produced a result)
        self.assertEqual(trace.resolver, {})

    def test_timing_preserved_on_resolver_error(self):
        """Bug fix: when resolver raises, timing_ms.retriever must reflect
        the actual time spent, not be reset to 0."""
        import time

        class _SlowR:
            def run(self, *, subject, body, sender_email):
                time.sleep(0.05)  # 50ms
                from src.agent.retriever_agent.agent_retriever import RetrieverContext
                return RetrieverContext(_sender_email=sender_email)

        class _BoomRes:
            def run(self, **_): raise RuntimeError("kaboom")

        w = self.Workflow(trace_dir=self.tmp,
                          retriever=_SlowR(),
                          resolver=_BoomRes())
        trace = w.run("T_slow",
                      self.Email("S", "B", "a@b.com"))
        # Retriever timing must be > 0
        self.assertGreater(
            trace.timing_ms["retriever"], 0,
            f"retriever timing was {trace.timing_ms['retriever']}")
        # Total must be >= retriever (we know resolver threw immediately)
        self.assertGreaterEqual(
            trace.timing_ms["total"], trace.timing_ms["retriever"])

    def test_retriever_error_uses_warning_not_exception(self):
        """Bug fix: workflow logs a warning, not a full traceback, on
        retriever failure. (We don't assert the log call here, just the
        outcome/timing/error fields.)"""
        class _BoomR:
            def run(self, **_): raise RuntimeError("retriever kaboom")

        class _Res:
            def run(self, **_): raise RuntimeError("should not be called")

        w = self.Workflow(trace_dir=self.tmp,
                          retriever=_BoomR(),
                          resolver=_Res())
        trace = w.run("T_boom",
                      self.Email("S", "B", "a@b.com"))
        self.assertEqual(trace.outcome, "error")
        self.assertIn("retriever kaboom", trace.error or "")

    def test_outcome_is_error_when_resolver_fails(self):
        from src.agent.retriever_agent.agent_retriever import RetrieverContext
        class _GoodR:
            def run(self, *, subject, body, sender_email):
                return RetrieverContext(_sender_email=sender_email)
        class _BoomRes:
            def run(self, **_): raise RuntimeError("kaboom")

        w = self.Workflow(trace_dir=self.tmp,
                          retriever=_GoodR(), resolver=_BoomRes())
        trace = w.run("T1", self.Email("S", "B", "a@b.com"))
        self.assertEqual(trace.outcome, "error")


class RunOneScriptTests(unittest.TestCase):
    """scripts/run_one.py: --demo flag works without LLM calls."""

    def test_demo_flag_does_not_call_openai(self):
        import os, subprocess, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            r = subprocess.run(
                ["python3", "scripts/run_one.py",
                 "--deterministic-fallback",
                 "--email-id", "RUN_DEMO",
                 "--subject", "Order inquiry",
                 "--email-content", "Hi, where is my order? My order id is BM200001.",
                 "--sender-email", "sanya.delhi@gmail.com",
                 "--trace-dir", td],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r.returncode, 0,
                f"stdout={r.stdout!r}\nstderr={r.stderr!r}")
            # DEMO path produces auto_send + a non-None draft
            self.assertIn("resolver.action: 'auto_send'", r.stdout)
            # Phase 5F: outcome depends on the reflexive's verdict. The
            # DEMO reflexive may demote "complaint" + missing keyword
            # phrasings to hilt_other pending (safety net). For this
            # plain "where is my order?" we expect outcome=sent.
            self.assertIn("outcome:       sent", r.stdout)
            # Trace file was written
            trace = json.loads((Path(td) / "RUN_DEMO.json").read_text())
            self.assertEqual(trace["schema_version"], "1.2")
            self.assertEqual(trace["outcome"], "sent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
