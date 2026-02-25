#!/usr/bin/env python3
"""
OB1 Global Scout - Enrichment Module (Ghost Protocol)
Cross-references detected players with Transfermarkt to check for Information Asymmetry.
"""

import aiohttp
import asyncio
import logging
from pathlib import Path

# Setup logging
logger = logging.getLogger(__name__)

class OB1Enricher:
    def __init__(self):
        self.tm_base_url = "https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query="
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    async def check_transfermarkt(self, player_name: str) -> bool:
        """
        Check if a player exists on Transfermarkt.
        Returns True if found, False if not (potential 'Ghost').
        """
        search_url = f"{self.tm_base_url}{player_name.replace(' ', '+')}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, headers=self.headers, timeout=15) as response:
                    if response.status == 200:
                        content = await response.text()
                        # If "No results found" or similar is in the text, it's a ghost
                        if "No matches found" in content or "nessun risultato" in content.lower():
                            return False
                        
                        # In a more advanced version, we'd parse the table to ensure 
                        # it's the SAME player, but for asymmetry, even a missing exact match is a signal.
                        return True
                    else:
                        logger.warning(f"Transfermarkt check failed for {player_name} (HTTP {response.status})")
                        return True # Default to True to avoid false positive ghosts on error
        except Exception as e:
            logger.error(f"Error enriching {player_name}: {e}")
            return True

    def calculate_asymmetry_score(self, base_score: float, is_ghost: bool) -> float:
        """Boost score if the player is a 'Ghost' in the matrix."""
        if is_ghost:
            # Huge boost for ghosts as they represent the ultimate information asymmetry
            return min(100.0, base_score * 1.5)
        return base_score

if __name__ == "__main__":
    enricher = OB1Enricher()
    # Test
    player = "Gilberto Mora"
    found = asyncio.run(enricher.check_transfermarkt(player))
    print(f"Player: {player}, Found on TM: {found}")
