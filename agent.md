OB1 Scout - Agent Context

User Profile (Peterson Big Five)
- Agreeableness: 8% — async, diretto, no BS
- Conscientiousness: 25% — automazione o one-time setup
- Extraversion: 28% — no meeting/sales theater
- Neuroticism: 18% — decisioni razionali
- Openness: 71% — rischio over-engineer: RESISTI

## Prodotto (v2 in produzione)

- **Cosa è:** shortlist early-warning talenti (source-first + gate ≥2 fonti)
- **Cosa non è:** predittore AI, GPS, video analysis, partner white-label
- **Live:** https://ob1global.matchanalysispro.online (Cloudflare Access → Pages)
- **Promessa:** ogni nome con prove linkate; regge una telefonata di verifica
- **Revenue goal:** primo cliente pagante; finché €0 → no feature gonfie

## Stack canonico (tocca solo questo per il prodotto)

- Pipeline: `scripts/ingest_v2.py` → `data/ob1_v2.db` → `scripts/export_dashboard_v2.py`
- Feed: `docs/data/players_v2.json`
- UI: `docs/index.html` (statica)
- Fonti: `config/sources.json`
- Core: `src/*_v2.py`
- CI: `.github/workflows/global-radar-v2.yml` (cron 6h)

## Regole sviluppo

1. Non rompere la pipeline v2 su `main`
2. Non riattivare v1 cron senza richiesta esplicita
3. Lazy product: meno codice, più purezza shortlist
4. Costo basso (free tier LLM, budget call/run) + qualità gate alta
5. Niente brand/integrazioni partner nel prodotto o nel copy pubblico
6. No move/delete massivi senza backup + ok esplicito
7. Commit solo file del task; non committare `.env`, log, venv, dist

## Non fare

- Nuovi dashboard React / Babel CDN
- Feature finché non c’è un uso reale che le richiede
- Unificare lab (SENTINEL/Council) nel deploy OB1
- Refactor monorepo “per ordine” senza lista approvata

## Next step prodotto

Qualità shortlist + run stabili. Cliente. Non più codice decorative.
