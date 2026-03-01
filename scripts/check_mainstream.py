#!/usr/bin/env python3
"""
OB1 Global Scout - Mainstream Detection Check
Searches tracked players on Google News via Serper to calculate real Lead Time.
Runs as a separate cron (weekly) or at the end of each pipeline run.
"""

import asyncio
import aiohttp
import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.database import OB1Database

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Mainstream sources — if a player appears here, the "secret is out"
MAINSTREAM_DOMAINS = [
    'skysports.com', 'bbc.com', 'espn.com', 'goal.com', 'marca.com',
    'lequipe.fr', 'gazzetta.it', 'transfermarkt.com/news',
    'theguardian.com', 'telegraph.co.uk', 'atleticodemadrid.com',
    'fcbarcelona.com', 'realmadrid.com', 'chelseafc.com',
    'manutd.com', 'liverpoolfc.com', 'mancity.com',
]


async def check_player_mainstream(session, serper_key: str, player_name: str) -> dict | None:
    """
    Search Google News for a player name on mainstream sources.
    Returns {'url': ..., 'title': ...} if found, None otherwise.
    """
    # Build OR query for mainstream domains
    sites = " OR ".join(f"site:{d}" for d in MAINSTREAM_DOMAINS[:8])
    query = f'"{player_name}" ({sites})'

    payload = json.dumps({"q": query, "num": 3, "type": "news"})
    headers = {'X-API-KEY': serper_key, 'Content-Type': 'application/json'}

    try:
        async with session.post("https://google.serper.dev/news",
                                data=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                articles = data.get('news', [])
                name_lower = player_name.lower()
                name_parts = set(p for p in name_lower.split() if len(p) > 2)

                for article in articles:
                    title = article.get('title', '').lower()
                    # Verify the article actually mentions the player
                    if any(part in title for part in name_parts):
                        return {
                            'url': article.get('link', ''),
                            'title': article.get('title', ''),
                            'date': article.get('date', ''),
                            'source': article.get('source', ''),
                        }
            return None
    except Exception as e:
        logger.debug(f"Mainstream check failed for {player_name}: {e}")
        return None


async def run_mainstream_check():
    """Check all tracked players against mainstream media."""
    serper_key = os.getenv('SERPER_API_KEY', '')
    if not serper_key or len(serper_key) < 5:
        logger.warning("No SERPER_API_KEY — cannot check mainstream.")
        return

    db = OB1Database()
    tracked = db.get_tracked_players()

    if not tracked:
        logger.info("No players to check for mainstream detection.")
        return

    logger.info(f"Checking {len(tracked)} players against mainstream media...")
    found_count = 0

    async with aiohttp.ClientSession() as session:
        for row in tracked:
            player_name = row[0]
            result = await check_player_mainstream(session, serper_key, player_name)

            if result:
                lead_days = db.record_mainstream_hype(
                    player_name,
                    source_url=result['url']
                )
                if lead_days is not None:
                    found_count += 1
                    logger.info(
                        f"MAINSTREAM HIT: {player_name} — {result['source']} "
                        f"(Lead Time: {lead_days}d)"
                    )

    logger.info(f"Mainstream check complete: {found_count}/{len(tracked)} players detected.")


if __name__ == "__main__":
    asyncio.run(run_mainstream_check())
