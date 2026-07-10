# OB1 v2 — Audit Fase A e Design Fase B

**Data:** 10 luglio 2026 · **Stato:** bozza di lavoro interna
**Scopo:** base decisionale per la Fase B. Audit onesto dei 5 mesi di produzione, diagnosi
architetturale, design della v2 e definizione del prodotto da rendere irresistibile per i club.

---

## 0. Contesto

- Pilota K-Sport attivo da fine maggio 2026 in modalità AS-IS (freeze su scoring/pipeline).
- K-Sport al momento silente; la Fase B si prepara ora, in parallelo, **senza toccare il
  sistema in produzione** finché il pilota non viene chiuso formalmente.
- Fonte dei dati di questo audit: `data/ob1_global.db` di produzione al 10/07/2026
  (67 giocatori, rilevamenti da febbraio a luglio 2026).

---

## 1. Audit della produzione (feb–lug 2026)

### 1.1 Cosa funziona — e va tenuto

| Aspetto | Evidenza |
|---|---|
| Costo infrastruttura | ~€0/mese: GitHub Actions + scraper gratuiti (DDG/SearXNG/Jina) + SQLite + dashboard statica |
| Affidabilità meccanica | 4 run/giorno da mesi; il tracking multi-rilevamento funziona (38/67 giocatori visti ≥2 volte, punte di 79 rilevamenti) |
| Costo LLM | 1 chiamata Gemini/run = 4/giorno → free tier con margine 5x (fix giugno 2026) |
| Monitoring | admin_alert su Telegram + sanity check (4 controlli, incluso il rilevamento dei fallimenti silenziosi Gemini) |
| Deep-dive manuale | Il dossier Callegari (giugno 2026) ha dimostrato che con le stesse fonti gratuite si produce un documento di livello professionale in ~1 ora di lavoro assistito |

La tecnologia "noiosa" (cron, SQLite, pagine statiche) **non è il problema** e resta la base giusta.

### 1.2 Finding 1 — Il KPI centrale non sta misurando nulla di affidabile

Il lead time ("quanti giorni prima del mainstream OB1 ha segnalato il giocatore") è la
metrica che dovrebbe dimostrare la tesi del prodotto. Stato reale:

- **24 tracking completati su 33 hanno lead time = 0 giorni** → nessun vantaggio informativo
  dimostrato in quei casi.
- Il matching mainstream produce **falsi positivi documentati**: per André Maia la "scoperta
  mainstream" è un articolo **UFC/MMA** di ESPN; per Pirituba un articolo **BBC del 2013**.
  Con nomi a token singolo ("Felipe", "Rayan", "Mora") il match scatta su qualsiasi testo.
- I lead time positivi (91gg ×3, 73, 17, 9, 4, 1) includono match su pagine-profilo
  (L'Équipe fiche, Goal player page), che non sono "hype mainstream" in senso proprio.

**Conclusione:** o le detection non sono asimmetriche, o lo strumento che misura
l'asimmetria è rotto. In entrambi i casi, oggi **non abbiamo una prova difendibile del
valore del prodotto** — e questa è la cosa più urgente da sistemare in v2.

### 1.3 Finding 2 — Il "radar globale" è di fatto un radar brasiliano

- Brazil: **39/67 (58%)**. Africa: **0** nella top 8 regioni. Giappone/Corea: **0**.
- 8 delle 20 query di scraping riguardano Africa e Asia, ma non producono quasi nulla che
  superi i filtri: il recall delle query generiche su motori di ricerca è cieco e
  sbilanciato verso ciò che l'SEO fa galleggiare (media brasiliani).

### 1.4 Finding 3 — Identità dei giocatori debole

- **24/67 nomi a token singolo**, tra cui handle social ("Cauazinn_.08"), sigle ("KG9"),
  concatenazioni ("Gustavogoes"), soprannomi ("Sorriso", "Pirituba").
- Nomi non verificabili → non deduplicabili, non cercabili su Transfermarkt, inservibili
  per un DS che deve alzare il telefono.
- Presenza non intenzionale di calcio femminile ("Dulce Maria", "Tainá", "Clarinha" —
  Corinthians U20 femminile): lo scope di genere non è mai stato definito né filtrato.

### 1.5 Finding 4 — Lo score premia l'evidenza più debole

I tre giocatori a **score 100** in cima alla dashboard (Pirituba, Dulce Maria, Dinics) sono
tutti **ghost, visti 1 sola volta, fonte singola**. Intanto Bruno Baldini — corroborato
**79 volte** — sta sotto di loro (89), e André Maia (16 rilevamenti) a 95.

Meccanismo: il ghost bonus (+8) si somma a score già alti da singolo snippet, e la
corroborazione ripetuta non pesa. Risultato: **in cima alla dashboard c'è l'evidenza più
fragile**. Per un club che apre la dashboard la prima volta, i primi 3 nomi sono i meno
difendibili — l'opposto di "irresistibile".

---

## 2. Diagnosi architetturale

L'idraulica (scraper, cron, storage, dashboard) è appropriata. Le debolezze sono
nell'**architettura dell'informazione** — tre assenze strutturali:

1. **Query-first invece che source-first.** 20 query fisse su motori generalisti: il
   sistema vede ciò che l'SEO promuove, non ciò che conta. Copertura cieca (Finding 2).
2. **Articolo-centrico invece che entità-centrico.** Un giocatore è "un nome in un
   articolo": niente accumulo di evidenze, identità fragili, score da singolo snippet
   (Finding 3 e 4).
3. **Nessun ciclo di feedback funzionante.** Il lead time è misurato con match laschi su
   nomi deboli (Finding 1): il sistema non sa se le sue detection erano buone, quindi non
   può migliorare né dimostrare il proprio valore.

---

## 3. Design v2 — quattro pilastri

Principio guida (in linea con la filosofia Karpathy già adottata): **codice per tutto ciò
che è deterministico, LLM solo dove serve giudizio, il più tardi possibile, il meno
possibile.** Il costo LLM resta nell'ordine del free tier / centesimi.

### P1 — Discovery source-first
- **Registro curato di fonti primarie per regione** (referti e comunicati di federazioni,
  campionati giovanili, testate locali affidabili), monitorate **a delta**: si riprocessa
  solo ciò che è nuovo/cambiato.
- La ricerca generica (DDG/SearXNG) resta ma cambia ruolo: serve a **scoprire fonti
  nuove** da promuovere nel registro, non a scoprire giocatori a ogni run.
- Copertura misurabile per regione: se l'Africa non produce, si vede subito *quale fonte
  manca*, invece di sperare in una query.

### P2 — Store entità-centrico con soglia di corroborazione
- Un giocatore è un'**entità** che accumula evidenze (fonte, data, testo, dati estratti).
- **Gate di pubblicazione**: un giocatore entra in dashboard solo con identità risolta
  (nome completo + club + età) **e ≥2 fonti indipendenti** (o 1 fonte primaria).
- L'LLM **estrae** dalle singole fonti (nome, età, club, stat, contesto); il **codice**
  aggrega, corrobora e decide. Lo score cresce con l'evidenza, non nonostante l'evidenza —
  risolve il Finding 4 alla radice.
- Scope di genere esplicito in estrazione (campo `gender`), con filtro configurabile.

### P3 — Feedback loop riparato
- Match mainstream **solo su identità forti** (nome completo + club), mai su token singoli.
- Distinzione tra "pagina-profilo esistente" e "copertura mainstream" (hype reale).
- Tracciamento outcome verificabili: trasferimento, convocazione, esordio in prima
  squadra. Questi diventano la **ground truth** con cui calibrare lo scoring tra una fase
  e l'altra — mai al volo, sempre tra fasi dichiarate.

### P4 — Deep-dive agentico on demand
- Per i candidati sopra soglia (o su richiesta del club), un agente esegue il **dossier
  automatico multi-fonte** — ricerca mirata, lettura integrale via Jina, cross-check,
  documento condivisibile. Il prototipo esiste già: è il processo del dossier Callegari,
  da processo manuale a prodotto.
- Strumenti già in repo da riusare come base: `scripts/player_lookup.py` (ricerca ad-hoc),
  `scripts/compare_llm.py` (validazione modelli a parità di prompt).

---

## 4. Il prodotto irresistibile (cosa vede il club)

La v2 non è un refactoring: è ciò che rende possibile **quattro promesse verificabili**.

1. **Ogni nome ha le prove.** Niente più nomi nudi: ogni giocatore in dashboard mostra le
   ≥2 fonti linkate, età, club, e il perché in linguaggio da DS. Un nome non difendibile
   non entra.
2. **Dossier in minuti, non in giorni.** Il club chiede di un giocatore (qualsiasi, anche
   fuori radar) → dossier verificato multi-fonte entro minuti. È il servizio che oggi un
   club paga giorni di lavoro di uno scout. (Demo già fatta: Callegari.)
3. **Lead time con le ricevute.** "OB1 ha segnalato X il giorno D₁; la prima copertura
   mainstream è del giorno D₂: ecco i link." Verificabile da chiunque — è la prova della
   tesi dell'asimmetria, misurata in modo difendibile.
4. **Digest settimanale profilato.** Ogni club riceve solo ciò che è rilevante per il suo
   profilo (ruoli cercati, budget, regioni), non un feed indistinto.

Il punto di vendita non è "abbiamo l'AI": è **"ogni nome che ti diamo regge una telefonata
di verifica"** — l'opposto esatto dei Finding 1/3/4 di oggi.

---

## 5. Piano di migrazione

Vincolo: il pilota resta AS-IS su `main` finché non viene chiuso formalmente (comunicazione
a K-Sport). La v2 si costruisce in parallelo, senza rischi per la produzione.

| Fase | Contenuto | Riuso |
|---|---|---|
| **B0 — Fondamenta** | Schema entità (players / evidences / outcomes), migrazione dei 67 esistenti con flag di qualità identità | SQLite, dati attuali |
| **B1 — Estrazione** | LLM come estrattore puro per-fonte (JSON tipizzato, incl. genere); aggregazione e gate ≥2 fonti in codice | `scraper_global.py`, Jina, prompt attuale come base della rubrica |
| **B2 — Sorgenti** | Registro fonti curate per 2-3 regioni prioritarie + monitoraggio a delta; ricerca generica declassata a scoperta-fonti | scraper attuale per la scoperta-fonti |
| **B3 — Prova del valore** | Mainstream check su identità forti, outcome tracking, dashboard "con le ricevute"; dossier on-demand automatizzato | `player_lookup.py`, formato dossier Callegari, `notifier.py`, dashboard |

Ordine pensato per il rischio: B0/B1 sono il cuore (risolvono Finding 3+4), B2 allarga la
copertura (Finding 2), B3 costruisce la prova di valore (Finding 1) — ma il gate ≥2 fonti
migliora la dashboard già da B1.

---

## 6. Metriche di successo della v2

| Metrica | Oggi (Fase A) | Target v2 |
|---|---|---|
| Identità complete in dashboard (nome pieno + club + età) | ~64% | **100%** (gate) |
| Giocatori con ≥2 fonti indipendenti | 57% (38/67) | **100%** (gate) |
| Lead time mediano misurato in modo difendibile | non misurabile (KPI rotto) | **> 0 e verificabile con link** |
| Regioni con copertura attiva | di fatto 1 (Brasile) | ≥ 3 con fonti primarie |
| Falsi match mainstream | documentati (UFC, BBC 2013) | ~0 (match su identità forti) |
| Tempo dossier su richiesta | ~1h assistita (manuale) | **minuti** (automatizzato) |
| Costo infrastruttura | ~€0 | ~€0 (invariato per design) |

---

## 7. Decisioni aperte (da confermare prima di B1)

1. **Scope di genere**: solo maschile, solo femminile, o entrambi con filtro per club?
   (Oggi il femminile entra per caso — va reso una scelta.)
2. **Regioni prioritarie per B2**: proposta Sud America + Africa Ovest + una wildcard, da
   validare anche in ottica interesse club.
3. **Chiusura formale del pilota**: prima di sbloccare `main`, un ping a K-Sport per
   chiudere la Fase A in modo pulito (anche solo per documentare l'esito del test).
4. **Soglie/rubrica scoring v2**: si ricalibrano una volta sola all'avvio della Fase B,
   con i dati outcome come riferimento — poi si ri-congela (la disciplina del freeze ha
   funzionato: si tiene, ma con KPI misurabili).

---

*Documento generato a partire dall'audit del DB di produzione del 10/07/2026. I numeri
citati sono riproducibili con query dirette su `data/ob1_global.db`.*
