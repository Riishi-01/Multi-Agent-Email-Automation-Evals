"""src/agent/logger.py — structured logging for Phase 4D.

Two outputs:
  1. JSONLinesLogger — appends one JSON object per event to a file
     (typically data/runs/run.jsonl).
  2. get_logger — stdlib logging.Logger with a human-readable console
     handler installed by the eval runner.

The JSONL file is the canonical structured log. The console handler is
for human-readable feedback during a run.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TextIO


_LOGGER_NAME = "bytemart.eval"
_DEFAULT_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


# ---------------------------------------------------------------------------
# JSONLinesLogger
# ---------------------------------------------------------------------------
class JSONLinesLogger:
    """Append-only JSONL event log.

    Each event is a flat dict with at minimum:
        event  (str)  — event name (e.g. "email_start", "judge_call")
        level  (str)  — log level (INFO/WARN/ERROR)
        ts     (str)  — ISO 8601 UTC timestamp

    Optional fields:
        email_id, cost_usd, tokens_in, tokens_out, duration_ms,
        metric_id, judge_model, error, ...
    """

    def __init__(self, path: Path | str, *, level: str = _DEFAULT_LEVEL) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._level = getattr(logging, level, logging.INFO)
        # Append mode so multiple runs accumulate; the eval runner wipes
        # the file before a fresh run.
        self._fh: TextIO = self.path.open("a", encoding="utf-8")

    # ----- public API -----
    def event(self, event: str, *, level: str = "INFO", **fields: Any) -> None:
        record = {
            "event": event,
            "level": level,
            "ts":    datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Stdlib logger adapter
# ---------------------------------------------------------------------------
def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a stdlib logger under the bytemart.eval namespace."""
    full = name if name else _LOGGER_NAME
    if not full.startswith("bytemart."):
        full = f"{_LOGGER_NAME}.{full}"
    return logging.getLogger(full)


def _install_console_handler(
    logger: logging.Logger,
    *,
    stream: Optional[TextIO] = None,
    level: str = _DEFAULT_LEVEL,
    fmt: str = "%(asctime)s %(levelname)-5s %(name)s | %(message)s",
) -> logging.Handler:
    """Install a single StreamHandler on `logger`. Idempotent."""
    for h in logger.handlers:
        if getattr(h, "_bytemart_console", False):
            return h
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(getattr(logging, level, logging.INFO))
    handler.setFormatter(logging.Formatter(fmt))
    handler._bytemart_console = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(getattr(logging, level, logging.INFO))
    return handler


def install_console(level: str = _DEFAULT_LEVEL) -> logging.Logger:
    """Convenience: install console handler on the package root logger."""
    lg = get_logger(_LOGGER_NAME)
    _install_console_handler(lg, level=level)
    lg.propagate = False
    return lg
