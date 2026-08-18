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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.scoring_v2 import score_player

ROOT = Path(__file__).parent.parent


def _version_and_build() -> tuple:
    """
    (version, build) per il footer "è aggiornato al deploy giusto?" — stesso
    scopo del footer Sentinel (VERSION + K_REVISION), ma questo prodotto è
    statico su GitHub Pages: non esiste una revision iniettata dalla
    piattaforma, quindi build = short SHA del commit che ha generato
    l'export. Mai deve poter rompere l'export: qualunque errore (repo non
    git, comando assente) ripiega su "dev" invece di sollevare.
    version viene da VERSION in root, bumpato a mano — non ad ogni commit,
    è per un umano che guarda il footer, non un hash.
    """
    version = "0.0.0"
    try:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip() or version
    except OSError:
        pass
    build = "dev"
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=5,
        )
        if sha.returncode == 0 and sha.stdout.strip():
            build = sha.stdout.strip()
    except Exception:
        pass
    return version, build


def assess_player(p: dict, evidence_count: int = 1) -> dict:
    """
    "Perché sì / cautele / prossimi passi" per la scheda giocatore.
    Derivato in CODICE dagli stessi segnali del punteggio (mai da un LLM):
    non può contraddire il numero, non costa nulla, ed è testabile.
    """
    pros, cautions, steps = [], [], []
    flags = {f for f in (p.get("review_flags") or "").split(",") if f}
    stats = p.get("stats") or {}
    has_stats = any(stats.values())
    bd = p.get("breakdown") or {}
    age = p.get("age")
    n_src = p.get("n_sources") or 0

    # --- Perché sì ---
    if age is not None and age <= 17:
        pros.append(f"Molto giovane ({age} anni): ampio margine di sviluppo")
    elif age is not None and age <= 19:
        pros.append(f"Giovane ({age} anni)")
    if stats.get("goals") or stats.get("assists"):
        det = " e ".join(x for x in [
            f"{stats['goals']} gol" if stats.get("goals") else "",
            f"{stats['assists']} assist" if stats.get("assists") else ""] if x)
        suffix = f" in {stats['apps']} presenze" if stats.get("apps") else ""
        pros.append(f"Produzione documentata: {det}{suffix}")
    elif stats.get("apps"):
        pros.append(f"Continuità documentata: {stats['apps']} presenze")
    asym = bd.get("asymmetry") or 0  # 'or' voluto: coerce anche un eventuale None
    if asym >= 12:
        pros.append("Fuori dai radar mainstream: alta asimmetria informativa")
    elif asym >= 6:
        pros.append("Contesto minore: valore potenzialmente sottoprezzato")
    if n_src >= 2:
        pros.append(f"Identità confermata da {n_src} fonti indipendenti")
    if evidence_count >= 5:
        pros.append(f"Segnale persistente: {evidence_count} rilevamenti")

    # --- Cautele ---
    if n_src < 2:
        cautions.append("Una sola fonte: non ancora corroborato")
    if not has_stats:
        cautions.append("Nessuna statistica di rendimento documentata")
    if "eta_mancante" in flags or age is None:
        cautions.append("Età non confermata")
    if "club_mancante" in flags:
        cautions.append("Club non identificato")
    if "nome_singolo" in flags or "handle_o_soprannome" in flags:
        cautions.append("Identità debole: nome incompleto o soprannome")
    if asym < 0:
        cautions.append("Club/lega ad alta visibilità: poca asimmetria, concorrenza probabile")

    # --- Prossimi passi (azioni da scout, in ordine di blocco) ---
    if "nome_singolo" in flags or "handle_o_soprannome" in flags:
        steps.append("Risolvere l'identità: serve un nome completo verificabile")
    if "club_mancante" in flags:
        steps.append("Verificare il club attuale")
    if "eta_mancante" in flags or age is None:
        steps.append("Confermare l'anno di nascita con la società")
    if n_src < 2:
        steps.append("Trovare una seconda fonte indipendente (aggregatori, stampa locale)")
    if not has_stats:
        steps.append("Recuperare statistiche di rendimento (referti gara)")
    if p.get("publishable"):
        steps.append("Richiedere video o programmare una visione diretta")
        steps.append("Telefonata al club: conferma anagrafica e status contrattuale")

    return {"pros": pros[:4], "cautions": cautions[:4], "next_steps": steps[:3]}


def export(db_path: Path, out_path: Path) -> dict:
    # Heal score NULL così priorità tracking e rank non restano a zero
    try:
        from src.database_v2 import OB1DatabaseV2
        OB1DatabaseV2(str(db_path)).heal_scores()
    except Exception as e:
        print(f"Warning: heal_scores failed: {e}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Pre-carica le fonti in una sola query (evita N+1 col crescere del DB)
    sources_by_player = {}
    for r in conn.execute(
            """SELECT player_id, source_domain, source_url,
                      MIN(observed_at) AS observed_at
               FROM evidences WHERE source_domain != ''
               GROUP BY player_id, source_domain ORDER BY observed_at"""):
        sources_by_player.setdefault(r["player_id"], []).append(
            {"domain": r["source_domain"], "url": r["source_url"],
             "seen": r["observed_at"]})

    def _clean_sources(raw):
        """Domain unici; URL http se c'è, altrimenti domain-only (chip senza link)."""
        out, seen = [], set()
        for s in raw or []:
            url = (s.get("url") or "").strip()
            dom = (s.get("domain") or "").strip()
            if not dom:
                continue
            key = dom.lower()
            if key in seen:
                continue
            seen.add(key)
            if not url.startswith(("http://", "https://")):
                url = ""
            out.append({"domain": dom, "url": url, "seen": s.get("seen")})
            if len(out) >= 4:
                break
        return out

    def _tracking_rank(p: dict) -> tuple:
        """Prima identity quasi-gate, poi fonti, poi score. Rumore in coda."""
        flags = p.get("review_flags") or ""
        weak = ("nome_singolo" in flags) or ("handle_o_soprannome" in flags)
        return (
            0 if p.get("identity_complete") else 1,
            0 if (p.get("n_sources") or 0) >= 1 else 1,
            1 if weak else 0,
            -(p.get("score") or 0),
        )

    players = []
    for p in conn.execute("SELECT * FROM players ORDER BY score DESC"):
        pid = p["id"]
        sources = _clean_sources(sources_by_player.get(pid, []))
        n_sources = len(sources)
        stats = json.loads(p["stats_json"]) if p["stats_json"] else {}
        sc = score_player(
            age=p["age"], is_ghost=bool(p["is_ghost"]), club=p["club"],
            league=p["league"], stats=stats, n_sources=max(n_sources, 1),
            detection_count=p["evidence_count"] or 1,
        )
        # Nome senza contenuto utile → non in dashboard pubblica
        name = (p["canonical_name"] or "").strip()
        if len(name) < 3:
            continue
        entry = {
            "name": name,
            "age": p["age"], "position": p["position"], "club": p["club"],
            "league": p["league"], "region": p["region"],
            "gender": p["gender"],
            "score": sc["score"], "confidence": sc["confidence"],
            "breakdown": sc["breakdown"],
            "n_sources": n_sources, "sources": sources,
            "stats": stats,
            "publishable": bool(p["publishable"]),
            "identity_complete": bool(p["identity_complete"]),
            "review_flags": p["review_flags"] or "",
            "first_detected": p["first_detected"], "last_seen": p["last_seen"],
        }
        entry["assessment"] = assess_player(entry, p["evidence_count"] or 1)
        players.append(entry)
    conn.close()

    players.sort(key=lambda x: (not x["publishable"], -x["score"]))
    # Shortlist pubblica: tutti i verificati + top tracking (resto resta in DB)
    pub = [x for x in players if x["publishable"]]
    trk = sorted([x for x in players if not x["publishable"]], key=_tracking_rank)
    TRACK_CAP = 15
    shown = pub + trk[:TRACK_CAP]
    try:
        from src.database_v2 import OB1DatabaseV2
        outcomes = OB1DatabaseV2(str(db_path)).outcomes_summary()
    except Exception as e:
        print(f"Warning: outcomes_summary failed: {e}")
        outcomes = {"checked": 0, "avg_lead_time_days": None}

    version, build = _version_and_build()
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "build": build,
        "total": len(players),
        "publishable": len(pub),
        "tracking": len(trk),
        "shown": len(shown),
        "tracking_capped": max(0, len(trk) - TRACK_CAP),
        "outcomes": outcomes,
        "players": shown,
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
        version, build = _version_and_build()
        doc = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "version": version, "build": build,
               "total": 0, "publishable": 0, "tracking": 0, "players": []}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(doc), encoding="utf-8")
        return
    doc = export(Path(args.db), Path(args.out))
    print(f"Export: {doc['total']} giocatori ({doc['publishable']} pubblicabili) → {args.out}")


if __name__ == "__main__":
    main()
