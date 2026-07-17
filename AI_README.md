# OB1 Scout — mappa tecnica (v2)

## Pipeline

```
config/sources.json
  → scripts/ingest_v2.py          # monitor fonti + extract + corroborate
  → data/ob1_v2.db
  → scripts/export_dashboard_v2.py
  → docs/data/players_v2.json
  → docs/index.html (GitHub Pages)
```

CI: `.github/workflows/global-radar-v2.yml` (schedule 6h + manual).

## Dashboard

- Host: GitHub Pages `main` / `docs`
- CNAME: `ob1global.matchanalysispro.online`
- Access: Cloudflare Access (OTP email)
- Data: **solo** `players_v2.json` (v1 `anomalies.json` = archivio)

## Gate pubblicazione

Identità completa + ≥2 domini fonte indipendenti → `publishable`.  
Export mostra tutti i verificati + max 15 “da corroborare” (resto in DB).

## Env / secrets (Actions)

- `GEMINI_API_KEY` (primario)
- `GROQ_API_KEY` (fallback)
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (alert opz.)
- `INGEST_LLM_BUDGET` (default 15)

## Pensionato

- `scripts/run_pipeline.py`, `src/intelligence.py` (v1)
- `.github/workflows/global-radar.yml` (solo `workflow_dispatch`)
- Old radar React (`radar_app.jsx` rimosso)
