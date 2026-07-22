#!/usr/bin/env python3
"""
OB1 — Snapshot per demo esterna (partner commerciale)

Estrae i findings più recenti e verificati (identità completa + corroborazione
indipendente) dallo store v2 e li scrive in una forma sanificata: niente
punteggi, pesi, endpoint, nomi di fonte o riferimenti di codice — solo ciò che
serve a mostrare cosa il sistema trova, non come lo trova.

Uso:
    python scripts/export_partner_snapshot.py [--db data/ob1_v2.db] [--out ob1_global.json] [--limit 5]
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _role_age(position, age) -> str:
    role = (position or "").strip() or "Ruolo non specificato"
    if age is None:
        return role
    return f"{role}, {age} anni"


def _confidence_tier(evidence_count: int) -> str:
    if evidence_count >= 4:
        return "high"
    if evidence_count >= 2:
        return "medium"
    return "watch"


def _signal(club, region, evidence_count) -> str:
    where = club or region or "un contesto locale"
    if evidence_count >= 4:
        return f"Segnalato più volte nel tempo attorno a {where}, con conferma da una fonte indipendente"
    return f"Emerso di recente attorno a {where}, confermato da una seconda fonte indipendente"


def _note(stats_json) -> str:
    if not stats_json:
        return ""
    try:
        stats = json.loads(stats_json)
    except (TypeError, ValueError):
        return ""
    if not any(stats.values()):
        return "Statistiche di rendimento non ancora disponibili pubblicamente"
    return ""


def build_findings(db_path: Path, limit: int) -> list:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT p.canonical_name, p.age, p.position, p.club, p.region,
               p.stats_json, p.evidence_count,
               MAX(e.observed_at) AS last_evidence
        FROM players p
        JOIN evidences e ON e.player_id = p.id
        WHERE p.publishable = 1
        GROUP BY p.id
        ORDER BY last_evidence DESC, p.evidence_count DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    findings = []
    for r in rows:
        findings.append({
            "name": r["canonical_name"],
            "role_age": _role_age(r["position"], r["age"]),
            "signal": _signal(r["club"], r["region"], r["evidence_count"] or 0),
            "confidence_tier": _confidence_tier(r["evidence_count"] or 0),
            "note": _note(r["stats_json"]),
        })
    return findings


def _freshness(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    last = conn.execute("SELECT MAX(observed_at) FROM evidences").fetchone()[0]
    conn.close()
    if not last:
        return "Ultimo ciclo di raccolta: non disponibile"
    date_part = last.split("T")[0]
    return f"Ultimo ciclo di raccolta: {date_part}"


def export(db_path: Path, out_path: Path, limit: int) -> dict:
    doc = {
        "source": "ob1_global",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_scope": (
            "Rilevamento continuo di giovani talenti del calcio da fonti "
            "pubbliche curate, con pubblicazione solo dopo doppia conferma "
            "indipendente e identità completa."
        ),
        "freshness": _freshness(db_path),
        "findings": build_findings(db_path, limit),
    }
    for f in doc["findings"]:
        if not f["note"]:
            del f["note"]
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "ob1_v2.db"))
    ap.add_argument("--out", default=str(ROOT / "ob1_global.json"))
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()
    doc = export(Path(args.db), Path(args.out), args.limit)
    print(f"Snapshot: {len(doc['findings'])} findings -> {args.out}")


if __name__ == "__main__":
    main()
