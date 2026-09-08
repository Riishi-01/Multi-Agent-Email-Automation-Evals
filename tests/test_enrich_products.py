"""tests/test_enrich_products.py — unit tests for the Products.md parser.

The parser reads the pandoc-emitted fixed-width table at
data/policies/Products.md and returns a list of dicts with keys:
    s_no, name, category, tech_description, warranty_text, tech_details,
    price_inr, stock_count

Source-of-truth columns verified by fixture: Products.md (15 rows).
The 5 SKUs NOT in Products.md (RZR-ORN-V3, RZR-KRAKEN-V3, HYPX-PFC-001,
EXP-DEL-001, COD-CHG) must not appear in the parser output.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PRODUCTS_MD = REPO_ROOT / "data" / "policies" / "Products.md"


class TestProductsMdParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PRODUCTS_MD.exists():
            cls._skip = True
            return
        from scripts.enrich_products import parse_products_md
        cls.rows = parse_products_md(PRODUCTS_MD)
        cls._skip = False

    def setUp(self):
        if self._skip:
            self.skipTest(f"{PRODUCTS_MD} not present")

    def test_returns_fifteen_rows(self):
        self.assertEqual(len(self.rows), 15)

    def test_first_row_is_ps5_disc(self):
        r0 = self.rows[0]
        self.assertEqual(r0["s_no"], 1)
        self.assertIn("PlayStation 5", r0["name"])
        self.assertEqual(r0["category"], "Gaming Console")
        self.assertGreater(r0["price_inr"], 50000)
        self.assertEqual(r0["stock_count"], 7)
        # warranty + tech fields are non-empty for the 15 enriched SKUs
        self.assertTrue(r0["warranty_text"])
        self.assertTrue(r0["tech_description"])
        self.assertTrue(r0["tech_details"])

    def test_row_15_is_razer_blackshark(self):
        last = self.rows[-1]
        self.assertEqual(last["s_no"], 15)
        self.assertIn("Razer", last["name"])
        self.assertEqual(last["category"], "Audio")

    def test_no_new_skus_appear(self):
        """The 3 new gaming SKUs (Ornata/Kraken/Pulsefire) and 2 service fees
        are not in Products.md and must not appear in the parser output."""
        names = " | ".join(r["name"] for r in self.rows)
        for missing in [
            "Ornata", "Kraken", "Pulsefire",
            "Express Delivery", "COD Service Charge",
        ]:
            self.assertNotIn(missing, names, f"{missing} should not be parsed")

    def test_categories_in_known_set(self):
        cats = {r["category"] for r in self.rows}
        for c in cats:
            self.assertIn(c, {
                "Gaming Console", "Gaming Desktop", "Game Controller",
                "Gaming Keyboard", "Gaming Mouse", "Racing Wheel",
                "VR Headset", "Monitor", "Audio",
            })

    def test_prices_are_positive_ints(self):
        for r in self.rows:
            self.assertIsInstance(r["price_inr"], int)
            self.assertGreater(r["price_inr"], 0)

    def test_stock_counts_positive(self):
        for r in self.rows:
            self.assertIsInstance(r["stock_count"], int)
            self.assertGreater(r["stock_count"], 0)


class TestEnrichmentMapping(unittest.TestCase):
    """The enrichment step maps a parsed Products.md row -> a products.sku."""

    @classmethod
    def setUpClass(cls):
        if not PRODUCTS_MD.exists():
            cls._skip = True
            return
        from scripts.enrich_products import parse_products_md
        cls.rows = parse_products_md(PRODUCTS_MD)
        cls._skip = False

    def setUp(self):
        if self._skip:
            self.skipTest(f"{PRODUCTS_MD} not present")
        from scripts.enrich_products import match_to_sku
        self.match = match_to_sku

    def test_ps5_disc_maps_to_known_sku(self):
        ps5 = next(r for r in self.rows if "PlayStation 5 Disc" in r["name"])
        sku = self.match(ps5["name"])
        self.assertEqual(sku, "PS5-DISC-001")

    def test_xbox_series_x_maps(self):
        xsx = next(r for r in self.rows if "Xbox Series X" in r["name"])
        self.assertEqual(self.match(xsx["name"]), "XSX-001")

    def test_xbox_controller_black_maps(self):
        ctl = next(r for r in self.rows if "Xbox Wireless Controller - Black" in r["name"])
        self.assertEqual(self.match(ctl["name"]), "XCTRL-BLK")

    def test_express_delivery_not_present(self):
        # The 2 service fees are in products.csv but not in Products.md,
        # so they must NOT appear in the parser's row list.
        self.assertFalse(any("Express Delivery" in r["name"] for r in self.rows))
        self.assertFalse(any("COD Service" in r["name"] for r in self.rows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
