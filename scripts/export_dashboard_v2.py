#!/usr/bin/env python3
"""
OB1 v2 — Export dashboard (cutover)

Legge lo store v2 (ob1_v2.db) e scrive docs/data/players_v2.json per la
dashboard pubblica. Ogni giocatore esce con le sue PROVE (fonti linkate),
il punteggio trasparente (merito × confidenza, con breakdown ricalcolato)
e lo stato del gate — la promessa "ogni nome con le sue prove" resa dato.

Uso:
    python scripts/export_dashboard_v2.py [--db data/ob1_v2.db] [--out docs/data/players_v2.json]
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.scoring_v2 import score_player

ROOT = Path(__file__).parent.parent


def export(db_path: Path, out_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    players = []
    for p in conn.execute("SELECT * FROM players ORDER BY score DESC"):
        pid = p["id"]
        sources = [
            {"domain": r["source_domain"], "url": r["source_url"],
             "seen": r["observed_at"]}
            for r in conn.execute(
                """SELECT source_domain, source_url, MIN(observed_at) AS observed_at
                   FROM evidences WHERE player_id=? AND source_domain != ''
                   GROUP BY source_domain ORDER BY observed_at""", (pid,))
        ]
        stats = json.loads(p["stats_json"]) if p["stats_json"] else {}
        sc = score_player(
            age=p["age"], is_ghost=bool(p["is_ghost"]), club=p["club"],
            league=p["league"], stats=stats, n_sources=max(len(sources), 1),
            detection_count=p["evidence_count"] or 1,
        )
        players.append({
            "name": p["canonical_name"],
            "age": p["age"], "position": p["position"], "club": p["club"],
            "league": p["league"], "region": p["region"],
            "gender": p["gender"],
            "score": sc["score"], "confidence": sc["confidence"],
            "breakdown": sc["breakdown"],
            "n_sources": len(sources), "sources": sources[:4],
            "stats": stats,
            "publishable": bool(p["publishable"]),
            "identity_complete": bool(p["identity_complete"]),
            "review_flags": p["review_flags"] or "",
            "first_detected": p["first_detected"], "last_seen": p["last_seen"],
        })
    conn.close()

    players.sort(key=lambda x: (not x["publishable"], -x["score"]))
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(players),
        "publishable": sum(1 for x in players if x["publishable"]),
        "tracking": sum(1 for x in players if not x["publishable"]),
        "players": players,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "ob1_v2.db"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "players_v2.json"))
    args = ap.parse_args()
    if not Path(args.db).exists():
        print(f"DB v2 non trovato: {args.db} — esporto struttura vuota.")
        doc = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "total": 0, "publishable": 0, "tracking": 0, "players": []}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(doc), encoding="utf-8")
        return
    doc = export(Path(args.db), Path(args.out))
    print(f"Export: {doc['total']} giocatori ({doc['publishable']} pubblicabili) → {args.out}")


if __name__ == "__main__":
    main()
