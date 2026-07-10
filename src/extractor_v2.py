#!/usr/bin/env python3
"""
OB1 v2 — Estrattore (Fase B1)

L'LLM fa UNA cosa sola: leggere il testo di una fonte ed estrarne i giocatori
citati come dati TIPIZZATI. NON dà punteggi, NON giudica l'interesse — quello è
lavoro del codice (scoring_v2.py). Principio Karpathy: il modello il più tardi
possibile, il meno possibile, solo dove serve leggere linguaggio naturale.

Una chiamata per FONTE (un articolo con 5 giocatori = 1 chiamata che ne estrae 5),
non una per giocatore. Economico e cachabile per URL.

La validazione/normalizzazione (normalize_observations) è codice puro e testabile
senza chiamare l'LLM.
"""

import json
import logging

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
    text = text.replace("```json", "").replace("```", "").strip()
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
    """
    Valida/normalizza le osservazioni grezze dell'LLM in record puliti.
    Codice puro: testabile senza chiamare l'LLM. Scarta i record inutilizzabili
    (nessun nome verificabile).
    """
    out = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip()
        if not name:
            continue  # senza nome verificabile non entra (il soprannome resta traccia, non entità)

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
            age = 2026 - by  # deriva l'età dall'anno se manca

        out.append({
            "name": name,
            "nickname": r.get("nickname"),
            "age": age if isinstance(age, int) else None,
            "birth_year": by if isinstance(by, int) else None,
            "gender": gender,
            "position": r.get("position"),
            "club": r.get("club"),
            "league": r.get("league"),
            "nationality": r.get("nationality"),
            "stats": stats,
            "evidence_quote": (r.get("evidence_quote") or "")[:200],
            "confidence": (r.get("confidence") or "medium").lower(),
            "source_url": source_url,
        })
    return out


class OB1Extractor:
    def __init__(self, api_key: str = None, model: str = "gemini-2.5-flash"):
        self.model = model
        self.client = None
        if api_key:
            try:
                from google import genai
                self.client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.warning(f"Extractor LLM non disponibile: {e}")

    def extract_from_source(self, source_text: str, source_url: str = "",
                            max_chars: int = 6000) -> list:
        """Una chiamata LLM → lista di osservazioni normalizzate da questa fonte."""
        if not self.client or not source_text:
            return []
        from google.genai import types
        prompt = EXTRACTION_PROMPT.format(
            source_url=source_url or "n/d", source_text=source_text[:max_chars])
        try:
            resp = self.client.models.generate_content(
                model=self.model, contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=EXTRACTION_SYSTEM,
                    temperature=0.0,          # estrazione: deterministica, non creativa
                    max_output_tokens=8192,
                ),
            )
            raw = _extract_first_json_array(resp.text or "")
            return normalize_observations(raw, source_url)
        except Exception as e:
            logger.error(f"Estrazione fallita per {source_url}: {e}")
            return []


if __name__ == "__main__":
    # Test della normalizzazione senza LLM
    sample = [
        {"name": "Edoardo Callegari", "age": 18, "gender": "male",
         "club": "Cremonese", "stats": {"goals": 1, "assists": 1, "apps": 35},
         "confidence": "high"},
        {"name": None, "nickname": "Pirituba", "confidence": "low"},  # scartato
    ]
    norm = normalize_observations(sample, "https://example.com")
    print(f"Estratti {len(norm)}/2 (1 scartato per nome mancante):")
    for o in norm:
        print(" ", o["name"], o["age"], o["stats"])
