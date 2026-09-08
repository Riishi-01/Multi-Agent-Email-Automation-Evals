"""src/parser.py — extract identifiers from inbound emails.

Source of truth: docs/phase-2-tools.yaml#parser

Three regexes:
  - ORDER_ID_RE matches the 8-char [A-Za-z]{2}\\d{6} format used by ByteMart.
  - TXN_ID_RE matches 8-digit numeric transaction IDs.
  - EMAIL_RE is exposed (T-001 does the actual DB lookup; this is just a sanity check).

These run BEFORE the agent loop (in AM-003, Phase 3). The agent may also call
them implicitly via T-002 / T-003.
"""
from __future__ import annotations

import re
from typing import Optional


ORDER_ID_RE = re.compile(r"\b([A-Za-z]{2}\d{6})\b")
TXN_ID_RE   = re.compile(r"\b(\d{8})\b")
EMAIL_RE    = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def extract_order_id(text: str) -> Optional[str]:
    """First matching 8-char order ID (e.g. 'BM123244'), case preserved.

    Returns None if no order ID is present. Multiple order IDs -> only the first
    wins (if you need all, see extract_order_ids()).
    """
    if not text:
        return None
    m = ORDER_ID_RE.search(text)
    return m.group(1) if m else None


def extract_order_ids(text: str) -> list[str]:
    """All matching 8-char order IDs in document order, deduplicated."""
    if not text:
        return []
    return list(dict.fromkeys(ORDER_ID_RE.findall(text)))


def extract_txn_id(text: str) -> Optional[str]:
    """First matching 8-digit numeric transaction ID."""
    if not text:
        return None
    m = TXN_ID_RE.search(text)
    return m.group(1) if m else None


def extract_txn_ids(text: str) -> list[str]:
    """All matching 8-digit numeric transaction IDs in document order, deduplicated."""
    if not text:
        return []
    return list(dict.fromkeys(TXN_ID_RE.findall(text)))


def has_email_address(text: str) -> Optional[str]:
    """Sanity-check helper; returns the first email found or None."""
    if not text:
        return None
    m = EMAIL_RE.search(text)
    return m.group(0) if m else None


def is_valid_order_id(s: str) -> bool:
    """Strict check for tool input validation (T-002)."""
    return bool(s) and bool(ORDER_ID_RE.fullmatch(s))


def is_valid_txn_id(s: str) -> bool:
    """Strict check for tool input validation (T-003)."""
    return bool(s) and bool(TXN_ID_RE.fullmatch(s))
