"""tests/test_logger.py — RED tests for src/agent/logger.py.

Two outputs:
  1. JSONL file at data/runs/run.jsonl (one JSON object per line).
  2. Stdlib logging adapter: human-readable console output.

Required fields per event:
  event, level, ts, email_id, cost_usd, tokens_in, tokens_out, duration_ms
"""
from __future__ import annotations

import json
import logging
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class TestJSONLinesLogger(unittest.TestCase):
    def test_writes_one_json_per_event(self):
        from src.agent.logger import JSONLinesLogger
        tmp = Path("/tmp/test_logger_jsonl")
        tmp.mkdir(exist_ok=True)
        for p in tmp.glob("*.jsonl"):
            p.unlink()
        log = JSONLinesLogger(tmp / "run.jsonl")
        log.event("email_start", email_id="E1")
        log.event("email_done", email_id="E1",
                  cost_usd=0.001, tokens_in=10, tokens_out=20, duration_ms=42)
        log.close()

        lines = (tmp / "run.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        e0 = json.loads(lines[0])
        e1 = json.loads(lines[1])
        self.assertEqual(e0["event"], "email_start")
        self.assertEqual(e0["email_id"], "E1")
        self.assertIn("ts", e0)
        self.assertEqual(e1["event"], "email_done")
        self.assertEqual(e1["cost_usd"], 0.001)
        self.assertEqual(e1["tokens_in"], 10)
        self.assertEqual(e1["tokens_out"], 20)
        self.assertEqual(e1["duration_ms"], 42)

    def test_appends_to_existing_file(self):
        from src.agent.logger import JSONLinesLogger
        tmp = Path("/tmp/test_logger_jsonl_append")
        tmp.mkdir(exist_ok=True)
        path = tmp / "run.jsonl"
        path.write_text('{"event": "existing"}\n')
        log = JSONLinesLogger(path)
        log.event("new_event")
        log.close()
        lines = path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["event"], "existing")
        self.assertEqual(json.loads(lines[1])["event"], "new_event")

    def test_failed_event_field_present(self):
        from src.agent.logger import JSONLinesLogger
        tmp = Path("/tmp/test_logger_failed")
        tmp.mkdir(exist_ok=True)
        for p in tmp.glob("*.jsonl"):
            p.unlink()
        log = JSONLinesLogger(tmp / "run.jsonl")
        log.event("judge_call", email_id="E1", cost_usd=0.002,
                  tokens_in=100, tokens_out=50, duration_ms=200, level="INFO")
        log.close()
        e = json.loads((tmp / "run.jsonl").read_text().strip())
        self.assertEqual(e["level"], "INFO")
        self.assertEqual(e["cost_usd"], 0.002)

    def test_creates_parent_directory(self):
        from src.agent.logger import JSONLinesLogger
        tmp = Path("/tmp/test_logger_mkdir/deep")
        if tmp.exists():
            for p in tmp.glob("*"):
                if p.is_file():
                    p.unlink()
        JSONLinesLogger(tmp / "run.jsonl").close()
        self.assertTrue((tmp / "run.jsonl").exists())


class TestStdlibAdapter(unittest.TestCase):
    def test_get_logger_returns_logger(self):
        from src.agent.logger import get_logger
        lg = get_logger("src.agent.test_logger")
        self.assertIsInstance(lg, logging.Logger)

    def test_logger_writes_to_console(self):
        import io
        from src.agent.logger import get_logger, _install_console_handler
        buf = io.StringIO()
        lg = get_logger("src.agent.test_logger.console")
        _install_console_handler(lg, stream=buf)
        lg.info("hello %s", "world")
        out = buf.getvalue()
        self.assertIn("hello world", out)
        # The format includes level + name
        self.assertIn("INFO", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
