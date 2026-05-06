# Recon Lusofono — OB1 Global
**Data:** 2026-05-06  
**Destinatario mandata successiva:** Dossier Sbraccia / Sferico Sports Management  
**Tipo:** Ricognizione pura — nessuna modifica al dataset, nessun nuovo scraping

---

## ⚠️ Finding critico (leggi prima del resto)

OB1 Global **non è un database scouting tradizionale**. Non contiene campi
clausola, contratto, valore di mercato, minuti, piede, agente.
È un sistema di rilevazione anomalia mediatica: scraping → scoring → tracking.

I 6 campi richiesti dal template Sbraccia (CFO/asset/ROI) sono strutturalmente
assenti dallo schema. Questo non è un gap di dati — è un gap di design.
La Mandata 2 deve partire da questa premessa.

---

## Sezione 1 — Conteggi per lega

**Dataset totale: 22 record**

### Leghe lusofone richieste

| Lega | Record trovati | Under-23 | Note |
|---|---|---|---|
| Primeira Liga (PT) | 0 | 0 | Nessun record |
| Liga Portugal 2 (PT) | 0 | 0 | Nessun record |
| Liga 3 (PT) | 0 | 0 | Nessun record |
| Série A (BR) | 0 | 0 | Nessun record senior |
| Série B (BR) | 0 | 0 | Nessun record senior |
| Série C (BR) | 0 | 0 | Nessun record senior |

**Risultato: copertura zero su tutte le leghe senior lusofone.**

### Cosa c'è davvero (Brasil)

| Contesto | Record | U-23 con età nota |
|---|---|---|
| Competizioni giovanili brasiliane (U17/U20/Copinha) | 17 | 8 |
| Libertadores U20 | 2 | 0 (età null) |
| Campionato Carioca (menzione in narrative) | 1 | 0 (età null) |

### Portogallo — unico record

| Giocatore | Club | Lega (come da DB) | Score |
|---|---|---|---|
| Saviolo | Vitória SC | "Liga Portoghese" | 82 |

Saviolo gioca nel Vitória de Guimarães, che milita in Primeira Liga.
Il campo `league` è compilato con un termine generico non standardizzato.

### Altri lusofoni (Angola, Capo Verde)
Nessun record. Nessuna menzione nei campi `region` o `league`.

---

## Sezione 2 — Matrice copertura campi

Campi esistenti nello schema vs. campi necessari per dossier Sbraccia.

### Campi presenti nel DB (population rate su 22 record)

| Campo | Popolato | % | Usabile come filtro primario? |
|---|---|---|---|
| player_name | 22/22 | 100% | ✅ |
| position | 22/22 | 100% | ⚠️ naming inconsistente (vedi §3) |
| region | 22/22 | 100% | ⚠️ granularità bassa ("Brazil" generico) |
| source_url | 22/22 | 100% | — |
| club | 18/22 | 82% | ⚠️ club U20, non club senior |
| league | 20/22 | 91% | ❌ naming non standardizzato |
| stats_summary | 17/22 | 77% | ⚠️ testo grezzo, non strutturato |
| age | 12/22 | 55% | ⚠️ sotto soglia |
| detection_count | 22/22 | 100% | ✅ (proxy confidence) |
| score | 22/22 | 100% | ✅ |

### Campi richiesti dal template — non presenti nello schema

| Campo Sbraccia | Presente in OB1? | Alternativa parziale |
|---|---|---|
| Clausola rescissoria | ❌ | Nessuna |
| Scadenza contratto | ❌ | Nessuna |
| Valore di mercato attuale | ❌ | `score` come proxy asimmetria, non valore |
| Minuti giocati stagione corrente | ❌ | `stats_summary` (testo grezzo, non strutturato) |
| Piede preferito | ❌ | Nessuna |
| Agente / procuratore | ❌ | Nessuna |

---

## Sezione 3 — Anomalie e raccomandazioni per Mandata 2

### Anomalie strutturali

**A. Dataset sotto soglia critica**
- 22 record totali. Zero sui mercati senior lusofoni.
- Soglia < 50 record per lega segnalata come "copertura parziale": ogni
  lega del perimetro Sbraccia è a zero, non a parziale.

**B. Tutti i record brasiliani sono giovanili**
- Il sistema è stato orientato su U17/U20 come proxy anomalia.
- Un profilo come Sbraccia ragiona su giocatori con contratto cedibile,
  clausola esercitabile, minuti professionistici documentati.
  Il dataset attuale non intercetta questo profilo.

**C. League naming: 11 varianti per "giovani brasiliani"**

```
"Brazilian U20 competitions"          (3x)
"Brazilian Youth Leagues"             (1x)
"Brazilian U20 League"                (1x)
"Brazilian U17/U20 competitions"      (1x)
"Brazilian U17 League (lesser-known)" (1x)
"Brazilian League / U20"              (1x)
"Competizioni giovanili brasiliane"   (1x)  ← italiano
"Copa São Paulo de Futebol Júnior (giovanile)" (1x)
"Copa São Paulo de Futebol Júnior (Brazil)"    (1x)
"Copa São Paulo"                      (1x)
```
Impossibile filtrare per lega senza normalizzazione.

**D. Position naming: misto italiano/inglese**
- "Attacker", "Attaccante", "Esterno", "Box-to-box Midfielder"
- Nessun vocabolario controllato. GROUP BY position dà 14 valori unici su 22 record.

**E. Age null al 45%**
- 10 record su 22 senza età.
- Filtro `age ≤ 23` inapplicabile su quasi metà dataset.

**F. Record da rivedere manualmente**
- `Fode Diallo` — 14 anni, Youth League U13. Non è un target scouting.
  (filtro aggiunto al pipeline per run futuri, ma il record è ancora nel DB)
- `Dulce Maria` — probabilmente calcio femminile. Nessun flag nel dataset.
- `Tchola` — portiere, zero campi anagrafici compilati (age/club/league null).

### Raccomandazioni filtri per Mandata 2

1. **Non usare OB1 attuale come fonte dati per Sbraccia.**
   Zero copertura senior lusofona. I record esistenti sono U17/U20 brasiliani
   con campi finanziari strutturalmente assenti.

2. **Se l'obiettivo è costruire un dossier credibile per un CFO:**
   Serve un secondo layer dati (Transfermarkt API, Wyscout export, o
   compilazione manuale) con: valore, clausola, scadenza contratto.
   OB1 può fornire il *timing* (quando il giocatore è emerso) ma non
   il *pricing* (quanto vale/costa).

3. **Se si vuole espandere OB1 verso il perimetro Sbraccia:**
   - Aggiungere query mirate: "Vitória SC jovem destaque", "Sporting CP B
     talento", "Benfica B promessa", "Primeira Liga sub-23 clausola"
   - Aggiungere campo `estimated_value` e `contract_until` nello schema
     (popolati da Transfermarkt enrichment)
   - Normalizzare `league` con vocabolario fisso prima del save

4. **Unico record lusofono sfruttabile oggi:** Saviolo (Vitória SC).
   Ha `stats_summary` popolato, score 82, 10 detection. Utile come
   esempio qualitativo ma non come base statistica.

---

## Sezione 4 — Record esempio per area (ispezione qualità)

### Brasil — giovanili (campione random)

**Record 1 — Bruno Baldini** (score 85, 69 detection)
```
age:      18
position: Defender
club:     Avaí U20
league:   Brazilian U20 competitions
region:   Brazil
stats:    "Player stats of Bruno Baldini (Avaí FC (SC)) ➤ Goals ➤ Assists..."
          [testo grezzo Transfermarkt, non strutturato]
ghost:    No
```

**Record 2 — André Maia** (score 95, 16 detection)
```
age:      16
position: Attacker
club:     null
league:   Copa São Paulo
region:   South America
stats:    [non disponibile]
ghost:    No
notes:    "Miglior marcatore nella storia Copa SP, 30 gol in 24 partite"
          Club attuale non identificato nel dataset.
```

**Record 3 — Ryan Evaristo** (score 86, 47 detection)
```
age:      17
position: Attacker
club:     Corinthians U20
league:   Brazilian U20 competitions
region:   Brazil
stats:    [testo grezzo, non strutturato]
ghost:    Yes
```

### Portogallo — unico record disponibile

**Record 4 — Saviolo** (score 82, 10 detection)
```
age:      null
position: Esterno
club:     Vitória SC
league:   "Liga Portoghese" (non standard — reale: Primeira Liga)
region:   Europe
stats:    "These are the detailed performance data of Vitória Guimarães SC
           player Noah Saviolo. The website contains a statistic ab..."
           [troncato, fonte Transfermarkt]
ghost:    No
```

---

## Sintesi per handoff Mandata 2

| Domanda | Risposta |
|---|---|
| OB1 copre leghe senior lusofone? | No. Zero record. |
| I campi Sbraccia (clausola/contratto/valore) esistono? | No. Assenti dallo schema. |
| Il dataset è filtrabile per età ≤ 23? | Solo su 55% dei record. |
| C'è un record sfruttabile oggi per PT? | 1 (Saviolo, qualitativo). |
| Cosa sa davvero OB1 del mercato lusofono? | Giovanili brasiliani U17/U20, niente senior, niente PT strutturato. |

**Conclusione:** OB1 oggi è un early-warning system, non un database scouting.
Per Sbraccia serve o integrare un layer dati finanziario, o usare OB1
esclusivamente come segnale di timing ("questo giocatore è emerso X giorni
prima della stampa") lasciando il pricing ad altra fonte.
