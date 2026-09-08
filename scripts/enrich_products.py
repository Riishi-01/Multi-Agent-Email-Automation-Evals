#!/usr/bin/env python3
"""scripts/enrich_products.py — parse data/policies/Products.md into DB rows.

Source: data/policies/Products.md (pandoc-emitted fixed-width table).
Output: enrich app.products with warranty_text, tech_description, tech_details,
        stock_count (only the 15 SKUs in Products.md; the other 5 SKUs stay NULL).

Idempotent: each invocation writes the same data.

Usage:
    python scripts/enrich_products.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text

from src.db import get_session


# ---------------------------------------------------------------------------
# Column boundaries (from the dashes rule in Products.md)
# ---------------------------------------------------------------------------
COL_BOUNDS = [
    ("s_no",               2,  12),
    ("name",              13,  27),
    ("category",          28,  40),
    ("tech_description",  41,  61),
    ("tech_details",      62, 105),
    ("warranty_text",    106, 121),
    ("price_inr",        122, 132),
    ("stock_count",      133, 143),
]
COL_NAMES = [c[0] for c in COL_BOUNDS]


# ---------------------------------------------------------------------------
# Light HTML/markdown cleanup
# ---------------------------------------------------------------------------
_BR_TAG = re.compile(r"`<br>`\{=html\}")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _clean_cell(s: str) -> str:
    """Drop pandoc <br> tags and **bold** markers; collapse whitespace."""
    s = _BR_TAG.sub(" ", s)
    s = _BOLD.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def _find_data_rule(lines: list[str]) -> tuple[int, list[tuple[int, int]]]:
    """Return (rule_index, column_boundaries) for the data-table dashes line.

    The Products.md file has THREE dashes lines:
      line 2:  '  ----...' (document-title underline; ignore)
      line 5:  '  ---- --- --- ...' (8-column data table; use this)
      line 168: '  ----...' (closing rule; ignore)
    """
    for i, line in enumerate(lines):
        if line.startswith("  -----") and line.count("-") >= 40:
            groups = [(m.start(), m.end()) for m in re.finditer(r"-+", line)]
            if len(groups) >= 8:
                return i, groups
    raise ValueError("could not locate 8-column dashes rule in Products.md")


def parse_products_md(path: Path) -> list[dict]:
    """Return list of dicts (one per product row) from the Products.md table.

    Returned shape:
        {"s_no": int, "name": str, "category": str,
         "tech_description": str, "tech_details": str,
         "warranty_text": str, "price_inr": int, "stock_count": int}
    """
    lines = path.read_text().splitlines()
    rule_idx, groups = _find_data_rule(lines)

    # Find the closing rule (the next line that is a single-group dashes line
    # after the data rule).
    end = len(lines)
    for j in range(rule_idx + 1, len(lines)):
        s = lines[j]
        if not (s.startswith("  ---") and s.count("-") >= 40):
            continue
        cur_groups = [(m.start(), m.end()) for m in re.finditer(r"-+", s)]
        if len(cur_groups) == 1:
            end = j
            break

    # Walk the rows. A "row" starts when its S.No column is non-empty and
    # continues across any number of subsequent lines whose S.No column is empty
    # (these are wrapped continuation lines).
    rows: list[dict] = []
    cur_cells: dict[str, str] = {n: "" for n in COL_NAMES}
    cur_started = False

    def _flush():
        nonlocal cur_cells, cur_started
        if not cur_started:
            return
        # Parse price (comma-separated thousands → int) + stock (int).
        s_no = int(cur_cells["s_no"].strip())
        price_raw = cur_cells["price_inr"].replace(",", "").strip()
        price = int(price_raw) if price_raw else 0
        stock_raw = cur_cells["stock_count"].strip()
        stock = int(stock_raw) if stock_raw else 0
        rows.append({
            "s_no":              s_no,
            "name":              _clean_cell(cur_cells["name"]),
            "category":          _clean_cell(cur_cells["category"]),
            "tech_description":  _clean_cell(cur_cells["tech_description"]),
            "tech_details":      _clean_cell(cur_cells["tech_details"]),
            "warranty_text":     _clean_cell(cur_cells["warranty_text"]),
            "price_inr":         price,
            "stock_count":       stock,
        })
        cur_cells = {n: "" for n in COL_NAMES}
        cur_started = False

    for line in lines[rule_idx + 1:end]:
        # Slice each cell by the column boundary.
        cell_slices = [line[start:end] for (start, end) in groups]
        # Skip lines that are entirely blank (no characters at all).
        if not any(c.strip() for c in cell_slices):
            continue
        s_no_cell = cell_slices[0].strip()
        if s_no_cell:
            # New row starts.
            _flush()
            cur_started = True
        if cur_started:
            for n, slc in zip(COL_NAMES, cell_slices):
                cur_cells[n] = (cur_cells[n] + " " + slc) if cur_cells[n] else slc
    _flush()
    return rows


# ---------------------------------------------------------------------------
# Name → SKU mapping (Products.md names match products.csv names)
# ---------------------------------------------------------------------------
NAME_TO_SKU: dict[str, str] = {
    "PlayStation 5 Disc Edition Console":                    "PS5-DISC-001",
    "PlayStation 5 Digital Edition Console":                 "PS5-DIGI-001",
    "Xbox Series X 1TB Console":                             "XSX-001",
    "Xbox Series S 512GB Console":                           "XSS-001",
    "ByteMart Custom Gaming PC RTX 4070":                    "BMPC-RTX4070",
    "Xbox Wireless Controller - Black":                      "XCTRL-BLK",
    "Xbox Wireless Controller - White":                      "XCTRL-WHT",
    "DualSense Wireless Controller - White":                 "DSC-WHT",
    "Logitech G PRO Racing Wheel and Pedals":                "LGPRO-RW",
    "Meta Quest 3 512GB VR Headset":                         "MQUEST3-512",
    "Meta Quest 3 128GB VR Headset":                         "MQUEST3-128",
    # Products.md drops the unit ("inch") and form ("Monitor") in the wrapped
    # name column; the inch mark is a straight double-quote (0x22) in the PDF.
    "Dell Alienware AW3423DWF 34\" OLED":                    "AW3423DWF",
    "LED Monitor 27-inch":                                   "LED27-001",
    "Wireless Earbuds Pro":                                  "WEP-001",
    # Products.md wraps "Wireless Headset" off the Name column into the
    # Category column for this row.
    "Razer BlackShark V2 Pro":                               "RBSV2-PRO",
}


def match_to_sku(name: str) -> Optional[str]:
    """Map a Products.md name to its products.sku. Returns None if absent."""
    return NAME_TO_SKU.get(name)


# ---------------------------------------------------------------------------
# DB apply
# ---------------------------------------------------------------------------
def enrich_db(parsed: list[dict]) -> tuple[int, int]:
    """UPDATE app.products with the enrichment columns for matched SKUs.

    Returns (updated, unmatched) counts.
    """
    sess = get_session("owner")
    updated = 0
    unmatched = 0
    try:
        for r in parsed:
            sku = match_to_sku(r["name"])
            if not sku:
                unmatched += 1
                continue
            sess.execute(text("""
                UPDATE app.products
                   SET warranty_text    = :w,
                       tech_description = :td,
                       tech_details     = :tde,
                       stock_count      = :sc
                 WHERE sku = :sku
            """), {
                "w":   r["warranty_text"] or None,
                "td":  r["tech_description"] or None,
                "tde": r["tech_details"] or None,
                "sc":  r["stock_count"] or None,
                "sku": sku,
            })
            updated += 1
        sess.commit()
    finally:
        sess.close()
    return updated, unmatched


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    md_path = REPO_ROOT / "data" / "policies" / "Products.md"
    if not md_path.exists():
        print(f"!! {md_path} not found")
        return 2

    parsed = parse_products_md(md_path)
    print(f"== Products.md parsed: {len(parsed)} rows")
    updated, unmatched = enrich_db(parsed)
    print(f"== Enrichment: {updated} rows updated, {unmatched} unmatched (5 service-fee SKUs are expected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
