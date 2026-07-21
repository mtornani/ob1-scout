#!/usr/bin/env python3
"""
OB1 Scout - Sample Brief (ad-hoc research tool)

Standalone, on-demand: dato un club, produce un JSON di "obiettivi di
mercato" nello schema ufficiale già usato per Ascoli/Juve Stabia/Ravenna
(vedi bridge Claude<->Grok). NON è parte della pipeline v2, non tocca
data/ob1_v2.db, non ha cron.

Principio (lo stesso di tutta la v2): l'LLM ESTRAE dati tipizzati da una
fonte, il CODICE decide gate (>=2 fonti indipendenti = verified) e deriva
pro/contro dai soli campi oggettivi (età, valore, stato contrattuale).
Nessun giudizio tecnico inventato — mai un pro/contro che non discenda
da un dato con la sua fonte.

Limite onesto: questo script automatizza scoperta + estrazione + gate a
soglia. Le contraddizioni tra fonti (ruolo sbagliato, nazionalità che non
torna, trattativa forse dirottata altrove) che hanno richiesto ricerche
di verifica manuali nei brief Ascoli/Juve Stabia/Ravenna NON sono
garantite qui: il campo "fonti" resta sempre visibile per un controllo
umano prima di considerare "verified" come definitivo.

Uso:
    python scripts/sample_brief.py --club "Ascoli Calcio 1898 FC" \
        --context "Serie B, neopromossa" --limit 3
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.extractor_v2 import resolve_fallback, _is_quota_error, _extract_first_json_array
from src.database_v2 import normalize_name, domain_of

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "briefs"
GROQ_MAX_CHARS = 2800  # stesso vincolo TPM free-tier di extractor_v2

INTRO_BOILERPLATE = {
    "in_breve": "OB1 non è un osservatore e non pretende di esserlo. È uno strumento che va a cercare dati pubblici su un giocatore, li incrocia su più fonti indipendenti prima di darli per buoni, e li mette in fila con la fonte attaccata a ogni riga — così chi legge può controllare di persona con un click, prima di alzare il telefono.",
    "cosa_fa": [
        "Legge fonti aperte (Wikipedia, Transfermarkt, siti ufficiali dei club, stampa di settore) e ne estrae dati oggettivi: ruolo, età, club, valore di mercato, stato della trattativa.",
        "Non pubblica un nome finché non lo trova confermato da almeno due fonti indipendenti — se un dato è a fonte singola, questo file lo dice chiaramente, non lo nasconde.",
        "Tiene traccia della fonte esatta di ogni singolo dato, non solo del nome del giocatore."
    ],
    "cosa_non_fa": [
        "Non ha visto giocare nessuno di questi ragazzi. Piede, personalità, tenuta nello spogliatoio, voglia di scommetterci: quello lo dice solo l'occhio di chi il campo lo mastica da una vita, non un motore di ricerca.",
        "Non conosce infortuni non pubblicati, clausole riservate, parole date a voce o la situazione di famiglia. Quello resta terreno di agenti e conoscenze dirette.",
        "Non decide lui chi prendere, e non garantisce che una trattativa segnalata sia ancora viva: restringe la lista, poi la parola passa a chi il mestiere lo fa sul campo."
    ],
    "come_leggere_il_file": "Ogni giocatore ha 'a_favore' e 'contro' — sono letture dei dati raccolti, non giudizi tecnici. Il verdetto sul giocatore resta suo. Prima di contattare qualcuno, ricontrollare le fonti elencate: automatizzare la ricerca non sostituisce l'ultimo controllo umano."
}

BRIEF_SYSTEM = """Sei un estrattore di dati di calciomercato. Leggi il testo di UNA fonte
ed estrai OGNI calciatore citato come possibile obiettivo di mercato, rumor, trattativa
o giocatore in uscita/entrata per il club indicato.

NON dare giudizi tecnici, NON inventare: se un dato non è nel testo, usa null.
Estrai solo nomi completi verificabili (mai un soprannome da solo)."""

BRIEF_PROMPT = """Club di riferimento: {club}

Dal testo qui sotto, estrai i calciatori citati in relazione al mercato di questo club,
come JSON array. Per ciascuno:
{{
  "name": "Nome Cognome completo (null se solo soprannome)",
  "role": "ruolo se citato, altrimenti null",
  "age": 24,
  "birth_year": 2001,
  "current_club": "squadra attuale/di provenienza se citata",
  "market_value_eur": 500000,
  "direction": "in_entrata | in_uscita | in_rosa_conteso | null (verso il club di riferimento)",
  "deal_status": "breve stato testuale della trattativa se citato (es. 'ufficiale', 'trattativa avviata', 'interesse'), altrimenti null",
  "objective_claim": "un dato oggettivo e verificabile citato nel testo (gol, presenze, scadenza contratto...), altrimenti null",
  "evidence_quote": "citazione testuale breve (max 200 char) a supporto",
  "confidence": "high | medium | low"
}}

Regole:
- Solo giocatori nominati esplicitamente in relazione a {club}.
- objective_claim: un fatto, non un'opinione ("9 gol in 28 presenze", non "giocatore interessante").
- Restituisci SOLO il JSON array, niente altro.

--- FONTE ({source_url}) ---
{source_text}"""


def normalize_brief_observations(raw: list, source_url: str) -> list:
    """Valida/normalizza le osservazioni grezze dell'LLM. Codice puro/testabile."""
    out = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip()
        if not name or len(name.split()) < 2:
            continue  # niente soprannomi/nomi singoli: stesso principio del gate v2
        age = r.get("age")
        by = r.get("birth_year")
        if age is None and isinstance(by, int) and 1970 < by < 2020:
            age = datetime.now().year - by
        mv = r.get("market_value_eur")
        out.append({
            "name": name,
            "role": r.get("role"),
            "age": age if isinstance(age, int) else None,
            "current_club": r.get("current_club"),
            "market_value_eur": mv if isinstance(mv, (int, float)) else None,
            "direction": r.get("direction") if r.get("direction") in
                         ("in_entrata", "in_uscita", "in_rosa_conteso") else None,
            "deal_status": r.get("deal_status"),
            "objective_claim": r.get("objective_claim"),
            "evidence_quote": (r.get("evidence_quote") or "")[:200],
            "confidence": (r.get("confidence") or "medium").lower(),
            "source_url": source_url,
        })
    return out


class BriefExtractor:
    """
    Estrattore per obiettivi di mercato: stessa architettura (Gemini primario,
    fallback Groq/OpenRouter, testo ridotto per il fallback) di src/extractor_v2,
    ma con prompt proprio — non tocca il file di produzione dell'ingest v2.
    """

    def __init__(self, api_key: str = None, model: str = "gemini-2.5-flash",
                 fallback: dict = None):
        self.model = model
        self.client = None
        self.gemini_exhausted = False
        self.fallback_exhausted = False
        self.fallback = resolve_fallback(fallback)
        self.stats = Counter()
        if api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning(f"Gemini non disponibile: {e}")

    def usable(self) -> bool:
        return (bool(self.client) and not self.gemini_exhausted) or \
               (bool(self.fallback) and not self.fallback_exhausted)

    def _prompt(self, club: str, source_text: str, source_url: str, max_chars: int) -> str:
        return BRIEF_PROMPT.format(club=club, source_url=source_url or "n/d",
                                   source_text=source_text[:max_chars])

    def _call_gemini(self, prompt: str) -> str:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=self.model, contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=BRIEF_SYSTEM, temperature=0.0, max_output_tokens=8192))
        return resp.text or ""

    def _call_fallback(self, prompt: str) -> str:
        fb = self.fallback
        resp = requests.post(
            fb["base_url"].rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {fb['api_key']}", "Content-Type": "application/json"},
            json={"model": fb["model"], "temperature": 0.0, "max_tokens": 8192,
                  "messages": [{"role": "system", "content": BRIEF_SYSTEM},
                               {"role": "user", "content": prompt}]},
            timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"fallback HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()["choices"][0]["message"]["content"] or ""

    def extract(self, club: str, source_text: str, source_url: str = "", max_chars: int = 6000):
        """None = tutti i provider falliti (ritenta altrove). [] = nessun nome trovato."""
        if not source_text:
            return []
        if not self.usable():
            self.stats["failed"] += 1
            return None
        if self.client and not self.gemini_exhausted:
            try:
                raw = _extract_first_json_array(
                    self._call_gemini(self._prompt(club, source_text, source_url, max_chars)))
                self.stats["gemini"] += 1
                return normalize_brief_observations(raw, source_url)
            except Exception as e:
                if _is_quota_error(e):
                    logger.warning("Gemini quota esaurita → passo al fallback.")
                    self.gemini_exhausted = True
                else:
                    logger.error(f"Gemini error {source_url}: {e}")
        if self.fallback and not self.fallback_exhausted:
            try:
                fb_chars = min(max_chars, GROQ_MAX_CHARS)
                raw = _extract_first_json_array(
                    self._call_fallback(self._prompt(club, source_text, source_url, fb_chars)))
                self.stats["fallback"] += 1
                return normalize_brief_observations(raw, source_url)
            except Exception as e:
                logger.error(f"Fallback error {source_url}: {e}")
                if _is_quota_error(e):
                    self.fallback_exhausted = True
        self.stats["failed"] += 1
        return None


def assess_target(entry: dict, n_sources: int) -> dict:
    """
    'a_favore / contro' derivati SOLO da campi oggettivi (età, valore,
    direzione, stato dichiarato, numero fonti) — mai un giudizio tecnico.
    Pura, testabile senza rete. Stesso principio di assess_player()
    in scripts/export_dashboard_v2.py.
    """
    pros = []
    # I "contro" sono in due fasce di priorità: gli strutturali (direzione
    # sbagliata, trattativa già chiusa) non devono MAI sparire per un taglio
    # a lunghezza fissa — vengono prima, i generici dopo.
    cons_priority, cons_generic = [], []

    age = entry.get("age")
    mv = entry.get("market_value_eur")
    direction = entry.get("direction")
    status = (entry.get("deal_status") or "").lower()
    claim = entry.get("objective_claim")

    if direction == "in_uscita":
        cons_priority.append("Le fonti lo descrivono in uscita dal club di riferimento, non in entrata: verificare la direzione reale prima di leggere il file")
    if any(w in status for w in ("ufficiale", "firmato", "annunciato")):
        cons_priority.append("Trattativa già chiusa secondo le fonti: non è un obiettivo aperto, solo contesto")
    elif any(w in status for w in ("interesse", "sondaggio", "valutazione")):
        cons_priority.append("Solo interesse iniziale dichiarato, non trattativa avviata")

    if n_sources >= 2:
        pros.append(f"Confermato da {n_sources} fonti indipendenti")
    else:
        cons_priority.append("Una sola fonte: da riconfermare prima di muoversi")

    if age is not None:
        if age <= 23:
            pros.append(f"{age} anni: margine di crescita e di rivendita")
        elif age >= 30:
            cons_generic.append(f"{age} anni: operazione a consumo, non da plusvalenza")

    if mv is not None:
        if mv <= 400000:
            pros.append("Costo contenuto: rischio economico basso")
        elif mv >= 2000000:
            cons_generic.append("Valutazione alta: probabile asta con altri club")

    if claim:
        pros.append(f"Dato oggettivo disponibile: {claim}")
    else:
        cons_generic.append("Nessun dato di rendimento raccolto, solo anagrafica")

    cons = (cons_priority + cons_generic)[:5]
    return {"a_favore": pros[:4], "contro": cons}


def merge_observations(obs_list: list) -> dict:
    """Fonde più osservazioni sullo stesso nome: primo valore non-nullo per campo."""
    merged = {"name": obs_list[0]["name"]}
    for key in ("role", "age", "current_club", "market_value_eur", "direction", "deal_status", "objective_claim"):
        for o in obs_list:
            if o.get(key) is not None:
                merged[key] = o[key]
                break
        merged.setdefault(key, None)
    return merged


PRIORITY_DOMAINS = ["transfermarkt", "soccerway", "besoccer", "sofascore", "fbref"]


async def build_brief(club: str, context: str = "", limit: int = 3,
                      max_articles: int = 8, api_key: str = None) -> dict:
    from src.scraper_global import AsyncGlobalScraper  # lazy: serve solo qui
    scraper = AsyncGlobalScraper()
    extractor = BriefExtractor(api_key=api_key or os.getenv("GEMINI_API_KEY", ""))
    if not extractor.usable():
        raise SystemExit("Nessun LLM configurato: serve GEMINI_API_KEY (o GROQ_API_KEY come fallback).")

    queries = [
        f'"{club}" calciomercato obiettivi trattative',
        f'"{club}" calciomercato svincolati parametro zero',
        f'"{club}" transfermarkt rumors trattative',
    ]
    raw_results = await scraper.run_batch(queries)

    seen, urls = set(), []
    for r in raw_results:
        u = r.get("url", "")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    urls.sort(key=lambda u: any(d in u for d in PRIORITY_DOMAINS), reverse=True)
    top_urls = urls[:max_articles]

    deep_texts = await scraper.deep_read_urls(top_urls, max_urls=len(top_urls))

    candidates = {}  # normalized_name -> list of observations
    stats = Counter()
    for url, text in deep_texts.items():
        obs_list = extractor.extract(club, text, url)
        if not obs_list:
            stats["fonti_senza_estrazione"] += 1
            continue
        for obs in obs_list:
            key = normalize_name(obs["name"])
            candidates.setdefault(key, []).append(obs)

    entries = []
    for key, obs_list in candidates.items():
        domains = {domain_of(o["source_url"]) for o in obs_list if domain_of(o["source_url"])}
        merged = merge_observations(obs_list)
        n_src = len(domains) or 1
        assessment = assess_target(merged, n_src)
        entries.append({
            "nome": merged["name"],
            "ruolo": merged.get("role"),
            "eta": merged.get("age"),
            "club_attuale": merged.get("current_club"),
            "valore_mercato_eur": merged.get("market_value_eur"),
            "direzione": merged.get("direction"),
            "stato_trattativa": merged.get("deal_status"),
            "dato_oggettivo": merged.get("objective_claim"),
            "a_favore": assessment["a_favore"],
            "contro": assessment["contro"],
            "verified": n_src >= 2,
            "fonti": [{"claim": o.get("evidence_quote") or "dato estratto",
                      "url": o["source_url"]} for o in obs_list],
        })

    entries.sort(key=lambda e: (not e["verified"], -(e["eta"] or 99)))
    verified = [e for e in entries if e["verified"]][:limit]
    da_corroborare = [e for e in entries if not e["verified"]]

    return {
        "cosa_fa_e_non_fa_ob1": INTRO_BOILERPLATE,
        "meta": {
            "club_target": club,
            "contesto": context or None,
            "generato": datetime.now(timezone.utc).isoformat(),
            "metodo": "Ricerca automatica OB1 (scripts/sample_brief.py): scoperta fonti + estrazione LLM tipizzata + gate a >=2 fonti indipendenti. Nessun dato senza URL di origine.",
            "avvertenze": "verified=true richiede 2 fonti indipendenti trovate in QUESTO run. Non sostituisce un controllo umano sulle contraddizioni tra fonti (ruoli/nazionalità/direzione della trattativa) prima dell'invio.",
            "statistiche_run": dict(stats, **{f"llm_{k}": v for k, v in extractor.stats.items()}),
        },
        "target_verificati": verified,
        "da_corroborare": da_corroborare,
    }


async def main():
    ap = argparse.ArgumentParser(description="Genera un brief di mercato per un club, schema OB1")
    ap.add_argument("--club", required=True, help='Es. "Ascoli Calcio 1898 FC"')
    ap.add_argument("--context", default="", help='Es. "Serie B, neopromossa"')
    ap.add_argument("--limit", type=int, default=3, help="Max target verificati in output")
    ap.add_argument("--max-articles", type=int, default=8)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    doc = await build_brief(args.club, args.context, args.limit, args.max_articles)

    out_path = Path(args.out) if args.out else OUTPUT_DIR / "sample_latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Salvato: {out_path}")
    print(f"  {len(doc['target_verificati'])} verificati, {len(doc['da_corroborare'])} da corroborare")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main())
