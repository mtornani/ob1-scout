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


async def run(limit_sources=None, max_articles=6, llm_budget=None):
    db = OB1DatabaseV2()
    scraper = AsyncGlobalScraper()
    monitor = SourceMonitor(db, scraper)
    extractor = OB1Extractor(api_key=os.getenv("GEMINI_API_KEY", ""))

    if llm_budget is None:
        llm_budget = int(os.getenv("INGEST_LLM_BUDGET", "15"))

    if not extractor.available():
        print("Nessun LLM configurato (GEMINI_API_KEY o fallback). Stop.")
        return

    sources = load_registry()
    if limit_sources:
        sources = sources[:limit_sources]

    stamp = datetime.now().isoformat()
    stats = Counter()
    calls_used = 0
    # Ritmo tra chiamate LLM: il free tier Groq ha un tetto di token/minuto
    # (~12k TPM). Una pausa tiene le chiamate sotto il limite invece di prendere
    # 429. Configurabile; 0 per disattivare.
    try:
        call_delay = max(0.0, float(os.getenv("INGEST_CALL_DELAY", "7")))
    except (ValueError, TypeError):
        call_delay = 7.0

    for src in sources:
        if calls_used >= llm_budget:
            stats["sources_skipped_budget"] += 1
            continue
        new_urls = await monitor.new_items(src)
        new_urls = new_urls[:max_articles]
        if not new_urls:
            continue
        stats["sources_with_new"] += 1

        texts = await scraper.deep_read_urls(new_urls, max_urls=len(new_urls))
        processed = []
        for url, text in texts.items():
            if calls_used >= llm_budget:
                break  # budget finito: lascia il resto al prossimo run (delta)
            obs_list = extractor.extract_from_source(text, url)
            calls_used += 1
            if call_delay and calls_used < llm_budget:
                await asyncio.sleep(call_delay)
            if obs_list is None:
                # estrazione fallita (quota/errore): NON marcare visto → si ritenta
                stats["extract_failed"] += 1
                continue
            processed.append(url)
            for obs in obs_list:
                obs["region"] = obs.get("region") or src.get("region")
                obs["observed_at"] = stamp
                _, status = db.ingest_observation(obs)
                stats[f"obs_{status}"] += 1
                stats["observations"] += 1
        # marca visti SOLO gli articoli estratti con successo
        if processed:
            db.mark_seen(src["id"], processed, stamp)
            stats["articles"] += len(processed)

    stats["llm_calls"] = calls_used
    for provider, n in extractor.stats.items():
        stats[f"via_{provider}"] = n

    print("=== INGEST v2 ===")
    print(f"budget LLM: {llm_budget} · usate: {calls_used}")
    for k, v in stats.most_common():
        print(f"  {k}: {v}")
    print(f"DB: {db.db_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-sources", type=int, default=None)
    ap.add_argument("--max-articles", type=int, default=6)
    ap.add_argument("--llm-budget", type=int, default=None,
                    help="Max chiamate LLM per run (default env INGEST_LLM_BUDGET o 15)")
    args = ap.parse_args()
    asyncio.run(run(args.limit_sources, args.max_articles, args.llm_budget))


if __name__ == "__main__":
    main()
