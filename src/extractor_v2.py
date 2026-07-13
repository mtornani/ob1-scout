#!/usr/bin/env python3
"""
OB1 v2 — Estrattore (Fase B1 + ottimizzazione free tier B3)

L'LLM fa UNA cosa sola: leggere il testo di una fonte ed estrarne i giocatori
citati come dati TIPIZZATI. NON dà punteggi (quello è scoring_v2.py).

Economia free tier:
  - una chiamata per FONTE (non per giocatore);
  - CATENA DI FALLBACK: Gemini primario → su 429/quota passa a un provider
    gratuito OpenAI-compatible (Groq/OpenRouter/generico). Non solo resilienza:
    è capacità gratuita aggiuntiva quando il free tier Gemini (20/giorno) è finito.
  - il budget di chiamate per run lo impone chi orchestra (ingest_v2.py).

normalize_observations() è codice puro, testabile senza rete.
"""

import json
import logging
import os
from collections import Counter

import requests

logger = logging.getLogger(__name__)

VALID_GENDERS = {"male", "female", "unknown"}

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
    s = str(exc).lower()
    return "429" in s or "resource_exhausted" in s or "quota" in s


def resolve_fallback(explicit: dict = None) -> dict | None:
    """
    Config del provider di fallback OpenAI-compatible. Ordine: esplicito →
    GROQ_API_KEY → OPENROUTER_API_KEY → COMPARE_* generico → None.
    """
    if explicit:
        return explicit
    if os.getenv("GROQ_API_KEY"):
        # 70b-versatile: sul free tier ha il tetto token/minuto più alto (12k)
        # tra i modelli Groq. Il vero vincolo è la DIMENSIONE del prompt: va
        # tenuta sotto il TPM (vedi max_chars ridotto in extract_from_source).
        return {"base_url": "https://api.groq.com/openai/v1",
                "api_key": os.getenv("GROQ_API_KEY"),
                "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                "label": "groq"}
    if os.getenv("OPENROUTER_API_KEY"):
        return {"base_url": "https://openrouter.ai/api/v1",
                "api_key": os.getenv("OPENROUTER_API_KEY"),
                "model": os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
                "label": "openrouter"}
    if os.getenv("COMPARE_BASE_URL") and os.getenv("COMPARE_API_KEY"):
        return {"base_url": os.getenv("COMPARE_BASE_URL"),
                "api_key": os.getenv("COMPARE_API_KEY"),
                "model": os.getenv("COMPARE_MODEL", "gpt-4o-mini"),
                "label": "custom"}
    return None


class OB1Extractor:
    def __init__(self, api_key: str = None, model: str = "gemini-2.5-flash",
                 fallback: dict = None):
        self.model = model
        self.client = None
        self.gemini_exhausted = False          # una volta a quota, non ritenta Gemini
        self.fallback = resolve_fallback(fallback)
        self.stats = Counter()                 # {gemini, fallback, failed}
        if api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning(f"Gemini non disponibile: {e}")

    # --- provider primario ---
    def _call_gemini(self, prompt: str) -> str:
        from google.genai import types
        resp = self.client.models.generate_content(
            model=self.model, contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=EXTRACTION_SYSTEM, temperature=0.0,
                max_output_tokens=8192))
        return resp.text or ""

    # --- provider di fallback (OpenAI-compatible) ---
    def _call_fallback(self, prompt: str) -> str:
        fb = self.fallback
        resp = requests.post(
            fb["base_url"].rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {fb['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": fb["model"], "temperature": 0.0, "max_tokens": 8192,
                  "messages": [{"role": "system", "content": EXTRACTION_SYSTEM},
                               {"role": "user", "content": prompt}]},
            timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"fallback HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()["choices"][0]["message"]["content"] or ""

    def extract_from_source(self, source_text: str, source_url: str = "",
                            max_chars: int = 2800):
        """
        Una estrazione (Gemini o fallback) → lista di osservazioni normalizzate.
        Ritorna [] se l'estrazione è riuscita ma non ha trovato giocatori (fonte
        legittimamente vuota → si può marcare 'vista'). Ritorna None se TUTTI i
        provider hanno fallito (quota/errore) → l'orchestratore la ritenta dopo.
        """
        if not source_text:
            return []
        prompt = EXTRACTION_PROMPT.format(
            source_url=source_url or "n/d", source_text=source_text[:max_chars])

        # 1) Gemini, se disponibile e non già esaurito
        if self.client and not self.gemini_exhausted:
            try:
                raw = _extract_first_json_array(self._call_gemini(prompt))
                self.stats["gemini"] += 1
                return normalize_observations(raw, source_url)
            except Exception as e:
                if _is_quota_error(e):
                    logger.warning("Gemini quota esaurita → passo al fallback.")
                    self.gemini_exhausted = True
                else:
                    logger.error(f"Gemini error {source_url}: {e}")

        # 2) Fallback OpenAI-compatible
        if self.fallback:
            try:
                raw = _extract_first_json_array(self._call_fallback(prompt))
                self.stats["fallback"] += 1
                return normalize_observations(raw, source_url)
            except Exception as e:
                logger.error(f"Fallback error {source_url}: {e}")

        self.stats["failed"] += 1
        return None  # nessun provider disponibile: NON marcare visto, ritenta

    def available(self) -> bool:
        return bool(self.client) or bool(self.fallback)


if __name__ == "__main__":
    norm = normalize_observations([
        {"name": "Edoardo Callegari", "age": 18, "gender": "male", "club": "Cremonese",
         "stats": {"goals": 1, "assists": 1, "apps": 35}, "confidence": "high"},
        {"name": None, "nickname": "Pirituba", "confidence": "low"},
    ], "https://example.com")
    print(f"Estratti {len(norm)}/2 (1 scartato).")
    fb = resolve_fallback()
    print("Fallback configurato:", fb["label"] if fb else "nessuno (imposta GROQ_API_KEY)")
