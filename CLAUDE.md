# OB1 Scout — Istruzioni per sessioni Claude

## REGOLE PILOTA K-SPORT (in vigore fino a fine pilota ~settembre 2026)

Questo repo è in produzione AS-IS per un test pilota di 3 mesi
presso ~5 squadre selezionate dal partner K-Sport. Il sistema
viene valutato così come è. Qualsiasi modifica al comportamento
del sistema durante il pilota invalida il test.

REGOLE OBBLIGATORIE per qualsiasi sessione di lavoro su questo
repo durante il pilota:

1. PRIMA di applicare qualsiasi modifica, leggi il commento
   "FREEZE PILOTA K-SPORT" presente in testa ai file di scoring.
   Se la tua modifica tocca uno di questi file, FERMATI e
   chiedi conferma esplicita all'utente.

2. NON ricostruire file da snapshot, system-reminder, o branch
   alternativi senza chiedere. Il baseline è lo stato di main
   al momento dell'inizio del task. Se hai bisogno di git reset
   durante l'esecuzione, FERMATI e chiedi prima di procedere.

3. NON aggiungere features non richieste, anche se sembrano
   miglioramenti tecnici evidenti. Il pilota è AS-IS:
   miglioramenti possibili vanno discussi in Fase B post-pilota
   con il partner.

4. Modifiche LEGITTIME durante il pilota sono SOLO:
   - Monitoring, alerting, sanity checks
   - Bug fix esplicitamente richiesti
   - UX/display senza impatto sull'output di scoring
   - Tracciabilità accessi utenti
   - Documentazione (README, METRICS.md interno)

5. Modifiche VIETATE durante il pilota:
   - Pesi e soglie di scoring (HOT/WARM/COLD)
   - Formule di scoring
   - Rubriche di scoring nei prompt LLM
   - Query dello scraper (lista, contenuto, numero)
   - Filtri pre-scoring (età, lega, categoria)
   - Backend LLM (Gemini resta Gemini)
   - Logiche di deduplicazione che incidono sullo scoring

6. SE incontri una situazione ambigua o non documentata,
   presenta opzioni, NON decidere autonomamente.

7. AL TERMINE di ogni task: confronta git diff con il baseline
   atteso e segnala esplicitamente modifiche fuori perimetro
   PRIMA del commit.

Riferimento incidente: commit 4250505 (revert) ha ripristinato
modifiche fuori perimetro introdotte in 630e286. Questa policy
nasce per evitare il ripetersi.
