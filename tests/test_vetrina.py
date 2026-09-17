#!/usr/bin/env python3
"""
La vetrina pubblica è la telefonata.

Un nome in dashboard / JSON `publishable` deve avere, *così come è mostrato*:
nome completo, età, club, due URL http. Se l'export nasconde l'età o un
link è un dominio nudo, quel nome non è pubblicabile — anche se il DB dice
di sì. Altrimenti la promessa e la lista divergono.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.export_dashboard_v2 import passa_vetrina


def _ok(**over):
    base = {
        "name": "Juan José Fori Viveros",
        "age": 16,
        "club": "C.D Estudiantil",
        "sources": [
            {"domain": "fcf.com.co", "url": "https://fcf.com.co/a"},
            {"domain": "transfermarkt.com", "url": "https://transfermarkt.com/b"},
        ],
    }
    base.update(over)
    return base


class TestPassaVetrina(unittest.TestCase):
    def test_completo_passa(self):
        self.assertTrue(passa_vetrina(_ok()))

    def test_eta_nascosta_non_passa(self):
        self.assertFalse(passa_vetrina(_ok(age=None)))

    def test_club_mancante_non_passa(self):
        self.assertFalse(passa_vetrina(_ok(club="")))
        self.assertFalse(passa_vetrina(_ok(club=None)))

    def test_una_fonte_non_passa(self):
        self.assertFalse(passa_vetrina(_ok(sources=[
            {"domain": "fcf.com.co", "url": "https://fcf.com.co/a"},
        ])))

    def test_fonte_senza_url_non_passa(self):
        self.assertFalse(passa_vetrina(_ok(sources=[
            {"domain": "fcf.com.co", "url": "https://fcf.com.co/a"},
            {"domain": "tiktok.com", "url": ""},
        ])))

    def test_nome_singolo_non_passa(self):
        self.assertFalse(passa_vetrina(_ok(name="Sorriso")))

    def test_handle_non_passa(self):
        self.assertFalse(passa_vetrina(_ok(name="Cauazinn_.08")))


if __name__ == "__main__":
    unittest.main()
