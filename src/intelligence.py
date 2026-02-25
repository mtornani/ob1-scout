#!/usr/bin/env python3
"""
OB1 Global Scout - Intelligence Engine
Powered by Gemini to analyze raw intelligence and identify deep-context anomalies.
"""

import logging
import json
from pathlib import Path
import google.generativeai as genai
from config.ob1_config import GEMINI_API_KEY

# Setup logging
logger = logging.getLogger(__name__)

class OB1Intelligence:
    def __init__(self):
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not found. Intelligence engine will be disabled.")
            self.model = None
        else:
            genai.configure(api_key=GEMINI_API_KEY)
            self.model = genai.GenerativeModel('gemini-flash-latest') # Standard alias for broader access

    def analyze_scraped_data(self, data_samples: list):
        """
        Analyze raw scraped data to find high-value anomalies.
        In a full RAG implementation, this would query a file-search index.
        For now, we use a structured prompt with the latest samples.
        """
        if not self.model:
            return []

        prompt = f"""
        Sei l'Analista Senior del sistema OB1 Radar, un'intelligence calcistica specilaizzata in Anomalie Globali U20.
        Il tuo compito è analizzare i dati grezzi per identificare giocatori che mostrano MASSIMA ASIMMETRIA INFORMATIVA.

        DATI GREZZI:
        {json.dumps(data_samples, indent=2)}

        REGOLE CRITICHE PER LO SCORING (0-100):
        1. PENALIZZA DURAMENTE (> -50 punti) giocatori già famosi, figli d'arte (es. figli di Marcelo, CR7), o che giocano già stabilmente in prime squadre di Top 5 leghe europee.
        2. PREMIA (> 80 punti) "Fantasmi": debutti in leghe esotiche, news solo in lingua locale, rumor di scout europei in mercati "chiusi".
        3. Identifica se è un "Ghost" (ZERO presenza sui radar mainstream).
        4. SPIEGAZIONE TATTICA: Non limitarti a "buon giocatore". Spiega PERCHÉ è un'anomalia (es. "Isolato segnale da forum locale in Paraguay, nessun link Transfermarkt").

        Restituisci solo un JSON array:
        [
            {{
                "player_name": "Nome",
                "score": 0-100,
                "reason": "Spiegazione breve e tecnica...",
                "is_ghost": true/false,
                "region": "Area geografica"
            }}
        ]
        """

        try:
            response = self.model.generate_content(prompt)
            # Basic cleanup of markdown JSON blocks if present
            text = response.text.replace('```json', '').replace('```', '').strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Intelligence analysis failed: {e}")
            return []

if __name__ == "__main__":
    intelligence = OB1Intelligence()
    # Test data
    samples = [
        {"title": "Mora shines in Tijuana", "content": "15-year old Gilberto Mora scored a brace in his debut.", "url": "local-mx-news.com/123"}
    ]
    results = intelligence.analyze_scraped_data(samples)
    print(json.dumps(results, indent=2))
