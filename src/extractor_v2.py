#!/usr/bin/env python3
"""
OB1 v2 — Estrattore (Fase B1 + zero-cost)

L'LLM fa UNA cosa sola: leggere il testo di una fonte ed estrarne i giocatori
citati come dati TIPIZZATI. NON dà punteggi (quello è scoring_v2.py).

Economia:
  - una chiamata per FONTE (non per giocatore);
  - CATENA DI PROVIDER GRATUITI (src/llm_free_chain.py) provata PRIMA di Gemini.
    Non è solo resilienza: con il billing attivo sul progetto Google, Gemini
    oltre il free tier fattura. Chiamandolo per ultimo (o mai, in free_only) il
    costo resta zero per costruzione, non per fortuna;
  - il budget di chiamate per run lo impone chi orchestra (ingest_v2.py).

Modalità, via OB1_LLM_MODE: free_first (default se c'è una chiave free),
free_only (mai Gemini), gemini_first (comportamento storico).

normalize_observations() è codice puro, testabile senza rete: invariata.
"""

import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.llm_free_chain import (FREE_MAX_CHARS, call_free_chain, is_quota_error,
                                resolve_free_providers, resolve_llm_mode)

logger = logging.getLogger(__name__)

VALID_GENDERS = {"male", "female", "unknown"}

# Tetto input per i provider gratuiti: i free tier limitano i token/minuto
# (12k sul 70b Groq) e una richiesta troppo grande fallisce con 413/429. Gemini,
# quando lo si usa, riceve il testo pieno (max_chars della chiamata).
# Nome storico mantenuto per retro-compatibilità con chi lo importa.
GROQ_MAX_CHARS = FREE_MAX_CHARS

EXTRACTION_SYSTEM = """Sei un estrattore di dati calcistici. Leggi il testo di UNA fonte
ed estrai OGNI giovane calciatore/calciatrice citato come soggetto (non allenatori,
dirigenti, o giocatori nominati solo di sfuggita).

NON dare punteggi, NON giudicare se è un talento: estrai solo ciò che il testo dice.
Se un dato non è nel testo, usa null — NON inventare. Meglio null che un valore inventato."""

EXTRACTION_PROMPT = """Dal testo qui sotto, estrai i giovani calciatori come JSON array.
Per ciascuno:
{{
  "name": "Nome Cognome completo (null se il testo dà solo un soprannome/handle)",
  "nickname": "eventuale soprannome citato, altrimenti null",
  "birth_year": 2008,
  "age": 17,
  "gender": "male | female | unknown (deducilo dal contesto: squadra femminile, 'femminile', pronomi)",
  "position": "ruolo se citato, altrimenti null",
  "club": "club attuale se citato",
  "league": "campionato/categoria se citato",
  "nationality": "nazionalità se citata",
  "stats": {{"goals": 0, "assists": 0, "apps": 0, "minutes": 0}},
  "evidence_quote": "breve citazione testuale (max 200 char) che sostiene l'estrazione",
  "confidence": "high | medium | low (quanto è chiaro nel testo che è un profilo reale)"
}}

Regole:
- Estrai SOLO nomi verificabili. Se il testo dà solo un soprannome, metti name=null e nickname.
- stats: solo numeri esplicitamente nel testo, altrimenti ometti la chiave o 0.
- Restituisci SOLO il JSON array, niente altro.

--- FONTE ({source_url}) ---
{source_text}"""


def _extract_first_json_array(text: str) -> list:
    """Primo array JSON bilanciato nel testo (robusto a testo attorno)."""
    text = (text or "").replace("```json", "").replace("```", "").strip()
    try:
        start = text.index('[')
    except ValueError:
        return []
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return []
    return []


def normalize_observations(raw: list, source_url: str = "") -> list:
    """Valida/normalizza le osservazioni grezze dell'LLM. Codice puro/testabile."""
    out = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip()
        if not name:
            continue

        gender = (r.get("gender") or "unknown").strip().lower()
        if gender not in VALID_GENDERS:
            gender = "unknown"

        stats = r.get("stats") or {}
        stats = {k: v for k, v in stats.items()
                 if k in ("goals", "assists", "apps", "minutes")
                 and isinstance(v, (int, float))}

        age = r.get("age")
        by = r.get("birth_year")
        if age is None and isinstance(by, int) and 1990 < by < 2020:
            age = 2026 - by

        out.append({
            "name": name, "nickname": r.get("nickname"),
            "age": age if isinstance(age, int) else None,
            "birth_year": by if isinstance(by, int) else None,
            "gender": gender, "position": r.get("position"),
            "club": r.get("club"), "league": r.get("league"),
            "nationality": r.get("nationality"), "stats": stats,
            "evidence_quote": (r.get("evidence_quote") or "")[:200],
            "confidence": (r.get("confidence") or "medium").lower(),
            "source_url": source_url,
        })
    return out


def _is_quota_error(exc: Exception) -> bool:
    """Retro-compat: la logica vera sta in llm_free_chain.is_quota_error()."""
    return is_quota_error(exc)


def resolve_fallback(explicit: dict = None) -> dict | None:
    """
    Retro-compatibilità: il "fallback" singolo di prima è il primo anello della
    catena gratuita di oggi. Tenuto perché script diagnostici lo importano.
    """
    if explicit:
        return explicit
    chain = resolve_free_providers()
    return chain[0] if chain else None


class OB1Extractor:
    """
    Ordine dei provider secondo la modalità:
      free_first    catena gratuita → Gemini (solo se tutta la catena fallisce)
      free_only     catena gratuita e basta: Gemini non viene mai chiamato
      gemini_first  Gemini → catena gratuita (comportamento storico)
    """

    def __init__(self, api_key: str = None, model: str = None,
                 fallback: dict = None, mode: str = None):
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.mode = resolve_llm_mode(mode)
        self.client = None
        self.gemini_exhausted = False          # una volta a quota, non ritenta Gemini
        self.free_providers = resolve_free_providers()
        if fallback:                           # provider esplicito: ha la precedenza
            self.free_providers = [fallback] + self.free_providers
        self._dead_free = set()                # label dei free già esauriti nel run
        self.stats = Counter()                 # {groq, cerebras, gemini, failed, ...}

        api_key = os.getenv("GEMINI_API_KEY", "") if api_key is None else api_key
        if api_key and self.mode != "free_only":
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning(f"Gemini non disponibile: {e}")
        elif api_key and self.mode == "free_only":
            logger.info("Modalità free_only: GEMINI_API_KEY presente ma non verrà usata.")

    # --- stato ---

    @property
    def free_exhausted(self) -> bool:
        """True se ogni provider gratuito configurato è fuori uso per questo run."""
        return bool(self.free_providers) and \
            all(p["label"] in self._dead_free for p in self.free_providers)

    def _gemini_ok(self) -> bool:
        return bool(self.client) and not self.gemini_exhausted and self.mode != "free_only"

    def _free_ok(self) -> bool:
        return bool(self.free_providers) and not self.free_exhausted

    def llm_usable(self) -> bool:
        """True se almeno un provider può ancora essere chiamato in questo run."""
        return self._gemini_ok() or self._free_ok()

    def available(self) -> bool:
        """True se esiste una configurazione utilizzabile (prima di iniziare)."""
        return bool(self.free_providers) or (bool(self.client) and self.mode != "free_only")

    # --- chiamate ---

    def _call_gemini(self, prompt: str) -> str:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=self.model, contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=EXTRACTION_SYSTEM, temperature=0.0,
                max_output_tokens=8192))
        return resp.text or ""

    def _prompt(self, source_text: str, source_url: str, max_chars: int) -> str:
        return EXTRACTION_PROMPT.format(
            source_url=source_url or "n/d", source_text=source_text[:max_chars])

    def _try_free(self, source_text: str, source_url: str, max_chars: int):
        """Catena gratuita. Ritorna la lista normalizzata, o None se nessuno risponde."""
        if not self._free_ok():
            return None
        free_chars = min(max_chars, FREE_MAX_CHARS)
        text, label = call_free_chain(
            self.free_providers, EXTRACTION_SYSTEM,
            self._prompt(source_text, source_url, free_chars), dead=self._dead_free)
        if text is None:
            return None
        self.stats[label] += 1
        return normalize_observations(_extract_first_json_array(text), source_url)

    def _try_gemini(self, source_text: str, source_url: str, max_chars: int):
        """Gemini col testo pieno. Ritorna la lista normalizzata, o None."""
        if not self._gemini_ok():
            return None
        try:
            raw = _extract_first_json_array(
                self._call_gemini(self._prompt(source_text, source_url, max_chars)))
            self.stats["gemini"] += 1
            return normalize_observations(raw, source_url)
        except Exception as e:
            if is_quota_error(e):
                logger.warning("Gemini quota esaurita → resta la catena gratuita.")
                self.gemini_exhausted = True
            else:
                logger.error(f"Gemini error {source_url}: {e}")
            return None

    def extract_from_source(self, source_text: str, source_url: str = "",
                            max_chars: int = 6000):
        """
        Una estrazione → lista di osservazioni normalizzate.
        Ritorna [] se l'estrazione è riuscita ma non ha trovato giocatori (fonte
        legittimamente vuota → si può marcare 'vista'). Ritorna None se TUTTI i
        provider ammessi hanno fallito (quota/errore) → l'orchestratore ritenta.

        I provider gratuiti ricevono il testo ridotto a FREE_MAX_CHARS (tetto
        token/minuto dei free tier); Gemini, quando lo si usa, riceve max_chars.
        """
        if not source_text:
            return []

        if not self.llm_usable():
            self.stats["failed"] += 1
            return None   # niente provider vivi: non martellare le API

        order = ((self._try_gemini, self._try_free) if self.mode == "gemini_first"
                 else (self._try_free, self._try_gemini))
        for attempt in order:
            obs = attempt(source_text, source_url, max_chars)
            if obs is not None:
                return obs

        self.stats["failed"] += 1
        return None  # nessun provider disponibile: NON marcare visto, ritenta


if __name__ == "__main__":
    norm = normalize_observations([
        {"name": "Edoardo Callegari", "age": 18, "gender": "male", "club": "Cremonese",
         "stats": {"goals": 1, "assists": 1, "apps": 35}, "confidence": "high"},
        {"name": None, "nickname": "Pirituba", "confidence": "low"},
    ], "https://example.com")
    print(f"Estratti {len(norm)}/2 (1 scartato).")

    # --- ordine dei provider per modalità, senza toccare la rete ---
    def _extractor(mode, free_labels, with_gemini=True):
        e = OB1Extractor.__new__(OB1Extractor)      # niente __init__: niente env, niente client
        e.model, e.mode = "gemini-2.5-flash", mode
        e.client = object() if (with_gemini and mode != "free_only") else None
        e.gemini_exhausted = False
        e.free_providers = [{"label": l, "base_url": "http://x/v1", "api_key": "k",
                             "model": "m"} for l in free_labels]
        e._dead_free, e.stats = set(), Counter()
        return e

    called = []
    globals()["call_free_chain"] = lambda providers, system, prompt, dead=None, **k: (
        called.append("free") or ('[{"name": "Kauan Ribeiro", "club": "Athletico", "age": 17}]',
                                  providers[0]["label"]))
    OB1Extractor._call_gemini = lambda self, prompt: called.append("gemini") or "[]"

    for mode, expected_first in (("free_first", "free"), ("free_only", "free"),
                                 ("gemini_first", "gemini")):
        called.clear()
        ex = _extractor(mode, ["groq", "cerebras"])
        obs = ex.extract_from_source("testo della fonte", "https://ex.com/a")
        assert called[0] == expected_first, (mode, called)
        print(f"{mode:13} → primo provider: {called[0]:6} · estratti: {len(obs)} "
              f"· stats: {dict(ex.stats)}")

    # free_only non chiama Gemini nemmeno quando la catena gratuita è morta
    called.clear()
    ex = _extractor("free_only", ["groq"])
    ex._dead_free.add("groq")
    assert ex.extract_from_source("testo", "https://ex.com/b") is None
    assert called == [], called
    assert ex.free_exhausted and not ex.llm_usable()

    # free_first: catena morta → Gemini interviene (rete di sicurezza, non default)
    called.clear()
    ex = _extractor("free_first", ["groq"])
    ex._dead_free.add("groq")
    ex.extract_from_source("testo", "https://ex.com/c")
    assert called == ["gemini"], called

    # Senza chiavi free e senza Gemini non si finge di poter lavorare
    ex = _extractor("free_first", [], with_gemini=False)
    assert not ex.available() and not ex.llm_usable()

    print(f"\nmodalità con l'ambiente attuale: {resolve_llm_mode()}")
    chain = resolve_free_providers()
    print(f"catena gratuita: {[p['label'] for p in chain] or 'nessuna (imposta GROQ_API_KEY)'}")
    print("OK — self-test estrattore superato.")
