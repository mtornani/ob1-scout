# OB1 Global Scout - AI & Agent README
> [!NOTE]
> This document is designed for LLMs and AI agents (Cursor, Windsurf, Antigravity, etc.) to quickly grasp the system's architecture and logic.

## 🧭 Core Objective
Automated intelligence loop to identify global football talent (U20) before mainstream media.
Key metrics: 
- **Asymmetry Score**: 0-100 ( talento rilevato / copertura mainstream ).
- **Lead Time**: Days between OB1 detection and mainstream appearance.

## 🏗️ Architecture
- **Language**: Python 3.12 (Backend) + HTML/JS (Frontend/PWA).
- **Scraper (`src/scraper_global.py`)**: Asynchronous (aiohttp). Connects to Serper (Primary), Tavily (Fallback), SearXNG.
- **Intelligence (`src/intelligence.py`)**: Gemini 2.0 Flash Lite RAG logic. Identifies "Anomalies" from raw text.
- **Database (`src/database.py`)**: SQLite (`data/ob1_global.db`). Tracks player history for Lead Time calculation.
- **Enricher (`src/enricher.py`)**: "Ghost Protocol". Checks Transfermarkt API/Scrape to confirm information vacuum.
- **Dashboard (`docs/`)**: Static site hosted on GitHub Pages (Tactical HUD). Data source: `docs/data/anomalies.json`.

## 🔄 Pipeline Flow
1. `scripts/run_pipeline.py` (Master): Search -> Scrape -> Analyze -> Enrich -> Notify.
2. `scripts/generate_dashboard_data.py`: Syncs SQLite -> JSON.
3. `scripts/generate_murales_global_post.py`: Generates social posts exposing Lead Time.

## 🤖 Integration & Context
- **Telegram**: Alerts delivered to `@Ob1WorldBot`.
- **Automation**: GitHub Actions (`.github/workflows/global-radar.yml`) runs every 6 hours.
- **Threshold**: Only signals with Score >= 70 move to the dashboard (High Purity).

## 📊 Data Schema (SQLite)
- `anomalies`: `player_name`, `score`, `raw_content`, `region`, `source_url`, `detection_date`.
- `lead_times`: `player_name`, `ob1_detection_date`, `market_appearance_date`, `lead_time_days`.
