#!/usr/bin/env python3
"""
OB1 Scout - Player Lookup (ad-hoc research tool)

Standalone, on-demand search for a single player by name.
NOT part of the automated pipeline — does not run on a schedule,
does not write to the anomalies database, does not affect scoring.

Use case: a partner asks "what do you have on player X?" — run this,
get a markdown dump of search results + full-text reads from
Transfermarkt/Soccerway/BeSoccer/news, then write up a dossier from it
(see reports/dossier_callegari_edoardo_20260615.html for the format).

Usage:
    python scripts/player_lookup.py "Edoardo Callegari" "Cremonese"
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scraper_global import AsyncGlobalScraper

JINA_BASE = "https://r.jina.ai/"
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "lookups"
PRIORITY_DOMAINS = ["transfermarkt", "soccerway", "besoccer", "sofascore", "fbref"]


async def deep_read(session: aiohttp.ClientSession, url: str, max_chars: int = 4000) -> str | None:
    """Full-text read of a single URL via Jina Reader."""
    headers = {'Accept': 'text/plain', 'User-Agent': 'OB1-Scout/2.0'}
    try:
        async with session.get(f"{JINA_BASE}{url}", headers=headers,
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                text = await resp.text()
                if text.strip():
                    return text[:max_chars]
    except Exception:
        pass
    return None


async def lookup_player(name: str, club: str = "") -> str:
    scraper = AsyncGlobalScraper()
    club_part = f" {club}" if club else ""

    queries = [
        f'"{name}"{club_part} transfermarkt',
        f'"{name}"{club_part} soccerway statistics',
        f'"{name}"{club_part} news',
    ]

    results = await scraper.run_batch(queries)

    seen = set()
    urls = []
    for r in results:
        url = r.get('url', '')
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)

    # Prioritize known stat/profile sites for deep-read
    urls.sort(key=lambda u: any(d in u for d in PRIORITY_DOMAINS), reverse=True)
    top_urls = urls[:8]

    async with aiohttp.ClientSession() as session:
        deep_texts = await asyncio.gather(*[deep_read(session, u) for u in top_urls])

    out = [f"# Ricerca: {name}{club_part}", f"Generato: {datetime.now().isoformat()}"]

    out.append("\n## Risultati di ricerca\n")
    for r in results:
        out.append(f"- [{r.get('title', '')}]({r.get('url', '')})\n  {r.get('content', '')[:200]}")

    out.append("\n## Lettura integrale (Jina Reader)\n")
    for url, text in zip(top_urls, deep_texts):
        if text:
            out.append(f"\n### {url}\n```\n{text}\n```")
        else:
            out.append(f"\n### {url}\n_(non leggibile)_")

    return "\n".join(out)


async def main():
    if len(sys.argv) < 2:
        print('Uso: python scripts/player_lookup.py "Nome Cognome" ["Club"]')
        sys.exit(1)

    name = sys.argv[1]
    club = sys.argv[2] if len(sys.argv) > 2 else ""

    output = await lookup_player(name, club)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = name.lower().replace(" ", "_")
    out_path = OUTPUT_DIR / f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(output, encoding="utf-8")

    print(f"Salvato: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
