# OB1 — Architettura di produzione a scala (Fase C)

**2 agosto 2026**

La v2 gira in produzione da tre settimane e funziona: source-first, gate a due fonti,
corroborazione attiva. Ma l'architettura che ha in pancia è quella di uno sviluppo, non di un
prodotto che deve coprire il mondo. Questo documento dice dove si rompe, quanto in là si può
arrivare **senza pagare un euro di API**, e come ci si arriva senza fermare la pipeline.

---

## In una riga

Oggi OB1 dipende da un provider (Gemini) con un budget deciso a mano (15 chiamate a run).
Domani deve dipendere da **una quota gratuita aggregata**, contabilizzata, distribuita su più
provider — e soprattutto deve **spendere molte meno chiamate per lo stesso lavoro**.

---

## Il problema non è il prezzo. È la quota.

Vale la pena dirlo chiaro perché cambia tutte le decisioni a valle: **oggi non stiamo pagando
nulla.** Gemini free tier costa zero euro. Il problema è che il free tier non è "poco costoso",
è **contingentato**: circa 250 richieste al giorno su `gemini-2.5-flash`, 10 al minuto. Passare
a pagamento costerebbe poco in assoluto (l'estrazione è un compito piccolo), ma introdurrebbe
la cosa che finora ha reso OB1 sostenibile: una bolletta che cresce con l'ambizione.

Quindi la domanda giusta non è "come riduciamo la spesa Gemini" (è zero), ma:

> **quante estrazioni al giorno riusciamo a fare restando dentro i free tier — e quante ce ne
> servono per coprire davvero il mondo?**

I numeri di oggi:

| | oggi | serve per "radar globale" |
|---|---|---|
| fonti monitorate | 18 | 150–250 |
| articoli nuovi/giorno | ~40 | 1.500–2.500 |
| chiamate LLM/giorno | max 60 (15 × 4 run) | 800–1.500 |
| costo | 0 € | 0 € |

Il salto è **25×**. Non lo si fa alzando `INGEST_LLM_BUDGET`: a 250 richieste/giorno il muro
Gemini arriva al quarto run. Lo si fa cambiando tre cose nell'architettura e una nel modo in cui
si spende una chiamata.

---

## Cosa si rompe a scala (i tre vincoli veri)

**1. Il budget è una costante, la quota è uno stato.**
`INGEST_LLM_BUDGET=15` è un numero scritto a mano che non sa niente di quanta capacità gratuita
esista davvero in quel momento. E lo stato "Gemini esaurito" (`self.gemini_exhausted`) vive
**dentro il processo**: su GitHub Actions ogni run parte da un container nuovo, quindi ogni sei
ore ricominciamo a bussare a una porta che sappiamo già chiusa, prendendo 429 finché il budget
del run non si consuma in errori. La quota va **contabilizzata su disco**, non ricordata in RAM.

**2. Scoperta ed estrazione sono nello stesso ciclo.**
In `ingest_v2.py` un articolo viene trovato, letto ed estratto nella stessa iterazione. Quando
il budget finisce, tutto ciò che non è stato estratto **viene semplicemente dimenticato** (non
marcato `seen`, quindi ri-scoperto e ri-letto al run dopo: lavoro di rete rifatto da capo). A
40 articoli/giorno si sopporta; a 2.000 no. Fetch e estrazione vanno separati da una **coda
persistita**: la scoperta è gratis e illimitata, l'estrazione è la risorsa scarsa.

**3. Un solo scrittore: il DB SQLite committato in git.**
È stata una scelta giusta (zero infrastruttura, storia versionata, dashboard statica). Ma
impedisce di parallelizzare: due job che scrivono `data/ob1_v2.db` producono un conflitto
binario irrisolvibile. Finché la pipeline è un job da 15 chiamate va bene; per fare 1.500
estrazioni al giorno servono job paralleli.

E poi il punto che vale più di tutti gli altri messi insieme:

**4. Spendiamo chiamate su testo che non contiene giocatori.**
Oggi ogni URL trovato va al modello con 6.000 caratteri, senza chiedersi prima se dentro c'è
un giovane. Spot-check su quattro fonti vere del registro (pagine lette via Jina, oggi):

| fonte | char grezzi | dopo pulizia | dopo condensazione | il prefiltro la manda al modello? |
|---|---|---|---|---|
| ge.globo.com/futebol/times | 58.634 | 962 | — | **no** (nessun segnale giovanile) |
| promiedos.com.ar | 19.045 | 396 | — | **no** |
| thenff.com | 42.964 | 10.617 | 2.498 | sì |
| ghanafa.org | 109.571 | 71.675 | 2.496 | sì |

Su queste quattro pagine, oggi spenderemmo 4 chiamate da ~1.500 token (6.000 char). Con
prefiltro + condensazione: 2 chiamate da ~650 token.

### La misura vera (3 agosto 2026, 17 pagine del nostro storico)

Le quattro pagine sopra erano un indizio. La misura l'ho fatta sul corpus giusto: gli articoli
che la pipeline **ha già processato**, di cui sappiamo l'esito — 11 che avevano prodotto un
giocatore poi diventato `identity_complete`, 6 viste senza esito, riscaricate oggi via Jina.

| | oggi (`text[:6000]`) | con prefiltro + condensazione |
|---|---|---|
| chiamate | 17 | 14 |
| token inviati | ~14.650 | ~4.050 |
| | | **−73%** |

Due cose che questa misura dice, e che vanno dette anche se scomode:

**Il risparmio viene dalla condensazione, non dallo scarto.** Delle 6 pagine "viste senza
esito" il prefiltro ne ha tenute 6 su 6: sono pagine di federazione piene di "sub-17" e di nomi
propri, che *sembrano* rilevanti e non lo sono. L'ipotesi che il prefiltro scartasse metà del
lavoro **non regge**: scarta poco, ma taglia i tre quarti dei token di ciò che tiene. Il calcolo
del tetto più sotto va letto con questo numero, non con quello che avevo stimato.

**Ha tre falsi negativi, tutti social.** Instagram e TikTok (454, 563, 2.060 char scaricati)
vengono scartati come "nessun segnale giovanile" — ma erano pagine che in passato avevano
prodotto profili verificati. Oggi Jina da quegli URL non riporta più contenuto utile, quindi la
chiamata sarebbe sprecata comunque; resta che **la regola del prefiltro non può valere per i
social**, che vanno trattati a parte (o tenuti sempre) prima di attivarlo in produzione. È
esattamente il tipo di cosa che il criterio di uscita di C1 deve bloccare.

---

## L'architettura

```
  fonti (config/sources.json)
        │
        ▼
  [1] DISCOVERY            ← gratis, nessun LLM: link nuovi per fonte (delta)
        │
        ▼
  [2] FETCH (Jina)         ← gratis, parallelizzabile
        │
        ▼
  [3] PREFILTRO in codice  ← scarta ciò che non contiene giovani  ─┐
        │                     + condensa il testo che resta        │ qui si
        ▼                                                          │ salva
  [4] CACHE per fingerprint ← contenuto già estratto = zero costo ─┘ la quota
        │
        ▼
  [5] CODA di estrazione (persistita, con priorità)
        │
        ▼
  [6] POOL LLM + LEDGER DI QUOTA
        ├── classe fast     → Cerebras, Groq, (locale)
        ├── classe mid      → Groq, Gemini, OpenRouter
        └── classe frontier → Gemini, GitHub Models   ← riserva: identità, dossier
        │
        ▼
  [7] entità + gate + scoring (invariati: codice puro)
```

### 1. La quota diventa una risorsa contabilizzata

`config/llm_providers.json` è il registro dei provider (limiti dichiarati, classi di compito,
priorità, note sui ToS). `src/llm_pool_v2.py` è il router: sceglie a **ogni chiamata** chi ha
quota residua per quella classe, rispetta gli RPM, e scrive tutto in un ledger persistito
(`data/llm_ledger.json`, da committare col `git add -f` come già si fa per il DB, dato che
`.gitignore` esclude `/data/`).

Tre conseguenze concrete:

- un 429 "per day" mette il provider in cooldown **fino a mezzanotte UTC**, e il cooldown
  sopravvive alla fine del run: il run successivo non ci sbatte più contro;
- un 429 "per minute" costa 120 secondi, non la giornata: oggi li trattiamo uguali e buttiamo
  via capacità buona;
- il budget di un run **non si dichiara più**: si chiede al pool con `capacity()`, che risponde
  guardando cosa resta davvero. `INGEST_LLM_BUDGET` diventa un tetto di sicurezza opzionale,
  non il numero che comanda.

### 2. Il modello di frontiera smette di fare il lavoro di massa

Tre classi di compito, tre livelli di modello:

| classe | compito | chi lo serve |
|---|---|---|
| `fast` | estrazione tipizzata da testo già condensato | Cerebras (1M token/giorno), Groq, modello locale |
| `mid` | testo lungo, articoli ambigui, lingue difficili | Groq 70B, Gemini, OpenRouter |
| `frontier` | matching identità in corroborazione, dossier on-demand, arbitrato sui casi contesi | Gemini, GitHub Models |

Il punto non è "usare modelli peggiori per risparmiare". È che **l'estrazione tipizzata da un
paragrafo già selezionato è un compito facile**: nome, età, club, citazione. Il modello di
frontiera serve dove la decisione è difficile — capire se il "Kauan" di questo articolo è lo
stesso "Kauan Ribeiro" che abbiamo in DB. Quella è la chiamata che vale la quota buona.

Regola d'ingaggio (rispetta la regola 2 del CLAUDE.md): **un provider entra in classe `fast`
solo dopo un confronto con `compare_llm.py` sugli stessi input**, non perché è capiente.

### 3. Fan-out / fan-in: parallelizzare senza rompere il DB

Il DB in git resta, ma smette di essere ciò che i worker scrivono:

```
  worker shard A (Sud America)  ─┐
  worker shard B (Africa)       ─┤→  data/inbox/<shard>-<run_id>.jsonl   (append-only, no conflitti)
  worker shard C (Balcani)      ─┘
                                        │
                                        ▼
                              reducer (job unico, seriale)
                                        │
                                 data/ob1_v2.db  +  docs/data/players_v2.json
```

Ogni worker è un job della matrix con **la sua chiave provider e la sua fetta di ledger**
(`data/ledger/<shard>.json`); scrive osservazioni grezze in un JSONL sotto un path suo, quindi
due worker non toccano mai lo stesso file. Un job reducer — uno solo, `concurrency: radar-v2`
come oggi — ingerisce gli inbox in ordine, applica entità/gate/scoring, esporta la dashboard e
committa. La scala orizzontale arriva senza cambiare né lo storage né il modello di dati, e
senza introdurre un servizio da mantenere.

Quando questo non basterà più (indicativamente oltre ~5.000 osservazioni/giorno, o quando il
`.db` in git supererà qualche decina di MB), l'evoluzione naturale è un Postgres/Turso gestito
su free tier — ma è un problema che non abbiamo, e non va risolto oggi.

### 4. Cache per impronta del contenuto

Gli aggregatori ripubblicano lo stesso pezzo. Le homepage cambiano di poco tra un run e
l'altro. `content_fingerprint()` (in `src/prefilter_v2.py`) normalizza il testo e ne fa
un'impronta: se l'abbiamo già estratto, l'estrazione si riusa a costo zero. Su fonti di tipo
`aggregator` — 5 delle 18 attuali — è il risparmio più grosso e più stupido da prendere.

### 5. Osservabilità: senza numeri non si tara

Tabella `llm_usage` (provider, modello, classe, token in/out, esito, latenza, url) ed export in
`docs/data/llm_health.json`. Serve a tre cose: sapere quanta capacità gratuita ci resta davvero,
scoprire quale provider produce estrazioni che il validatore scarta (e retrocederlo), e
riconoscere il giorno in cui un free tier cambia in silenzio — perché succederà.

---

## Cosa hanno detto le API vere (3 agosto 2026)

Tutto il resto di questo documento nasce da numeri dichiarati. Questa sezione no: sono le
risposte delle API con le nostre chiavi, e correggono tre cose.

**1. Il catalogo cambia sotto i piedi.** Il modello Cerebras che avevo messo come default
(`llama-3.3-70b`) non esiste più: `/v1/models` oggi risponde `gemma-4-31b`, `zai-glm-4.7`,
`gpt-oss-120b`. Un modello sbagliato non degrada, **fallisce 404 e brucia un anello della
catena**. È la ragione per cui il registro deve restare configurazione e non codice.

**2. Il free tier Cerebras non è attivo sul nostro account.** Ogni modello risponde
`402 payment_required — Visit your billing tab`. Il milione di token al giorno su cui poggia
metà del calcolo di capacità **oggi non ce l'abbiamo**: va sbloccato dal pannello Cerebras
prima di contarci.

**3. La chiave Groq è già satura, e il limite è a finestra scorrevole.** Il 429 dice:
`tokens per day (TPD): Limit 100000, Used 99573`. Non è un caso: la pipeline attuale manda
6.000 caratteri per articolo (~2.000 token) e gira quattro volte al giorno — **consuma da sola
l'intero tetto giornaliero gratuito di Groq**. Ed è un tetto che si riapre a gocce: lo stesso
errore suggeriva "riprova tra 3.456s" in un caso e "tra 1h2m30s" in un altro. Trattarlo come
"finito per oggi", che è ciò che il codice faceva, buttava via un giorno intero di capacità —
e in `free_first` avrebbe spinto il lavoro su Gemini, cioè sulla fattura. Ora il cooldown segue
il suggerimento del provider e la catena aspetta e riprova quando l'attesa è di secondi.

La conseguenza pratica è che **la condensazione non è un'ottimizzazione, è la condizione per
restare a costo zero**: a ~300 token di input invece di ~2.000, gli stessi 100k/giorno di Groq
passano da ~50 estrazioni a oltre 150.

## Il tetto reale a costo zero

Con estrazione condensata (~2.500 char in, ~300 token out ≈ **1.000 token per chiamata**):

| provider | quota gratuita dichiarata (ago 2026) | estrazioni/giorno | classe |
|---|---|---|---|
| Cerebras | 1.000.000 token/giorno, 30 RPM | ~1.000 | fast |
| Groq | 100.000 token/giorno, 1.000 RPD, 12k TPM | ~100 | fast / mid |
| Gemini 2.5 Flash | 250 richieste/giorno, 10 RPM | ~250 | mid / frontier |
| OpenRouter (:free) | 50 richieste/giorno (20 RPM) | ~50 | mid |
| GitHub Models | ~50 richieste/giorno | ~50 | frontier |
| **totale** | | **~1.450/giorno** | |

Con un prefiltro che scarta anche solo metà delle pagine, 1.450 estrazioni coprono **~2.900
articoli valutati al giorno**, cioè **250–300 fonti** a dieci pezzi nuovi al giorno l'una. È
sopra il target del radar globale. **La conclusione è che la scala che vogliamo sta dentro i
free tier — a patto di non sprecarli.**

Due avvertenze oneste:

- **Questi numeri hanno la data sopra.** I free tier cambiano senza preavviso e le fonti
  pubbliche si contraddicono (per Gemini si leggono 250 e 1.500 RPD a seconda di chi scrive).
  Per questo il registro è dichiarativo e il **ledger è la fonte di verità**: se un provider
  dice 429, quello vale, non il JSON.
- **I minuti di GitHub Actions sono l'altro tetto.** Se il repo è pubblico sono illimitati e non
  ci pensiamo; se è privato sono 2.000 al mese, e a quel punto il vincolo diventa quello, non i
  token. Da verificare prima di moltiplicare gli shard.

### Quello che non facciamo

- **Niente account multipli sullo stesso provider** per moltiplicare i free tier. Lo vietano
  praticamente tutti i ToS, e un prodotto che vende affidabilità non può poggiare su una
  violazione che può sparire in un giorno. La capacità si aggiunge cambiando provider, non
  identità.
- **Niente Cohere free tier**: è esplicitamente non-commercial.
- **Niente Mistral "Experiment"**: 1 miliardo di token al mese è golosissimo, ma richiede
  l'opt-in all'addestramento sui dati inviati. Noi inviamo testo di editori terzi: non è nostro
  da regalare.
- **Niente fine-tuning o modelli custom.** L'estrazione tipizzata non ne ha bisogno e ci
  legherebbe a un'infrastruttura da mantenere.

---

## Migrazione: quattro passi, produzione sempre viva

La pipeline v2 gira ogni 6 ore da `main`. Nessuno di questi passi la ferma; ognuno ha un criterio
di uscita misurabile e un modo per tornare indietro.

**C0 — Catena gratuita prima di Gemini** *(fatto)*
`src/llm_free_chain.py` + `src/extractor_v2.py` in modalità `free_first`: i
provider gratuiti vengono provati per primi, Gemini resta rete di sicurezza (o
niente, con `free_only`). Risolve il problema immediato — il billing Google —
senza aspettare il resto della Fase C. È la versione tattica di ciò che
`llm_pool_v2.py` farà in modo contabilizzato: quando il pool entrerà in
produzione (C3), la catena diventerà un suo caso particolare.

**C1 — Prefiltro e pool, in ombra** *(questo commit)*
`src/prefilter_v2.py` e `src/llm_pool_v2.py` esistono, sono testati offline, e **non sono
collegati a `ingest_v2.py`**: la produzione è byte-per-byte quella di prima. Il passo successivo
è farli girare in *shadow mode* — il prefiltro calcola il verdetto e lo logga, ma l'estrazione
avviene comunque come oggi — per una settimana.
*Criterio di uscita:* su ~200 articoli reali, il prefiltro non scarta nessun articolo da cui
l'estrattore aveva tirato fuori un giocatore poi diventato `identity_complete`. Zero falsi
negativi utili, o si allentano le soglie prima di attivarlo.

**C2 — Prefiltro attivo + condensazione**
L'estrattore riceve testo condensato e i pezzi senza segnale non partono. Rischio basso e
reversibile con un flag.
*Criterio di uscita:* a parità di articoli scoperti, chiamate/giorno in calo ≥50% e osservazioni
utili per chiamata **non** in calo.

**C3 — Pool attivo, budget dal ledger**
`ingest_v2.py` chiede il budget a `capacity()` invece che a `INGEST_LLM_BUDGET`, e chiama via
pool. Gemini scende a classe `mid`/`frontier`. Da fare solo dopo che `compare_llm.py` ha
confrontato Cerebras/Groq contro Gemini sugli stessi input.
*Criterio di uscita:* qualità dell'estrazione (osservazioni valide / scartate dal validatore)
entro il 10% del baseline Gemini; chiamate/giorno ≥300 senza un solo 429 ripetuto.

**C4 — Coda persistita e shard**
Tabella `extraction_queue` + fan-out/fan-in con inbox JSONL e reducer unico. È il passo che
tocca l'orchestrazione, quindi va per ultimo e con la v2 attuale ancora eseguibile in un job
solo (feature flag `RADAR_SHARDS=1`).
*Criterio di uscita:* un run con 3 shard produce lo stesso DB di un run singolo con lo stesso
input, e nessun conflitto git in una settimana.

---

## Cosa c'è già in questo commit

| file | cosa fa | stato |
|---|---|---|
| `config/llm_providers.json` | registro provider: limiti, classi, priorità, note ToS | nuovo, dichiarativo |
| `src/llm_pool_v2.py` | router multi-provider + ledger di quota persistito + classificazione errori | nuovo, self-test offline |
| `src/prefilter_v2.py` | verdetto di rilevanza, condensazione, impronta contenuto | nuovo, self-test offline |
| `FASE_C.md` | questo documento | nuovo |

**Non è stato toccato nulla della pipeline in produzione**: `ingest_v2.py`, `extractor_v2.py`,
il workflow e lo schema del DB sono invariati. I due moduli nuovi si eseguono da soli
(`python src/llm_pool_v2.py`, `python src/prefilter_v2.py`) e passano i rispettivi test.

## Le domande aperte, che tocca a te decidere

1. **Repo pubblico o privato?** Cambia se i minuti Actions sono un vincolo (e quindi se il
   modello locale sul runner è capacità gratuita o un modo per finire i minuti).
2. **Quali chiavi apriamo per prime?** Cerebras è quella che sposta i numeri (1M token/giorno);
   GitHub Models è gratis con un token che hai già. Con quelle due il tetto passa da 60 a oltre
   1.000 estrazioni al giorno.
3. **Prima si scala in larghezza o in profondità?** Più fonti (copertura geografica, il buco di
   Fase A) oppure più letture per fonte (meno articoli persi dove già guardiamo). L'architettura
   regge entrambe; l'ordine è una scelta di prodotto.
