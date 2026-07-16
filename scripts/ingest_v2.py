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
    # Riserva ~1/3 del budget alla corroborazione, così l'estrazione delle fonti
    # non lo esaurisce lasciando i giocatori a 1 fonte.
    extract_budget = max(1, llm_budget * 2 // 3)

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
        call_delay = max(0.0, float(os.getenv("INGEST_CALL_DELAY", "12")))
    except (ValueError, TypeError):
        call_delay = 12.0

    for src in sources:
        if calls_used >= extract_budget:
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
            if calls_used >= extract_budget:
                break  # tetto estrazione: il resto del budget va alla corroborazione
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

    # --- Corroborazione attiva: cerca i giocatori a 1 fonte sugli aggregatori ---
    from src.corroborate_v2 import find_profile
    for pid, name in db.players_to_corroborate():
        if calls_used >= llm_budget:
            stats["corr_skipped_budget"] += 1
            continue
        prof = await find_profile(scraper, name, exclude_domains=db.player_domains(pid))
        if not prof:
            stats["corr_not_found"] += 1
            continue
        texts = await scraper.deep_read_urls([prof], max_urls=1)
        text = texts.get(prof)
        if not text:
            continue
        obs_list = extractor.extract_from_source(text, prof)
        calls_used += 1
        if call_delay and calls_used < llm_budget:
            await asyncio.sleep(call_delay)
        if not obs_list:
            continue
        # ingerisci solo l'osservazione che corrisponde a questo giocatore
        for o in obs_list:
            if db._names_match(o.get("name", ""), name):
                o["observed_at"] = stamp
                db.ingest_observation(o)
                stats["corroborated"] += 1
                break

    # --- Notifica Telegram: nuovi giocatori PUBBLICABILI (una volta sola) ---
    to_notify = db.publishable_to_notify()
    if to_notify:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            import requests
            lines = [f"<b>OB1 SCOUT v2</b>  {datetime.now().strftime('%d/%m %H:%M')}",
                     f"✅ {len(to_notify)} nuovo/i profilo/i verificato/i (≥2 fonti):", ""]
            for p in to_notify[:8]:
                bits = [str(p.get("age") or "?") + "y", p.get("position") or "?",
                        p.get("club") or "?"]
                lines.append(f"• <b>{p['canonical_name']}</b> — {' · '.join(bits)} [{p['score']}]")
            lines.append('\n<a href="https://mtornani.github.io/ob1-scout/">Dashboard</a>')
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines),
                          "parse_mode": "HTML"}, timeout=10)
                if r.status_code == 200:
                    db.mark_notified([p["id"] for p in to_notify])
                    stats["notified"] = len(to_notify)
            except Exception as e:
                print(f"Notifica Telegram fallita (non bloccante): {e}")
        else:
            # niente config Telegram: non marcare, riprova al prossimo run
            stats["notify_skipped_no_config"] = len(to_notify)

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
