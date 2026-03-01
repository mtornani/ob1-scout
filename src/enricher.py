#!/usr/bin/env python3
"""
OB1 Global Scout - Enrichment Module (Ghost Protocol)
Cross-references detected players with Transfermarkt via Serper site-search.
Falls back to direct HTTP if Serper is unavailable.
"""

import aiohttp
import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


class OB1Enricher:
    def __init__(self):
        self.serper_key = os.getenv('SERPER_API_KEY', '')
        self.tm_base_url = "https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query="
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    async def check_transfermarkt(self, player_name: str) -> bool:
        """
        Check if a player exists on Transfermarkt.
        Returns True if found, False if not (potential 'Ghost').

        Strategy:
        1. Serper site-search (API, no 403 risk) - PRIMARY
        2. Direct HTTP GET (fallback, may 403)
        """
        # --- Strategy 1: Serper site-search (works everywhere, no 403) ---
        if self.serper_key and len(self.serper_key) > 5:
            try:
                found = await self._check_via_serper(player_name)
                if found is not None:
                    return found
            except Exception as e:
                logger.debug(f"Serper TM check failed for {player_name}: {e}")

        # --- Strategy 2: Direct HTTP (may get 403) ---
        try:
            return await self._check_via_http(player_name)
        except Exception as e:
            logger.debug(f"Direct TM check failed for {player_name}: {e}")
            return True  # Default to "found" to avoid false ghost positives

    async def _check_via_serper(self, player_name: str) -> bool | None:
        """Use Serper to search Transfermarkt for the player. Returns None on failure."""
        url = "https://google.serper.dev/search"
        payload = json.dumps({
            "q": f'site:transfermarkt.com "{player_name}"',
            "num": 3
        })
        headers = {
            'X-API-KEY': self.serper_key,
            'Content-Type': 'application/json'
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    organic = data.get('organic', [])
                    if organic:
                        # Check that at least one result actually contains the player name
                        name_lower = player_name.lower()
                        for item in organic:
                            title = (item.get('title', '') + item.get('snippet', '')).lower()
                            # Match any part of the name (first or last name)
                            name_parts = name_lower.split()
                            if any(part in title for part in name_parts if len(part) > 2):
                                logger.info(f"[Serper/TM] {player_name} FOUND on Transfermarkt")
                                return True
                    logger.info(f"[Serper/TM] {player_name} NOT found - potential GHOST")
                    return False
                else:
                    logger.debug(f"[Serper/TM] HTTP {resp.status}")
                    return None

    async def _check_via_http(self, player_name: str) -> bool:
        """Direct HTTP check against Transfermarkt (may get blocked)."""
        search_url = f"{self.tm_base_url}{player_name.replace(' ', '+')}"

        async with aiohttp.ClientSession() as session:
            async with session.get(search_url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                if response.status == 200:
                    content = await response.text()
                    if "No matches found" in content or "nessun risultato" in content.lower():
                        return False
                    return True
                else:
                    logger.warning(f"Transfermarkt HTTP {response.status} for {player_name}")
                    return True  # Default to found on error

    async def search_player_stats(self, player_name: str) -> str:
        """
        Search FBref/Sofascore/Transfermarkt for real statistics.
        Returns a text summary of stats snippets, or empty string on failure.
        Called only for high-purity players (score >= 70) to minimize API usage.
        """
        if not self.serper_key or len(self.serper_key) < 5:
            return ""

        url = "https://google.serper.dev/search"
        payload = json.dumps({
            "q": f'"{player_name}" stats goals assists 2026 site:fbref.com OR site:sofascore.com OR site:transfermarkt.com',
            "num": 3
        })
        headers = {
            'X-API-KEY': self.serper_key,
            'Content-Type': 'application/json'
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        stats_parts = []
                        for item in data.get('organic', []):
                            snippet = item.get('snippet', '')
                            if snippet:
                                stats_parts.append(snippet)
                        if stats_parts:
                            combined = " | ".join(stats_parts)
                            logger.info(f"[Stats] {player_name}: found {len(stats_parts)} stat snippets")
                            return combined
                    logger.debug(f"[Stats] No results for {player_name}")
                    return ""
        except Exception as e:
            logger.debug(f"[Stats] Search failed for {player_name}: {e}")
            return ""

    def calculate_asymmetry_score(self, base_score: float, is_ghost: bool) -> float:
        """Boost score if the player is a 'Ghost' in the matrix."""
        if is_ghost:
            return min(100.0, base_score * 1.5)
        return base_score


if __name__ == "__main__":
    enricher = OB1Enricher()
    player = "Gilberto Mora"
    found = asyncio.run(enricher.check_transfermarkt(player))
    print(f"Player: {player}, Found on TM: {found}")
