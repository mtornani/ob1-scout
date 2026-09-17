#!/usr/bin/env python3
"""Registro: Balkans/UY coperti da fonti che il lettore sa leggere."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.sources_v2 import YOUTH_TERMS, load_registry


class TestCoverageMercati(unittest.TestCase):
    def setUp(self):
        self.by_id = {s["id"]: s for s in load_registry(only_active=False)}
        self.active = {s["id"] for s in load_registry(only_active=True)}

    def test_fss_resta_attiva_wp_json(self):
        s = self.by_id["rs_fss"]
        self.assertTrue(s["active"])
        self.assertIn("fss.rs", s["url"])

    def test_hns_cff_spenta_redirect_spa(self):
        self.assertNotIn("hr_hns", self.active)
        self.assertFalse(self.by_id["hr_hns"]["active"])

    def test_stampa_balcani_attiva(self):
        self.assertIn("hr_sportnet", self.active)
        self.assertIn("ba_sportsport", self.active)
        self.assertEqual(self.by_id["hr_sportnet"]["type"], "national_press")
        self.assertEqual(self.by_id["ba_sportsport"]["type"], "national_press")

    def test_serbo_ha_cirillico(self):
        terms = YOUTH_TERMS["sr"]
        self.assertIn("омладинац", terms)
        self.assertIn("пионири", terms)

    def test_bosniaco_non_cade_su_inglese(self):
        self.assertIn("bs", YOUTH_TERMS)
        self.assertNotEqual(YOUTH_TERMS["bs"], YOUTH_TERMS["en"])


if __name__ == "__main__":
    unittest.main()
