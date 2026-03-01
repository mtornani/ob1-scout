#!/usr/bin/env python3
"""
OB1 Global Scout - Master Pipeline
Orchestrates the entire global radar workflow.
"""

import asyncio
import logging
import os
import re
import sys
import requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper_global import AsyncGlobalScraper
from src.database import OB1Database
from src.intelligence import OB1Intelligence
from src.enricher import OB1Enricher
from config.ob1_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def send_telegram_notification(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram config missing. Skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Telegram error: {response.text}")
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram notification failed: {e}")
        return False


def _extract_clean_stats(stats_text):
    """Extract key numbers from raw stats text into a compact line."""
    if not stats_text:
        return None
    stats = {}
    # Try to find appearances/matches played
    mp = re.search(r'(?:MP|Appearances)[.\s:]*(\d+)', stats_text, re.IGNORECASE)
    if mp:
        stats['MP'] = mp.group(1)
    # Goals
    gls = re.search(r'(?:Gls|Goals)[.\s:]*(\d+)', stats_text, re.IGNORECASE)
    if gls:
        stats['G'] = gls.group(1)
    # Assists
    ast = re.search(r'(?:Ast|Assists)[.\s:]*(\d+)', stats_text, re.IGNORECASE)
    if ast:
        stats['A'] = ast.group(1)
    # Minutes
    mins = re.search(r"(?:Min|Minutes)[.\s:]*(\d[\d,']*)", stats_text, re.IGNORECASE)
    if mins:
        stats["'"] = mins.group(1)
    if not stats:
        return None
    return " | ".join(f"{k}: {v}" for k, v in stats.items())


def format_telegram_player(player, score, is_ghost, anomaly, stats_text):
    """Format a single player detection for Telegram (HTML mode)."""
    age = anomaly.get('age')
    pos = anomaly.get('position')
    club = anomaly.get('club')
    league = anomaly.get('league')
    reason = anomaly.get('reason', '')

    score_bar = ">" * (score // 10) + "-" * (10 - score // 10)

    # Header: player name + score
    lines = [f"<b>{player}</b>{' [GHOST]' if is_ghost else ''}"]

    # Profile line: age · position · club
    profile = []
    if age:
        profile.append(f"{age}y")
    if pos:
        profile.append(pos)
    if club:
        profile.append(club)
    if profile:
        lines.append(" · ".join(profile))

    if league:
        lines.append(league)

    # Score bar
    lines.append(f"[{score_bar}] {score:.0f}/100")

    # Compact stats (extracted numbers only)
    clean_stats = _extract_clean_stats(stats_text)
    if clean_stats:
        lines.append(clean_stats)

    # Short reason (max 150 chars, no raw stat dumps)
    if reason:
        # Strip any stats appended to reason
        stats_idx = reason.find('\n\nStats:')
        if stats_idx > 0:
            reason = reason[:stats_idx]
        short = reason[:150].strip()
        if len(reason) > 150:
            short = short.rsplit(' ', 1)[0] + "..."
        lines.append(f"<i>{short}</i>")

    return "\n".join(lines)


def filter_noise(results: list) -> list:
    """Pre-Gemini noise filter: remove junk before wasting tokens."""
    filtered = []
    for r in results:
        title = r.get('title', '')
        content = r.get('content', '')
        text = f"{title} {content}"

        # Skip very short snippets (no real info)
        if len(text.strip()) < 60:
            continue
        # Skip multi-player entries (commas in title = list, not a profile)
        if title.count(',') >= 2:
            continue
        # Skip generic "Unknown" entries
        if 'unknown' in title.lower() and len(title) < 40:
            continue
        # Skip pure video/gallery links with no text
        if len(content.strip()) < 30:
            continue

        filtered.append(r)

    removed = len(results) - len(filtered)
    if removed > 0:
        logger.info(f"[Noise] Filtered {removed}/{len(results)} low-quality items.")
    return filtered


async def main_pipeline():
    logger.info("OB1 GLOBAL RADAR - STARTING RUN")

    db = OB1Database()
    scraper = AsyncGlobalScraper()
    intelligence = OB1Intelligence()
    enricher = OB1Enricher()

    queries = [
        "U17 breakout performance youth tournament 2026",
        "Africa wonderkid talent scout report",
        "rising star football news global underground",
        "jovem promessa futebol sub-20 destaque gols 2026",
        "revelação brasileirão série B scout report",
        "joven promesa fútbol sudamericano debut goles",
        "canterano revelación liga argentina 2026",
        "jeune talent football africain repéré scout",
        "موهبة كرة قدم شابة اكتشاف 2026"
    ]

    # 1. Scrape
    raw_results = await scraper.run_batch(queries)
    if not raw_results:
        logger.warning("No results found in scraping. Run aborted.")
        return

    # 1b. Noise filter
    raw_results = filter_noise(raw_results)

    # 2. Deep-Read
    urls_to_read = [r.get('url') for r in raw_results[:20] if r.get('url')]
    deep_texts = await scraper.deep_read_urls(urls_to_read, max_urls=10)

    enriched_results = []
    for r in raw_results[:20]:
        item = dict(r)
        url = item.get('url', '')
        if url in deep_texts:
            item['content'] = deep_texts[url]
            item['deep_read'] = True
        enriched_results.append(item)

    deep_count = sum(1 for r in enriched_results if r.get('deep_read'))
    logger.info(f"Data enrichment: {deep_count}/{len(enriched_results)} articles deep-read.")

    # 3. Gemini RAG analysis
    logger.info("Running Gemini Intelligence Engine...")
    anomalies = intelligence.analyze_scraped_data(enriched_results)
    if not anomalies:
        logger.warning("No anomalies identified by Gemini.")
        return

    # 4. Enrichment + Storage
    logger.info(f"Found {len(anomalies)} potential anomalies. Enriching...")
    new_detections = []
    updated_count = 0

    for anomaly in anomalies:
        player = anomaly.get('player_name')
        base_score = anomaly.get('score', 0)

        is_ghost = not await enricher.check_transfermarkt(player)
        final_score = enricher.calculate_asymmetry_score(base_score, is_ghost)

        if final_score < 70:
            logger.info(f"Skipping {player} (Score: {final_score:.1f})")
            continue

        stats_text = await enricher.search_player_stats(player)

        reason = anomaly.get('reason', 'N/A')
        sources = anomaly.get('sources', [])

        matching_url = "N/A"
        for r in raw_results:
            if player.lower() in (r.get('title', '') + r.get('content', '')[:200]).lower():
                matching_url = r.get('url', 'N/A')
                break
        if matching_url == "N/A" and sources:
            matching_url = sources[0]

        enriched_reason = reason
        if stats_text:
            enriched_reason += f"\n\nStats: {stats_text[:500]}"

        result = db.add_anomaly(
            player_name=player,
            source_url=matching_url,
            score=final_score,
            raw_content=enriched_reason,
            region=anomaly.get('region', 'Global'),
            age=anomaly.get('age'),
            position=anomaly.get('position'),
            club=anomaly.get('club'),
            league=anomaly.get('league'),
            stats_summary=stats_text[:500] if stats_text else None,
            is_ghost=is_ghost
        )

        if result == 'new':
            tg_block = format_telegram_player(player, final_score, is_ghost, anomaly, stats_text)
            new_detections.append(tg_block)
        elif result == 'updated':
            updated_count += 1

    # 5. Telegram (only for NEW detections, not updates)
    if new_detections:
        timestamp = datetime.now().strftime("%d/%m %H:%M")
        msg = f"<b>OB1 GLOBAL RADAR</b>  {timestamp}\n"
        msg += f"{len(new_detections)} new detection{'s' if len(new_detections) > 1 else ''}"
        if updated_count > 0:
            msg += f" + {updated_count} tracked"
        msg += "\n\n"
        msg += "\n\n----------\n\n".join(new_detections)
        msg += '\n\n<a href="https://mtornani.github.io/ob1-scout/">Open Dashboard</a>'
        send_telegram_notification(msg)
        logger.info(f"Run complete. {len(new_detections)} new, {updated_count} updated.")
    elif updated_count > 0:
        logger.info(f"Run complete. No new players, {updated_count} existing updated.")

    # 6. Mainstream Lead Time check
    from scripts.check_mainstream import run_mainstream_check
    logger.info("Running mainstream detection check...")
    await run_mainstream_check()

    # 7. Export dashboard
    from scripts.generate_dashboard_data import generate_json
    logger.info("Exporting dashboard data...")
    generate_json()

    logger.info("Pipeline run complete.")


if __name__ == "__main__":
    asyncio.run(main_pipeline())
