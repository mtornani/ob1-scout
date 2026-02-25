#!/usr/bin/env python3
"""
OB1 Global Scout - High-Efficiency Asynchronous Scraper
Powered by aiohttp to concurrently scrape hundreds of global sources.
"""

import asyncio
import aiohttp
import json
import logging
from pathlib import Path
from datetime import datetime
import os

from config.ob1_config import get_search_config, SEARXNG_INSTANCES, TIMEOUT_SECONDS

# Setup logging
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "scraper_global.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class AsyncGlobalScraper:
    def __init__(self):
        self.config = get_search_config()
        self.searxng_instances = SEARXNG_INSTANCES
        self.timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        
    async def fetch_searxng(self, session: aiohttp.ClientSession, query: str, instance: str) -> list:
        """Fetch results from a single SearXNG instance asynchronously."""
        url = f"{instance}/search"
        params = {
            'q': query,
            'format': 'json'
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        try:
            async with session.get(url, params=params, headers=headers, timeout=self.timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get('results', [])
                    logger.debug(f"[{instance}] Got {len(results)} results for: {query}")
                    return results
                else:
                    logger.debug(f"[{instance}] HTTP {response.status} for: {query}")
                    return []
        except Exception as e:
            logger.debug(f"[{instance}] Failed: {str(e)}")
            return []

    async def fetch_serper(self, session: aiohttp.ClientSession, query: str) -> list:
        """Fetch results from Serper.dev asynchronously."""
        key = self.config.get('serper_key')
        if not key:
            return []
            
        url = "https://google.serper.dev/search"
        payload = json.dumps({"q": query, "num": 5, "tbs": "qdr:w"})
        headers = {
            'X-API-KEY': key,
            'Content-Type': 'application/json'
        }
        
        try:
            async with session.post(url, data=payload, headers=headers, timeout=self.timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    organic = data.get('organic', [])
                    results = []
                    for item in organic:
                        results.append({
                            'url': item.get('link'),
                            'title': item.get('title'),
                            'content': item.get('snippet')
                        })
                    logger.debug(f"[Serper] Got {len(results)} results for: {query}")
                    return results
                else:
                    logger.debug(f"[Serper] HTTP {response.status} for: {query}")
                    return []
        except Exception as e:
            logger.debug(f"[Serper] Failed: {str(e)}")
            return []

    async def fetch_tavily(self, session: aiohttp.ClientSession, query: str) -> list:
        """Fetch results from Tavily Search API asynchronously."""
        key = self.config.get('tavily_key')
        if not key:
            return []
            
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": key,
            "query": query,
            "search_depth": "balanced",
            "include_answer": False,
            "max_results": 5
        }
        
        try:
            async with session.post(url, json=payload, timeout=self.timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    results = []
                    for item in data.get('results', []):
                        results.append({
                            'url': item.get('url'),
                            'title': item.get('title'),
                            'content': item.get('content')
                        })
                    logger.debug(f"[Tavily] Got {len(results)} results for: {query}")
                    return results
                else:
                    logger.debug(f"[Tavily] HTTP {response.status} for: {query}")
                    return []
        except Exception as e:
            logger.debug(f"[Tavily] Failed: {str(e)}")
            return []

    async def search_query(self, query: str) -> list:
        """Search a single query across multiple engines with fallbacks."""
        async with aiohttp.ClientSession() as session:
            serper_key = self.config.get('serper_key')
            tavily_key = self.config.get('tavily_key')
            
            results_gather = []
            
            # 1. Try Serper (Fastest/Best)
            if serper_key and len(serper_key) > 5:
                res = await self.fetch_serper(session, query)
                if res:
                    results_gather.append(res)
            
            # 2. Try Tavily (Best for AI/Fallback)
            if not results_gather and tavily_key and len(tavily_key) > 5:
                res = await self.fetch_tavily(session, query)
                if res:
                    results_gather.append(res)
            
            # 3. Try SearXNG (Last resort / Free)
            if not results_gather:
                tasks = [self.fetch_searxng(session, query, inst) for inst in self.searxng_instances[:3]]
                res_lists = await asyncio.gather(*tasks, return_exceptions=True)
                for res_list in res_lists:
                    if isinstance(res_list, list) and res_list:
                        results_gather.append(res_list)
            
            all_results = []
            seen_urls = set()
            
            for res_list in results_gather:
                if isinstance(res_list, list):
                    for r in res_list:
                        url = r.get('url')
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_results.append({
                                'title': r.get('title', ''),
                                'content': r.get('content', ''),
                                'url': url,
                                'source': 'searxng',
                                'timestamp': datetime.now().isoformat()
                            })
            return all_results

    async def run_batch(self, queries: list) -> list:
        """Run multiple queries concurrently."""
        logger.info(f"🚀 Starting async batch scrape for {len(queries)} queries...")
        tasks = [self.search_query(q) for q in queries]
        results_list = await asyncio.gather(*tasks)
        
        flat_results = []
        for r in results_list:
            flat_results.extend(r)
            
        logger.info(f"✅ Batch complete. Found {len(flat_results)} raw items across {len(queries)} queries.")
        return flat_results

# Example Usage
if __name__ == "__main__":
    scraper = AsyncGlobalScraper()
    test_queries = [
        "south america U20 breakout performance 2026",
        "africa U17 tournament standout player",
        "next wonderkid signed europe transfer leak"
    ]
    results = asyncio.run(scraper.run_batch(test_queries))
    logger.info(f"Test run finished. Output sample: {len(results)} items")
    if results:
        print(json.dumps(results[:2], indent=2))
