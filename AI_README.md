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
- **Scraper (`src/scraper_global.py`)**: Async (aiohttp + duckduckgo-search). DuckDuckGo primary, SearXNG fallback. Jina Reader (`r.jina.ai`) for full-article deep reads. No paid API keys required.
- **Intelligence (`src/intelligence.py`)**: Gemini 2.5 Flash. Scraped content injected directly into context window — no File Search Store. Identifies "Anomalies" from raw signals.
- **Database (`src/database.py`)**: SQLite (`data/ob1_global.db`). Tracks player history for Lead Time calculation.
- **Enricher (`src/enricher.py`)**: "Ghost Protocol". Checks Transfermarkt to confirm information vacuum.
- **Dashboard (`docs/`)**: Static site hosted on GitHub Pages (Tactical HUD). Data source: `docs/data/anomalies.json`.

## 🔄 Pipeline Flow
1. `scripts/run_pipeline.py` (Master): Search -> Deep Read -> Analyze -> Enrich -> Notify.
2. `scripts/generate_dashboard_data.py`: Syncs SQLite -> JSON.
3. `scripts/sanity_check.py`: Post-pipeline output verification (exit 0/1).

## 🤖 Integration & Context
- **Telegram**: Operational alerts to team (TELEGRAM_CHAT_ID). Admin alerts to TELEGRAM_OFFICE_CHAT_ID via `src/notifier.py`.
- **Automation**: GitHub Actions (`.github/workflows/global-radar.yml`) runs every 6 hours.
- **Threshold**: Only signals with Score >= 70 move to the dashboard (High Purity).

## 📊 Data Schema (SQLite)
- `anomalies`: `player_name`, `score`, `raw_content`, `region`, `source_url`, `detection_date`.
- `lead_times`: `player_name`, `ob1_detection_date`, `market_appearance_date`, `lead_time_days`.

## ⚠️ Architecture Decision Record — maggio 2026

**Problema**: Il layer di scraping originale (Serper → Tavily → SearXNG) dipendeva da due API pagate esterne che hanno prodotto fallimenti silenti in produzione:
- Tavily ha cambiato il formato di autenticazione (api_key nel body → Bearer header), causando HTTP 400 su tutte le query senza errori visibili nel log utente.
- Serper opera su quota mensile limitata (2500 req), esauribile in ~6 giorni a regime normale.
- Entrambi i fallimenti non generavano alert admin → la pipeline terminava con exit 0 senza produrre nulla.

**Decisione**: Eliminazione completa delle dipendenze da API esterne paganti per il layer di ricerca.
- `duckduckgo-search` sostituisce Serper + Tavily search. Gratuito, nessuna chiave, nessuna quota.
- `Jina Reader` (`r.jina.ai`) sostituisce Tavily extract. Gratuito, nessuna chiave.
- SearXNG mantenuto come fallback secondario (già presente).
- File Search Store Gemini rimosso: il contenuto viene iniettato direttamente nel contesto Gemini 2.5 Flash (1M token context window). Latenza ridotta, nessun overhead di upload/polling.

**Invariato**: scoring rubric, pesi, soglie HOT/WARM/COLD, modello LLM, query list, filtri pre-scoring.
