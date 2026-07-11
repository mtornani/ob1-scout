#!/usr/bin/env python3
"""
OB1 v2 — Migrazione Fase B0

Legge il DB di produzione (`ob1_global.db`, tabella `anomalies` + `lead_times`)
e costruisce lo store entità-centrico v2 (`ob1_v2.db`):
  - ogni anomalia diventa un PLAYER, con flag di qualità identità e gate;
  - la source_url/testo salvati diventano una EVIDENCE (origine legacy);
  - i lead_times completati diventano OUTCOME (mainstream_hype), marcando i
    sospetti (anticipo 0, identità debole, fonte non calcistica).

NON tocca la produzione: legge sola-lettura da ob1_global.db, scrive su ob1_v2.db.

Uso:
    python scripts/migrate_to_v2.py [--source data/ob1_global.db] [--dest data/ob1_v2.db]
"""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.database_v2 import OB1DatabaseV2, normalize_name, domain_of

ROOT = Path(__file__).parent.parent

# Segnali che un lead time NON è copertura calcistica mainstream: falso match.
# Vanno cercati nell'URL COMPLETO, non nel solo dominio — l'articolo UFC di André
# Maia è su espn.com ma il path è /mma/.../ufc-fight-night.
NOISE_URL_HINTS = ("ufc", "mma", "boxing", "/fight")


def migrate(source: Path, dest: Path) -> dict:
    if not source.exists():
        raise FileNotFoundError(f"DB sorgente non trovato: {source}")
    if dest.exists():
        dest.unlink()  # migrazione idempotente: si ricostruisce da zero

    db = OB1DatabaseV2(str(dest))
    src = sqlite3.connect(str(source))
    src.row_factory = sqlite3.Row

    # --- Players + Evidences ---
    anomalies = src.execute("SELECT * FROM anomalies").fetchall()
    name_to_pid = {}
    flag_counter = Counter()

    for a in anomalies:
        source_count = a["source_count"] if "source_count" in a.keys() else 1
        pid = db.add_player(
            canonical_name=a["player_name"],
            age=a["age"] if "age" in a.keys() else None,
            position=a["position"] if "position" in a.keys() else None,
            club=a["club"] if "club" in a.keys() else None,
            league=a["league"] if "league" in a.keys() else None,
            region=a["region"],
            is_ghost=bool(a["is_ghost"]) if "is_ghost" in a.keys() else False,
            legacy_score=a["score"],
            detection_count=a["detection_count"] if "detection_count" in a.keys() else 1,
            source_count=source_count or 1,
            first_detected=a["first_detected"] if "first_detected" in a.keys() else None,
            last_seen=a["last_seen"] if "last_seen" in a.keys() else None,
            legacy_id=a["id"],
            created_at=a["detection_date"] if "detection_date" in a.keys() else None,
        )
        name_to_pid[normalize_name(a["player_name"])] = pid

        # 1 sola evidence: è tutto ciò che il vecchio schema ha conservato.
        db.add_evidence(
            player_id=pid,
            source_url=a["source_url"],
            observed_at=a["first_detected"] if "first_detected" in a.keys() else a["detection_date"],
            raw_content=a["raw_content"],
            origin="legacy_migration",
        )

        # conteggio flag per il report
        row = db._conn().execute("SELECT review_flags FROM players WHERE id=?", (pid,)).fetchone()
        for f in (row[0] or "").split(","):
            if f:
                flag_counter[f] += 1

    # --- Outcomes (da lead_times completati) ---
    out_total = 0
    out_suspect = 0
    clean_leads = []
    try:
        leads = src.execute(
            "SELECT * FROM lead_times WHERE status='completed'").fetchall()
    except sqlite3.OperationalError:
        leads = []

    for lt in leads:
        pid = name_to_pid.get(normalize_name(lt["player_name"]))
        if pid is None:
            continue
        ltd = lt["lead_time_days"]
        src_url = lt["mainstream_source"] if "mainstream_source" in lt.keys() else None
        dom = domain_of(src_url)

        # motivi di sospetto
        reasons = []
        if ltd == 0:
            reasons.append("anticipo_0")
        if any(h in (src_url or "").lower() for h in NOISE_URL_HINTS):
            reasons.append("fonte_non_calcistica")
        prow = db._conn().execute(
            "SELECT review_flags FROM players WHERE id=?", (pid,)).fetchone()
        pflags = prow[0] or ""
        if "nome_singolo" in pflags or "handle_o_soprannome" in pflags:
            reasons.append("identita_debole")

        suspect = bool(reasons)
        db.add_outcome(
            player_id=pid, outcome_type="mainstream_hype",
            outcome_date=lt["mainstream_hype_date"] if "mainstream_hype_date" in lt.keys() else None,
            source_url=src_url, lead_time_days=ltd, suspect=suspect,
            note=",".join(reasons) or None,
        )
        out_total += 1
        if suspect:
            out_suspect += 1
        elif ltd and ltd > 0:
            clean_leads.append((lt["player_name"], ltd, dom))

    src.close()

    # --- Statistiche finali ---
    conn = sqlite3.connect(str(dest))
    total = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    publishable = conn.execute("SELECT COUNT(*) FROM players WHERE publishable=1").fetchone()[0]
    id_complete = conn.execute("SELECT COUNT(*) FROM players WHERE identity_complete=1").fetchone()[0]
    ev = conn.execute("SELECT COUNT(*) FROM evidences").fetchone()[0]
    conn.close()

    return {
        "total": total, "publishable": publishable, "id_complete": id_complete,
        "evidences": ev, "flags": flag_counter, "out_total": out_total,
        "out_suspect": out_suspect, "clean_leads": clean_leads, "dest": str(dest),
    }


def print_report(r: dict):
    total = r["total"] or 1
    print("=" * 60)
    print("MIGRAZIONE v2 — REPORT")
    print("=" * 60)
    print(f"Giocatori migrati:        {r['total']}")
    print(f"Evidenze create:          {r['evidences']}  (1 per giocatore: è tutto")
    print( "                          ciò che il vecchio schema ha conservato)")
    print()
    print("--- GATE DI PUBBLICAZIONE (identità completa + ≥2 fonti) ---")
    print(f"  Pubblicabili:           {r['publishable']}/{r['total']} ({100*r['publishable']//total}%)")
    print(f"  Identità completa:      {r['id_complete']}/{r['total']} ({100*r['id_complete']//total}%)")
    print()
    print("--- MOTIVI DI ESCLUSIONE (un giocatore può averne più d'uno) ---")
    for flag, n in r["flags"].most_common():
        print(f"  {flag:24s} {n}")
    print()
    print("--- OUTCOME (lead time dal mainstream) ---")
    print(f"  Totali migrati:         {r['out_total']}")
    print(f"  Sospetti (scartati):    {r['out_suspect']}  (anticipo 0 / identità debole / fonte non calcistica)")
    print(f"  Anticipi puliti (>0):   {len(r['clean_leads'])}")
    for name, days, dom in sorted(r["clean_leads"], key=lambda x: -x[1]):
        print(f"      {name:22s} {days:>4} gg   [{dom}]")
    print()
    print("--- GENERE ---")
    print("  Tutti a 'unknown' — è una scelta di prodotto ancora aperta (vedi FASE_B.md).")
    print()
    print(f"Store v2 scritto in: {r['dest']}")
    print("=" * 60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "data" / "ob1_global.db"))
    ap.add_argument("--dest", default=str(ROOT / "data" / "ob1_v2.db"))
    args = ap.parse_args()
    r = migrate(Path(args.source), Path(args.dest))
    print_report(r)


if __name__ == "__main__":
    main()
