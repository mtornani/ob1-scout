# OB1 Scout — Istruzioni per sessioni Claude

## STATO: v2 in produzione (dal 20 luglio 2026) — v1 pensionata

Il "FREEZE PILOTA K-SPORT" non è più in vigore: il pilota non è mai partito
formalmente e la disciplina auto-imposta si è chiusa con la Fase B. La v2 è
il sistema principale; la v1 è pensionata (il suo workflow resta solo
manuale, come archivio).

## Il sistema (v2)

Radar di scouting source-first: monitora fonti curate (config/sources.json),
estrae giocatori con l'LLM usato SOLO come estrattore tipizzato, accumula
prove per entità, e pubblica solo profili con identità completa e ≥2 fonti
indipendenti (gate). Scoring trasparente in codice (merito × confidenza).
Promessa di prodotto: "ogni nome che diamo regge una telefonata di verifica".

- Pipeline: `.github/workflows/global-radar-v2.yml` (cron 6h) →
  `scripts/ingest_v2.py` → `data/ob1_v2.db` → `scripts/export_dashboard_v2.py`
  → `docs/data/players_v2.json` → dashboard `docs/index.html`
- Core: `src/{database,scoring,extractor,sources,outcomes,corroborate}_v2.py`
- Strumenti: `scripts/dossier_v2.py` (dossier on-demand), `scripts/rank_v2.py`,
  `scripts/compare_llm.py`, `scripts/player_lookup.py`
- LLM: Gemini free tier primario, fallback Groq su 429 (prompt ridotto a
  GROQ_MAX_CHARS per il TPM). Budget chiamate per run: INGEST_LLM_BUDGET.
- Design di riferimento: FASE_B.md (audit Fase A + architettura v2).

## Regole di lavoro

1. **Non rompere la produzione.** La pipeline v2 gira da `main` ogni 6h.
   Se un cambio rischia di fermarla, FERMATI e segnalalo. La v1
   (`scripts/run_pipeline.py`, `src/intelligence.py`, ecc.) è pensionata:
   non svilupparci sopra, non riattivarne il cron senza richiesta esplicita.

2. **I cambi a scoring/gate si validano coi dati, non a intuito.** Pesi,
   soglie e gate si possono cambiare, ma un cambio diventa default solo dopo
   un confronto su dati reali (rank_v2, outcome). Niente ritarature "a naso".

3. **Non ricostruire file da snapshot o branch alternativi senza chiedere.**
   Per git reset/ripristini, FERMATI e chiedi prima.

4. **Sulle scelte di prodotto ambigue, presenta opzioni.** Aperte: scope di
   genere (oggi 'unknown', non filtrato), regioni prioritarie del registro
   fonti, eventuale budget pay-as-you-go per superare i limiti free tier.

5. **Prima del commit, confronta il git diff con l'atteso** e segnala
   modifiche fuori perimetro.

6. I banner "FREEZE PILOTA K-SPORT" ancora presenti nei file v1 sono
   obsoleti: ignorali (e rimuovili se capita di toccare quei file).
