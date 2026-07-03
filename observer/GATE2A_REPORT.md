# OBSERVER — Report Gate 2a (Mandata 2, §5.1 + §5.2)

Data: 2026-07-02 · Repo: mtornani/ob1-scout (mercato globale) · Branch: claude/symbiont-signal-hazard-mapping-lcesta
Modalità: **read-only** sulla pipeline. Nessun file esistente modificato. Scritture solo in `/observer/`.
Zero chiamate LLM (statistica pura, budget 0 token). Zero interventi sulla pipeline.

Riferimento Mandata 1: prereg congelata 23eb698, report M1 accettato al Gate 1c
(σ*=1.13 IC95 [1.07,1.19]; w_ctrl=0.202 vs w_open=1.000 a σ=0; H1/H2/H3 confermate),
repo mtornani/symbiont-architecture, HEAD cb0fc03.

---

## 0. Verifica impronte OB1 (prerequisito)

| Impronta | Esito | Dettaglio |
|---|---|---|
| `src/notifier.py` | ✅ | `admin_alert(severity, source, message)`, best-effort/never-raise, canale admin `TELEGRAM_OFFICE_CHAT_ID`, prefisso `[ADMIN-OB1-GLOBAL]` |
| `scripts/sanity_check.py` | ✅ | 3 check post-pipeline (JSON output, freshness ≤6h, DB count + drop>50%), alert via `admin_alert`, exit 1 su fallimento |
| Workflow GitHub Actions | ✅ | `global-radar.yml` cron `0 */6 * * *` + commit `data/ob1_global.db` e `docs/data/anomalies.json`; `eater-of-logs.yml` (lun/gio 08:00); `publish-eater.yml` (manuale) |
| Commenti FREEZE PILOTA K-SPORT | ✅ | In testa a `run_pipeline.py`, `intelligence.py`, `enricher.py`, `scraper_global.py`, `database.py`, `generate_dashboard_data.py` |
| `PipelineStats` | ❌ **N/A** | Non esiste in questo repo né in tutta la storia git (`git log -S`, tutti i branch). Probabile impronta di ob1-serie-c. **Discrepanza sottoposta a Mirko in corso d'opera: confermato "procedi, PipelineStats=N/A"** |

Equivalente funzionale di PipelineStats in ob1-scout: contatori impliciti nei log di run
(`logs/pipeline.log`, effimero sul runner) + stato cumulativo nel DB versionato a ogni run.

---

## 1. §5.1 — Inventario segnali osservabili (read-only)

### 1.1 Architettura della pipeline (per contesto, nessuna modifica)

`global-radar.yml` (cron 6h) → `scripts/run_pipeline.py`:
scrape (DDGS + fallback SearXNG, 20 query fisse) → `filter_noise` → deep-read Jina (max 10 URL)
→ Gemini 2.5 Flash (rubrica scoring 0–100) → filtri (nomi placeholder, età<15, U12/U13)
→ ghost-check Transfermarkt via Serper (+8 flat se ghost) → soglia finale **score ≥ 70** per storage
→ `OB1Database.add_anomaly` (dedup fuzzy, media pesata 0.7·nuovo + 0.3·vecchio, history max 20)
→ Telegram utenti / digest 06 UTC → `check_mainstream` (lead time) → export `anomalies.json`
→ commit bot "Update Radar Memex" → `sanity_check.py`.

### 1.2 Fonti di segnale disponibili senza toccare la pipeline

**A. Artefatti versionati per-run (fonte primaria, con memoria)**
- `data/ob1_global.db` — SQLite, committato con `git add -f` a ogni run che produce cambiamenti.
  Stato attuale: 58 righe `anomalies` (tutte score≥70 per costruzione), 59 `lead_times`
  (26 tracking, 33 completed). Dati dal 2026-03-01 a oggi.
- `docs/data/anomalies.json` — 58 entry, stessa cadenza, filtro score≥70.
- La sequenza dei commit "Update Radar Memex" è una **serie storica di snapshot**: ogni diff
  tra snapshot consecutivi è un'osservazione run-level.

**B. Metadati GitHub Actions (API read-only)**
- `global-radar.yml`: **533 run totali** a oggi. Ultime 30 run (25/6→2/7): tutte `schedule`,
  tutte `success`, 1 `workflow_dispatch`. `run_started_at`, durata, conclusion, attempt.
- Log dei job (righe `[admin_alert]`, contatori scraper/noise-filter): disponibili via API
  con retention limitata (~90 giorni) — fonte secondaria.

**C. Storia interna al DB (retrospettiva)**
- `score_history` per giocatore (max 20 entry con data; media attuale 6.2), `first_detected`,
  `last_seen`, `detection_count`, `source_count`.
- `lead_times`: 33 completati, range 0–91 giorni.

**D. Canale notifiche esistente**
- `admin_alert` è importabile senza modifiche: heartbeat `[OBSERVER]` realizzabile come
  `admin_alert("INFO", "observer", "[OBSERVER] …")` → esce sul canale admin come
  `[ADMIN-OB1-GLOBAL] INFO | observer`. Nessun file esistente da toccare. (Implementazione: M2, post Gate 2a.)

### 1.3 Catalogo segnali

Catalogo machine-readable in `observer/signals.jsonl` (S01–S20). Sintesi:

| ID | Segnale | Fonte | Cadenza |
|---|---|---|---|
| S01 | Nuovi giocatori per run (delta righe `anomalies`) | A | per-run (4/die) |
| S02 | Aggiornamenti per run (delta Σ`detection_count`) | A | per-run |
| S03 | Run senza commit (run verde ma nessun "Update Radar Memex") | A+B | per-run |
| S04 | Esito workflow (success/failure/cancelled) | B | per-run |
| S05 | Drift orario run vs cron nominale; gap tra run | B | per-run |
| S06 | Durata run | B | per-run |
| S07 | Count righe `anomalies` + delta/drop% tra snapshot | A | per-run |
| S08 | Distribuzione score (media, mediana, quota ≥88; min=70 strutturale) | A | per-run |
| S09 | Quota `is_ghost` (attuale: 6/58 ≈ 10%) | A | per-run |
| S10 | Mix regioni (attuale: Brazil 33/58 ≈ 57%) | A | per-run |
| S11 | Null-rate campi anagrafici (age/position/club/league) | A | per-run |
| S12 | Null-rate `stats_summary` (efficacia enricher/Serper) | A | per-run |
| S13 | Lead-time: nuovi completamenti; quota lead=0gg | A | per-run |
| S14 | Dinamica `score_history` per giocatore; saturazione a 20 entry | A | per-run |
| S15 | Rapporto updated/new; cluster di nomi simili (proxy dedup) | A | giornaliera |
| S16 | Età di max(`last_seen`) — freshness reale lato repo | A | giornaliera |
| S17 | Alert admin emessi (righe `[admin_alert]` nei log job) | B | per-run |
| S18 | Volumi raw scrape + removal rate `filter_noise` | B (solo log) | per-run |
| S19 | Heartbeat `[OBSERVER]` (da emettere, M2) | D | giornaliera |
| S20 | Entry e size `anomalies.json` (coerenza con S07) | A | per-run |

### 1.4 Frequenza run e storico disponibile (requisito calibrazione 14gg)

- **Frequenza**: nominale 4 run/die (cron ogni 6h). Osservata: 4/die regolari, con drift di
  avvio 25–60 min (normale per GitHub scheduled). → **14 giorni ≈ 56 run: SUFFICIENTE.**
- **Storico**: sul remote la storia dei commit bot risale almeno a inizio marzo 2026
  (DB: prime detection 2026-03-01; 533 run ≈ 130+ giorni × 4/die). → retrospettiva ampia,
  **SUFFICIENTE**. Caveat operativo: il clone locale di questa sessione è **shallow (50 commit,
  dal 10/6)**; per calibrazione retrospettiva completa servirà `git fetch --unshallow` o API.
- **Caveat di contenuto**: finestra 22–28/6 con run tutte verdi ma **zero commit** (output
  piatto per ~8 giorni). Riduce la varianza di alcuni segnali ma è essa stessa
  un'osservazione preziosa (vedi HZ-05): consigliato includere sia periodi attivi sia piatti
  nella finestra di calibrazione.

**Verdetto §5.1: frequenza e storico sufficienti per la calibrazione di 14 giorni.**

---

## 2. §5.2 — Mapping hazard → segnale

Mapping machine-readable in `observer/hazard_map.jsonl`. Copertura: F=full, P=parziale, B=blind.

| Hazard | Descrizione | Segnali | Copertura | Evidenza |
|---|---|---|---|---|
| HZ-01 | Scraper degradato / 0 risultati (DDG rate-limit; istanze SearXNG instabili) | S03, S17 (`CRITICAL scraper`), S18, S01 | P (volumi raw solo nei log) | — |
| HZ-02 | Gemini: quota, parse-fail, drift rubrica | S17 (`WARN intelligence/parse`, `CRITICAL intelligence`), S01, S08 | F | — |
| HZ-03 | Enricher/Serper: ghost misclassificati, stats mancanti | S09, S12 | F | fallback `_check_via_http` ritorna True su errore → bias anti-ghost osservabile via S09 |
| HZ-04 | Dedup patologica (fuzzy `_names_match`: match su singola parola condivisa >2 char → rischio merge di giocatori distinti; o duplicati se troppo debole) | S15, S02 vs S01, S07 | F | nomi generici in DB ("Felipe", "Sorriso") |
| HZ-05 | **Flatline silente**: run verdi ma output congelato | S03 (streak), S16, S01+S02=0 | F | **OSSERVATO 22–28/6/2026**: 28 run success, zero commit, nessun alert |
| HZ-06 | Perdita dati / drop righe DB | S07 (con memoria, via snapshot git) | F | il check drop>50% di `sanity_check` è **inerte in CI**: `.sanity_state.json` matcha `*.json` in `.gitignore` e il runner parte pulito → `prev_count` sempre assente |
| HZ-07 | Infra/cron: failure Actions, disattivazione cron (60gg inattività), coda runner | S04, S05 (gap>12h) | F | — |
| HZ-08 | Notifiche Telegram fallite (utenti e/o admin) | S17 (`ERROR telegram`) | P/B (**common-mode**: se Telegram è giù, anche l'alert admin e il futuro heartbeat falliscono; delivery agli utenti finali non osservabile) | — |
| HZ-09 | Shift distribuzione scoring / compressione banda (media pesata 0.7/0.3; bonus ghost +8; soglia 70 tronca a sinistra) | S08, S09, S14 | F | tutte le 58 righe ≥70 per costruzione: la coda sotto soglia è invisibile (blind by design) |
| HZ-10 | Lead-time falsi positivi (match mainstream su parti di nomi generici → lead=0gg) | S13 | F | osservati lead=0 su "Felipe", "Sorriso", "Saviolo", "Ryan Evaristo" |

### Blind spot dichiarati
1. Coda score <70: mai memorizzata — drift di scoring sotto soglia non osservabile (by design, FREEZE).
2. Volumi raw di scraping e removal rate del noise-filter: solo nei log Actions (retention ~90gg).
3. Delivery Telegram agli utenti finali: non osservabile.
4. Common-mode observer↔pipeline: heartbeat e alert condividono la stessa infra (Actions + Telegram);
   un guasto infra silenzia sia la pipeline sia chi la osserva. Mitigazione possibile (M2): il
   *mancato* heartbeat giornaliero come segnale lato Mirko.
5. Check drop-DB del sanity_check inerte in CI (vedi HZ-06): S07 lo sostituisce lato observer
   senza toccare la pipeline.

---

## 3. Note fuori perimetro (segnalazione, nessuna azione)

- `run.py` (root, "Ouroboros Radar v0.4.2.5", legacy, non richiamato da alcun workflow)
  contiene una **API key hardcoded** (`anycrawl.dev`). Da ruotare/rimuovere a discrezione
  di Mirko — nessuna modifica effettuata (file esistente, fuori perimetro observer).
- `gh.exe` (55 MB) versionato nel repo — solo nota di igiene, nessuna azione.

## 4. Conformità §5.0 (autoverifica pre-commit)

- File esistenti modificati: **0** (git diff: solo aggiunte sotto `/observer/`).
- Chiamate LLM effettuate: **0**.
- Interventi sulla pipeline: **0**.
- Scritture: solo `/observer/` (`.md` + `.jsonl`; nota: `*.json` sarebbe ignorato da `.gitignore`,
  i file observer usano estensione `.jsonl` anche per questo).

**STOP al Gate 2a.** Nessuna implementazione observer avviata: si attende revisione di questo
report (decisioni richieste: conferma taxonomy hazard vs prereg §5.2 su symbiont-architecture;
finestra di calibrazione; mitigazione common-mode HZ-08).
