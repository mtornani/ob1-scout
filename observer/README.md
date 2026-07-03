# /observer/ — Symbiont M2 (osservatore passivo)

Osservatore statistico della pipeline OB1 Global Radar. **Read-only sulla
pipeline**: legge solo gli snapshot git di `data/ob1_global.db`; scrive solo
file JSONL append-only in questa directory. Zero LLM, zero interventi.
Perimetro: §5.0 piano Symbiont + emendamento v1.3 (Gate 2a, 3/7/2026).

## File

| File | Contenuto |
|---|---|
| `observer_monitor.py` | Monitor: observe (giornaliero via `observer.yml`), `--calibrate`, `--retrodict` |
| `test_observer.py` | Test unitari (stdlib, `python3 observer/test_observer.py`) |
| `observations.jsonl` | Cache append-only: metriche per snapshot del DB (sha, righe, attività, attività per regione) |
| `calibration.jsonl` | Soglie calibrate (ultima riga vince) |
| `alarms.jsonl` | Allarmi emessi (append-only, dedup per episodio) |
| `heartbeat.jsonl` | Heartbeat giornaliero — secondo canale liveness (via git, indipendente da Telegram) |
| `retrodiction.jsonl` | Esiti retrodizione su storia completa |
| `GATE2A_REPORT.md` | Report §5.1+§5.2 (inventario segnali, mapping hazard) |
| `signals.jsonl` / `hazard_map.jsonl` | Catalogo S01–S20 / HZ-01–HZ-10 |

## Coppie hazard→segnale attive (M2)

- **P1** (HZ-05/07, "silent death"): streak di slot nominali 6h senza delta-dati.
  Soglia calibrata: 6 slot = 36h (max streak pulito 4 + margine 2; finestra
  1/6→3/7/2026, esclusa settimana piatta 21–29/6 = positivo di validazione).
- **P2** (HZ-06, drop di volume): calo >50% righe `anomalies` tra snapshot
  (resuscita il check inerte di `sanity_check` — vedi GATE2A_REPORT §HZ-06).
- **P3** (HZ-01/03, drift fonte): TV-distance della composizione per `region`
  (proxy dei pack di query regionali) su finestra 7gg vs baseline; soglia 0.59.

Heartbeat a doppio canale: messaggio `[OBSERVER]` sul canale admin Telegram
(notifier esistente, non modificato) + riga in `heartbeat.jsonl` committata.
Il rilevamento di ASSENZA del heartbeat è a carico di Mirko (Gate 2a §3).

## Known limitations (per M3)

- Indipendenza infrastrutturale parziale: observer e pipeline condividono
  GitHub Actions; heartbeat Telegram condivide il bot della pipeline.
- P1 non distingue "pipeline morta" da "run regolari con output piatto"
  (indistinguibili dal solo git); il messaggio d'allarme lo dichiara.
- P3 usa `region` come proxy della fonte; sensibile a regimi di composizione
  genuinamente nuovi (vedi retrodizione: fire su marzo 2026, regime pre-pilota).
