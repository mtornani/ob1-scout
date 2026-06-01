#!/usr/bin/env python3
"""
OB1 Global Scout - Master Pipeline
Orchestrates the entire global radar workflow.
"""

# ============================================================
# FREEZE PILOTA K-SPORT
# Dal 27 maggio 2026 fino a fine pilota (~settembre 2026):
# - NON modificare pesi
# - NON modificare soglie HOT/WARM/COLD
# - NON modificare formule di scoring
# - NON cambiare backend LLM
# Solo monitoring, alerting, sanity checks, presentazione UX.
# Vincolo Karpathy attivo.
# ============================================================

import asyncio
import html
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
from src.notifier import admin_alert
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


_REGION_MAP = {
    # Brazil variants
    "brasile": "Brazil", "brasil": "Brazil", "brazil": "Brazil",
    "south america (brazil)": "Brazil", "sud america (brasile)": "Brazil",
    "america del sud (brasile)": "Brazil",
    # South America
    "sud america": "South America", "south america": "South America",
    "sudamerica": "South America", "america del sud": "South America",
    "sudamérica": "South America",
    # Argentina
    "argentina": "Argentina",
    # Colombia
    "colombia": "Colombia",
    # Mexico
    "messico": "Mexico", "mexico": "Mexico", "méxico": "Mexico",
    "mexico/brazil": "Latin America",
    # Africa
    "africa": "Africa", "africa subsahariana": "Africa",
    "west africa": "West Africa", "africa occidentale": "West Africa",
    "east africa": "East Africa", "africa orientale": "East Africa",
    "north africa": "North Africa", "africa del nord": "North Africa",
    # Specific African countries
    "nigeria": "Nigeria", "ghana": "Ghana", "senegal": "Senegal",
    "côte d'ivoire": "Ivory Coast", "ivory coast": "Ivory Coast",
    "morocco": "Morocco", "marocco": "Morocco", "egypt": "Egypt", "egitto": "Egypt",
    # Asia
    "asia": "Asia", "southeast asia": "Southeast Asia",
    "japan": "Japan", "giappone": "Japan", "japon": "Japan",
    "south korea": "South Korea", "corea del sud": "South Korea", "korea": "South Korea",
    "china": "China", "cina": "China",
    "thailand": "Thailand", "tailandia": "Thailand",
    "vietnam": "Vietnam", "indonesia": "Indonesia",
    # Europe
    "europa": "Europe", "europe": "Europe",
    "france": "France", "francia": "France",
    "germany": "Germany", "germania": "Germany",
    "england": "England", "inghilterra": "England",
    "spain": "Spain", "spagna": "Spain",
    "portugal": "Portugal", "portogallo": "Portugal",
    "serbia": "Serbia", "croatia": "Croatia", "croazia": "Croatia",
    "netherlands": "Netherlands", "paesi bassi": "Netherlands",
    "scandinavia": "Scandinavia",
}

def normalize_region(raw: str) -> str:
    if not raw:
        return "Global"
    key = raw.strip().lower()
    # Direct match
    if key in _REGION_MAP:
        return _REGION_MAP[key]
    # Substring match (longest key wins)
    matches = [(k, v) for k, v in _REGION_MAP.items() if k in key]
    if matches:
        return max(matches, key=lambda x: len(x[0]))[1]
    # Capitalize as-is if nothing matches
    return raw.strip().title()


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

    def escape(value):
        return html.escape(str(value))

    reason = str(reason) if reason else ""
    score_bar = ">" * int(score // 10) + "-" * (10 - int(score // 10))

    # Header: player name + score
    lines = [f"<b>{escape(player)}</b>{' [GHOST]' if is_ghost else ''}"]

    # Profile line: age · position · club
    profile = []
    if age:
        profile.append(f"{age}y")
    if pos:
        profile.append(escape(pos))
    if club:
        profile.append(escape(club))
    if profile:
        lines.append(" · ".join(profile))

    if league:
        lines.append(escape(league))

    # Score bar
    lines.append(f"[{score_bar}] {score:.0f}/100")

    # Compact stats (extracted numbers only)
    clean_stats = _extract_clean_stats(stats_text)
    if clean_stats:
        lines.append(clean_stats)

    # Full reason — strip raw stat dumps only, escape HTML for Telegram parse_mode=HTML
    if reason:
        stats_idx = reason.find('\n\nStats:')
        if stats_idx > 0:
            reason = reason[:stats_idx]
        lines.append(f"<i>{escape(reason.strip())}</i>")

    return "\n".join(lines)


def filter_noise(results: list) -> list:
    """Pre-Gemini noise filter: remove junk before wasting tokens."""
    filtered = []
    for r in results:
        title = r.get('title') or ''
        content = r.get('content') or ''
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
        # South America — Brazil
        "jovem promessa futebol sub-20 destaque gols 2026",
        "revelação brasileirão série B menor clube 2026 scout",
        "talento copinha sub-17 gols desconhecido 2026",
        # South America — Spanish
        "joven promesa fútbol sudamericano debut goles 2026",
        "canterano revelación liga argentina colombia chile 2026",
        "jugador sub-20 colombia ecuador peru goles scout report",
        # Africa
        "Africa U17 U20 wonderkid talent scout 2026",
        "Nigeria Ghana Senegal young football talent breakthrough 2026",
        "jeune talent football africain AFCON CAF U20 2026",
        "Morocco Egypt North Africa young football prospect scout",
        # Asia
        "Japan J-League U23 young talent breakthrough 2026",
        "South Korea K-League young player debut scout report 2026",
        "Southeast Asia football talent Thailand Vietnam Indonesia 2026",
        "AFC U20 Asian Cup young prospect underrated 2026",
        # Europe — hidden gems
        "Eastern Europe Serbia Croatia Poland young talent scout 2026",
        "Ligue 2 lower league France young prospect debut 2026",
        "Portugal Primeira Liga B youth academy talent 2026",
        "Scandinavia young football talent Norway Denmark Sweden 2026",
        # Global / generic
        "U17 U19 breakout performance lesser-known club 2026",
        "football wonderkid transfer target undervalued scout 2026",
    ]

    # 1. Scrape
    raw_results = await scraper.run_batch(queries)
    if not raw_results:
        logger.warning("No results found in scraping. Run aborted.")
        admin_alert("CRITICAL", "scraper", "Scraping returned 0 results — pipeline aborted. Check API keys and search engine availability.")
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
        admin_alert("CRITICAL", "intelligence", "Gemini analysis returned 0 anomalies — pipeline aborted. Check GEMINI_API_KEY quota and content quality from scraper.")
        return

    # 4. Enrichment + Storage
    logger.info(f"Found {len(anomalies)} potential anomalies. Enriching...")
    new_detections = []
    updated_count = 0

    for anomaly in anomalies:
        player = anomaly.get('player_name')
        if not player:
            logger.debug("Skipping anomaly with missing player_name")
            continue
        # Reject placeholder names from Gemini (no real identification)
        if any(kw in player.lower() for kw in ('non identificato', 'unknown', 'giocatore', 'jugador', 'player unknown', 'unnamed')):
            logger.info(f"Skipping unidentified player: {player!r}")
            continue
        # Reject players under 15 or in U12/U13 leagues (not scouting targets)
        age = anomaly.get('age')
        league = (anomaly.get('league') or '').lower()
        if (age and age < 15) or any(u in league for u in ('u12', 'u13', 'u-12', 'u-13')):
            logger.info(f"Skipping too-young player: {player}, age={age}, league={anomaly.get('league')}")
            continue
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
            region=normalize_region(anomaly.get('region', '')),
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

    # 5. Telegram alerts
    timestamp = datetime.now().strftime("%d/%m %H:%M")

    if new_detections:
        # Immediate alert for new players
        msg = f"<b>OB1 GLOBAL RADAR</b>  {timestamp}\n"
        msg += f"🆕 {len(new_detections)} new signal{'s' if len(new_detections) > 1 else ''}"
        if updated_count > 0:
            msg += f" + {updated_count} tracked"
        msg += "\n\n"
        msg += "\n\n----------\n\n".join(new_detections)
        msg += '\n\n<a href="https://mtornani.github.io/ob1-scout/">Open Dashboard</a>'
        ok = send_telegram_notification(msg)
        if not ok:
            admin_alert("ERROR", "telegram", f"User-facing alert failed — {len(new_detections)} detection(s) not delivered to teams.")
        logger.info(f"Run complete. {len(new_detections)} new, {updated_count} updated.")
    else:
        # Daily digest at 06:xx UTC — send top signals even with no new detections
        current_hour = datetime.utcnow().hour
        if current_hour == 6:
            top_players = db.get_top_anomalies(limit=5)
            if top_players:
                msg = f"<b>OB1 RADAR — Daily Digest</b>  {timestamp}\n"
                msg += f"No new signals. {updated_count} tracked. Top active:\n\n"
                for p in top_players:
                    name = p.get('player_name', '?')
                    score = round(p.get('score', 0))
                    region = p.get('region', '?')
                    count = p.get('detection_count', 1)
                    msg += f"• <b>{name}</b> [{region}] — {score}/100 ({count}x)\n"
                msg += f'\n<a href="https://mtornani.github.io/ob1-scout/">Dashboard</a>'
                ok = send_telegram_notification(msg)
                if not ok:
                    admin_alert("ERROR", "telegram", "Daily digest Telegram send failed.")
                logger.info(f"Daily digest sent. {updated_count} updated.")
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
