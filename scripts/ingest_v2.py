#!/usr/bin/env python3
"""
OB1 v2 — Ingest source-first (Fase B2)

Il ciclo completo della v2:
  registro fonti → articoli NUOVI (delta) → lettura integrale (Jina) →
  estrazione tipizzata (LLM) → risoluzione entità + gate + scoring (codice).

A differenza della v1 NON cerca giocatori a caso: parte dalle fonti curate.
Richiede rete + GEMINI_API_KEY (gira in produzione / Actions).

Uso:
    python scripts/ingest_v2.py [--limit-sources N] [--max-articles N]
"""

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.database_v2 import OB1DatabaseV2
from src.sources_v2 import load_registry, SourceMonitor
from src.extractor_v2 import OB1Extractor
from src.scraper_global import AsyncGlobalScraper


async def run(limit_sources=None, max_articles=6):
    db = OB1DatabaseV2()
    scraper = AsyncGlobalScraper()
    monitor = SourceMonitor(db, scraper)
    extractor = OB1Extractor(api_key=os.getenv("GEMINI_API_KEY", ""))

    sources = load_registry()
    if limit_sources:
        sources = sources[:limit_sources]

    stamp = datetime.now().isoformat()
    stats = Counter()

    for src in sources:
        new_urls = await monitor.new_items(src)
        new_urls = new_urls[:max_articles]
        if not new_urls:
            continue
        stats["sources_with_new"] += 1

        texts = await scraper.deep_read_urls(new_urls, max_urls=len(new_urls))
        for url, text in texts.items():
            obs_list = extractor.extract_from_source(text, url)
            for obs in obs_list:
                obs["region"] = obs.get("region") or src.get("region")
                obs["observed_at"] = stamp
                _, status = db.ingest_observation(obs)
                stats[f"obs_{status}"] += 1
                stats["observations"] += 1
        db.mark_seen(src["id"], new_urls, stamp)
        stats["articles"] += len(new_urls)

    print("=== INGEST v2 ===")
    for k, v in stats.most_common():
        print(f"  {k}: {v}")
    print(f"DB: {db.db_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-sources", type=int, default=None)
    ap.add_argument("--max-articles", type=int, default=6)
    args = ap.parse_args()
    asyncio.run(run(args.limit_sources, args.max_articles))


if __name__ == "__main__":
    main()
