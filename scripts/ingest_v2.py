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


def _health_check(stats, calls_used: int, llm_budget: int, scraper) -> list:
    """
    Segnali di run degradato — soglie dichiarate qui, non un giudizio a
    naso. Puro/testabile: legge solo stats+contatori già calcolati, non
    fa rete. Ritorna una lista di problemi (stringhe), vuota se il run
    sembra sano. Non blocca nulla — decide solo se mandare l'alert.
    """
    problems = []
    # Zero chiamate LLM riuscite con budget disponibile: il segnale più
    # affidabile che qualcosa è rotto, a prescindere da QUALE pezzo
    # (modello deprecato, rate limit, ricerca morta — tutti e tre successi
    # in sequenza la notte del 19/20 ago 2026, tutti con questa firma).
    if calls_used == 0 and llm_budget > 0:
        problems.append(f"0 chiamate LLM riuscite su budget {llm_budget}")

    # Ricerca degradata E nessuna estrazione: il sospetto è il livello di
    # ricerca (Jina/ddgs/SearXNG), non l'estrattore.
    search_touched = bool(scraper.jina_failures or scraper.ddg_failures
                          or scraper.searxng_failures)
    if search_touched and calls_used == 0:
        problems.append(
            f"ricerca web degradata (jina_failures={scraper.jina_failures}, "
            f"ddgs_failures={scraper.ddg_failures}, "
            f"searxng_failures={scraper.searxng_failures}) e nessuna "
            f"estrazione riuscita")

    # Estrazioni fallite in modo consistente senza un solo successo da
    # nessun provider: il sospetto è la catena LLM (modello deprecato,
    # come llama-3.3-70b-versatile il 17 giugno 2026), non la ricerca.
    had_provider_success = any(k.startswith("via_") and k != "via_failed" for k in stats)
    if stats.get("extract_failed", 0) >= 3 and not had_provider_success:
        problems.append(
            f"{stats['extract_failed']} estrazioni fallite, nessun provider "
            f"LLM riuscito (catena gratuita probabilmente rotta)")

    # Catena esaurita a metà run con quasi nessuna chiamata passata: il caso
    # che i primi tre controlli non vedono, perché calls_used non è zero.
    # Trovato il 21 ago 2026 su 4 run consecutivi (07:24→02:09 UTC, 18h):
    # ogni run faceva 1 sola chiamata su budget 15, poi "quota/rate limit
    # esaurita → escluso dal run" per il resto del ciclo — 247/56 giocatori
    # invariati per tutta la notte, "success" a livello workflow. Soglia:
    # non "1 è sospetto in sé", ma "la catena si è dichiarata morta avendo
    # usato meno di 1/5 del budget disponibile".
    stall_threshold = max(2, llm_budget // 5)
    if stats.get("llm_exhausted_stop", 0) > 0 and 0 < calls_used <= stall_threshold:
        problems.append(
            f"catena LLM esaurita dopo sole {calls_used} chiamate su budget "
            f"{llm_budget} (soglia stallo: {stall_threshold}) — verificare "
            f"max_tokens/pacing vs il tier del provider gratuito attivo")

    # Jina Search morto al 100% nel run, a prescindere da calls_used: la
    # regola sopra (riga 56) tace ogni volta che il budget LLM viene comunque
    # speso — tramite corroborazione o un ddgs riuscito per caso — anche se
    # la ricerca primaria non ha funzionato UNA volta sola. Successo il 26
    # ago 2026: verificato sui log di produzione che il credito Jina è
    # esaurito (402 InsufficientBalanceError) su OGNI singola chiamata da
    # almeno il 24 agosto (tre giorni, ~15+ run da 6h), e la regola di sopra
    # non si era mai accesa perché calls_used non era mai stato zero. Un
    # canale di ricerca primario morto per giorni, silenzioso, è esattamente
    # il tipo di errore nascosto contro cui questo controllo esiste.
    if scraper.jina_attempts > 0 and scraper.jina_failures >= scraper.jina_attempts:
        dettaglio = f" — {scraper.last_jina_http_error}" if scraper.last_jina_http_error else ""
        problems.append(
            f"Jina Search: {scraper.jina_failures}/{scraper.jina_attempts} "
            f"richieste fallite in questo run (100%){dettaglio}")

    return problems


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

    # Il cancello si ripassa su TUTTI, a ogni run, non solo su chi riceve una
    # fonte nuova. Aggiunto il 26 ago 2026 con l'avvocato del diavolo
    # (src/challenge_v2.py): una regola piu' severa che non tocca il passato
    # lascia online proprio i profili che ha appena dichiarato non
    # pubblicabili. Costa un ricalcolo in RAM su qualche centinaio di righe,
    # zero rete e zero LLM — il prezzo giusto per non dover ricordare a mano
    # di fare una migrazione ogni volta che una soglia cambia.
    gate = db.reapply_gate()
    if gate["ritirati"] or gate["ammessi"]:
        print(f"gate v{db.GATE_VERSION}: {gate['esaminati']} esaminati · "
              f"{gate['ritirati']} ritirati · {gate['ammessi']} ammessi")

    sources = load_registry()
    # Turno di scansione (27 ago 2026): prima le fonti mai scansionate, poi
    # quelle che aspettano da più tempo. Il ciclo di discovery qui sotto si
    # ferma sul budget LLM (`break`), e finché l'ordine era quello fisso del
    # registro si fermava SEMPRE nello stesso punto: 38 fonti primary su 67
    # non erano mai state scansionate nemmeno una volta (contate sulle
    # seen_items di produzione — tutta l'Asia, tutte e 4 le confederazioni,
    # tutte e 4 le accademie). Vedi OB1DatabaseV2.sources_in_scan_order.
    # Applicato PRIMA di limit_sources: con --limit-sources si vuole comunque
    # il turno corrente, non ogni volta la stessa testa del registro.
    sources = db.sources_in_scan_order(sources)
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
        """
        Pausa fra chiamate LLM, divisa per il numero di provider VIVI.

        Il ritardo esiste per una ragione sola: stare sotto il tetto di
        token-al-minuto di UN provider. Da quando le chiamate ruotano fra gli
        anelli (call_free_chain(start=...), 27 ago 2026), N provider vivi si
        dividono le chiamate: con pausa globale D, ciascuno viene toccato ogni
        D×N secondi. Dividere la pausa per N lascia quindi la spaziatura
        PER-PROVIDER esattamente dov'era — cioè la grandezza che i tetti
        misurano davvero — e restituisce il tempo di attesa che oggi non serve
        a nessuno (6 minuti degli 11 del run #186).

        Con un provider solo il conto dà D/1 = D: identico a prima. È la
        proprietà che rende il cambio sicuro sulla configurazione di oggi,
        dove tolto Cerebras potrebbe restare la sola Groq.

        Il floor a 5s è prudenza, non calcolo: protegge dai limiti per-minuto
        di RICHIESTE (Groq ne dichiara 30) che restano condivisi nel tempo
        anche quando i token no.
        """
        nonlocal calls_used
        if not (call_delay and calls_used < llm_budget and extractor.llm_usable()):
            return
        vivi = max(1, len([p for p in extractor.free_providers
                           if p["label"] not in extractor._dead_free]))
        await asyncio.sleep(max(5.0, call_delay / vivi) if vivi > 1 else call_delay)

    async def _corroborate(call_cap: int):
        """Cerca seconda fonte per profili a 1 fonte. call_cap = max calls_used assoluto."""
        nonlocal calls_used, search_attempts
        from src.corroborate_v2 import find_profile, observation_fits_target
        from src.profilo_tm_v2 import leggi_profilo, e_scheda_tm
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
            # proven_domains, non player_domains: un aggregatore con
            # un'evidenza che non ha mai provato nulla (es. la scheda di un
            # pro adulto omonimo, scartata da claims_v2) va ricercato di
            # nuovo, non escluso per sempre. Vedi database_v2.proven_domains.
            prof = await find_profile(scraper, name, exclude_domains=db.proven_domains(pid))
            # Sempre, trovato o no: è la memoria che players_to_corroborate()
            # usa per il cooldown — un fallimento è informazione quanto un
            # successo (non ripescare subito lo stesso candidato appena
            # provato, vedi commento in database_v2.py).
            db.record_corr_attempt(pid)
            if not prof:
                stats["corr_not_found"] += 1
                continue
            # Una scheda TM va letta INTERA. deep_read_urls taglia a 1500
            # caratteri perché è il testo destinato al modello, ma su
            # Transfermarkt i primi 1500 sono tutti menu di navigazione: il
            # blocco dati ("Date of birth/Age:") comincia intorno al
            # carattere 4900. Misurato sul run #187, il primo dopo il merge:
            # corr_via_parser 0 su 5, con una scheda TM davvero corroborata
            # in quel run — il parser veniva chiamato e rispondeva None
            # perché non vedeva i dati, non perché non sapesse leggerli
            # (sulle stesse pagine INTERE aggancia 12 su 12). Il parser era
            # stato validato con read_raw e collegato a deep_read_urls:
            # testata la parte, non la giuntura.
            scheda_tm = e_scheda_tm(prof)
            if scheda_tm:
                text = await scraper.read_raw(prof)
            else:
                texts = await scraper.deep_read_urls([prof], max_urls=1)
                text = texts.get(prof)
            if not text:
                continue
            # Parser prima, modello dopo — stessa forma di
            # _da_indice_sito → _da_ricerca in sources_v2. Misurato il 27 ago
            # 2026 sull'archivio: 70 delle 301 chiamate LLM mai fatte (23%)
            # sono servite a leggere una scheda Transfermarkt, e producevano
            # citazioni-prova come "Mateus Romero - Player profile", cioè il
            # titolo della pagina ricopiato. La scheda è una tabella con
            # etichette fisse: si legge in codice.
            obs_list, via_parser = None, False
            if scheda_tm:
                letto = leggi_profilo(text, prof)
                if letto:
                    obs_list, via_parser = [letto], True
                    stats["corr_via_parser"] += 1
            if not via_parser:
                # leggi_profilo() ha detto "non lo so" (pagina d'errore,
                # layout cambiato, campi mancanti) oppure non era una scheda
                # TM: si paga il modello, esattamente come prima. Sul ripiego
                # di una scheda TM si taglia a 1500 come faceva
                # deep_read_urls, così il modello riceve esattamente quello
                # che riceveva prima di questo cambio.
                obs_list = extractor.extract_from_source(
                    text[:1500] if scheda_tm else text, prof)
                calls_used += 1
                stats["corr_via_llm"] += 1
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
        # Segnata SUBITO, prima di qualunque `continue`: una fonte guardata è
        # guardata anche se non aveva niente di nuovo, ed è proprio quel caso
        # che deve cedere il turno alle altre al prossimo run. Se si segnasse
        # solo quando produce, le fonti a vuoto resterebbero per sempre
        # "mai scansionate" e monopolizzerebbero la testa della coda.
        db.record_source_scan(src["id"])
        stats["sources_scanned"] += 1
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
    # Diagnostica ricerca (19/20 ago 2026): prima "nessun risultato" e
    # "DuckDuckGo/SearXNG hanno fallito la chiamata" finivano nella stessa
    # statistica corr_not_found, indistinguibili. Questi contatori vengono
    # da AsyncGlobalScraper (src/scraper_global.py) e dicono se il motore di
    # ricerca stesso ha un problema, invece di lasciarlo indovinare.
    if scraper.jina_failures:
        stats["search_jina_failures"] = scraper.jina_failures
    if scraper.jina_empty:
        stats["search_jina_empty"] = scraper.jina_empty
    if scraper.ddg_failures:
        stats["search_ddg_failures"] = scraper.ddg_failures
    if scraper.ddg_empty:
        stats["search_ddg_empty"] = scraper.ddg_empty
    if scraper.searxng_failures:
        stats["search_searxng_failures"] = scraper.searxng_failures
    # Quante fonti sono state lette dal proprio indice (niente motore di
    # ricerca in mezzo) e quante sono dovute passare dalla ricerca — la
    # prima strada regge anche quando la seconda è degradata o ferma.
    if monitor.via_indice:
        stats["sources_via_indice"] = monitor.via_indice
    if monitor.via_ricerca:
        stats["sources_via_ricerca"] = monitor.via_ricerca

    # --- Controllo di salute + alert Telegram (20 ago 2026) ---
    # Nato dalla notte del 19/20 ago: il modello Groq era morto da almeno
    # 4 ore prima che qualcuno lo scoprisse controllando i log a mano — il
    # workflow segnava "success" lo stesso, perché extract_from_source()
    # ritorna None sui fallimenti invece di sollevare (corretto: permette il
    # retry al giro dopo). Va bene per un run singolo degradato, male se
    # nessuno se ne accorge per giorni. Vive nella pipeline (gira ogni 6h da
    # solo), non in una sessione Claude che scade — un controllo legato a
    # questa sessione morirebbe con lei entro 7 giorni al massimo.
    problems = _health_check(stats, calls_used, llm_budget, scraper)
    if problems:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            import requests
            lines = [f"<b>⚠️ OB1 SCOUT v2 — run degradato</b>  {datetime.now().strftime('%d/%m %H:%M')}", ""]
            lines.extend(f"• {p}" for p in problems)
            lines.append("")
            lines.append(f"budget LLM: {llm_budget} · usate: {calls_used}")
            try:
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines),
                          "parse_mode": "HTML"}, timeout=10)
                stats["health_alert_sent"] = len(problems)
            except Exception as e:
                print(f"Alert di salute fallito (non bloccante): {e}")
        else:
            stats["health_alert_skipped_no_config"] = len(problems)

    print("=== INGEST v2 ===")
    print(f"budget LLM: {llm_budget} · usate: {calls_used}")
    print(f"ricerca: {'Jina Search (primaria)' if scraper.jina_api_key else 'ddgs (Jina non configurata)'}")
    # Quante corroborazioni sono state lette in codice invece che dal modello.
    # Misurato in archivio: 70 delle 301 chiamate LLM mai fatte erano schede
    # Transfermarkt. Se corr_via_parser resta 0 mentre corr_via_llm sale, il
    # parser non sta agganciando (redesign del sito?) e va guardato.
    if stats.get("corr_via_parser") or stats.get("corr_via_llm"):
        print(f"corroborazione: {stats.get('corr_via_parser', 0)} via parser "
              f"(zero token) · {stats.get('corr_via_llm', 0)} via LLM")
    # Copertura del registro: quante fonti scansionabili non sono ancora mai
    # state guardate. Al 27 ago 2026 erano 38 su 67 — invisibili, perché
    # nessuna statistica diceva "questa parte del registro non la tocchiamo
    # mai". Deve scendere verso 0 nei run successivi: se resta ferma, la
    # rotazione non sta girando e va guardata.
    _scan = {r[0] for r in db._conn().execute("SELECT source_id FROM source_scans")}
    _scansionabili = [s for s in load_registry() if s.get("tier") != "secondary"]
    _mai = [s for s in _scansionabili if s["id"] not in _scan]
    print(f"registro: {len(_scansionabili)} fonti scansionabili · "
          f"mai scansionate: {len(_mai)}")
    if _mai:
        print("  in attesa del primo turno: "
              + ", ".join(s["id"] for s in _mai[:12])
              + (f" … (+{len(_mai) - 12})" if len(_mai) > 12 else ""))
    for k, v in stats.most_common():
        print(f"  {k}: {v}")
    if scraper.jina_status_counts:
        # Distribuzione dei codici HTTP visti nel run, non solo l'ultimo —
        # un solo "ultimo errore" nasconde se il 422 è sistematico o raro
        # rispetto a un timeout capitato per caso a fine run.
        counts = ", ".join(f"{code}×{n}" for code, n in
                            scraper.jina_status_counts.most_common())
        print(f"  distribuzione errori HTTP Jina Search: {counts}")
    if scraper.last_jina_http_error:
        print(f"  ultimo errore HTTP Jina Search (con body): {scraper.last_jina_http_error}")
    if scraper.last_jina_error:
        print(f"  ultimo errore Jina Search (qualsiasi): {scraper.last_jina_error}")
    if scraper.last_ddg_error:
        print(f"  ultimo errore ricerca web (ddgs): {scraper.last_ddg_error}")
    print(f"DB: {db.db_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-sources", type=int, default=None)
    ap.add_argument("--max-articles", type=int, default=6)
    ap.add_argument("--llm-budget", type=int, default=None,
                    help="Max chiamate LLM per run (default env INGEST_LLM_BUDGET o 15)")
    args = ap.parse_args()
    asyncio.run(run(args.limit_sources, args.max_articles, args.llm_budget))


class _FakeScraper:
    """Stand-in minimo per testare _health_check() senza rete/DB."""
    def __init__(self, jina=0, ddg=0, searxng=0, jina_attempts=0,
                last_jina_http_error=None):
        self.jina_failures, self.ddg_failures, self.searxng_failures = jina, ddg, searxng
        self.jina_attempts = jina_attempts
        self.last_jina_http_error = last_jina_http_error


def _selftest_health_check():
    """
    Il controllo che ha lo scopo di svegliarci se il run degrada — verificato
    che DAVVERO si accenda sui tre casi visti la notte del 19/20 ago 2026
    (modello morto, ricerca morta, estrazioni fallite senza successi) e
    resti zitto su un run sano. Puro, nessuna rete/DB.
    """
    # Caso sano: chiamate riuscite, nessun problema.
    healthy = Counter({"via_groq": 1})
    assert _health_check(healthy, calls_used=1, llm_budget=15,
                         scraper=_FakeScraper()) == []

    # Caso 1: zero chiamate riuscite con budget disponibile (il sintomo
    # comune a tutti e tre gli incidenti di quella notte).
    problems = _health_check(Counter(), calls_used=0, llm_budget=15,
                             scraper=_FakeScraper())
    assert any("0 chiamate LLM riuscite" in p for p in problems), problems

    # Caso 2: ricerca degradata + zero chiamate -> il sospetto è la ricerca.
    problems = _health_check(Counter(), calls_used=0, llm_budget=15,
                             scraper=_FakeScraper(jina=7, ddg=1, searxng=1))
    assert any("ricerca web degradata" in p for p in problems), problems

    # Caso 3: estrazioni fallite senza un solo provider riuscito -> il
    # sospetto è la catena LLM, non la ricerca.
    problems = _health_check(Counter({"extract_failed": 5}), calls_used=0,
                             llm_budget=15, scraper=_FakeScraper())
    assert any("nessun provider" in p for p in problems), problems

    # Budget 0 (run disattivato apposta): 0 chiamate non è un problema.
    assert _health_check(Counter(), calls_used=0, llm_budget=0,
                         scraper=_FakeScraper()) == []

    # Caso 4 (21 ago 2026): catena esaurita dopo 1 sola chiamata su budget
    # 15 — il pattern reale visto per 4 run di fila, invisibile ai casi 1-3
    # perché calls_used non è zero.
    problems = _health_check(Counter({"via_groq": 1, "llm_exhausted_stop": 1}),
                             calls_used=1, llm_budget=15, scraper=_FakeScraper())
    assert any("catena LLM esaurita dopo" in p for p in problems), problems

    # Un run che usa gran parte del budget e POI esaurisce la catena non è
    # uno stallo: ha comunque prodotto lavoro reale.
    healthy_tail = Counter({"via_groq": 12, "llm_exhausted_stop": 1})
    assert _health_check(healthy_tail, calls_used=12, llm_budget=15,
                         scraper=_FakeScraper()) == []

    # Caso 5 (26 ago 2026): Jina Search morto al 100% con budget LLM
    # comunque speso — il caso REALE osservato in produzione su ~15 run
    # consecutivi (22-27 ago) per il credito esaurito, mai visto dai casi
    # 1-3 perché calls_used non era mai zero (corroborazione + ddgs
    # riuscito coprivano il buco).
    problems = _health_check(Counter({"via_groq": 15}), calls_used=15,
                             llm_budget=15,
                             scraper=_FakeScraper(
                                 jina=30, jina_attempts=30,
                                 last_jina_http_error="HTTP 402 · InsufficientBalanceError"))
    assert any("Jina Search" in p and "100%" in p for p in problems), problems

    # Un fallimento PARZIALE (alcune richieste passano) non è lo stesso
    # segnale: un run con 3 errori su 10 tentativi è normale rumore di rete,
    # non un credito esaurito.
    problems = _health_check(Counter({"via_groq": 15}), calls_used=15,
                             llm_budget=15,
                             scraper=_FakeScraper(jina=3, jina_attempts=10))
    assert not any("Jina Search" in p for p in problems), problems

    print("OK _health_check: si accende sui 5 incidenti reali, tace su un run sano")


if __name__ == "__main__":
    _selftest_health_check()
    main()
