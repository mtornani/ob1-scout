#!/usr/bin/env python3
"""
OB1 v2 — Ingest source-first (Fase B2)

Il ciclo completo della v2:
  registro fonti → articoli NUOVI (delta) → lettura integrale (Jina) →
  estrazione tipizzata (LLM) → risoluzione entità + gate + scoring (codice).

A differenza della v1 NON cerca giocatori a caso: parte dalle fonti curate.
Richiede rete + GEMINI_API_KEY (gira in produzione / Actions).

Ordine del budget LLM (free tier):
  1) corroborazione prioritaria (identity_complete a 1 fonte → gate)
  2) discovery/estrazione nuove fonti col resto
Così un giorno con quota stretta converte profili quasi-pronti invece di
bruciare token su articoli nuovi che restano monofonte.

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
from src.database_v2 import OB1DatabaseV2, region_from_nationality
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
    # Riserva ~1/3 del budget alla corroborazione (eseguita PRIMA della discovery).
    corr_budget = max(1, llm_budget // 3)

    if not extractor.available():
        print("Nessun LLM configurato (chiave free o GEMINI_API_KEY). Stop.")
        return

    chain = [p["label"] for p in extractor.free_providers]
    print(f"LLM mode: {extractor.mode} · catena gratuita: {chain or 'nessuna'} · "
          f"Gemini: {'sì' if extractor.client else 'no'}")

    healed = db.heal_scores()
    if healed:
        print(f"heal_scores: ricalcolati {healed} profili con score NULL")

    sources = load_registry()
    if limit_sources:
        sources = sources[:limit_sources]

    stamp = datetime.now().isoformat()
    stats = Counter()
    calls_used = 0
    # Ritmo tra chiamate LLM: il free tier Groq ha un tetto di token/minuto.
    # 12s era calibrato su llama-3.3-70b-versatile (~12k TPM, deprecato il 17
    # giugno 2026 — vedi src/llm_free_chain.py). Il rimpiazzo openai/gpt-oss-120b
    # ha un tetto free molto più stretto (8k TPM, console.groq.com/docs/
    # rate-limits, verificato 19 ago 2026): misurato nel run di produzione
    # dopo il cambio modello, la prima chiamata (~2.5-3k token con FREE_MAX_CHARS
    # a 2800 char) faceva scattare il rate limit sulla finestra scorrevole,
    # ed escludeva Groq dal resto del run (budget 15, usate 1). A 8k TPM
    # servono ~22-23s tra chiamate da ~3k token per restare sotto soglia con
    # margine; 25s. Una pausa tiene le chiamate sotto il limite invece di
    # prendere 429. Configurabile; 0 per disattivare.
    try:
        call_delay = max(0.0, float(os.getenv("INGEST_CALL_DELAY", "25")))
    except (ValueError, TypeError):
        call_delay = 25.0

    # Evita di ritentare gli stessi pid se _corroborate gira 2× (pre + post discovery).
    attempted_pids: set[int] = set()

    # Tetto sui TENTATIVI di ricerca (non sulle chiamate LLM riuscite): trovato
    # il 19/20 ago 2026 su un run reale da 56 minuti con llm_calls: 0 — il
    # ciclo sotto scorre fino a 100 candidati (players_to_corroborate default),
    # e l'unico freno esistente era calls_used >= budget, che NON si
    # incrementa mai su una ricerca fallita (find_profile -> None). Se le
    # ricerche falliscono e basta (mercato poco coperto, o il motore di
    # ricerca ha un problema suo), il ciclo non aveva un limite reale: provava
    # tutti e 100, una ricerca di rete alla volta, senza mai arrivare alla
    # discovery. search_attempts conta OGNI tentativo (trovato o no),
    # condiviso tra le due chiamate a _corroborate() nello stesso run (pre e
    # post discovery) così il tetto vale sul totale, non per singola chiamata.
    # ~30s/tentativo osservato quella notte: 20 tentativi ≈ 10 minuti nel
    # caso peggiore, lascia comunque spazio alla discovery nel resto del run.
    try:
        max_search_attempts = max(1, int(os.getenv("CORR_MAX_SEARCH_ATTEMPTS", "20")))
    except (ValueError, TypeError):
        max_search_attempts = 20
    search_attempts = 0

    async def _pace():
        nonlocal calls_used
        if call_delay and calls_used < llm_budget and extractor.llm_usable():
            await asyncio.sleep(call_delay)

    async def _corroborate(call_cap: int):
        """Cerca seconda fonte per profili a 1 fonte. call_cap = max calls_used assoluto."""
        nonlocal calls_used, search_attempts
        from src.corroborate_v2 import find_profile, observation_fits_target
        for row in db.players_to_corroborate():
            pid, name = row["id"], row["name"]
            if pid in attempted_pids:
                continue
            if calls_used >= call_cap or calls_used >= llm_budget:
                stats["corr_skipped_budget"] += 1
                break
            if not extractor.llm_usable():
                stats["corr_skipped_exhausted"] += 1
                break
            if search_attempts >= max_search_attempts:
                stats["corr_skipped_search_cap"] += 1
                break
            attempted_pids.add(pid)
            age, club = row.get("age"), row.get("club")
            search_attempts += 1
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
            await _pace()
            if obs_list is None:
                stats["extract_failed"] += 1
                if not extractor.llm_usable():
                    stats["llm_exhausted_stop"] += 1
                    return
                continue
            # Solo osservazioni che reggono nome+età(+club): no omonimi pro
            matched = False
            for o in obs_list:
                if observation_fits_target(
                        o, name, age=age, club=club,
                        names_match_fn=db._names_match):
                    o["observed_at"] = stamp
                    db.ingest_observation(o)
                    stats["corroborated"] += 1
                    matched = True
                    break
            if not matched:
                stats["corr_rejected_mismatch"] += 1

    # --- 1) Corroborazione prima: converte identity_complete → publishable ---
    await _corroborate(corr_budget)

    # --- 2) Discovery: estrazione nuove fonti col budget residuo ---
    for src in sources:
        if calls_used >= llm_budget or not extractor.llm_usable():
            stats["sources_skipped_budget"] += 1
            break
        new_urls = await monitor.new_items(src)
        new_urls = new_urls[:max_articles]
        if not new_urls:
            continue
        stats["sources_with_new"] += 1

        texts = await scraper.deep_read_urls(new_urls, max_urls=len(new_urls))
        processed = []
        for url, text in texts.items():
            if calls_used >= llm_budget or not extractor.llm_usable():
                break
            obs_list = extractor.extract_from_source(text, url)
            calls_used += 1
            await _pace()
            if obs_list is None:
                # estrazione fallita (quota/errore): NON marcare visto → si ritenta
                stats["extract_failed"] += 1
                if not extractor.llm_usable():
                    stats["llm_exhausted_stop"] += 1
                    break
                continue
            processed.append(url)
            for obs in obs_list:
                # Fonti mono-paese: src["region"] basta. Fonti multi-paese
                # (confederazioni CAF/AFC, algoritmo copertura bassa
                # 2026-08-19c) coprono decine di paesi — lì va preferita la
                # nationality estratta dal testo, tradotta in nome paese via
                # region_from_nationality(); se non riconosciuta, ripiega
                # comunque su src["region"] invece di lasciare vuoto.
                obs["region"] = (obs.get("region") or region_from_nationality(obs.get("nationality"))
                                 or src.get("region"))
                obs["observed_at"] = stamp
                _, status = db.ingest_observation(obs)
                stats[f"obs_{status}"] += 1
                stats["observations"] += 1
        # marca visti SOLO gli articoli estratti con successo
        if processed:
            db.mark_seen(src["id"], processed, stamp)
            stats["articles"] += len(processed)

    # --- 3) Corroborazione extra con eventuale budget rimasto ---
    if calls_used < llm_budget and extractor.llm_usable():
        await _corroborate(llm_budget)

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
            if len(to_notify) > 8:
                lines.append(f"<i>…e altri {len(to_notify) - 8} profili in dashboard.</i>")
            lines.append("")
            lines.append('<a href="https://ob1global.matchanalysispro.online/">Dashboard</a>')
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
