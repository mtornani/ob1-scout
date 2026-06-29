#!/usr/bin/env python3
"""
OB1 Global Scout - Sanity Check
Verifica tre condizioni minime di sanità output post-pipeline.
Exit 0 se tutto OK, exit 1 se una o più condizioni falliscono.
"""

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.notifier import admin_alert

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-5s [sanity] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
JSON_OUTPUT = ROOT / "docs" / "data" / "anomalies.json"
DB_PATH = ROOT / "data" / "ob1_global.db"
STATE_PATH = Path(__file__).parent / ".sanity_state.json"


def check_json_output() -> tuple:
    """Condizione 1: file esiste, JSON valido, non vuoto."""
    if not JSON_OUTPUT.exists():
        return False, f"file not found: {JSON_OUTPUT}"
    try:
        data = json.loads(JSON_OUTPUT.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        return False, f"invalid JSON: {e}"
    if not data:
        return False, "JSON array is empty"
    size_kb = JSON_OUTPUT.stat().st_size / 1024
    return True, f"{len(data)} entries, {size_kb:.1f} KB"


def check_output_freshness() -> tuple:
    """Condizione 2: mtime del file output < SANITY_MAX_AGE_HOURS."""
    try:
        max_age_hours = int(os.getenv('SANITY_MAX_AGE_HOURS', '6'))
    except (ValueError, TypeError):
        logger.warning("SANITY_MAX_AGE_HOURS non parsabile, uso default 6h")
        max_age_hours = 6
    logger.info(f"freshness threshold: {max_age_hours}h (SANITY_MAX_AGE_HOURS)")

    if not JSON_OUTPUT.exists():
        return False, "file not found (impossibile verificare freshness)"
    age_hours = (time.time() - JSON_OUTPUT.stat().st_mtime) / 3600
    if age_hours > max_age_hours:
        return False, f"file vecchio: {age_hours:.1f}h fa (max {max_age_hours}h)"
    return True, f"modificato {age_hours:.1f}h fa (max {max_age_hours}h)"


def check_db_count() -> tuple:
    """Condizione 3: DB count > 0 e drop < 50% rispetto alla run precedente."""
    if not DB_PATH.exists():
        return False, f"DB non trovato: {DB_PATH}"
    try:
        conn = sqlite3.connect(str(DB_PATH))
        current_count = conn.execute("SELECT COUNT(*) FROM anomalies").fetchone()[0]
        conn.close()
    except Exception as e:
        return False, f"errore lettura DB: {e}"

    if current_count == 0:
        return False, "DB vuoto: 0 righe nella tabella anomalies"

    # Leggi conteggio run precedente
    prev_count = None
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
            prev_count = state.get('db_count')
        except Exception:
            pass

    # Scrivi stato attuale per la prossima run
    try:
        STATE_PATH.write_text(json.dumps({'db_count': current_count}), encoding='utf-8')
    except Exception as e:
        logger.warning(f"impossibile scrivere stato in {STATE_PATH}: {e}")

    if prev_count is not None and prev_count > 0:
        drop_pct = (prev_count - current_count) / prev_count * 100
        if drop_pct > 50:
            return False, f"drop drastico: {current_count} righe (prev: {prev_count}, -{drop_pct:.0f}%)"
        return True, f"{current_count} righe (prev: {prev_count})"

    return True, f"{current_count} righe (nessuno stato precedente)"


def check_db_activity() -> tuple:
    """Condizione 4: l'ultima attività sul DB (last_seen) è recente.

    Cattura il punto cieco in cui la pipeline GIRA ma Gemini non produce nulla
    (es. 429/quota esaurita): il JSON viene comunque riscritto e i check 1-3
    passano, ma nessuna riga viene aggiunta/aggiornata e MAX(last_seen) resta
    indietro. Soglia volutamente generosa (default 18h = 3 cicli da 6h) per non
    allarmare su un singolo run quieto, ma beccare un'outage prolungata.
    """
    try:
        max_age_hours = int(os.getenv('SANITY_MAX_ACTIVITY_HOURS', '18'))
    except (ValueError, TypeError):
        logger.warning("SANITY_MAX_ACTIVITY_HOURS non parsabile, uso default 18h")
        max_age_hours = 18

    if not DB_PATH.exists():
        return False, f"DB non trovato: {DB_PATH}"
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute("SELECT MAX(last_seen) FROM anomalies").fetchone()
        conn.close()
    except Exception as e:
        return False, f"errore lettura DB: {e}"

    last_seen = row[0] if row else None
    if not last_seen:
        # Nessun timestamp (DB pre-migrazione mai aggiornato): non blocco, segnalo
        return True, "last_seen non disponibile — check saltato"
    try:
        age_hours = (datetime.now() - datetime.fromisoformat(last_seen)).total_seconds() / 3600
    except (ValueError, TypeError):
        return True, f"last_seen non parsabile ({last_seen!r}) — check saltato"

    if age_hours > max_age_hours:
        return False, (f"nessuna attività DB da {age_hours:.1f}h (max {max_age_hours}h) — "
                       f"la pipeline gira ma non produce rilevamenti (possibile quota/errore Gemini)")
    return True, f"ultima attività DB {age_hours:.1f}h fa (max {max_age_hours}h)"


def run_checks() -> bool:
    checks = [
        ("check_json",      check_json_output),
        ("check_freshness", check_output_freshness),
        ("check_db",        check_db_count),
        ("check_activity",  check_db_activity),
    ]

    failures = []
    for name, fn in checks:
        ok, detail = fn()
        if ok:
            logger.info(f"{name}: OK — {detail}")
        else:
            logger.error(f"{name}: FAIL — {detail}")
            failures.append(f"{name}: FAIL — {detail}")

    if not failures:
        logger.info("ALL CHECKS PASSED")
        return True

    summary = " | ".join(failures)
    logger.error(f"{len(failures)}/{len(checks)} checks failed — admin_alert inviato")
    admin_alert("ERROR", "sanity_check", summary)
    return False


if __name__ == "__main__":
    ok = run_checks()
    sys.exit(0 if ok else 1)
