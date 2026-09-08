"""tests/test_judge.py — RED tests for src/agent/judge.py.

JudgeClient is provider-agnostic (OpenAI-compatible). The transport seam
allows tests to inject canned responses without making real HTTP calls.

absent-config path: if JUDGE_BASE_URL/JUDGE_MODEL/JUDGE_API_KEY are all
unset, is_configured() is False; calling judge(...) returns an "unscored"
result instead of hitting any network.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _canned_transport(payload: dict, status: int = 200) -> "Callable":
    """Build a transport function that returns a canned response."""
    body = json.dumps(payload).encode("utf-8")

    def _t(method: str, url: str, headers: dict, data: bytes, timeout: float):
        return status, dict(headers), body

    return _t


def _capturing_transport(payload: dict) -> "tuple":
    """Returns (transport_fn, captured) where captured is filled with the last request."""
    captured: dict[str, Any] = {}
    body = json.dumps(payload).encode("utf-8")

    def _t(method, url, headers, data, timeout):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["data"] = data
        captured["timeout"] = timeout
        return 200, dict(headers), body

    return _t, captured


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class TestJudgeConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Force-unset env so the "unset" semantics are deterministic
        # regardless of what's in .env. setUp restores; tearDown re-applies.
        import os
        cls._env_snapshot = {
            k: os.environ.get(k) for k in
            ("JUDGE_BASE_URL", "JUDGE_MODEL", "JUDGE_API_KEY")
        }
        for k in cls._env_snapshot:
            os.environ.pop(k, None)

    @classmethod
    def tearDownClass(cls):
        import os
        for k, v in cls._env_snapshot.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_is_configured_false_when_env_unset(self, monkeypatch=None):
        # We need to import fresh; tests that follow will set env explicitly.
        from src.agent.judge import JudgeClient
        # Create with no args → not configured
        c = JudgeClient()
        self.assertFalse(c.is_configured())

    def test_is_configured_true_when_all_env_set(self):
        from src.agent.judge import JudgeClient
        c = JudgeClient(base_url="https://x.test", model="m", api_key="k")
        self.assertTrue(c.is_configured())

    def test_partial_config_does_not_configure(self):
        from src.agent.judge import JudgeClient
        c = JudgeClient(base_url="https://x.test", model="m", api_key=None)
        self.assertFalse(c.is_configured())
        c = JudgeClient(base_url=None, model="m", api_key="k")
        self.assertFalse(c.is_configured())


class TestJudgeAbsentConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        cls._env_snapshot = {
            k: os.environ.get(k) for k in
            ("JUDGE_BASE_URL", "JUDGE_MODEL", "JUDGE_API_KEY")
        }
        for k in cls._env_snapshot:
            os.environ.pop(k, None)

    @classmethod
    def tearDownClass(cls):
        import os
        for k, v in cls._env_snapshot.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


class TestJudgeAbsentConfig(unittest.TestCase):
    def test_judge_returns_unscored_when_not_configured(self):
        from src.agent.judge import JudgeClient, UnscoredResult
        c = JudgeClient()                # no config (env was unset in setUpClass)
        out = c.judge(system="x", user="y", metric_id="tone_professional")
        self.assertIsInstance(out, UnscoredResult)
        self.assertTrue(out.unscored)
        self.assertIn("not configured", out.reason.lower())


# ---------------------------------------------------------------------------
# JudgeClient.judge with injected transport
# ---------------------------------------------------------------------------
class TestJudgeWithMockTransport(unittest.TestCase):
    def test_returns_parsed_likert_score(self):
        from src.agent.judge import JudgeClient
        payload = {
            "choices": [{"message": {"content": json.dumps(
                {"score": 5, "rationale": "great"})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        }
        transport = _canned_transport(payload)
        c = JudgeClient(base_url="https://x.test", model="judge-1",
                        api_key="k", transport=transport)
        out = c.judge(system="sys", user="usr", metric_id="tone_professional")
        self.assertFalse(out.unscored)
        self.assertEqual(out.score, 5)
        self.assertEqual(out.tokens_in, 100)
        self.assertEqual(out.tokens_out, 30)

    def test_captures_request_for_audit(self):
        from src.agent.judge import JudgeClient
        payload = {
            "choices": [{"message": {"content": json.dumps(
                {"score": 4, "rationale": "ok"})}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        transport, captured = _capturing_transport(payload)
        c = JudgeClient(base_url="https://x.test/v1", model="judge-1",
                        api_key="K", transport=transport)
        c.judge(system="S", user="U", metric_id="clarity_structure")
        self.assertIn("/chat/completions", captured["url"])
        self.assertEqual(captured["method"], "POST")
        body = json.loads(captured["data"])
        self.assertEqual(body["model"], "judge-1")
        self.assertIn("Authorization", captured["headers"])

    def test_handles_missing_usage_gracefully(self):
        from src.agent.judge import JudgeClient
        payload = {
            "choices": [{"message": {"content": json.dumps(
                {"score": 3, "rationale": "ok"})}}],
            # no usage block
        }
        transport = _canned_transport(payload)
        c = JudgeClient(base_url="https://x.test", model="m",
                        api_key="k", transport=transport)
        out = c.judge(system="s", user="u", metric_id="m")
        self.assertEqual(out.tokens_in, 0)
        self.assertEqual(out.tokens_out, 0)
        self.assertEqual(out.score, 3)

    def test_retries_on_5xx(self):
        from src.agent.judge import JudgeClient
        # First two calls return 500; third returns 200
        call_count = {"n": 0}
        body_ok = json.dumps({
            "choices": [{"message": {"content": json.dumps(
                {"score": 5, "rationale": "ok"})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode("utf-8")

        def transport(method, url, headers, data, timeout):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return 500, {}, b"internal error"
            return 200, {}, body_ok

        c = JudgeClient(base_url="https://x.test", model="m",
                        api_key="k", transport=transport, max_retries=3,
                        retry_backoff=0.0)
        out = c.judge(system="s", user="u", metric_id="m")
        self.assertFalse(out.unscored)
        self.assertEqual(call_count["n"], 3)

    def test_429_still_retries(self):
        from src.agent.judge import JudgeClient
        call_count = {"n": 0}
        body_ok = json.dumps({
            "choices": [{"message": {"content": json.dumps(
                {"score": 5, "rationale": "ok"})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }).encode("utf-8")

        def transport(method, url, headers, data, timeout):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return 429, {}, b"rate limited"
            return 200, {}, body_ok

        c = JudgeClient(base_url="https://x.test", model="m",
                        api_key="k", transport=transport, max_retries=3,
                        retry_backoff=0.0)
        out = c.judge(system="s", user="u", metric_id="m")
        self.assertFalse(out.unscored)
        self.assertEqual(call_count["n"], 2)

    def test_max_retries_exhausted_returns_unscored(self):
        from src.agent.judge import JudgeClient
        def transport(method, url, headers, data, timeout):
            return 500, {}, b"err"
        c = JudgeClient(base_url="https://x.test", model="m",
                        api_key="k", transport=transport, max_retries=2,
                        retry_backoff=0.0)
        out = c.judge(system="s", user="u", metric_id="m")
        self.assertTrue(out.unscored)
        self.assertIn("retries", out.reason.lower())

    def test_invalid_score_clamps_to_range(self):
        from src.agent.judge import JudgeClient
        payload = {
            "choices": [{"message": {"content": json.dumps(
                {"score": 99, "rationale": "off the chart"})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        transport = _canned_transport(payload)
        c = JudgeClient(base_url="https://x.test", model="m",
                        api_key="k", transport=transport)
        out = c.judge(system="s", user="u", metric_id="m")
        # Clamp to [1, 5] for Likert
        self.assertLessEqual(out.score, 5)


class TestUnscoredResult(unittest.TestCase):
    def test_dataclass_shape(self):
        from src.agent.judge import UnscoredResult
        u = UnscoredResult(reason="no api key")
        self.assertTrue(u.unscored)
        self.assertEqual(u.score, 0)
        self.assertEqual(u.tokens_in, 0)
        self.assertEqual(u.tokens_out, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
