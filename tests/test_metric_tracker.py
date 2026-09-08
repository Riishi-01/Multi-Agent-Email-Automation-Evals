"""tests/test_metric_tracker.py — RED tests for src/agent/metric_tracker.py.

Cost model:
  _DEFAULT_PRICING maps model name -> {in: $/1k tok, out: $/1k tok}
  Unknown model -> $0.00 with a warning logged.

MetricTracker collects per-call events and emits:
  - per-email aggregates
  - per-run aggregates
  - cost.json (one event per call + per-email + per-run summary)
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class TestPricingRegistry(unittest.TestCase):
    def test_known_models_have_rates(self):
        from src.agent.metric_tracker import _DEFAULT_PRICING
        for m in ("gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet-latest",
                  "claude-3-haiku-20240307"):
            self.assertIn(m, _DEFAULT_PRICING)
            self.assertIn("in", _DEFAULT_PRICING[m])
            self.assertIn("out", _DEFAULT_PRICING[m])

    def test_unknown_model_returns_zero_cost_with_warning(self):
        from src.agent.metric_tracker import compute_cost, _DEFAULT_PRICING
        # Unknown model returns 0.00 cost + a warning flag
        cost, warn = compute_cost(model="unknown-model-xyz",
                                   tokens_in=1000, tokens_out=500)
        self.assertEqual(cost, 0.0)
        self.assertTrue(warn)


class TestComputeCost(unittest.TestCase):
    def test_gpt4o_mini_known_rate(self):
        from src.agent.metric_tracker import compute_cost
        # 1000 in, 500 out @ gpt-4o-mini rates
        cost, warn = compute_cost("gpt-4o-mini", 1000, 500)
        self.assertGreater(cost, 0.0)
        self.assertFalse(warn)

    def test_zero_tokens_yields_zero_cost(self):
        from src.agent.metric_tracker import compute_cost
        cost, warn = compute_cost("gpt-4o-mini", 0, 0)
        self.assertEqual(cost, 0.0)

    def test_cost_monotonic_in_tokens(self):
        from src.agent.metric_tracker import compute_cost
        a, _ = compute_cost("gpt-4o-mini", 100, 100)
        b, _ = compute_cost("gpt-4o-mini", 200, 200)
        self.assertGreater(b, a)


class TestMetricTracker(unittest.TestCase):
    def test_record_call_appends_event(self):
        from src.agent.metric_tracker import MetricTracker
        tmp = Path("/tmp/test_metric_tracker")
        tmp.mkdir(exist_ok=True)
        for p in tmp.glob("*"):
            if p.is_file():
                p.unlink()
        mt = MetricTracker(out_path=tmp / "cost.json")
        mt.record(email_id="E1", model="gpt-4o-mini",
                  tokens_in=100, tokens_out=50, duration_ms=200,
                  judge_model_for_event="judge_call")
        mt.record(email_id="E1", model="gpt-4o-mini",
                  tokens_in=50, tokens_out=25, duration_ms=100)
        mt.finalize()

        # Per-email aggregate
        per_email = mt.per_email["E1"]
        self.assertEqual(per_email["tokens_in"], 150)
        self.assertEqual(per_email["tokens_out"], 75)
        self.assertEqual(per_email["calls"], 2)
        self.assertGreater(per_email["cost_usd"], 0.0)

        # Per-run aggregate
        self.assertEqual(mt.run_totals["tokens_in"], 150)
        self.assertEqual(mt.run_totals["calls"], 2)

    def test_finalize_writes_cost_json(self):
        from src.agent.metric_tracker import MetricTracker
        tmp = Path("/tmp/test_metric_tracker_cost")
        tmp.mkdir(exist_ok=True)
        path = tmp / "cost.json"
        if path.exists():
            path.unlink()
        mt = MetricTracker(out_path=path)
        mt.record(email_id="E2", model="claude-3-haiku-20240307",
                  tokens_in=200, tokens_out=100, duration_ms=300)
        mt.finalize()

        data = json.loads(path.read_text())
        self.assertIn("calls", data)
        self.assertIn("per_email", data)
        self.assertIn("totals", data)
        self.assertEqual(data["calls"][0]["email_id"], "E2")
        self.assertEqual(data["per_email"]["E2"]["tokens_in"], 200)

    def test_multiple_emails_aggregate_separately(self):
        from src.agent.metric_tracker import MetricTracker
        mt = MetricTracker(out_path="/tmp/test_metric_multi.json")
        mt.record(email_id="E1", model="gpt-4o-mini",
                  tokens_in=10, tokens_out=20, duration_ms=100)
        mt.record(email_id="E2", model="gpt-4o-mini",
                  tokens_in=30, tokens_out=40, duration_ms=200)
        mt.finalize()
        self.assertEqual(mt.per_email["E1"]["tokens_in"], 10)
        self.assertEqual(mt.per_email["E2"]["tokens_in"], 30)
        self.assertEqual(mt.run_totals["tokens_in"], 40)

    def test_cost_usd_aggregated_per_email_and_run(self):
        from src.agent.metric_tracker import MetricTracker
        mt = MetricTracker(out_path="/tmp/test_metric_cost_agg.json")
        mt.record(email_id="E1", model="gpt-4o-mini",
                  tokens_in=1000, tokens_out=500, duration_ms=200)
        mt.record(email_id="E1", model="gpt-4o-mini",
                  tokens_in=1000, tokens_out=500, duration_ms=200)
        mt.finalize()
        # 2 calls, each with cost > 0
        e1_cost = mt.per_email["E1"]["cost_usd"]
        self.assertGreater(e1_cost, 0.0)
        # Run total = per-email total (one email)
        self.assertAlmostEqual(mt.run_totals["cost_usd"], e1_cost, places=6)


class TestMetricTrackerPersistence(unittest.TestCase):
    def test_persists_to_json_with_schema(self):
        from src.agent.metric_tracker import MetricTracker
        tmp = Path("/tmp/test_metric_persist")
        tmp.mkdir(exist_ok=True)
        path = tmp / "cost.json"
        if path.exists():
            path.unlink()
        mt = MetricTracker(out_path=path)
        mt.record(email_id="E5", model="gpt-4o",
                  tokens_in=300, tokens_out=150, duration_ms=500)
        mt.finalize()
        data = json.loads(path.read_text())
        self.assertIn("schema_version", data)
        self.assertEqual(data["schema_version"], "1.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
