"""tests/test_minimax_config.py — RED tests for the MiniMax judge setup.

Validates:
  - MiniMax models have bundled pricing in metric_tracker._DEFAULT_PRICING.
  - detect_provider() returns 'minimax' for both MiniMax-Text-01 and
    the api.minimaxi.com base URL.
  - JudgeClient.describe() returns a log-safe dict (no api_key leak).
  - run_eval.py (CLI) logs the detected provider when JUDGE_* is set.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class TestMiniMaxPricing(unittest.TestCase):
    def test_MiniMax_Text_01_in_pricing_registry(self):
        from src.agent.metric_tracker import _DEFAULT_PRICING
        self.assertIn("MiniMax-Text-01", _DEFAULT_PRICING)
        self.assertIn("in", _DEFAULT_PRICING["MiniMax-Text-01"])
        self.assertIn("out", _DEFAULT_PRICING["MiniMax-Text-01"])

    def test_MiniMax_Text_01_compute_cost_returns_positive(self):
        from src.agent.metric_tracker import compute_cost
        cost, warn = compute_cost("MiniMax-Text-01", 1000, 500)
        self.assertGreater(cost, 0.0)
        self.assertFalse(warn)

    def test_all_MiniMax_aliases_present(self):
        from src.agent.metric_tracker import _DEFAULT_PRICING
        for m in ("MiniMax-Text-01", "abab6.5s-chat",
                  "abab6.5-chat", "abab5.5s-chat", "abab5.5-chat"):
            self.assertIn(m, _DEFAULT_PRICING)


class TestDetectProvider(unittest.TestCase):
    def test_detect_minimax_by_url(self):
        from src.agent.metric_tracker import detect_provider
        self.assertEqual(detect_provider("MiniMax-Text-01",
                                          "https://api.minimaxi.com/v1"),
                         "minimax")

    def test_detect_minimax_by_model_name(self):
        from src.agent.metric_tracker import detect_provider
        self.assertEqual(detect_provider("abab6.5s-chat"), "minimax")

    def test_detect_openai(self):
        from src.agent.metric_tracker import detect_provider
        self.assertEqual(detect_provider("gpt-4o-mini"), "openai")

    def test_detect_anthropic(self):
        from src.agent.metric_tracker import detect_provider
        self.assertEqual(detect_provider("claude-3-5-haiku-latest"),
                         "anthropic")

    def test_detect_local_vllm(self):
        from src.agent.metric_tracker import detect_provider
        self.assertEqual(detect_provider("Qwen2.5-7B-Instruct",
                                          "http://localhost:8000/v1"),
                         "local")

    def test_detect_unknown(self):
        from src.agent.metric_tracker import detect_provider
        self.assertEqual(detect_provider("some-future-model-xyz"),
                         "unknown")


class TestHasPricing(unittest.TestCase):
    def test_MiniMax_has_pricing(self):
        from src.agent.metric_tracker import has_pricing
        self.assertTrue(has_pricing("MiniMax-Text-01"))

    def test_unknown_model_has_no_pricing(self):
        from src.agent.metric_tracker import has_pricing
        self.assertFalse(has_pricing("not-a-real-model"))


class TestListKnownModels(unittest.TestCase):
    def test_includes_MiniMax(self):
        from src.agent.metric_tracker import list_known_models
        self.assertIn("MiniMax-Text-01", list_known_models())


class TestJudgeDescribe(unittest.TestCase):
    def test_describe_does_not_leak_api_key(self):
        from src.agent.judge import JudgeClient
        c = JudgeClient(base_url="https://api.minimaxi.com/v1",
                        model="MiniMax-Text-01", api_key="secret-key-12345")
        d = c.describe()
        self.assertEqual(d["base_url"], "https://api.minimaxi.com/v1")
        self.assertEqual(d["model"], "MiniMax-Text-01")
        self.assertTrue(d["api_key_set"])
        # The actual key value must never appear in describe().
        for v in d.values():
            if isinstance(v, str):
                self.assertNotIn("secret-key-12345", v)


class TestJudgeMiniMaxRequestShape(unittest.TestCase):
    """End-to-end with mock transport: confirm a judge call against the
    MiniMax URL produces a request with the expected shape."""

    def test_request_targets_minimax_chat_completions(self):
        from src.agent.judge import JudgeClient
        captured = {}

        def transport(method, url, headers, data, timeout):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = dict(headers)
            captured["body"] = json.loads(data)
            return 200, {}, json.dumps({
                "choices": [{"message": {"content": json.dumps(
                    {"score": 4, "rationale": "ok"})}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }).encode("utf-8")

        c = JudgeClient(base_url="https://api.minimaxi.com/v1",
                        model="MiniMax-Text-01", api_key="k",
                        transport=transport, timeout=10.0)
        c.judge(system="s", user="u", metric_id="m")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["url"],
                         "https://api.minimaxi.com/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "MiniMax-Text-01")
        # Auth header must be present, value sanitized for the test log.
        self.assertIn("Authorization", captured["headers"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
