#!/usr/bin/env python3
"""
OB1 Global Scout - Scraper
DuckDuckGo primary + Jina Reader deep reads. No paid API keys required.
SearXNG as fallback.
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
import aiohttp
import logging
from pathlib import Path
from datetime import datetime

from ddgs import DDGS
from config.ob1_config import SEARXNG_INSTANCES, TIMEOUT_SECONDS

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)

JINA_BASE = "https://r.jina.ai/"


class AsyncGlobalScraper:
    def __init__(self):
        self.searxng_instances = SEARXNG_INSTANCES
        self.timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        self._ddg_semaphore = None

    @property
    def ddg_semaphore(self) -> asyncio.Semaphore:
        if self._ddg_semaphore is None:
            self._ddg_semaphore = asyncio.Semaphore(1)
        return self._ddg_semaphore

    def _fetch_duckduckgo(self, query: str, max_results: int = 5) -> list:
        """DuckDuckGo search. Sync — called via asyncio.to_thread."""
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            logger.debug(f"[DDG] {len(results)} results for: {query}")
            return results
        except Exception as e:
            logger.debug(f"[DDG] Failed: {e}")
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
        """DuckDuckGo primary, SearXNG fallback."""
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

        seen_urls: set = set()
        results = []
        for r in raw:
            url = r.get('href') or r.get('url') or r.get('link', '')
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({
                'title': r.get('title', ''),
                'content': r.get('body') or r.get('content') or r.get('snippet', ''),
                'url': url,
                'timestamp': datetime.now().isoformat()
            })
        return results

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
