#!/usr/bin/env python3
"""scripts/ingest_policies_parent_child.py — Phase 4B + 5G.5 ingest.

Phase 5G.5: source can be either a PDF (legacy, paragraph-pack) or a
directory of Markdown files (preferred, clause-aware). Auto-detected:
a directory containing `*.md` is treated as Markdown; a `.pdf` is PDF.

Markdown source: clause-aware primary + paragraph-pack fallback
+ 300/75 sliding children (CHILD_OVERLAP from 50 → 75).

Idempotent: deletes prior rows of the given version before inserting,
rebuilds ivfflat + ANALYZE at the end.

Usage:
    python scripts/ingest_policies_parent_child.py [--version v1]
    python scripts/ingest_policies_parent_child.py --source data/policies/markdown
    python scripts/ingest_policies_parent_child.py --source data/policies/bytemart-policy-pack.pdf
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text

from src.db import get_session
from src.rag import ingest


DEFAULT_MD_DIR = REPO_ROOT / "data" / "policies" / "markdown"
DEFAULT_PDF    = REPO_ROOT / "data" / "policies" / "bytemart-policy-pack.pdf"


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest policies into parent/child tables.")
    ap.add_argument("--version", default="v1",
                    help="Schema version tag (default: v1).")
    ap.add_argument("--source", default=None,
                    help="Path to source (PDF file or Markdown directory). "
                         "Defaults to Markdown dir if it has *.md files, "
                         "else the bundled PDF.")
    args = ap.parse_args()

    # Resolve source.
    if args.source:
        source = Path(args.source)
    elif DEFAULT_MD_DIR.is_dir() and any(DEFAULT_MD_DIR.glob("*.md")):
        source = DEFAULT_MD_DIR
    else:
        source = DEFAULT_PDF

    if not source.exists():
        print(f"!! {source} not found")
        return 2

    # Sanity: tables must exist (apply setup_db.sql first).
    sess = get_session("owner")
    try:
        sess.execute(text("SELECT 1 FROM app.policy_parents LIMIT 0"))
        sess.execute(text("SELECT 1 FROM app.policy_children LIMIT 0"))
    except Exception as exc:
        print(f"!! app.policy_parents/children missing — run scripts/setup_db.py first: {exc}")
        return 3
    finally:
        sess.close()

    fmt = "markdown" if source.is_dir() else "pdf"
    print(f"Ingesting source={source} format={fmt} version={args.version}")
    n_parents, n_children = ingest(source, version=args.version, source_format=fmt)
    print(f"[OK] Ingested {n_parents} parents + {n_children} children "
          f"into app.policy_parents/children (version={args.version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
