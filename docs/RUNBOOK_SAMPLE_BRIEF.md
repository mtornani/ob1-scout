# Runbook — sample brief di mercato (schema OB1)

Riferimento: bridge Claude Code ↔ Grok (opzione ibrida). Questo documento
descrive comandi e prerequisiti **del container/ambiente Linux** in cui gira
questo repo — non fa riferimento a nessun path Windows locale.

## Repo attiva

`mtornani/ob1-scout` — file: `scripts/sample_brief.py`

Non è parte della pipeline v2: nessun cron, nessuna scrittura su
`data/ob1_v2.db`. È un tool ad-hoc, stesso spirito di
`scripts/player_lookup.py` e `scripts/dossier_v2.py`.

## Prerequisiti

- Python 3.12
- `pip install -r requirements.txt` (già copre tutto: `aiohttp`, `ddgs`,
  `google-genai`, `requests`)
- Variabili d'ambiente:
  - `GEMINI_API_KEY` — obbligatoria (estrattore primario)
  - `GROQ_API_KEY` — opzionale, fallback gratuito quando Gemini va in quota
    (stessa catena già usata da `src/extractor_v2.py`)
- Nessun altro segreto. Le chiavi restano in `.env` locale o nei secrets del
  runner: non passarle mai in chiaro in chat o nei file condivisi.

## Comando sample (3 profili)

```bash
cd /path/al/repo
python scripts/sample_brief.py \
  --club "Ascoli Calcio 1898 FC" \
  --context "Serie B, neopromossa" \
  --limit 3 \
  --out output/briefs/sample_latest.json
```

Argomenti:
- `--club` (obbligatorio): nome esatto del club, usato nelle query di ricerca
  e nel prompt di estrazione.
- `--context` (opzionale): una riga di contesto (es. "Serie B, neopromossa")
  che finisce in `meta.contesto` nell'output.
- `--limit` (default 3): quanti target **verificati** (≥2 fonti indipendenti)
  includere in `target_verificati`. Il resto finisce in `da_corroborare`.
- `--max-articles` (default 8): quante fonti leggere per intero via Jina
  Reader.
- `--out` (default `output/briefs/sample_latest.json`): `output/` è già in
  `.gitignore` — il file va copiato manualmente dove serve, non si committa.

## Output — schema

```json
{
  "cosa_fa_e_non_fa_ob1": { ... testo fisso, uguale in ogni brief ... },
  "meta": {
    "club_target": "...", "contesto": "...", "generato": "ISO8601",
    "metodo": "...", "avvertenze": "...", "statistiche_run": { ... }
  },
  "target_verificati": [
    {
      "nome": "...", "ruolo": "...", "eta": 24, "club_attuale": "...",
      "valore_mercato_eur": 500000, "direzione": "in_entrata|in_uscita|in_rosa_conteso",
      "stato_trattativa": "...", "dato_oggettivo": "...",
      "a_favore": ["..."], "contro": ["..."],
      "verified": true,
      "fonti": [{"claim": "...", "url": "https://..."}]
    }
  ],
  "da_corroborare": [ ... stessa forma, verified=false ... ]
}
```

Stesso schema usato a mano per i brief Ascoli / Juve Stabia / Ravenna
(`name/club/role/year` + `a_favore/contro/fonti` del bridge doc, con i nomi
italiani già in uso in questo repo).

## Come funziona (in breve)

1. Cerca notizie di mercato per `--club` (DuckDuckGo/SearXNG, stesso motore
   di `player_lookup.py`), legge integralmente le fonti migliori via Jina Reader.
2. Un LLM (Gemini, fallback Groq) **estrae** dati tipizzati per ogni giocatore
   citato: ruolo, età, club, valore, direzione, stato della trattativa, un
   dato oggettivo verificabile. Non giudica, non inventa.
3. Il **codice** (non l'LLM) fonde le osservazioni sullo stesso nome, conta
   le fonti indipendenti (dominio diverso), e apre il gate `verified=true`
   solo con ≥2 fonti — stesso principio del gate v2 di produzione.
4. Il **codice** deriva `a_favore`/`contro` da soli campi oggettivi (età,
   valore, numero fonti, direzione dichiarata, stato della trattativa) —
   mai un giudizio tecnico. Vedi `assess_target()` in `sample_brief.py`.

## Limite onesto — leggere prima di inviare

I brief Ascoli/Juve Stabia/Ravenna sono stati costruiti a mano, e durante la
ricerca sono emerse **contraddizioni reali tra fonti** (un ruolo sbagliato,
una nazionalità che non tornava, una trattativa forse dirottata su un altro
club) che hanno richiesto ricerche di verifica aggiuntive con giudizio umano.

Questo script automatizza scoperta + estrazione + gate a soglia, ma **non
garantisce di catturare questo tipo di contraddizione** in automatico: si
ferma alle fonti trovate in un solo giro di ricerca. Il campo `fonti` con gli
URL resta sempre in chiaro apposta — va controllato a mano prima di
considerare `verified: true` come definitivo, specialmente prima di passare
i nomi a Grok per l'outreach.

## Se fallisce

- `"Nessun LLM configurato"` → manca `GEMINI_API_KEY` nell'ambiente.
- Pochi o zero risultati → il nome del club nelle query potrebbe essere
  troppo generico o troppo specifico; provare varianti (es. con/senza
  ragione sociale completa).
- Rate limit Gemini (429) → passa automaticamente al fallback Groq se
  configurato; altrimenti l'estrazione di quella fonte fallisce e viene
  saltata (non blocca l'intero run).

## Strumenti correlati in questo repo

- `scripts/player_lookup.py` — ricerca ad-hoc su un singolo giocatore già
  noto per nome.
- `scripts/dossier_v2.py` — genera il dossier HTML completo (formato
  Callegari) per un singolo giocatore.
- `scripts/compare_llm.py` — confronto tra modelli LLM a parità di prompt.
- `src/corroborate_v2.py` — guardrail anti-omonimia (età, slug URL) usati
  dalla pipeline v2 di produzione per corroborare un nome già noto su un
  aggregatore; non riusati qui perché `sample_brief.py` fa discovery aperta
  da notizie di mercato, non corroborazione di un nome già identificato.
