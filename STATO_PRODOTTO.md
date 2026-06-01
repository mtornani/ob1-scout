# OB1 Global Scout — Stato Prodotto
**Data:** 2026-06-01  
**Branch attivo:** `claude/setup-daas-platform-PXYST` (PR #7, pending merge)  
**Autori revisione:** Mirko Tornani · Claude AI

---

## 1. Cos'è il prodotto

OB1 Global Scout è un sistema di scouting calcistico automatizzato che:
- Monitora talenti emergenti under-23 da leghe a bassa visibilità (Brasile, LATAM, Africa, Europa minore)
- Genera uno **Scout Score** (0–100) per ogni giocatore usando Gemini 2.5 Flash
- Calcola il **vantaggio temporale sui media** (giorni tra la nostra detection e la prima uscita sulla stampa mainstream)
- Espone i dati tramite una dashboard web interattiva + canale Telegram

Il sistema è in **pilota K-Sport** (≈3 mesi, ≈5 squadre selezionate). Comportamento AS-IS — nessuna modifica al core di scoring durante il pilota.

---

## 2. Architettura attuale

```
Pipeline backend (GitHub Actions, schedulata 2×/giorno)
    │
    ├── scraper.py         → 20 query Google/DDG per talenti emergenti
    ├── scorer.py          → Gemini 2.5 Flash → Scout Score 0-100
    ├── deduplicator.py    → merge rilevamenti dello stesso giocatore
    └── anomalies.json     → output → docs/data/anomalies.json

Dashboard web (GitHub Pages → docs/)
    │
    ├── index.html         → entry point, carica React 18 + Babel standalone
    ├── radar_parts.jsx    → componenti SVG, utility functions
    ├── radar_app.jsx      → app React principale (Shell, KpiCluster, SignalCard, Dossier, TelegramCta)
    ├── css/radar-v3.css   → design system dark (JetBrains Mono + Space Grotesk)
    └── js/tracker.js      → ping anonimo accessi (sendBeacon → Cloudflare Worker)

Canale Telegram
    └── @WorldOuroboros    → notifiche automatiche nuovi rilevamenti HOT

Infrastruttura
    ├── GitHub Pages        → hosting dashboard (docs/ su main)
    ├── Cloudflare DNS      → ob1global.matchanalysispro.online → mtornani.github.io
    ├── Cloudflare SSL/TLS  → Full mode, HTTPS trasparente
    ├── Cloudflare Access   → login email OTP, policy "K-Sport Pilot"
    └── Cloudflare Worker   → orange-truth-2304 (tracker ping receiver)
```

---

## 3. Stato componenti

### 3.1 Pipeline di scoring — FREEZE PILOTA

| Componente | Stato | Note |
|---|---|---|
| Scraper (20 query) | ✅ Operativo | Schedulato 2×/giorno via GHA |
| Scorer Gemini 2.5 Flash | ✅ Operativo | Pesi e rubriche invariati |
| Deduplicatore | ✅ Operativo | Logica intatta |
| Output anomalies.json | ✅ Aggiornato | 28 giocatori al 01/06 |
| Notifiche Telegram | ✅ Operativo | @WorldOuroboros |

**FREEZE ATTIVO** — nessuna modifica a scoring, query, filtri, LLM durante il pilota.

### 3.2 Dashboard web

| Feature | Stato | Note |
|---|---|---|
| KPI cluster (giocatori, score max, vantaggio medio) | ✅ | |
| Signal cards con score, narrative, vantaggio | ✅ | |
| Scheda dossier giocatore (destra) | ✅ | |
| Filtro per regione | ✅ | |
| Filtro sconosciuti ai grandi club (ghost) | ✅ | |
| Ricerca testuale | ✅ | |
| Ordinamento (score / vantaggio / recenti) | ✅ | |
| Banner Telegram CTA | ✅ nel branch | ⚠️ non su main ancora |
| Mobile responsive | ✅ | Drawer mobile operativo |
| Tracker accessi (anonimo) | ✅ nel branch | ⚠️ non su main ancora |
| Animazioni decorative rimosste | ✅ nel branch | ⚠️ non su main ancora |
| Readability fix (contrasto, font sizes) | ✅ nel branch | ⚠️ non su main ancora |

### 3.3 Accesso e autenticazione

| Componente | Stato | Note |
|---|---|---|
| DNS `ob1global.matchanalysispro.online` | ✅ Attivo | CNAME → mtornani.github.io, proxied |
| HTTPS | ✅ Attivo | Cloudflare Universal SSL, Full mode |
| Cloudflare Access (login email OTP) | ✅ Policy creata | Email K-Sport da aggiungere quando disponibili |
| CNAME GitHub Pages | ✅ Attivo | DNS check successful |

### 3.4 Tracking

| Componente | Stato | Note |
|---|---|---|
| tracker.js | ✅ nel branch | Invia ping anonimo via sendBeacon |
| Worker `orange-truth-2304` | ⚠️ Da configurare | Codice da incollare nel Cloudflare Quick Editor |
| Dati di utilizzo | ⏳ Non disponibili | Attivi dopo deploy worker + merge PR |

---

## 4. Azioni pending (ordine di priorità)

### Alta priorità — blocca il go-live corretto

1. **Merge PR #7** (`claude/setup-daas-platform-PXYST` → `main`)  
   Porta in produzione: readability fix, tracker, banner Telegram, rimozione animazioni  
   → Dashboard live si aggiorna automaticamente dopo merge

2. **Deploy worker `orange-truth-2304`**  
   Cloudflare dashboard → Workers → orange-truth-2304 → Quick Editor → incolla:
   ```js
   export default {
     async fetch(request) {
       if (request.method === "OPTIONS")
         return new Response(null, { headers: { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type" } });
       if (request.method === "POST") {
         try { console.log(JSON.stringify(await request.json())); } catch (_) {}
       }
       return new Response("ok", { headers: { "Access-Control-Allow-Origin": "*" } });
     },
   };
   ```

### Media priorità — da completare prima della consegna K-Sport

3. **Aggiungere email squadre K-Sport a Cloudflare Access**  
   Zero Trust → Access → Applications → OB1 Global → K-Sport Pilot policy → aggiungi email

4. **Test end-to-end accesso K-Sport**  
   Simulare login da email K-Sport: aprire `https://ob1global.matchanalysispro.online`, inserire email, ricevere OTP, verificare accesso dashboard

### Bassa priorità — post go-live

5. **Cloudflare Worker Analytics**  
   Abilitare Logpush o D1 per persistere i ping tracker (ora vanno solo in console.log)

6. **OB1 Lega Pro — seconda applicazione**  
   Stessa architettura, policy email separata, URL `ob1legapro.matchanalysispro.online`

---

## 5. Cosa NON è stato toccato (FREEZE)

Per documentazione e tracciabilità: i seguenti componenti sono invariati rispetto al baseline del pilota:

- `scorer.py` — pesi HOT/WARM/COLD, formula scoring
- `scraper.py` — 20 query, nessuna aggiunta/modifica
- `anomaly_pipeline.py` — logica deduplicazione
- Prompt Gemini — rubrica e criteri invariati
- Filtri pre-scoring (età, lega, categoria)
- Backend LLM (Gemini 2.5 Flash resta Gemini)

Riferimento: commit `4250505` (revert) + policy CLAUDE.md.

---

## 6. Valutazione debug pre-consegna

| Area | Pronto? |
|---|---|
| Core scoring pipeline | ✅ Sì |
| Dashboard funzionale | ✅ Sì (dopo merge PR #7) |
| URL pubblico e HTTPS | ✅ Sì |
| Accesso protetto (login) | ✅ Policy pronta, email K-Sport da aggiungere |
| Monitoring accessi | ⚠️ Parziale (worker da deployare) |
| Banner Telegram visibile | ⚠️ Dopo merge PR #7 |
| Readability | ⚠️ Dopo merge PR #7 |

**Giudizio complessivo:** il prodotto è consegnabile dopo il merge della PR #7 e l'aggiunta delle email K-Sport alla policy Access. Il worker tracker è nice-to-have, non bloccante.

---

*Documento interno — non includere in comunicazioni esterne.*
