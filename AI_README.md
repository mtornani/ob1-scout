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

## LLM: zero-cost first

`src/llm_free_chain.py` → catena di provider gratuiti OpenAI-compatible
(Groq → Cerebras → OpenRouter → NVIDIA → endpoint generico `COMPARE_*`).
`src/extractor_v2.py` la prova **prima** di Gemini.

| `OB1_LLM_MODE` | ordine | quando |
|---|---|---|
| `free_first` *(default in CI)* | free → Gemini | normale |
| `free_only` | solo free | se il billing Google non si può spegnere |
| `gemini_first` | Gemini → free | storico, sconsigliato con billing attivo |

⚠️ **Con il billing attivo sul progetto Google, Gemini oltre il free tier si
paga.** La difesa non è "usarlo meno": è non chiamarlo per primo. Chiave Gemini
solo da progetto con billing OFF, oppure `free_only`.

Un provider che risponde 429 esce dalla catena **per tutto il run**: non lo si
ritenta a ogni fonte. `FREE_MAX_CHARS` (default 2800) tiene il prompt sotto il
token/minuto dei free tier; Gemini, quando lo si usa, riceve il testo pieno.

Architettura a scala (pool multi-provider, ledger di quota, prefiltro): `FASE_C.md`.

## Env / secrets (Actions)

- `OB1_LLM_MODE` (`free_first` nel workflow)
- `GROQ_API_KEY` (consigliato: free, senza carta)
- `CEREBRAS_API_KEY`, `OPENROUTER_API_KEY` (opz., più quota gratis)
- `NVIDIA_API_KEY` (+ `NVIDIA_MODEL`): free tier a crediti, ultimo anello
- `GEMINI_API_KEY` (solo free tier, billing OFF) + `GEMINI_MODEL`
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` (alert opz.)
- `INGEST_LLM_BUDGET` (default 15) · `INGEST_CALL_DELAY` (default 12s)

Verifica rapida della configurazione:

```bash
PYTHONPATH=. python -c "from src.extractor_v2 import OB1Extractor; e=OB1Extractor(); \
print(e.mode, e.available(), [p['label'] for p in e.free_providers])"
```

## Pensionato

- `scripts/run_pipeline.py`, `src/intelligence.py` (v1)
- `.github/workflows/global-radar.yml` (solo `workflow_dispatch`)
- Old radar React (`radar_app.jsx` rimosso)
