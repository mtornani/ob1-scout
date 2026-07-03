#!/usr/bin/env python3
"""Test unitari per observer_monitor — statistica pura, nessun accesso git/rete."""

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import observer_monitor as om


T0 = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)


def snap(hours_offset, n_rows, sum_det, region_det=None, sha=None):
    ts = (T0 + timedelta(hours=hours_offset)).isoformat()
    return {"sha": sha or f"sha{hours_offset}", "ts": ts,
            "n_rows": n_rows, "sum_det": sum_det,
            "region_det": region_det or {"Brazil": sum_det}}


class TestDeltas(unittest.TestCase):
    def test_regular_cadence_no_missed_slots(self):
        series = [snap(0, 10, 20), snap(6, 11, 22), snap(12, 12, 24)]
        deltas = om.compute_deltas(series)
        self.assertEqual([d["slots_missed"] for d in deltas], [0, 0])
        self.assertEqual([d["d_rows"] for d in deltas], [1, 1])

    def test_gap_counts_missed_slots(self):
        # 24h di gap = 3 slot mancati (4 cicli nominali, 1 con snapshot)
        series = [snap(0, 10, 20), snap(24, 11, 22)]
        deltas = om.compute_deltas(series)
        self.assertEqual(deltas[0]["slots_missed"], 3)

    def test_drift_tolerance(self):
        # drift tipico GitHub (5.5h) non genera slot mancati
        series = [snap(0, 10, 20), snap(5.5, 11, 22)]
        self.assertEqual(om.compute_deltas(series)[0]["slots_missed"], 0)

    def test_region_activity_never_negative(self):
        series = [snap(0, 10, 20, {"Brazil": 15, "Asia": 5}),
                  snap(6, 10, 21, {"Brazil": 13, "Asia": 8})]
        d = om.compute_deltas(series)[0]
        self.assertEqual(d["region_act"], {"Asia": 3})


class TestStreak(unittest.TestCase):
    def test_flat_week_episode(self):
        # attività, poi 7 giorni piatti (28 slot), poi ripresa
        series = [snap(0, 10, 20), snap(6, 11, 22),
                  snap(6 + 7 * 24, 12, 24)]
        eps = om.streak_episodes(om.compute_deltas(series))
        self.assertEqual(len(eps), 1)
        self.assertEqual(eps[0]["peak_slots"], 27)  # 28 cicli, 1 porta dati
        self.assertEqual(eps[0]["start_ts"], series[1]["ts"])
        self.assertIsNotNone(eps[0]["end_ts"])

    def test_zero_delta_commit_counts_in_streak(self):
        series = [snap(0, 10, 20), snap(6, 10, 20), snap(12, 11, 22)]
        eps = om.streak_episodes(om.compute_deltas(series))
        self.assertEqual(eps[0]["peak_slots"], 1)

    def test_open_episode_and_current_streak(self):
        series = [snap(0, 10, 20), snap(6, 11, 22)]
        deltas = om.compute_deltas(series)
        now = T0 + timedelta(hours=6 + 30)  # 30h dopo l'ultimo snapshot
        streak, start = om.current_streak_slots(deltas, now)
        self.assertEqual(streak, 5)
        self.assertEqual(start, series[1]["ts"])

    def test_no_streak_when_fresh(self):
        series = [snap(0, 10, 20), snap(6, 11, 22)]
        deltas = om.compute_deltas(series)
        streak, start = om.current_streak_slots(deltas, T0 + timedelta(hours=8))
        self.assertEqual(streak, 0)
        self.assertIsNone(start)


class TestP2(unittest.TestCase):
    CAL = {"p2": {"drop_frac": 0.5, "min_prev_rows": 10}}

    def test_drop_over_50_fires(self):
        series = [snap(0, 40, 80), snap(6, 15, 30)]
        events = om.evaluate_p2(om.compute_deltas(series), self.CAL)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["hazard"], "HZ-06")

    def test_small_base_ignored(self):
        series = [snap(0, 4, 8), snap(6, 1, 2)]
        self.assertEqual(om.evaluate_p2(om.compute_deltas(series), self.CAL), [])

    def test_drop_exactly_50_does_not_fire(self):
        series = [snap(0, 40, 80), snap(6, 20, 40)]
        self.assertEqual(om.evaluate_p2(om.compute_deltas(series), self.CAL), [])


class TestP3(unittest.TestCase):
    def test_tv_distance(self):
        self.assertAlmostEqual(om.tv_distance({"a": 1.0}, {"b": 1.0}), 1.0)
        self.assertAlmostEqual(om.tv_distance({"a": 0.5, "b": 0.5},
                                              {"a": 0.5, "b": 0.5}), 0.0)

    def test_group_regions(self):
        g = om.group_regions({"Brazil": 10, "Asia": 2, "X": 1}, ["Brazil"])
        self.assertEqual(g, {"Brazil": 10, "other": 3})

    def test_window_requires_min_activity(self):
        series = [snap(0, 10, 20), snap(6, 11, 22)]
        deltas = om.compute_deltas(series)
        self.assertEqual(om.p3_windows(deltas, ["Brazil"], 7, 15), [])

    def test_composition_shift_fires_once_per_episode(self):
        # baseline Brazil-only; poi 7gg di attività solo Asia
        cal = {"p3": {"keep_regions": ["Brazil"], "baseline": {"Brazil": 1.0, "other": 0.0},
                      "window_days": 7, "min_activity": 15, "threshold_tv": 0.5}}
        series = [snap(0, 10, 20, {"Brazil": 20})]
        for i in range(1, 9):
            series.append(snap(i * 6, 10 + i * 3, 20 + i * 3,
                               {"Brazil": 20, "Asia": i * 3}))
        events = om.evaluate_p3_episodes(om.compute_deltas(series), cal)
        self.assertEqual(len(events), 1)  # rising edge unico
        self.assertEqual(events[0]["hazard"], "HZ-01/HZ-03")


class TestCalibrationLogic(unittest.TestCase):
    def test_spans(self):
        spans = [("2026-06-22T00:00:00+00:00", "2026-06-29T00:00:00+00:00")]
        self.assertTrue(om.in_any_span("2026-06-25T12:00:00+00:00", spans))
        self.assertFalse(om.in_any_span("2026-06-30T00:00:00+00:00", spans))
        self.assertTrue(om.episode_overlaps("2026-06-21T18:00:00+00:00",
                                            "2026-06-29T04:00:00+00:00", spans))
        self.assertFalse(om.episode_overlaps("2026-06-30T00:00:00+00:00",
                                             None, spans))

    def test_delta_spanning_window_start_is_excluded(self):
        # delta con prev fuori finestra (coda di una pausa storica) non entra
        series = [snap(-73 * 24, 10, 20), snap(4, 11, 22), snap(10, 12, 24)]
        deltas = om.compute_deltas(series)
        start = T0.isoformat()
        end = (T0 + timedelta(days=30)).isoformat()
        win = [d for d in deltas if om.delta_in_window(d, start, end, [])]
        self.assertEqual(len(win), 1)
        self.assertEqual(win[0]["sha"], series[2]["sha"])

    def test_delta_overlapping_exclude_is_excluded(self):
        series = [snap(0, 10, 20), snap(6, 11, 22), snap(6 + 174, 12, 24)]
        deltas = om.compute_deltas(series)
        excl = [((T0 + timedelta(hours=12)).isoformat(),
                 (T0 + timedelta(hours=170)).isoformat())]
        start, end = T0.isoformat(), (T0 + timedelta(days=30)).isoformat()
        win = [d for d in deltas if om.delta_in_window(d, start, end, excl)]
        self.assertEqual(len(win), 1)

    def test_p1_events_report_crossing_time(self):
        cal = {"p1": {"threshold_slots": 4}}
        series = [snap(0, 10, 20), snap(6, 11, 22), snap(6 + 48, 12, 24)]
        events = om.evaluate_p1_episodes(om.compute_deltas(series), cal)
        self.assertEqual(len(events), 1)
        expected_crossing = (T0 + timedelta(hours=6 + 4 * 6)).isoformat()
        self.assertEqual(events[0]["ts"], expected_crossing)


class TestFailureAsSignal(unittest.TestCase):
    """Review Gemini, disposizione 1: uno snapshot corrotto non viene mai
    saltato in silenzio — record d'errore esplicito, contato, serie coerente."""

    def test_corrupt_blob_produces_error_record_and_is_counted(self):
        commits = [
            {"sha": "good1", "ts": (T0 + timedelta(hours=0)).isoformat()},
            {"sha": "corrupt", "ts": (T0 + timedelta(hours=6)).isoformat()},
            {"sha": "good2", "ts": (T0 + timedelta(hours=12)).isoformat()},
        ]

        def fake_extract(sha):
            if sha == "corrupt":
                raise sqlite3.DatabaseError("file is not a database")
            return {"n_rows": 10, "sum_det": 20, "region_det": {"Brazil": 20}}

        old = (om.OBSERVATIONS, om.list_db_commits, om.extract_metrics)
        with tempfile.TemporaryDirectory() as td:
            om.OBSERVATIONS = Path(td) / "observations.jsonl"
            om.list_db_commits = lambda ref="HEAD": commits
            om.extract_metrics = fake_extract
            try:
                series, failures = om.build_series()  # nessun crash
                # ri-run: l'errore identico non viene duplicato in cache
                series2, failures2 = om.build_series()
                records = om.load_jsonl(om.OBSERVATIONS)
            finally:
                om.OBSERVATIONS, om.list_db_commits, om.extract_metrics = old

        self.assertEqual(failures, 1)
        self.assertEqual(failures2, 1)
        self.assertEqual([s["sha"] for s in series], ["good1", "good2"])
        self.assertEqual([s["sha"] for s in series2], ["good1", "good2"])
        errors = [r for r in records if "extract_error" in r]
        self.assertEqual(len(errors), 1)  # niente righe d'errore duplicate
        self.assertEqual(errors[0]["sha"], "corrupt")
        self.assertEqual(errors[0]["ts"], commits[1]["ts"])
        self.assertIn("not a database", errors[0]["extract_error"])

    def test_all_corrupt_raises_noisy_systemexit(self):
        # disposizione 4: serie vuota = guasto rumoroso, distinto dal degrado
        commits = [{"sha": "c1", "ts": T0.isoformat()}]

        def fake_extract(sha):
            raise sqlite3.DatabaseError("boom")

        old = (om.OBSERVATIONS, om.list_db_commits, om.extract_metrics)
        with tempfile.TemporaryDirectory() as td:
            om.OBSERVATIONS = Path(td) / "observations.jsonl"
            om.list_db_commits = lambda ref="HEAD": commits
            om.extract_metrics = fake_extract
            try:
                with self.assertRaises(SystemExit):
                    om.build_series()
            finally:
                om.OBSERVATIONS, om.list_db_commits, om.extract_metrics = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
