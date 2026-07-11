#!/usr/bin/env python3
"""
OB1 v2 — Ranker (Fase B1, dimostrazione)

Applica lo scoring trasparente (scoring_v2) ai dati reali e confronta la vecchia
classifica (punteggio LLM opaco) con la nuova (merito × confidenza). Serve a
*validare visivamente* che il nuovo modello mette in cima l'evidenza solida e non
i nomi fragili — prima di renderlo default (CLAUDE.md §2).

Sola-lettura: non modifica nessun DB.

Uso:
    python scripts/rank_v2.py [--source data/ob1_global.db]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.scoring_v2 import score_player

ROOT = Path(__file__).parent.parent


def load(source: Path) -> list:
    conn = sqlite3.connect(str(source))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM anomalies").fetchall()
    conn.close()
    out = []
    for a in rows:
        k = a.keys()
        res = score_player(
            age=a["age"] if "age" in k else None,
            is_ghost=bool(a["is_ghost"]) if "is_ghost" in k else False,
            club=a["club"] if "club" in k else None,
            league=a["league"] if "league" in k else None,
            stats=a["stats_summary"] if "stats_summary" in k else None,
            n_sources=(a["source_count"] if "source_count" in k else 1) or 1,
            detection_count=(a["detection_count"] if "detection_count" in k else 1) or 1,
        )
        out.append({
            "name": a["player_name"], "old": a["score"] or 0,
            "new": res["score"], "conf": res["confidence"],
            "det": (a["detection_count"] if "detection_count" in k else 1) or 1,
            "ghost": bool(a["is_ghost"]) if "is_ghost" in k else False,
            "bd": res["breakdown"],
        })
    return out


def show(players: list):
    # Assegna le posizioni in classifica nei due sistemi
    for rank, p in enumerate(sorted(players, key=lambda x: x["old"], reverse=True), 1):
        p["old_rank"] = rank
    for rank, p in enumerate(sorted(players, key=lambda x: x["new"], reverse=True), 1):
        p["new_rank"] = rank
    for p in players:
        p["climb"] = p["old_rank"] - p["new_rank"]  # >0 = salito in classifica

    def line(p):
        g = " GHOST" if p["ghost"] else ""
        return (f"{p['name'][:22]:22s} old#{p['old_rank']:>2} new#{p['new_rank']:>2}  "
                f"({p['old']:>3.0f}→{p['new']:>2d})  conf={p['conf']:.2f}  seen={p['det']:>2}x{g}")

    print("=" * 66)
    print("TOP 10 — VECCHIA classifica (punteggio LLM opaco)")
    print("=" * 66)
    for p in sorted(players, key=lambda x: x["old"], reverse=True)[:10]:
        print("  " + line(p))

    print()
    print("=" * 66)
    print("TOP 10 — NUOVA classifica (merito × confidenza, trasparente)")
    print("=" * 66)
    for p in sorted(players, key=lambda x: x["new"], reverse=True)[:10]:
        print("  " + line(p))

    print()
    print("--- SALITI in classifica (evidenza solida premiata) ---")
    for p in sorted(players, key=lambda x: x["climb"], reverse=True)[:5]:
        print("  " + line(p) + f"   ▲{p['climb']}")
    print()
    print("--- CROLLATI (evidenza fragile declassata) ---")
    for p in sorted(players, key=lambda x: x["climb"])[:5]:
        print("  " + line(p) + f"   ▼{-p['climb']}")
    print("=" * 66)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "data" / "ob1_global.db"))
    args = ap.parse_args()
    players = load(Path(args.source))
    show(players)


if __name__ == "__main__":
    main()
