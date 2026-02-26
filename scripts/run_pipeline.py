#!/usr/bin/env python3
"""
OB1 Global Scout - Master Pipeline
Orchestrates the entire global radar workflow.
"""

import asyncio
import logging
import os
import requests
from pathlib import Path
from datetime import datetime

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
    logger.info("🎯 OB1 GLOBAL RADAR - STARTING RUN")
    
    # 1. Initialization
    db = OB1Database()
    scraper = AsyncGlobalScraper()
    intelligence = OB1Intelligence()
    enricher = OB1Enricher()
    
    # 2. Define Queries (Dynamic/Global) - BUG 6: Multi-language coverage
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
    
    # 3. Asynchronous Scraping
    raw_results = await scraper.run_batch(queries)
    if not raw_results:
        logger.warning("No results found in scraping. Run aborted.")
        return

    # 4. Intelligence Analysis (Gemini RAG)
    logger.info("🧠 Running Gemini Intelligence Engine...")
    anomalies = intelligence.analyze_scraped_data(raw_results[:20]) # Top 20 for analysis
    
    if not anomalies:
        logger.warning("No anomalies identified by Gemini.")
        return

    # 5. Enrichment & Database Storage
    logger.info(f"✨ Found {len(anomalies)} potential anomalies. Enriching and storing...")
    new_detections = []
    
    for anomaly in anomalies:
        player = anomaly.get('player_name')
        base_score = anomaly.get('score', 0)
        
        # Check Ghost Protocol
        is_ghost = not await enricher.check_transfermarkt(player)
        final_score = enricher.calculate_asymmetry_score(base_score, is_ghost)
        
        # HIGH-PURITY FILTER: Only progress if score > 70
        if final_score < 70:
            logger.info(f"⏭️ Skipping {player} (Score: {final_score:.1f}) - below purity threshold.")
            continue
            
        # 5. Enrichment & Database Storage
        # BUG 4: Specific source_url mapping
        matching_url = "N/A"
        for r in raw_results:
            if player.lower() in (r.get('title', '') + r.get('content', '')).lower():
                matching_url = r.get('url', 'N/A')
                break
        
        # If no direct match, use the first result as fallback (better than nothing, but we tried)
        if matching_url == "N/A" and raw_results:
            matching_url = raw_results[0].get('url', 'N/A')

        success = db.add_anomaly(
            player_name=player,
            source_url=matching_url,
            score=final_score,
            raw_content=reason,
            region=anomaly.get('region', 'Global')
        )
        
        if success:
            level_emoji = "🔴 CRITICAL" if final_score >= 90 else "🟡 HIGH"
            detection_str = f"{level_emoji}\n"
            detection_str += f"📍 *{player}* ({final_score:.1f}/100) {'[GHOST]' if is_ghost else ''}\n"
            detection_str += f"_{reason}_"
            new_detections.append(detection_str)

    # 6. Notifications
    if new_detections:
        msg = "📡 *OB1 GLOBAL RADAR - High Purity Feed*\n\n"
        msg += "\n\n---\n\n".join(new_detections)
        msg += "\n\n🔗 [Tactical HUD Dashboard](https://mtornani.github.io/ob1-scout/)"
        send_telegram_notification(msg)
        logger.info(f"✅ Run complete. Notifications sent for {len(new_detections)} players.")
    # 7. Export Dashboard JSON - BUG 3
    from scripts.generate_dashboard_data import generate_json
    logger.info("📊 Exporting dashboard data...")
    generate_json()
    
    logger.info("🏁 Pipeline run complete.")

if __name__ == "__main__":
    asyncio.run(main_pipeline())
