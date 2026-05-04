# OB1 Radar

Early-warning system for youth football talent — flags players before mainstream scouting networks pick them up.

---

## What it does

- Runs 17+ targeted search queries across global football sources (multilingual: EN, PT, ES, JA, KO, AR, TH)
- Scores each candidate on an anomaly scale (0–100) based on news velocity, source count, and performance signals
- Persists detections to SQLite with score history, re-detecting the same player across runs to build a confidence track
- Outputs a ranked JSON feed consumed by a live React dashboard at [mtornani.github.io/ob1-scout](https://mtornani.github.io/ob1-scout/)

---

## Architecture

| Layer | Detail |
|---|---|
| Search | Serper.dev API — 17 query patterns, concurrent (10 workers) |
| Scraping | requests + BeautifulSoup, 85–95% success rate |
| Cache | SQLite-backed URL cache, 7-day TTL — keeps monthly API cost near $0 |
| Scoring | Weighted multi-signal algorithm: source count, text quality, news velocity, regional rarity |
| Storage | `data/ob1_global.db` — `anomalies` + `lead_times` tables |
| Dashboard | React 18 + Babel standalone (no build step), served via GitHub Pages |

---

## Quick start

```bash
# Python 3.8+
pip install -r requirements.txt

# Add your key
echo "SERPER_API_KEY=your-key-here" > .env

# Run
python3 run_v0.8.2_optimized.py

# Output
cat output/daily.json | jq '.items[0]'
```

Typical run: 8–12 min, 250–350 candidates evaluated, 10–15 signals above threshold.

---

## Output example

```json
{
  "id": 24,
  "player_name": "Ryan Evaristo",
  "age": 17,
  "position": "Attacker",
  "club": "Corinthians U20",
  "region": "Brazil",
  "score": 100.0,
  "detection_count": 47,
  "first_detected": "2026-03-01",
  "last_seen": "2026-03-20",
  "is_ghost": true,
  "score_history": [82.5, 90.0, 97.5, 75.0, 90.0, 97.5, 100.0],
  "narrative": "Attacker signed by Corinthians U20 from Boavista, where he was
    considered a standout. Acquisition by a top Brazilian club for their youth
    setup is a strong early signal."
}
```

`is_ghost: true` — high asymmetry signal, likely real but not yet priced by mainstream media.

---

## Validated case

**Neiser Villarreal**
- OB1 flagged at estimated market value: **€1.8M**
- Actual release clause at time of detection: **€51M**
- Ratio: **28×** — the market had not yet caught up

---

## Status

| | |
|---|---|
| Version | 0.9.0 |
| State | Production |
| API cost | ~$0–5 / month (Serper.dev free tier covers normal usage) |
| Dashboard | Live — updates on each pipeline run |

---

## Contact

info@matchanalysispro.online

---

*© 2024–2025 Mirko Tornani. All rights reserved.*
