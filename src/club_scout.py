#!/usr/bin/env python3
"""
OB1 Club Scout — Targeted player scouting for specific clubs and roles.
Implements PLAYER_SCHEMA standard output format.

Usage:
    from src.club_scout import ClubScoutEngine
    engine = ClubScoutEngine(serper_key=..., searxng_instances=[...])
    results = asyncio.run(engine.run_audit(audit_config))
"""

import re
import json
import logging
import asyncio
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── PLAYER_SCHEMA ─────────────────────────────────────────────────────────────
# Standard output format for all OB1 club scout results.
# Extends the existing anomalies schema with club-specific fields.

def make_player(target_club: str, query_id: str) -> dict:
    """Return an empty PLAYER_SCHEMA dict for a given club+query."""
    return {
        "player_name": None,       # str  — best-effort extraction from result title
        "age": None,               # int  — parsed from text (e.g. "22 anni")
        "position": None,          # str  — from query config position filter
        "club": None,              # str  — current club (parsed or null)
        "league": None,            # str  — current league (parsed or null)
        "nationality": None,       # str  — parsed if mentioned
        "foot": None,              # str  — "sinistro" | "destro" | "entrambi"
        "market_value_eur": None,  # int  — parsed from "€Xm" patterns
        "contract_expires": None,  # str  — "YYYY-06-30" inferred from text
        "season_stats": {},        # dict — {"goals": int, "assists": int, "appearances": int}
        "ob1_score": 0.0,          # float 0-100 — pattern-based relevance score
        "ob1_tags": [],            # list — matched scoring_boost pattern keys
        "reason": "",              # str  — raw snippet supporting the detection
        "target_club": target_club,
        "query_id": query_id,
        "sources": [],             # list — source URLs
        "detected_at": datetime.now().isoformat(),
    }


# ─── SCORING ───────────────────────────────────────────────────────────────────

_FOOTBALL_TERMS = [
    "goal", "gol", "assist", "calcio", "football", "soccer",
    "transfer", "mercato", "svincolato", "scout", "calciomercato",
]

_STAT_PATTERNS = [
    r"\d+\s*(?:gol|goal|goals)",
    r"\d+\s*(?:assist|assistenze|assists)",
    r"\d+\s*(?:partite|matches|appearances)",
]


def _base_score(text: str) -> float:
    """Simple relevance score from football term density and stat presence."""
    low = text.lower()
    score = 5.0
    score += sum(2.0 for t in _FOOTBALL_TERMS if t in low)
    score += sum(5.0 for p in _STAT_PATTERNS if re.search(p, low))
    return min(score, 40.0)


def _apply_boost(text: str, scoring_boost: dict) -> tuple[float, list]:
    """
    Apply query-specific scoring_boost patterns to a text blob.
    Returns (total_boost_points, matched_tag_list).
    """
    patterns = (scoring_boost or {}).get("patterns", {})
    total, tags = 0.0, []
    low = text.lower()
    for pattern, weight in patterns.items():
        try:
            if re.search(pattern, low, re.IGNORECASE):
                total += float(weight)
                tags.append(pattern)
        except re.error:
            if pattern.lower() in low:
                total += float(weight)
                tags.append(pattern)
    return total, tags


# ─── ENGINE ────────────────────────────────────────────────────────────────────

class ClubScoutEngine:
    """
    Targeted scouting engine for club-specific player profiles.

    Workflow per query:
        1. build_queries()   — build targeted search strings from config filters
        2. search()          — Serper (primary) or SearXNG (fallback)
        3. _extract()        — parse each result into PLAYER_SCHEMA + score it
        4. run_query()       — orchestrate 1-3, deduplicate, rank, cap results
        5. run_audit()       — iterate all queries in an audit config
    """

    _DEFAULT_SEARXNG = [
        "https://search.sapti.me",
        "https://searx.be",
        "https://searx.work",
        "https://search.privacyguides.net",
    ]

    def __init__(self, serper_key: str = "", searxng_instances: list = None):
        self.serper_key = serper_key
        self.searxng = searxng_instances or self._DEFAULT_SEARXNG
        self.timeout = aiohttp.ClientTimeout(total=20)

    # ── Query building ─────────────────────────────────────────────────────────

    def build_queries(self, cfg: dict) -> list[str]:
        """
        Build up to 8 targeted search strings from a query config.
        Handles regular role queries, nationality scans, and svincolati demos.
        """
        f = cfg.get("filters", {})
        positions = f.get("position", [])
        leagues = f.get("leagues", f.get("leagues_to_scan", []))
        age_max = f.get("age_range", [16, 30])[1]
        opportunity = f.get("opportunity_type", [])
        geo = f.get("geographic_radius", "")
        nationality = " ".join(f.get("nationality", []))
        search_type = f.get("search_type", "")

        qs = []

        # Nationality scan (e.g. COSMOS_DEMO_RSM_PASSPORTS)
        if search_type == "nationality_scan" or nationality:
            for league in leagues[:3]:
                qs.append(f"{nationality} passport football player {league} 2026")
            qs.append(f"San Marino FSGC national team player calcio italiano 2026")
            qs.append(f"sammarinese calciatore Serie D Eccellenza tesserato 2026")

        # Free-agent / svincolati queries (COSMOS_DEMO_SVINCOLATI)
        if opportunity:
            qs.append(f"svincolato calcio Serie D Eccellenza {geo[:40]} 2026")
            qs.append(f"rescissione contratto Serie D Eccellenza Emilia-Romagna Marche 2026")
            qs.append(f"free agent calcio Romagna Marche attaccante centrocampista 2026")

        # Standard role queries
        if not search_type and not opportunity:
            for pos in positions[:2]:
                for league in leagues[:3]:
                    qs.append(f"{pos} U{age_max} {league} goals stats 2025 2026 scout report")
                    qs.append(f"{pos} {league} transfer market value young talent 2026")

        # Reference player angle
        ref = cfg.get("reference", "")
        if ref:
            qs.append(f"football player profile similar {ref[:50]} scout 2026")

        return list(dict.fromkeys(qs))[:8]  # deduplicate, cap at 8

    # ── Search backends ────────────────────────────────────────────────────────

    async def _serper_search(self, session: aiohttp.ClientSession, query: str) -> list:
        """Search via Serper Google API (10 results per query)."""
        try:
            async with session.post(
                "https://google.serper.dev/search",
                json={"q": query, "num": 10},
                headers={"X-API-KEY": self.serper_key, "Content-Type": "application/json"},
                timeout=self.timeout,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [
                        {
                            "title": item.get("title", ""),
                            "url": item.get("link", ""),
                            "content": item.get("snippet", ""),
                        }
                        for item in data.get("organic", [])
                    ]
        except Exception as e:
            logger.debug(f"[Serper] {e}")
        return []

    async def _searxng_search(self, session: aiohttp.ClientSession, query: str) -> list:
        """Search via SearXNG public instances (free fallback, tries each in order)."""
        for inst in self.searxng:
            try:
                async with session.get(
                    f"{inst}/search",
                    params={"q": query, "format": "json"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=self.timeout,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [
                            {
                                "title": r.get("title", ""),
                                "url": r.get("url", ""),
                                "content": r.get("content", ""),
                            }
                            for r in data.get("results", [])[:10]
                        ]
            except Exception as e:
                logger.debug(f"[SearXNG] {inst}: {e}")
        return []

    async def search(self, query: str) -> list:
        """Search with Serper if key present, otherwise SearXNG."""
        async with aiohttp.ClientSession() as session:
            if self.serper_key:
                results = await self._serper_search(session, query)
                if results:
                    return results
            return await self._searxng_search(session, query)

    # ── Extraction + scoring ───────────────────────────────────────────────────

    def _extract(self, result: dict, cfg: dict) -> Optional[dict]:
        """
        Parse a raw search result into PLAYER_SCHEMA.
        Returns None if:
          - Result has no football relevance.
          - Final ob1_score is below the config's min_score threshold.
        """
        title = result.get("title", "")
        content = result.get("content", "")
        text = f"{title} {content}"

        # Football relevance gate
        if not re.search(
            r"calcio|football|soccer|goal|gol|assist|transfer|svincolato|scout|FSGC|sammarinese",
            text, re.I
        ):
            return None

        target_club = cfg.get("target_club", "")
        query_id = cfg.get("query_id", "")
        p = make_player(target_club, query_id)
        p["sources"] = [result.get("url", "")]
        p["reason"] = (content or title)[:300]

        # ── Field extraction (best-effort regex) ──────────────────────────────

        # Age
        m = re.search(r"\b(1[6-9]|2[0-9]|30)\s*(?:anni|years?|y\.?o\.?)", text, re.I)
        if m:
            p["age"] = int(m.group(1))

        # Season stats
        gm = re.search(r"(\d+)\s*(?:gol|goals?)", text, re.I)
        if gm:
            p["season_stats"]["goals"] = int(gm.group(1))
        am = re.search(r"(\d+)\s*(?:assist(?:enze|s)?)", text, re.I)
        if am:
            p["season_stats"]["assists"] = int(am.group(1))
        apm = re.search(r"(\d+)\s*(?:partite|matches|appearances)", text, re.I)
        if apm:
            p["season_stats"]["appearances"] = int(apm.group(1))

        # Foot
        if re.search(r"piede sinistro|mancino|left.?foot", text, re.I):
            p["foot"] = "sinistro"
        elif re.search(r"piede destro|destrimano|right.?foot", text, re.I):
            p["foot"] = "destro"

        # Market value (€Xm or €X.Xm)
        vm = re.search(r"(?:€|EUR)\s*([\d,.]+)\s*[Mm](?:ln|ilio)?", text)
        if vm:
            try:
                p["market_value_eur"] = int(float(vm.group(1).replace(",", ".")) * 1_000_000)
            except ValueError:
                pass

        # Contract expiry year
        cm = re.search(
            r"(?:scade|expires?|contract until|contratto fino|fino al)\s*(?:il\s*)?(20\d{2})",
            text, re.I
        )
        if cm:
            p["contract_expires"] = f"{cm.group(1)}-06-30"

        # Nationality (San Marino angle)
        if re.search(r"sammarinese|san marino|rsm|fsgc", text, re.I):
            p["nationality"] = "San Marino"

        # Position from config (first listed)
        positions = cfg.get("filters", {}).get("position", [])
        if positions:
            p["position"] = positions[0]

        # Best-effort player name from title (first 2 capitalised words)
        cap_words = [w for w in title.split() if w and w[0].isupper() and len(w) > 1]
        if len(cap_words) >= 2:
            p["player_name"] = " ".join(cap_words[:2])

        # ── Scoring ───────────────────────────────────────────────────────────
        base = _base_score(text)
        boost, tags = _apply_boost(text, cfg.get("scoring_boost", {}))
        p["ob1_score"] = round(min(100.0, base + boost * 4.0), 1)
        p["ob1_tags"] = tags

        min_score = cfg.get("scoring_boost", {}).get("min_score", 0)
        if p["ob1_score"] < min_score:
            return None

        return p

    # ── Query execution ────────────────────────────────────────────────────────

    async def run_query(self, cfg: dict) -> list:
        """
        Execute one club-specific query.
        Returns a list of PLAYER_SCHEMA dicts, sorted by ob1_score desc,
        capped at filters.max_results.
        """
        query_id = cfg.get("query_id", "?")
        role = cfg.get("critical_role", "")
        max_results = cfg.get("filters", {}).get("max_results", 5)

        logger.info(f"[ClubScout] {query_id} — {role[:65]}")

        queries = self.build_queries(cfg)
        seen_urls: set = set()
        players: list = []

        for q in queries:
            logger.debug(f"  >> {q[:80]}")
            raw = await self.search(q)
            for r in raw:
                url = r.get("url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                player = self._extract(r, cfg)
                if player:
                    players.append(player)
            await asyncio.sleep(0.5)
            if len(players) >= max_results * 2:
                break

        players.sort(key=lambda x: x["ob1_score"], reverse=True)
        logger.info(f"[ClubScout] {query_id}: {len(players[:max_results])} players kept")
        return players[:max_results]

    # ── Audit runner ───────────────────────────────────────────────────────────

    async def run_audit(self, audit_config: dict) -> dict:
        """
        Run all queries defined in an audit config dict.
        Returns a structured output with PLAYER_SCHEMA results per query.

        Output schema:
        {
            "meta": { ...audit meta + run_at + total_queries },
            "results": {
                "QUERY_ID": {
                    "target_club": str,
                    "tier": str,
                    "critical_role": str,
                    "notes": str,
                    "players_found": int,
                    "players": [ PLAYER_SCHEMA, ... ]
                },
                ...
            }
        }
        """
        queries = audit_config.get("queries", {})
        clubs_ctx = {c["club"]: c for c in audit_config.get("target_clubs_audit", [])}
        meta = audit_config.get("meta", {})

        output = {
            "meta": {
                **meta,
                "run_at": datetime.now().isoformat(),
                "total_queries": len(queries),
            },
            "results": {},
        }

        for query_id, cfg in queries.items():
            cfg_run = {**cfg, "query_id": query_id}
            players = await self.run_query(cfg_run)

            club_name = cfg.get("target_club", "")
            ctx = clubs_ctx.get(club_name, {})

            output["results"][query_id] = {
                "target_club": club_name,
                "tier": ctx.get("tier", ""),
                "critical_role": cfg.get("critical_role", ""),
                "notes": cfg.get("notes", ""),
                "players_found": len(players),
                "players": players,
            }

        return output


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config_path = Path(__file__).parent.parent / "config" / "audit_phase2.json"
    with open(config_path, encoding="utf-8") as f:
        audit_config = json.load(f)

    engine = ClubScoutEngine(serper_key=os.getenv("SERPER_API_KEY", ""))
    results = asyncio.run(engine.run_audit(audit_config))
    print(json.dumps(results, ensure_ascii=False, indent=2))
