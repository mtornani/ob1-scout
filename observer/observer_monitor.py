#!/usr/bin/env python3
"""
OB1 Observer — Symbiont M2 (§5.3)

Osservatore passivo della pipeline OB1 Global Radar.
- READ-ONLY sulla pipeline: legge SOLO gli snapshot git di data/ob1_global.db.
- Scrive SOLO file JSONL append-only in /observer/.
- Zero chiamate LLM. Zero interventi: emette solo alert e heartbeat.
- Heartbeat a doppio canale: messaggio [OBSERVER] via notifier esistente
  + riga timestamp in observer/heartbeat.jsonl (liveness via git).

Coppie hazard→segnale implementate (Gate 2a):
  P1  HZ-05/HZ-07  flatline silente : streak di slot nominali (6h) senza
                   delta-dati sugli snapshot git; soglia da calibrazione
                   (max streak pulito + margine).
  P2  HZ-06        drop di volume   : calo >50% delle righe `anomalies`
                   tra snapshot consecutivi (resuscita il check inerte
                   di sanity_check senza toccare la pipeline).
  P3  HZ-01/HZ-03  drift composizione per fonte: distribuzione dell'attività
                   per `region` (proxy 1:1 dei pack di query regionali dello
                   scraper) su finestra mobile vs baseline di calibrazione.

Modalità:
  --observe    (default) aggiorna cache osservazioni, valuta P1/P2/P3,
               appende alarm + heartbeat, invia notifiche admin.
  --calibrate  calcola soglie su una finestra [--start, --end] con
               esclusioni --exclude A..B (incident noti); appende una
               riga a calibration.jsonl.
  --retrodict  applica l'ultima calibrazione a TUTTA la storia; positivi
               etichettati via --label A..B; appende a retrodiction.jsonl.
"""

import argparse
import json
import logging
import math
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OBS_DIR = ROOT / "observer"
DB_REL = "data/ob1_global.db"

# --- Parametri (congelati alla calibrazione; vedi calibration.jsonl) ---
SLOT_HOURS = 6.0          # cadenza nominale della pipeline (cron 0 */6)
P1_MARGIN_SLOTS = 2       # margine sopra il max streak pulito di calibrazione
P2_DROP_FRAC = 0.5        # soglia drop righe (dal piano: >50%)
P2_MIN_PREV = 10          # non valutare P2 sotto questa base
P3_WINDOW_DAYS = 7.0      # finestra mobile per la composizione
P3_MIN_ACTIVITY = 15      # attività minima nella finestra per valutare P3
P3_MARGIN = 0.10          # margine sopra il max TV di calibrazione
P3_TOP_REGIONS = 5        # regioni tenute distinte; il resto in "other"

OBSERVATIONS = OBS_DIR / "observations.jsonl"
CALIBRATION = OBS_DIR / "calibration.jsonl"
ALARMS = OBS_DIR / "alarms.jsonl"
HEARTBEAT = OBS_DIR / "heartbeat.jsonl"
RETRODICTION = OBS_DIR / "retrodiction.jsonl"

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)-5s [observer] %(message)s')
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Utility git / jsonl
# ----------------------------------------------------------------------

def _git(*args, binary=False):
    res = subprocess.run(["git", "-C", str(ROOT), *args],
                         check=True, capture_output=True)
    return res.stdout if binary else res.stdout.decode()


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_jsonl(path: Path, records: list) -> None:
    if not records:
        return
    with path.open('a', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


# ----------------------------------------------------------------------
# Estrazione snapshot (read-only sulla storia git)
# ----------------------------------------------------------------------

def list_db_commits(ref: str = "HEAD") -> list:
    """Commit che toccano il DB, in ordine cronologico: [{'sha','ts'}]."""
    out = _git("log", "--format=%H %cI", "--reverse", ref, "--", DB_REL)
    commits = []
    for line in out.splitlines():
        sha, ts = line.split()
        commits.append({"sha": sha, "ts": parse_ts(ts).isoformat()})
    return commits


def extract_metrics(sha: str) -> dict:
    """Metriche di uno snapshot del DB: righe, attività, attività per regione."""
    blob = _git("show", f"{sha}:{DB_REL}", binary=True)
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    try:
        tmp.write(blob)
        tmp.close()
        conn = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(anomalies)")}
            det = "COALESCE(detection_count,1)" if "detection_count" in cols else "1"
            reg = "COALESCE(region,'Unknown')" if "region" in cols else "'Unknown'"
            n_rows = conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
            sum_det = conn.execute(
                f"SELECT COALESCE(SUM({det}),0) FROM anomalies").fetchone()[0]
            region_det = {r or "Unknown": d for r, d in conn.execute(
                f"SELECT {reg}, SUM({det}) FROM anomalies GROUP BY 1")}
        finally:
            conn.close()
    finally:
        os.unlink(tmp.name)
    return {"n_rows": n_rows, "sum_det": sum_det, "region_det": region_det}


def build_series(ref: str = "HEAD") -> list:
    """Serie completa snapshot (cache append-only in observations.jsonl)."""
    cache = {r["sha"]: r for r in load_jsonl(OBSERVATIONS)}
    commits = list_db_commits(ref)
    new = []
    for c in commits:
        if c["sha"] not in cache:
            m = extract_metrics(c["sha"])
            rec = {"sha": c["sha"], "ts": c["ts"], **m}
            cache[c["sha"]] = rec
            new.append(rec)
    if new:
        append_jsonl(OBSERVATIONS, new)
        logger.info(f"cache osservazioni: +{len(new)} snapshot (tot {len(cache)})")
    series = [cache[c["sha"]] for c in commits]
    return series


# ----------------------------------------------------------------------
# Statistica pura (testabile senza git)
# ----------------------------------------------------------------------

def compute_deltas(series: list) -> list:
    """Delta tra snapshot consecutivi."""
    deltas = []
    for prev, cur in zip(series, series[1:]):
        t_prev, t_cur = parse_ts(prev["ts"]), parse_ts(cur["ts"])
        gap_h = (t_cur - t_prev).total_seconds() / 3600.0
        slots_missed = max(0, round(gap_h / SLOT_HOURS) - 1)
        d_rows = cur["n_rows"] - prev["n_rows"]
        d_act = cur["sum_det"] - prev["sum_det"]
        regions = set(cur["region_det"]) | set(prev["region_det"])
        region_act = {r: max(0, cur["region_det"].get(r, 0) - prev["region_det"].get(r, 0))
                      for r in regions}
        region_act = {r: v for r, v in region_act.items() if v > 0}
        drop_frac = ((prev["n_rows"] - cur["n_rows"]) / prev["n_rows"]
                     if prev["n_rows"] > 0 else 0.0)
        deltas.append({
            "ts": cur["ts"], "sha": cur["sha"], "prev_sha": prev["sha"],
            "prev_ts": prev["ts"], "gap_h": round(gap_h, 2),
            "slots_missed": slots_missed,
            "d_rows": d_rows, "d_act": d_act,
            "prev_rows": prev["n_rows"], "n_rows": cur["n_rows"],
            "drop_frac": round(drop_frac, 4),
            "region_act": region_act,
        })
    return deltas


def streak_episodes(deltas: list) -> list:
    """
    Episodi di zero-delta: sequenze massimali di slot nominali senza dati.
    Ritorna [{'start_ts','end_ts','peak_slots'}]; start = ultimo snapshot
    con dati prima dell'episodio.
    """
    episodes = []
    streak = 0
    start_ts = deltas[0]["prev_ts"] if deltas else None
    for d in deltas:
        contribution = d["slots_missed"] + (1 if (d["d_rows"] <= 0 and d["d_act"] <= 0) else 0)
        if contribution > 0:
            if streak == 0:
                start_ts = d["prev_ts"]
            streak += contribution
        if d["d_rows"] > 0 or d["d_act"] > 0:
            if streak > 0:
                episodes.append({"start_ts": start_ts, "end_ts": d["ts"],
                                 "peak_slots": streak})
            streak = 0
            start_ts = d["ts"]
    if streak > 0:
        episodes.append({"start_ts": start_ts, "end_ts": None,
                         "peak_slots": streak})
    return episodes


def current_streak_slots(deltas: list, now: datetime) -> tuple:
    """Streak corrente in slot e timestamp di inizio episodio."""
    episodes = streak_episodes(deltas)
    open_ep = episodes[-1] if episodes and episodes[-1]["end_ts"] is None else None
    if deltas:
        last_ts = parse_ts(deltas[-1]["ts"])
    else:
        return 0, None
    tail_slots = max(0, math.floor((now - last_ts).total_seconds() / 3600.0 / SLOT_HOURS))
    if open_ep:
        return open_ep["peak_slots"] + tail_slots, open_ep["start_ts"]
    if tail_slots > 0:
        return tail_slots, deltas[-1]["ts"]
    return 0, None


def group_regions(region_act: dict, keep: list) -> dict:
    """Aggrega le regioni non in `keep` sotto 'other'."""
    out = {k: 0 for k in keep + ["other"]}
    for r, v in region_act.items():
        out[r if r in keep else "other"] += v
    return out


def tv_distance(p: dict, q: dict) -> float:
    """Total variation distance tra due distribuzioni (dict regione→share)."""
    keys = set(p) | set(q)
    return 0.5 * sum(abs(p.get(k, 0.0) - q.get(k, 0.0)) for k in keys)


def _proportions(counts: dict) -> dict:
    tot = sum(counts.values())
    if tot <= 0:
        return {}
    return {k: v / tot for k, v in counts.items()}


def p3_windows(deltas: list, keep: list, window_days: float = P3_WINDOW_DAYS,
               min_activity: int = P3_MIN_ACTIVITY) -> list:
    """Per ogni snapshot: composizione dell'attività nella finestra mobile."""
    out = []
    for i, d in enumerate(deltas):
        t_end = parse_ts(d["ts"])
        t_start = t_end - timedelta(days=window_days)
        acc = {}
        for j in range(i, -1, -1):
            tj = parse_ts(deltas[j]["ts"])
            if tj <= t_start:
                break
            for r, v in deltas[j]["region_act"].items():
                acc[r] = acc.get(r, 0) + v
        grouped = group_regions(acc, keep)
        total = sum(grouped.values())
        if total >= min_activity:
            out.append({"ts": d["ts"], "sha": d["sha"], "total": total,
                        "proportions": _proportions(grouped)})
    return out


def in_any_span(ts: str, spans: list) -> bool:
    t = parse_ts(ts)
    for a, b in spans:
        if parse_ts(a) <= t <= parse_ts(b):
            return True
    return False


def episode_overlaps(ep_start: str, ep_end: str, spans: list) -> bool:
    a = parse_ts(ep_start)
    b = parse_ts(ep_end) if ep_end else datetime.now(timezone.utc)
    for s, e in spans:
        if a <= parse_ts(e) and b >= parse_ts(s):
            return True
    return False


# ----------------------------------------------------------------------
# Calibrazione
# ----------------------------------------------------------------------

def delta_in_window(d: dict, start: str, end: str, excludes: list) -> bool:
    """Un delta appartiene alla finestra solo se [prev_ts, ts] è interamente
    contenuto in [start, end] e non sovrappone alcuno span escluso."""
    if not (parse_ts(start) <= parse_ts(d["prev_ts"])
            and parse_ts(d["ts"]) <= parse_ts(end)):
        return False
    return not episode_overlaps(d["prev_ts"], d["ts"], excludes)


def calibrate(series: list, start: str, end: str, excludes: list) -> dict:
    """Calcola soglie P1/P3 su finestra pulita. Appende a calibration.jsonl."""
    deltas = compute_deltas(series)
    win = [d for d in deltas if delta_in_window(d, start, end, excludes)]
    if not win:
        raise SystemExit("finestra di calibrazione vuota")

    # P1: max streak pulito nella finestra
    episodes = [e for e in streak_episodes(win) if e["end_ts"] is not None]
    max_clean_streak = max((e["peak_slots"] for e in episodes), default=0)
    p1_threshold = max_clean_streak + P1_MARGIN_SLOTS

    # P3: baseline composizione + max TV su finestre mobili interne
    base_counts = {}
    for d in win:
        for r, v in d["region_act"].items():
            base_counts[r] = base_counts.get(r, 0) + v
    keep = [r for r, _ in sorted(base_counts.items(),
                                 key=lambda kv: -kv[1])[:P3_TOP_REGIONS]]
    baseline = _proportions(group_regions(base_counts, keep))
    windows = p3_windows(win, keep)
    tvs = [tv_distance(w["proportions"], baseline) for w in windows]
    max_tv = max(tvs, default=0.0)
    p3_threshold = round(min(1.0, max_tv + P3_MARGIN), 4)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "calibration",
        "window": {"start": start, "end": end, "excludes": excludes},
        "n_snapshots": len(win),
        "gap_slots_distribution": _gap_histogram(win),
        "p1": {"max_clean_streak_slots": max_clean_streak,
               "margin_slots": P1_MARGIN_SLOTS,
               "threshold_slots": p1_threshold,
               "threshold_hours": p1_threshold * SLOT_HOURS},
        "p2": {"drop_frac": P2_DROP_FRAC, "min_prev_rows": P2_MIN_PREV},
        "p3": {"keep_regions": keep, "baseline": {k: round(v, 4) for k, v in baseline.items()},
               "window_days": P3_WINDOW_DAYS, "min_activity": P3_MIN_ACTIVITY,
               "max_tv_in_calibration": round(max_tv, 4),
               "margin": P3_MARGIN, "threshold_tv": p3_threshold,
               "n_windows": len(windows)},
        "note": "P3 usa `region` come proxy dei pack di query regionali dello scraper",
    }
    append_jsonl(CALIBRATION, [record])
    return record


def _gap_histogram(deltas: list) -> dict:
    hist = {}
    for d in deltas:
        k = str(d["slots_missed"])
        hist[k] = hist.get(k, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: int(kv[0])))


def load_calibration() -> dict | None:
    records = [r for r in load_jsonl(CALIBRATION) if r.get("type") == "calibration"]
    return records[-1] if records else None


# ----------------------------------------------------------------------
# Valutazione allarmi (condivisa da observe e retrodict)
# ----------------------------------------------------------------------

def evaluate_p2(deltas: list, calib: dict) -> list:
    thr = calib["p2"]["drop_frac"]
    min_prev = calib["p2"]["min_prev_rows"]
    return [{"type": "P2", "hazard": "HZ-06", "ts": d["ts"], "sha": d["sha"],
             "detail": f"drop righe anomalies: {d['prev_rows']}→{d['n_rows']} "
                       f"(-{d['drop_frac'] * 100:.0f}%)"}
            for d in deltas if d["prev_rows"] >= min_prev and d["drop_frac"] > thr]


def evaluate_p3_episodes(deltas: list, calib: dict) -> list:
    keep = calib["p3"]["keep_regions"]
    baseline = calib["p3"]["baseline"]
    thr = calib["p3"]["threshold_tv"]
    windows = p3_windows(deltas, keep,
                         calib["p3"]["window_days"], calib["p3"]["min_activity"])
    events, above = [], False
    for w in windows:
        tv = tv_distance(w["proportions"], baseline)
        if tv >= thr and not above:
            events.append({"type": "P3", "hazard": "HZ-01/HZ-03",
                           "ts": w["ts"], "sha": w["sha"],
                           "detail": f"drift composizione fonti: TV={tv:.3f} "
                                     f"(soglia {thr}) su {w['total']} attività/"
                                     f"{calib['p3']['window_days']:.0f}gg"})
        above = tv >= thr
    return events


def evaluate_p1_episodes(deltas: list, calib: dict) -> list:
    thr = calib["p1"]["threshold_slots"]
    events = []
    for ep in streak_episodes(deltas):
        if ep["peak_slots"] >= thr:
            crossing = parse_ts(ep["start_ts"]) + timedelta(hours=thr * SLOT_HOURS)
            events.append({"type": "P1", "hazard": "HZ-05/HZ-07",
                           "ts": crossing.isoformat(),
                           "episode_start": ep["start_ts"],
                           "episode_end": ep["end_ts"],
                           "peak_slots": ep["peak_slots"],
                           "detail": f"flatline: {ep['peak_slots']} slot (~"
                                     f"{ep['peak_slots'] * SLOT_HOURS:.0f}h) senza delta-dati "
                                     f"(soglia {thr} slot)"})
    return events


# ----------------------------------------------------------------------
# Retrodizione
# ----------------------------------------------------------------------

def retrodict(series: list, calib: dict, labels: list) -> dict:
    """Applica la calibrazione a tutta la storia. Positivi etichettati in `labels`."""
    deltas = compute_deltas(series)
    p1 = evaluate_p1_episodes(deltas, calib)
    p2 = evaluate_p2(deltas, calib)
    p3 = evaluate_p3_episodes(deltas, calib)

    def classify(events, use_episode=False):
        hits, false_alarms = [], []
        for e in events:
            if use_episode and e.get("episode_start"):
                match = episode_overlaps(e["episode_start"], e.get("episode_end"), labels)
            else:
                match = in_any_span(e["ts"], labels)
            (hits if match else false_alarms).append(e)
        return hits, false_alarms

    p1_hits, p1_fa = classify(p1, use_episode=True)
    p2_hits, p2_fa = classify(p2)
    p3_hits, p3_fa = classify(p3)

    t0, t1 = parse_ts(series[0]["ts"]), parse_ts(series[-1]["ts"])
    total_days = (t1 - t0).total_seconds() / 86400.0
    label_days = sum((parse_ts(b) - parse_ts(a)).total_seconds() / 86400.0
                     for a, b in labels)
    clean_days = max(total_days - label_days, 1e-9)
    n_fa = len(p1_fa) + len(p2_fa) + len(p3_fa)

    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "retrodiction",
        "calibration_ts": calib["ts"],
        "history": {"start": series[0]["ts"], "end": series[-1]["ts"],
                    "n_snapshots": len(series), "days": round(total_days, 1)},
        "labels": labels,
        "p1": {"events": p1, "hits": len(p1_hits), "false_alarms": len(p1_fa)},
        "p2": {"events": p2, "hits": len(p2_hits), "false_alarms": len(p2_fa)},
        "p3": {"events": p3, "hits": len(p3_hits), "false_alarms": len(p3_fa)},
        "false_alarms_per_day": round(n_fa / clean_days, 4),
        "clean_days": round(clean_days, 1),
    }
    append_jsonl(RETRODICTION, [record])
    return record


# ----------------------------------------------------------------------
# Notifica (best-effort, via notifier esistente — nessuna modifica ad esso)
# ----------------------------------------------------------------------

def notify(severity: str, message: str) -> None:
    try:
        sys.path.insert(0, str(ROOT))
        from src.notifier import admin_alert
        admin_alert(severity, "observer", f"[OBSERVER] {message}")
    except Exception as e:  # requests assente o altro: mai bloccare l'observer
        logger.warning(f"notifica non inviata ({e}); messaggio: [OBSERVER] {message}")


# ----------------------------------------------------------------------
# Observe (run giornaliera)
# ----------------------------------------------------------------------

def observe(ref: str = "HEAD", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    series = build_series(ref)
    calib = load_calibration()
    if not calib:
        notify("WARN", "heartbeat DEGRADATO: nessuna calibrazione trovata")
        append_jsonl(HEARTBEAT, [{"ts": now.isoformat(), "type": "heartbeat",
                                  "status": "no-calibration"}])
        return {"status": "no-calibration"}

    deltas = compute_deltas(series)
    known = {(a["type"], a.get("episode_start") or a.get("sha") or a["ts"])
             for a in load_jsonl(ALARMS)}
    new_alarms = []

    # P1 — sullo streak corrente (episodio aperto incluso il tempo trascorso)
    streak, ep_start = current_streak_slots(deltas, now)
    if streak >= calib["p1"]["threshold_slots"] and ep_start:
        key = ("P1", ep_start)
        if key not in known:
            new_alarms.append({
                "ts": now.isoformat(), "type": "P1", "hazard": "HZ-05/HZ-07",
                "episode_start": ep_start, "peak_slots": streak,
                "severity": "ERROR",
                "detail": f"flatline in corso: {streak} slot (~{streak * SLOT_HOURS:.0f}h) "
                          f"senza delta-dati dal {ep_start[:16]} "
                          f"(soglia {calib['p1']['threshold_slots']} slot). "
                          f"Cause possibili: output piatto o run non eseguite (verificare Actions)."})

    # P2 — sugli ultimi delta non ancora visti
    for e in evaluate_p2(deltas[-8:], calib):
        key = ("P2", e["sha"])
        if key not in known:
            new_alarms.append({**e, "severity": "CRITICAL",
                               "ts": now.isoformat(), "detected_at_sha": e["sha"]})

    # P3 — rising edge sull'ultima finestra
    p3_events = evaluate_p3_episodes(deltas, calib)
    for e in p3_events:
        if parse_ts(e["ts"]) >= now - timedelta(days=2):
            key = ("P3", e["sha"])
            if key not in known:
                new_alarms.append({**e, "severity": "WARN"})

    append_jsonl(ALARMS, new_alarms)
    for a in new_alarms:
        notify(a["severity"], a["detail"])

    hb = {"ts": now.isoformat(), "type": "heartbeat", "status": "alive",
          "last_snapshot_ts": series[-1]["ts"] if series else None,
          "n_snapshots": len(series), "current_streak_slots": streak,
          "new_alarms": len(new_alarms)}
    append_jsonl(HEARTBEAT, [hb])
    notify("INFO" if not new_alarms else "WARN",
           f"heartbeat {now.date()}: {len(series)} snapshot, streak {streak} slot, "
           f"{len(new_alarms)} alarm nuovi")
    return {"status": "ok", "streak": streak, "new_alarms": new_alarms}


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _parse_span(s: str) -> tuple:
    a, b = s.split("..")
    return (parse_ts(a).isoformat(), parse_ts(b).isoformat())


def main():
    ap = argparse.ArgumentParser(description="OB1 Observer (Symbiont M2)")
    ap.add_argument("--ref", default="HEAD", help="ref git da osservare")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--calibrate", action="store_true")
    mode.add_argument("--retrodict", action="store_true")
    ap.add_argument("--start", help="inizio finestra calibrazione (ISO)")
    ap.add_argument("--end", help="fine finestra calibrazione (ISO)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="span escluso dalla calibrazione: A..B (ripetibile)")
    ap.add_argument("--label", action="append", default=[],
                    help="span positivo etichettato per retrodizione: A..B")
    args = ap.parse_args()

    if args.calibrate:
        if not (args.start and args.end):
            ap.error("--calibrate richiede --start e --end")
        series = build_series(args.ref)
        rec = calibrate(series, parse_ts(args.start).isoformat(),
                        parse_ts(args.end).isoformat(),
                        [_parse_span(x) for x in args.exclude])
        print(json.dumps(rec, indent=2, ensure_ascii=False))
    elif args.retrodict:
        series = build_series(args.ref)
        calib = load_calibration()
        if not calib:
            raise SystemExit("nessuna calibrazione: eseguire prima --calibrate")
        rec = retrodict(series, calib, [_parse_span(x) for x in args.label])
        print(json.dumps(rec, indent=2, ensure_ascii=False))
    else:
        res = observe(args.ref)
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
