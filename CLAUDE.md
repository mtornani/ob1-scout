# OB1 Scout — Istruzioni per sessioni Claude

## FASE B — Costruzione v2 (dal 10 luglio 2026)

Il pilota K-Sport non è mai partito formalmente: nessun test contrattuale,
nessun vincolo. Il "FREEZE PILOTA K-SPORT" era una disciplina auto-imposta,
non un obbligo verso terzi, e **da ora non è più in vigore**. Siamo in Fase B:
si costruisce la v2 (vedi `FASE_B.md`) con mano libera.

Restano però alcune regole di buon senso, per non farsi male:

1. **Non rompere il sistema che gira.** La pipeline attuale su `main` continua
   a girare 4 volte/giorno e ci serve come fonte di dati durante la
   transizione. La v2 si costruisce **di fianco** (nuovi file, nuovo DB
   `data/ob1_v2.db`), senza toccare `anomalies` / `ob1_global.db` finché la v2
   non è pronta a sostituire il vecchio. Se un cambio rischia di fermare la
   pipeline in produzione, FERMATI e segnalalo.

2. **I cambi allo scoring/ranking si validano coi dati, non a intuito.** Pesi,
   soglie, rubriche e gate di pubblicazione si possono cambiare — ma un cambio
   diventa default solo dopo averlo confrontato con i dati reali (outcome
   tracciati, distribuzione, casi noti). Niente ritarature "a naso" spinte
   direttamente in produzione.

3. **Non ricostruire file da snapshot, system-reminder o branch alternativi
   senza chiedere.** Se serve un `git reset` o un ripristino, FERMATI e chiedi
   prima.

4. **Sulle scelte di prodotto ambigue, presenta opzioni, non decidere da
   solo.** Esempi aperti (vedi `FASE_B.md` §"Le scelte che restano tue"):
   scope di genere, aree geografiche prioritarie, rapporto con K-Sport.

5. **Prima di committare, confronta il git diff con quello che ti aspetti** e
   segnala esplicitamente qualsiasi modifica fuori da quanto concordato.

6. **Sviluppo sul branch designato** (`claude/setup-daas-platform-PXYST` salvo
   diversa indicazione), commit chiari, push a fine lavoro. `main` si tocca
   solo quando la v2 è pronta e concordata.

## Cos'è OB1

Radar di scouting calcistico: scandaglia fonti pubbliche gratuite, usa un LLM
per identificare giovani talenti ad alta asimmetria informativa (valore reale
> visibilità mediatica), traccia i giocatori nel tempo e misura l'anticipo sul
mainstream. Infrastruttura: GitHub Actions (cron), scraper gratuiti
(DuckDuckGo/SearXNG/Jina Reader), Gemini per l'analisi, SQLite, dashboard
statica su GitHub Pages.

I file storici (`src/intelligence.py`, `src/scraper_global.py`,
`src/enricher.py`, `src/database.py`, `scripts/run_pipeline.py`) contengono
ancora banner "FREEZE PILOTA K-SPORT" in testa: sono obsoleti e vanno rimossi
man mano che si tocca ciascun file in Fase B.

## Riferimento

`FASE_B.md` — audit dei 5 mesi di produzione e design completo della v2
(le quattro debolezze, i quattro pilastri, la roadmap B0→B3).
