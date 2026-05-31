#!/usr/bin/env python3
"""
OB1 Global Scout - Intelligence Engine
Powered by Gemini to analyze raw intelligence and identify deep-context anomalies.
"""

# ============================================================
# FREEZE PILOTA K-SPORT
# Dal 27 maggio 2026 fino a fine pilota (~settembre 2026):
# - NON modificare pesi
# - NON modificare soglie HOT/WARM/COLD
# - NON modificare formule di scoring
# - NON cambiare backend LLM
# Solo monitoring, alerting, sanity checks, presentazione UX.
# Vincolo Karpathy attivo.
# ============================================================

import logging
import json
from google import genai
from google.genai import types
from config.ob1_config import GEMINI_API_KEY
from src.notifier import admin_alert

logger = logging.getLogger(__name__)


class OB1Intelligence:

    def __init__(self):
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not found. Intelligence engine will be disabled.")
            self.client = None
        else:
            self.client = genai.Client(api_key=GEMINI_API_KEY)

    @staticmethod
    def _extract_first_json_array(text: str) -> list:
        """Extract the first balanced JSON array from text."""
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

    def analyze_scraped_data(self, data_samples: list) -> list:
        """
        Analyze scraped signals via Gemini direct context injection.
        No File Search Store — content passed directly in the prompt.
        """
        if not self.client:
            return []
        if not data_samples:
            logger.warning("No data samples to analyze.")
            return []

        # Build context — mark deep-read articles for richer analysis
        lines = []
        for item in data_samples:
            tag = "[FULL ARTICLE]" if item.get('deep_read') else "[SNIPPET]"
            lines.append(f"## {tag} {item.get('title', 'Unknown Title')}")
            lines.append(f"URL: {item.get('url', 'N/A')}")
            lines.append(f"Content: {item.get('content', '')}")
            lines.append("---")
        context = "\n".join(lines)

        system_instruction = """Sei il Direttore Sportivo del sistema OB1 Radar. Analizzi intelligence calcistica globale per identificare talenti Under 20 con MASSIMA ASIMMETRIA INFORMATIVA — giocatori il cui valore reale supera di gran lunga la loro visibilità mediatica.

Alcuni articoli sono completi [FULL ARTICLE], altri sono snippet brevi [SNIPPET]. Dai MOLTO PIU' PESO agli articoli completi perché contengono dettagli tattici, statistiche e contesto che gli snippet non hanno.

Ragiona come un DS che cerca valore nascosto, non hype."""

        prompt = f"""Analizza i seguenti segnali. Cerca giocatori Under 20 promettenti.

PRIORITA' DI ANALISI:
1. Dai precedenza ai documenti [FULL ARTICLE] — contengono dettagli reali.
2. Cerca menzioni di statistiche concrete (gol, assist, minuti, prestazioni).
3. Valuta il CONTESTO: sovraperformare in una squadra debole o lega minore vale di piu'.

SCORING — sii preciso, non inflazionare:
- 88-100: Statistiche eccezionali documentate + ZERO visibilità internazionale. Massima rarità.
- 75-87: Dati concreti (gol/assist/minuti) + club minore o lega secondaria. Asimmetria reale.
- 60-74: Menzione con qualche dato, ma contesto incerto o club semi-noto.
- 0-59: Già noto, trasferimento completato, o dati insufficienti per valutare.

PENALIZZAZIONI obbligatorie:
- Club top (Real Madrid, Man City, PSG, Bayern, Barcellona, Juventus): score <= 20
- Trasferimento > €5M già completato: score <= 30
- Giocatore già con pagina Wikipedia internazionale estesa: score <= 40

USA lo score intero range — non dare 80+ a tutto. Un 65 è legittimo.

Restituisci SOLO un JSON array:
[
    {{
        "player_name": "Full Name",
        "age": 17,
        "position": "CB/LB/RB/CM/CAM/CDM/LW/RW/ST/GK",
        "club": "Current Club Name",
        "league": "League Name",
        "score": 0-100,
        "reason": "Technical analysis based on found data...",
        "is_ghost": true/false,
        "region": "English canonical: Brazil / Argentina / Nigeria / Japan / South Korea / France / Serbia / etc.",
        "sources": ["URL1", "URL2"]
    }}
]
If age/position/club are not mentioned, use null.

--- SEGNALI ---
{context}"""

        try:
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    max_output_tokens=16384
                )
            )

            text = response.text.replace('```json', '').replace('```', '').strip()
            results = self._extract_first_json_array(text)

            if not results:
                logger.warning(f"Could not parse JSON from response ({len(text)} chars)")
                admin_alert("WARN", "intelligence/parse", f"Gemini JSON parse failed — response was {len(text)} chars (expected JSON array). Anomaly detection skipped for this run.")
                return []

            return results
        except Exception as e:
            logger.error(f"Intelligence analysis failed: {e}")
            return []


if __name__ == "__main__":
    intelligence = OB1Intelligence()
    samples = [
        {"title": "Mora shines in Tijuana", "content": "15-year old Gilberto Mora scored a brace in his debut.", "url": "local-mx-news.com/123"}
    ]
    results = intelligence.analyze_scraped_data(samples)
    print(json.dumps(results, indent=2))
