#!/usr/bin/env python3
"""
OB1 Club Scout — CLI entry point.
Loads an audit config and runs targeted club-specific player scouting.

Usage:
    python scripts/run_club_scout.py                        # uses config/audit_phase2.json
    python scripts/run_club_scout.py path/to/config.json    # custom audit config

Output:
    output/club_profiles/audit-YYYY-MM-DD.json   — full PLAYER_SCHEMA JSON
    output/club_profiles/audit-YYYY-MM-DD.md     — human-readable markdown summary
"""

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.club_scout import ClubScoutEngine
from config.ob1_config import SERPER_API_KEY, SEARXNG_INSTANCES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).parent.parent / "config" / "audit_phase2.json"
OUT_DIR = Path(__file__).parent.parent / "output" / "club_profiles"


# ─── Output helpers ─────────────────────────────────────────────────────────────

def save_json(results: dict) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = OUT_DIR / f"audit-{today}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def generate_md(results: dict) -> str:
    """Generate a readable markdown summary from audit results."""
    run_at = results.get("meta", {}).get("run_at", "")
    lines = [
        "# OB1 Club Scout — Audit Report",
        f"_Generated: {run_at}_",
        "",
    ]

    for query_id, r in results.get("results", {}).items():
        club = r.get("target_club", "?")
        tier = r.get("tier", "")
        role = r.get("critical_role", "")
        notes = r.get("notes", "")
        players = r.get("players", [])

        lines.append(f"## {club} ({tier})")
        lines.append(f"**Query**: `{query_id}`  ")
        lines.append(f"**Role**: {role}  ")
        lines.append(f"**Players found**: {len(players)}")
        lines.append("")

        if not players:
            lines.append("_No results above score threshold for this query._")
            lines.append("")
        else:
            for i, p in enumerate(players, 1):
                name = p.get("player_name") or "Unknown"
                age = p.get("age") or "?"
                pos = p.get("position") or "?"
                pclub = p.get("club") or "?"
                league = p.get("league") or "?"
                score = p.get("ob1_score", 0)
                tags = ", ".join(p.get("ob1_tags", [])[:4])
                reason = (p.get("reason") or "")[:150]
                foot = p.get("foot") or ""
                val = p.get("market_value_eur")
                val_str = f"€{val:,}" if val else ""
                exp = p.get("contract_expires") or ""
                stats = p.get("season_stats", {})
                stats_str = " | ".join(f"{k}: {v}" for k, v in stats.items()) if stats else ""
                sources = p.get("sources", [])

                lines.append(f"### {i}. {name} — OB1 Score: **{score}/100**")
                profile = f"{age}y · {pos} · {pclub} ({league})"
                if foot:
                    profile += f" · piede {foot}"
                if val_str:
                    profile += f" · {val_str}"
                if exp:
                    profile += f" · contratto: {exp}"
                lines.append(f"_{profile}_")
                if stats_str:
                    lines.append(f"**Stats**: {stats_str}")
                if tags:
                    lines.append(f"**Tags**: `{tags}`")
                if reason:
                    lines.append(f"> {reason}...")
                if sources:
                    lines.append(f"[Source]({sources[0]})")
                lines.append("")

        if notes:
            lines.append(f"> **Note DS**: {notes[:250]}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def print_summary(results: dict) -> None:
    """Print a compact console summary after the run."""
    total_players = sum(
        r.get("players_found", 0) for r in results.get("results", {}).values()
    )
    print("\n" + "=" * 65)
    print("OB1 CLUB SCOUT — AUDIT COMPLETE")
    print("=" * 65)
    for query_id, r in results.get("results", {}).items():
        count = r.get("players_found", 0)
        role = r.get("critical_role", "")[:52]
        club = r.get("target_club", "")
        print(f"  {query_id:<30} {count:>2} players  [{club}]")
        if count and r.get("players"):
            top = r["players"][0]
            top_name = top.get("player_name") or "?"
            top_score = top.get("ob1_score", 0)
            print(f"    → top: {top_name} (score {top_score})")
    print(f"\n  Total players detected: {total_players}")
    print("=" * 65)


# ─── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG

    if not config_path.exists():
        logger.error(f"Audit config not found: {config_path}")
        sys.exit(1)

    with open(config_path, encoding="utf-8") as f:
        audit_config = json.load(f)

    query_ids = list(audit_config.get("queries", {}).keys())
    logger.info(f"Loaded: {config_path.name} — {len(query_ids)} queries")
    logger.info(f"Queries: {query_ids}")

    engine = ClubScoutEngine(
        serper_key=SERPER_API_KEY,
        searxng_instances=SEARXNG_INSTANCES,
    )

    results = await engine.run_audit(audit_config)

    # Save outputs
    json_path = save_json(results)
    logger.info(f"JSON saved: {json_path}")

    md_content = generate_md(results)
    md_path = json_path.with_suffix(".md")
    md_path.write_text(md_content, encoding="utf-8")
    logger.info(f"Markdown saved: {md_path}")

    print_summary(results)
    print(f"\nOutput: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
