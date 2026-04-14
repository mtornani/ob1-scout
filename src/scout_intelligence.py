#!/usr/bin/env python3
"""
OB1 Scout Intelligence - Targeted player evaluation engine.
Evaluates scraped data against specific club/role criteria using Gemini.
"""

import json
import logging
from google import genai
from google.genai import types
from config.ob1_config import GEMINI_API_KEY

logger = logging.getLogger(__name__)


class ScoutIntelligence:
    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        self.model = 'gemini-2.0-flash'

    def _build_prompt(self, q: dict, snippets: list) -> str:
        filters = q.get('filters', {})
        boost = q.get('scoring_boost', {}).get('patterns', {})

        blocks = []
        for i, s in enumerate(snippets[:30], 1):
            title = s.get('title', '')
            content = (s.get('content', '') or '')[:400]
            url = s.get('url', '')
            if title or content:
                blocks.append(f"[{i}] {title}\n{content}\nURL: {url}")
        context = "\n\n".join(blocks) if blocks else "Nessun dato disponibile."

        boost_str = ", ".join(f'"{k}" (+{v})' for k, v in boost.items())
        leagues_str = ", ".join(filters.get('leagues', []))
        age = filters.get('age_range', [18, 30])
        value_max = filters.get('market_value_max_eur', 'N/A')
        positions = ", ".join(filters.get('position', []))
        notes = q.get('notes', '')
        strategic = q.get('strategic_context', '')

        return f"""Sei uno scout professionista che lavora per {q.get('target_club', 'N/A')}.
Ruolo cercato: {q.get('critical_role', 'N/A')}

FILTRI OBBLIGATORI:
- Posizione: {positions}
- Leghe target: {leagues_str}
- Età: {age[0]}-{age[1]} anni
- Valore di mercato max: €{value_max:,} (se numerico)
- Contesto: {strategic}

BOOST SCORE (parole chiave che aumentano il punteggio): {boost_str}
NOTE STRATEGICHE: {notes}

DATI WEB RACCOLTI:
{context}

Analizza i dati e identifica fino a 5 giocatori concreti compatibili con questi criteri.
Assegna scout_score 1-10 in base al fit con filtri e boost.
Penalizza giocatori già in club top (Real, City, Bayern, PSG) o con valore > 10M.

Rispondi SOLO con un JSON array (nessun testo aggiuntivo):
[
  {{
    "name": "Nome Completo",
    "age": 24,
    "nationality": "Olandese",
    "position": "GK",
    "current_club": "Club Attuale",
    "league": "Eredivisie",
    "contract_expires": "2026-06",
    "estimated_value_eur": 1500000,
    "scout_score": 7.5,
    "fit_summary": "Breve analisi del fit con il profilo richiesto...",
    "source_url": "https://..."
  }}
]
Usa null per campi sconosciuti. Ometti giocatori senza dati sufficienti."""

    @staticmethod
    def _extract_json(text: str) -> list:
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

    def evaluate_candidates(self, query_cfg: dict, data_samples: list) -> list:
        if not self.client:
            logger.warning("Gemini not configured — skipping evaluation.")
            return []
        if not data_samples:
            logger.warning(f"No data for query {query_cfg.get('query_id')} — skipping.")
            return []

        prompt = self._build_prompt(query_cfg, data_samples)
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=4096
                )
            )
            text = response.text.replace('```json', '').replace('```', '').strip()
            candidates = self._extract_json(text)
            # Attach query metadata to each candidate
            for c in candidates:
                c['query_id'] = query_cfg.get('query_id')
                c['target_club'] = query_cfg.get('target_club')
            return candidates
        except Exception as e:
            logger.error(f"Evaluation failed [{query_cfg.get('query_id')}]: {e}")
            return []
