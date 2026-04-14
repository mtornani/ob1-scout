#!/usr/bin/env python3
"""
OB1 Scout Pipeline - Targeted recruitment scanner.
Processes club-specific scouting queries (CFG / Monza / Triestina).
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper_global import AsyncGlobalScraper
from src.scout_intelligence import ScoutIntelligence
from config.ob1_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

CONFIG_PATH = Path(__file__).parent.parent / "config" / "scout_cfg_monza.json"
OUTPUT_PATH = Path(__file__).parent.parent / "data" / "scout_results.json"
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "scout.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── Search query generation ────────────────────────────────────────────────────

def build_search_queries(q: dict) -> list:
    """Generate targeted web search strings from a scout query config."""
    filters = q.get('filters', {})
    leagues = filters.get('leagues', [])
    positions = filters.get('position', [])
    age = filters.get('age_range', [18, 30])
    pos_str = "/".join(positions)

    queries = []
    for league in leagues[:4]:
        queries.append(
            f"{pos_str} {league} young player scouting contract 2026 "
            f"age {age[0]}-{age[1]}"
        )

    # Extra query using scoring_boost keywords
    boost_keys = list(q.get('scoring_boost', {}).get('patterns', {}).keys())[:3]
    if boost_keys:
        kw = " ".join(k.split('|')[0] for k in boost_keys)
        queries.append(f"{pos_str} football player {kw} scouting transfer 2026")

    # Extra query from strategic notes (first 8 words)
    note_words = q.get('notes', '').split()[:8]
    if note_words:
        queries.append(" ".join(note_words) + " football scouting")

    return queries[:6]

# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping notification.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10
        )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def format_query_block(q: dict, candidates: list) -> str:
    header = (
        f"<b>SCOUT: {q['query_id']}</b>\n"
        f"{q.get('target_club','?')} | {q.get('critical_role','?')}\n"
    )
    if not candidates:
        return header + "Nessun candidato identificato.\n"

    lines = [header]
    for i, p in enumerate(candidates[:5], 1):
        name = p.get('name', 'N/A')
        age = p.get('age', '?')
        club = p.get('current_club', '?')
        league = p.get('league', '?')
        expires = p.get('contract_expires', '?')
        score = p.get('scout_score', 0)
        summary = (p.get('fit_summary', '') or '')[:120]
        lines.append(f"{i}. <b>{name}</b> ({age}y) — {club} [{league}]")
        lines.append(f"   Contratto: {expires} | Score: {score}/10")
        if summary:
            lines.append(f"   <i>{summary}</i>")
    return "\n".join(lines)

# ── Main pipeline ──────────────────────────────────────────────────────────────

async def main():
    with open(CONFIG_PATH, encoding='utf-8') as f:
        config = json.load(f)

    scraper = AsyncGlobalScraper()
    intelligence = ScoutIntelligence()
    all_results = {}
    timestamp = datetime.utcnow().isoformat()

    for q in config['queries']:
        qid = q['query_id']
        logger.info(f"▶ Scout query: {qid}")

        search_queries = build_search_queries(q)
        raw = await scraper.run_batch(search_queries)
        logger.info(f"  {qid}: {len(raw)} raw results")

        candidates = intelligence.evaluate_candidates(q, raw)
        logger.info(f"  {qid}: {len(candidates)} candidates")

        all_results[qid] = {
            "query_id": qid,
            "target_club": q.get('target_club'),
            "critical_role": q.get('critical_role'),
            "timestamp": timestamp,
            "candidates": candidates
        }

    # Persist results
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    logger.info(f"Results saved → {OUTPUT_PATH}")

    # Telegram: summary header
    total = sum(len(v['candidates']) for v in all_results.values())
    header_msg = (
        f"<b>OB1 SCOUT RUN</b> — {datetime.utcnow().strftime('%d/%m %H:%M')}\n"
        f"{len(all_results)} query | {total} candidati totali\n"
        '<a href="https://mtornani.github.io/ob1-scout/">Dashboard</a>'
    )
    send_telegram(header_msg)

    # Telegram: one message per query
    for q in config['queries']:
        qid = q['query_id']
        result = all_results.get(qid, {})
        msg = format_query_block(q, result.get('candidates', []))
        send_telegram(msg)

    logger.info("Scout pipeline complete.")


if __name__ == "__main__":
    asyncio.run(main())
