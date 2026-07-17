# OB1 Scout

Lista corta di talenti early, con **prove linkate**.  
Pubblicato solo se regge una telefonata di verifica (≥2 fonti, identità completa).

**Live:** [ob1global.matchanalysispro.online](https://ob1global.matchanalysispro.online)

---

## Cosa fa

1. Monitora **fonti curate** (`config/sources.json`) — source-first, non Google random  
2. Estrae giocatori con LLM **solo come estrattore tipizzato**  
3. Accumula prove in SQLite (`data/ob1_v2.db`)  
4. **Gate:** pubblica solo profili con identità completa e ≥2 domini indipendenti  
5. Esporta `docs/data/players_v2.json` → dashboard statica GitHub Pages  

Promessa: *ogni nome con le sue prove.*

---

## Stack (economico)

| Pezzo | Scelta |
|--------|--------|
| Pipeline | GitHub Actions ogni 6h (`global-radar-v2.yml`) |
| LLM | Gemini free tier, fallback Groq, budget call/run |
| Store | SQLite v2 |
| Hosting | GitHub Pages + CNAME Cloudflare |
| Frontend | HTML/CSS/JS puro in `docs/index.html` (zero build, zero React) |

---

## Run locale

```bash
pip install -r requirements.txt
# .env: GEMINI_API_KEY=...  (opz. GROQ_API_KEY)
python scripts/ingest_v2.py
python scripts/export_dashboard_v2.py
# apri docs/index.html o servi docs/
```

Entrypoint produzione: solo **v2** (`ingest_v2` + `export_dashboard_v2`).  
v1 (`run_pipeline.py`, `global-radar.yml`) è pensionata — solo manuale archivio.

---

## Repo (file che contano)

```
config/sources.json          # registro fonti
src/*_v2.py                  # core v2
scripts/ingest_v2.py
scripts/export_dashboard_v2.py
docs/index.html              # dashboard
docs/data/players_v2.json    # feed pubblico
data/ob1_v2.db               # store (in CI)
.github/workflows/global-radar-v2.yml
```

---

## Contatto

info@matchanalysispro.online
