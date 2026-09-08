"""tests/test_openrouter_config.py — RED tests for the OpenRouter judge setup.

Validates:
  - detect_provider() returns 'openrouter' for the OpenRouter base URL.
  - detect_provider() returns 'openrouter' for `vendor/model` slugs when
    the base URL is OpenRouter.
  - minimax/minimax-m3:free has bundled $0.00 pricing.
  - JudgeClient builds the correct request shape for OpenRouter.
  - JudgeClient.describe() does not leak the API key.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class TestOpenRouterProviderDetection(unittest.TestCase):
    def test_detect_openrouter_by_url(self):
        from src.agent.metric_tracker import detect_provider
        self.assertEqual(
            detect_provider("minimax/minimax-m3:free",
                            "https://openrouter.ai/api/v1"),
            "openrouter")

    def test_detect_openrouter_for_any_model(self):
        from src.agent.metric_tracker import detect_provider
        self.assertEqual(
            detect_provider("gpt-4o-mini",
                            "https://openrouter.ai/api/v1"),
            "openrouter")

    def test_openrouter_vendor_slug_detected(self):
        from src.agent.metric_tracker import detect_provider
        # No URL passed; should be "unknown" because we can't infer.
        self.assertEqual(
            detect_provider("minimax/minimax-m3:free"),
            "unknown")
        # With OpenRouter URL, it should be openrouter.
        self.assertEqual(
            detect_provider("minimax/minimax-m3:free",
                            "https://openrouter.ai/api/v1"),
            "openrouter")


class TestOpenRouterPricing(unittest.TestCase):
    def test_minimax_m3_free_is_zero_cost(self):
        from src.agent.metric_tracker import compute_cost
        cost, warn = compute_cost("minimax/minimax-m3:free", 1000, 500)
        self.assertEqual(cost, 0.0)
        self.assertFalse(warn)

    def test_minimax_m3_free_listed_as_known_model(self):
        from src.agent.metric_tracker import list_known_models
        self.assertIn("minimax/minimax-m3:free", list_known_models())


class TestOpenRouterJudgeRequest(unittest.TestCase):
    def test_request_targets_openrouter_chat_completions(self):
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
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }).encode("utf-8")

        c = JudgeClient(base_url="https://openrouter.ai/api/v1",
                        model="minimax/minimax-m3:free", api_key="k",
                        transport=transport, timeout=10.0)
        c.judge(system="s", user="u", metric_id="m")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(
            captured["url"],
            "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(captured["body"]["model"],
                         "minimax/minimax-m3:free")
        self.assertIn("Authorization", captured["headers"])

    def test_describe_does_not_leak_api_key(self):
        from src.agent.judge import JudgeClient
        c = JudgeClient(base_url="https://openrouter.ai/api/v1",
                        model="minimax/minimax-m3:free",
                        api_key="sk-or-v1-secret-12345")
        d = c.describe()
        self.assertEqual(d["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(d["model"], "minimax/minimax-m3:free")
        self.assertTrue(d["api_key_set"])
        for v in d.values():
            if isinstance(v, str):
                self.assertNotIn("sk-or-v1-secret-12345", v)


if __name__ == "__main__":
    unittest.main(verbosity=2)
