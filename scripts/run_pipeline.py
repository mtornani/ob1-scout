#!/usr/bin/env python3
"""
OB1 Global Scout - Master Pipeline
Orchestrates the entire global radar workflow.

Flow:
  1. Scrape (snippets from Serper/Tavily/SearXNG)
  2. Deep-Read (full article text via Tavily extract)
  3. Gemini RAG analysis (on enriched data)
  4. Ghost check (Transfermarkt via Serper site-search)
  5. Stats search (FBref/Sofascore for high-purity players)
  6. DB store + Telegram + Dashboard export
"""

import asyncio
import logging
import os
import sys
import requests
from pathlib import Path
from datetime import datetime

# Ensure project root is on sys.path (needed when running from scripts/)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper_global import AsyncGlobalScraper
from src.database import OB1Database
from src.intelligence import OB1Intelligence
from src.enricher import OB1Enricher
from config.ob1_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Setup logging
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
    """Send a notification to the Telegram channel."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram config missing. Skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Telegram notification failed: {e}")
        return False

async def main_pipeline():
    logger.info("OB1 GLOBAL RADAR - STARTING RUN")

    # 1. Initialization
    db = OB1Database()
    scraper = AsyncGlobalScraper()
    intelligence = OB1Intelligence()
    enricher = OB1Enricher()

    # 2. Define Queries (multilingual global coverage)
    queries = [
        # English
        "U17 breakout performance youth tournament 2026",
        "Africa wonderkid talent scout report",
        "rising star football news global underground",
        # Portuguese (Brazil/Portugal)
        "jovem promessa futebol sub-20 destaque gols 2026",
        "revelação brasileirão série B scout report",
        # Spanish (LatAm/Spain)
        "joven promesa fútbol sudamericano debut goles",
        "canterano revelación liga argentina 2026",
        # French (Francophone Africa/Europe)
        "jeune talent football africain repéré scout",
        # Arabic (MENA region)
        "موهبة كرة قدم شابة اكتشاف 2026"
    ]

    # 3. Asynchronous Scraping (snippets)
    raw_results = await scraper.run_batch(queries)
    if not raw_results:
        logger.warning("No results found in scraping. Run aborted.")
        return

    # 4. Deep-Read: extract full article text for top URLs
    urls_to_read = [r.get('url') for r in raw_results[:20] if r.get('url')]
    deep_texts = await scraper.deep_read_urls(urls_to_read, max_urls=10)

    # Enrich raw_results with full text where available
    enriched_results = []
    for r in raw_results[:20]:
        item = dict(r)
        url = item.get('url', '')
        if url in deep_texts:
            # Replace snippet with full article text
            item['content'] = deep_texts[url]
            item['deep_read'] = True
        enriched_results.append(item)

    deep_count = sum(1 for r in enriched_results if r.get('deep_read'))
    logger.info(f"Data enrichment: {deep_count}/{len(enriched_results)} articles deep-read, rest are snippets.")

    # 5. Intelligence Analysis (Gemini RAG on enriched data)
    logger.info("Running Gemini Intelligence Engine...")
    anomalies = intelligence.analyze_scraped_data(enriched_results)

    if not anomalies:
        logger.warning("No anomalies identified by Gemini.")
        return

    # 6. Enrichment: Ghost check + Stats search + DB Storage
    logger.info(f"Found {len(anomalies)} potential anomalies. Enriching...")
    new_detections = []

    for anomaly in anomalies:
        player = anomaly.get('player_name')
        base_score = anomaly.get('score', 0)

        # Ghost Protocol: check Transfermarkt
        is_ghost = not await enricher.check_transfermarkt(player)
        final_score = enricher.calculate_asymmetry_score(base_score, is_ghost)

        # HIGH-PURITY FILTER: only progress if score >= 70
        if final_score < 70:
            logger.info(f"Skipping {player} (Score: {final_score:.1f}) - below purity threshold.")
            continue

        # Stats search: FBref/Sofascore/TM for high-purity players only
        stats_text = await enricher.search_player_stats(player)

        reason = anomaly.get('reason', 'N/A')
        sources = anomaly.get('sources', [])

        # Find matching source URL
        matching_url = "N/A"
        for r in raw_results:
            if player.lower() in (r.get('title', '') + r.get('content', '')[:200]).lower():
                matching_url = r.get('url', 'N/A')
                break
        if matching_url == "N/A" and sources:
            matching_url = sources[0]

        # Build enriched reason with stats if available
        enriched_reason = reason
        if stats_text:
            enriched_reason += f"\n\nStats: {stats_text[:500]}"

        success = db.add_anomaly(
            player_name=player,
            source_url=matching_url,
            score=final_score,
            raw_content=enriched_reason,
            region=anomaly.get('region', 'Global')
        )

        if success:
            level_emoji = "CRITICAL" if final_score >= 90 else "HIGH"
            ghost_tag = " [GHOST]" if is_ghost else ""
            stats_line = f"\nStats: _{stats_text[:200]}_" if stats_text else ""

            detection_str = f"*{level_emoji}*\n"
            detection_str += f"*{player}* ({final_score:.1f}/100){ghost_tag}\n"
            detection_str += f"_{reason}_"
            detection_str += stats_line
            new_detections.append(detection_str)

    # 7. Telegram Notifications
    if new_detections:
        msg = "*OB1 GLOBAL RADAR - High Purity Feed*\n\n"
        msg += "\n\n---\n\n".join(new_detections)
        msg += "\n\n[Dashboard](https://mtornani.github.io/ob1-scout/)"
        send_telegram_notification(msg)
        logger.info(f"Run complete. Notifications sent for {len(new_detections)} players.")

    # 8. Export Dashboard JSON
    from scripts.generate_dashboard_data import generate_json
    logger.info("Exporting dashboard data...")
    generate_json()

    logger.info("Pipeline run complete.")

if __name__ == "__main__":
    asyncio.run(main_pipeline())
