#!/usr/bin/env python3
"""
OB1 Global Scout - Scraper
Ricerca: Jina Search (s.jina.ai) primaria se JINA_API_KEY è impostata, poi
libreria ddgs (multi-motore) come ripiego, poi SearXNG. Jina Reader
(r.jina.ai) per il testo integrale degli articoli — invariato, sempre
gratuito e senza chiave.

Storia del cambio ricerca (19-20 ago 2026): diagnosticato un run di
produzione con fallimento totale della ricerca via ddgs (5/5 chiamate,
"DDGSException: No results found"). La libreria ddgs, lasciata al default
backend="auto", prova 9 motori (incluse Wikipedia/Grokipedia — scelta
semanticamente sbagliata per query come 'site:dominio "giovanile" 2026',
SEMPRE messe in testa alla lista da ddgs in modalità "auto") e falliva o
andava in timeout su più motori dall'infrastruttura CI usata (runner
GitHub Actions). Aggiunto Jina Search come fonte primaria: stesso
fornitore già usato per il reader, verificato dal vivo (200 su entrambe le
query di test, contenuto pertinente e già estratto per intero — non solo
uno snippet). ddgs (WEB_SEARCH_BACKENDS, DuckDuckGo escluso) resta come
ripiego se Jina non ha una chiave configurata o fallisce.
"""

# ============================================================
# FREEZE PILOTA K-SPORT — NON PIÙ IN VIGORE (vedi CLAUDE.md §STATO,
# aggiornato 20 luglio 2026): il pilota non è mai partito formalmente,
# la disciplina si è chiusa con la Fase B. Lasciato qui solo come nota
# storica: se un domani leggi questo blocco come un vincolo attivo,
# CLAUDE.md è la fonte di verità, non questo commento.
# ============================================================

import asyncio
import aiohttp
import logging
import os
from collections import Counter
from pathlib import Path
from datetime import datetime

from ddgs import DDGS
from config.ob1_config import SEARXNG_INSTANCES, TIMEOUT_SECONDS

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)

JINA_BASE = "https://r.jina.ai/"
JINA_SEARCH_BASE = "https://s.jina.ai/"
# Tetto al testo per risultato: Jina Search restituisce il contenuto della
# pagina già estratto (non solo uno snippet come ddgs) — comodo, ma senza
# un tetto un singolo risultato può gonfiare parecchio la lista che poi va
# a valle. Stesso ordine di grandezza degli altri tetti testo del progetto
# (FREE_MAX_CHARS=2800, deep_read_urls=1500).
JINA_SEARCH_CONTENT_CHARS = 2000

# Motori espliciti per ddgs.text(), invece del default backend="auto".
# "auto" prova 9 motori per la categoria "text" e mette SEMPRE Wikipedia e
# Grokipedia in testa alla lista (codice ddgs: keys = ["wikipedia",
# "grokipedia"] + resto) — enciclopediche, non pensate per query tipo
# 'site:dominio "giovanile" 2026' o '"Nome Cognome" transfermarkt". Esclusi
# anche duckduckgo (diagnosticato rotto sui runner GitHub il 19/20 ago 2026:
# vedi commento del modulo) e yandex (timeout osservato in test). Brave per
# primo: unico motore che ha risposto in un test dal vivo quella notte.
# Non è garanzia che regga per sempre — se torna a fallire, i contatori
# ddg_failures/ddg_empty/last_ddg_error (sotto) lo dicono nei log del
# prossimo run, non serve indovinare di nuovo.
WEB_SEARCH_BACKENDS = "brave,google,mojeek,startpage,yahoo"


class AsyncGlobalScraper:
    def __init__(self):
        self.searxng_instances = SEARXNG_INSTANCES
        self.timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        self._ddg_semaphore = None
        # Diagnostica ricerca (19/20 ago 2026): un run con 20 tentativi di
        # corroborazione + discovery su 83 fonti, zero risultati ovunque —
        # ma _fetch_duckduckgo() inghiottiva l'eccezione in logger.debug(),
        # invisibile nei log CI (nessun basicConfig a livello DEBUG in
        # produzione). "nessun risultato genuino" e "DuckDuckGo blocca l'IP
        # del runner GitHub" finivano nella stessa statistica "not found" —
        # indistinguibili. Questi contatori li separano: si leggono da
        # ingest_v2.py a fine run e finiscono nelle stats stampate, stesso
        # posto dove si legge tutto il resto.
        self.ddg_failures = 0
        self.ddg_empty = 0
        self.last_ddg_error = None
        self.searxng_failures = 0
        # Jina Search (20 ago 2026): fonte di ricerca primaria se c'è una
        # chiave. Nessuna chiave = ripiega su ddgs senza errore — stesso
        # pattern "chiave assente non è un fallimento" degli altri provider
        # LLM del progetto (src/llm_free_chain.py).
        self.jina_api_key = os.getenv("JINA_API_KEY", "")
        self.jina_failures = 0
        self.jina_empty = 0
        self.last_jina_error = None
        # Trovato il 21 ago 2026: un run con più tipi di fallimento diversi
        # (422 http E timeout di rete) lascia in last_jina_error SOLO
        # l'ultimo evento del run — quello diagnosticamente prezioso (il
        # body di un 422) sparisce se dopo arriva anche un solo timeout.
        # jina_status_counts conta OGNI codice HTTP visto nel run (non solo
        # l'ultimo); last_jina_http_error tiene il body del PIÙ RECENTE
        # fallimento con status HTTP, mai sovrascritto da un'eccezione di
        # rete (le due categorie sono cause diverse, non vanno confuse).
        self.jina_status_counts = Counter()
        self.last_jina_http_error = None
        # Lettura diretta d'indice (26 ago 2026): quante volte read_raw ha
        # risposto con qualcosa, e quante no — serve a sources_v2.py per
        # sapere se il canale che NON passa da un motore di ricerca sta
        # rispondendo, indipendentemente da search_query().
        self.index_reads_ok = 0
        self.index_reads_empty = 0

    @property
    def ddg_semaphore(self) -> asyncio.Semaphore:
        if self._ddg_semaphore is None:
            self._ddg_semaphore = asyncio.Semaphore(1)
        return self._ddg_semaphore

    async def _fetch_jina_search(self, session: aiohttp.ClientSession, query: str,
                                 max_results: int = 5) -> list:
        """
        Jina Search (s.jina.ai): stesso fornitore del reader già in uso.
        Verificato dal vivo il 20 ago 2026 su due query reali di questa
        pipeline ('"Nome Cognome" transfermarkt' e 'site:dominio "giovanile"
        2026'): 200 su entrambe, risultati pertinenti, content già estratto
        per intero. Ritorna [] su qualunque problema (chiave assente,
        errore, timeout) — mai un'eccezione che risalga a search_query().
        """
        if not self.jina_api_key:
            return []
        headers = {"Authorization": f"Bearer {self.jina_api_key}",
                  "Accept": "application/json"}
        params = {"q": query, "num": max_results}
        try:
            async with session.get(JINA_SEARCH_BASE, headers=headers, params=params,
                                   timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status != 200:
                    self.jina_failures += 1
                    self.jina_status_counts[resp.status] += 1
                    # Solo il codice HTTP, senza query né corpo della risposta,
                    # non bastava a diagnosticare NULLA: run dopo run "HTTP
                    # 422" e basta, stesso bug già trovato in _fetch_duckduckgo
                    # (errore vero solo in logger.debug, invisibile in CI).
                    # Un 422 di solito porta il motivo nel body JSON — lo si
                    # legge qui, non lo si scarta.
                    try:
                        body = (await resp.text())[:200]
                    except Exception:
                        body = "(corpo non leggibile)"
                    msg = (f"HTTP {resp.status} · query: {query[:100]!r} · "
                           f"body: {body}")
                    self.last_jina_error = msg
                    self.last_jina_http_error = msg
                    logger.debug(f"[Jina Search] HTTP {resp.status} for: {query}")
                    return []
                payload = await resp.json(content_type=None)
        except Exception as e:
            self.jina_failures += 1
            # SOLO last_jina_error, non last_jina_http_error: un timeout di
            # rete non deve cancellare il body di un 422 visto prima nello
            # stesso run — sono due cause diverse (vedi commento in __init__).
            self.last_jina_error = f"{type(e).__name__}: {e} · query: {query[:100]!r}"
            logger.debug(f"[Jina Search] Failed: {e}")
            return []

        data = payload.get("data") or []
        if not data:
            self.jina_empty += 1
        logger.debug(f"[Jina Search] {len(data)} results for: {query}")
        return data

    def _fetch_duckduckgo(self, query: str, max_results: int = 5) -> list:
        """Ricerca web multi-motore via ddgs (WEB_SEARCH_BACKENDS, non
        DuckDuckGo — vedi commento del modulo). Nome del metodo invariato per
        non toccare la chiamata sotto; il motore usato non è più DDG.
        Sincrono — chiamato via asyncio.to_thread."""
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results,
                                          backend=WEB_SEARCH_BACKENDS))
            logger.debug(f"[web-search] {len(results)} results for: {query}")
            if not results:
                self.ddg_empty += 1
            return results
        except Exception as e:
            self.ddg_failures += 1
            self.last_ddg_error = f"{type(e).__name__}: {e}"
            logger.debug(f"[web-search] Failed: {e}")
            return []

    async def _fetch_searxng(self, session: aiohttp.ClientSession, query: str, instance: str) -> list:
        """SearXNG fallback."""
        try:
            params = {'q': query, 'format': 'json'}
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            async with session.get(f"{instance}/search", params=params,
                                   headers=headers, timeout=self.timeout) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    results = data.get('results', [])
                    logger.debug(f"[SearXNG] {len(results)} results for: {query}")
                    return results
                return []
        except Exception as e:
            logger.debug(f"[SearXNG:{instance}] Failed: {e}")
            return []

    async def search_query(self, query: str) -> list:
        """Jina Search primaria (se c'è chiave), poi ddgs multi-motore, poi SearXNG."""
        raw = []
        if self.jina_api_key:
            async with aiohttp.ClientSession() as session:
                raw = await self._fetch_jina_search(session, query)

        if not raw:
            async with self.ddg_semaphore:
                raw = await asyncio.to_thread(self._fetch_duckduckgo, query)
                await asyncio.sleep(1.0)

        if not raw:
            async with aiohttp.ClientSession() as session:
                tasks = [self._fetch_searxng(session, query, inst)
                         for inst in self.searxng_instances[:3]]
                res_lists = await asyncio.gather(*tasks, return_exceptions=True)
                for res_list in res_lists:
                    if isinstance(res_list, list) and res_list:
                        raw = res_list
                        break
                else:
                    # DDG vuoto E nessuna istanza SearXNG ha risposto con
                    # qualcosa: entrambi i canali di ricerca hanno fallito su
                    # questa query, non solo "nessun risultato pertinente".
                    self.searxng_failures += 1

        seen_urls: set = set()
        results = []
        for r in raw:
            url = r.get('href') or r.get('url') or r.get('link', '')
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            content = r.get('body') or r.get('content') or r.get('snippet', '')
            results.append({
                'title': r.get('title', ''),
                # Jina Search dà il testo pieno della pagina, non uno snippet
                # come ddgs/SearXNG — tetto a JINA_SEARCH_CONTENT_CHARS per
                # non gonfiare la lista senza motivo (i risultati ddgs sono
                # già corti di loro, il taglio qui non li tocca).
                'content': content[:JINA_SEARCH_CONTENT_CHARS] if content else content,
                'url': url,
                'timestamp': datetime.now().isoformat()
            })
        return results

    async def read_raw(self, url: str, max_chars: int = 40000) -> str:
        """
        Testo grezzo di una pagina via Jina Reader — STESSA strada di
        deep_read_urls (nessun motore di ricerca in mezzo, solo il fetch
        r.jina.ai di un URL), ma senza il taglio a 1500 caratteri pensato
        per il testo che va all'estrattore LLM.

        Perché serve (26 ago 2026): un indice di sito (WordPress REST,
        RSS/Atom, sitemap) elenca decine di articoli in una pagina sola —
        tagliato a 1500 caratteri se ne perderebbero quasi tutti. Misurato
        su fcf.com.co: la risposta di /wp-json/wp/v2/posts?per_page=20 pesa
        circa 40 KB per 20 post.

        Usato da SourceMonitor._da_indice_sito() in src/sources_v2.py, che
        è il motivo per cui questo vive qui e non lì: leggere una pagina
        resta compito dello scraper, sapere quali percorsi provare resta
        compito del registro fonti.
        """
        headers = {'Accept': 'text/plain', 'User-Agent': 'OB1-Scout/2.0'}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{JINA_BASE}{url}", headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        self.index_reads_empty += 1
                        return ""
                    text = await resp.text()
        except Exception:
            self.index_reads_empty += 1
            return ""
        if not text.strip():
            self.index_reads_empty += 1
            return ""
        self.index_reads_ok += 1
        return text[:max_chars]

    async def deep_read_urls(self, urls: list, max_urls: int = 10) -> dict:
        """
        Full article text via Jina Reader (r.jina.ai).
        Free, no API key, returns LLM-ready markdown.
        """
        if not urls:
            return {}

        unique_urls = list(dict.fromkeys(urls))[:max_urls]
        logger.info(f"Deep-reading {len(unique_urls)} articles via Jina Reader...")

        extracted = {}
        headers = {'Accept': 'text/plain', 'User-Agent': 'OB1-Scout/2.0'}

        async def fetch_url(session: aiohttp.ClientSession, url: str) -> tuple[str, str | None]:
            try:
                async with session.get(f"{JINA_BASE}{url}", headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        if text.strip():
                            logger.debug(f"[Jina] OK: {url}")
                            return url, text[:1500]
                    logger.debug(f"[Jina] HTTP {resp.status}: {url}")
            except Exception as e:
                logger.debug(f"[Jina] Failed {url}: {e}")
            return url, None

        async with aiohttp.ClientSession() as session:
            pairs = await asyncio.gather(*[fetch_url(session, u) for u in unique_urls])
            extracted = {url: text for url, text in pairs if text is not None}

        logger.info(f"Deep-read complete: {len(extracted)}/{len(unique_urls)} articles extracted.")
        return extracted

    async def run_batch(self, queries: list) -> list:
        """Run all queries concurrently."""
        logger.info(f"Starting batch scrape for {len(queries)} queries...")
        results_list = await asyncio.gather(*[self.search_query(q) for q in queries])
        flat = [item for sublist in results_list for item in sublist]
        logger.info(f"Batch complete: {len(flat)} raw items across {len(queries)} queries.")
        return flat


if __name__ == "__main__":
    import json
    scraper = AsyncGlobalScraper()
    test_queries = ["Nigeria Ghana Senegal young football talent 2026"]
    results = asyncio.run(scraper.run_batch(test_queries))
    print(json.dumps(results[:2], indent=2))
