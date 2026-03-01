#!/usr/bin/env python3
"""
OB1 Global Scout - Intelligence Engine
Powered by Gemini to analyze raw intelligence and identify deep-context anomalies.
"""

import logging
import json
import time
from pathlib import Path
from datetime import datetime
from google import genai
from google.genai import types
from config.ob1_config import GEMINI_API_KEY

# Setup logging
logger = logging.getLogger(__name__)

class OB1Intelligence:
    STORE_NAME = "ob1-global-radar-kb"

    def __init__(self):
        if not GEMINI_API_KEY:
            logger.warning("GEMINI_API_KEY not found. Intelligence engine will be disabled.")
            self.client = None
        else:
            self.client = genai.Client(api_key=GEMINI_API_KEY)
            self.model_id = 'gemini-2.0-flash'

    def _get_or_create_store(self):
        """Retrieve or create the persistent File Search Store."""
        try:
            stores = list(self.client.file_search_stores.list())
            for s in stores:
                if self.STORE_NAME in s.display_name:
                    return s
        except Exception:
            pass
        
        logger.info(f"Creating new File Search Store: {self.STORE_NAME}")
        return self.client.file_search_stores.create(config={'display_name': self.STORE_NAME})

    @staticmethod
    def _extract_first_json_array(text: str) -> list:
        """Extract the first balanced JSON array from text (handles duplicated/thinking output)."""
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

    def ingest_data(self, data_samples: list):
        """Upload raw scraped data as a markdown file to the RAG store."""
        if not self.client or not data_samples:
            return None

        store = self._get_or_create_store()
        
        # Prepare content — mark deep-read articles for richer analysis
        lines = [f"# OB1 Radar Intelligence Update - {datetime.now().isoformat()}", ""]
        for item in data_samples:
            is_deep = item.get('deep_read', False)
            tag = "[FULL ARTICLE]" if is_deep else "[SNIPPET]"
            lines.append(f"## {tag} {item.get('title', 'Unknown Title')}")
            lines.append(f"URL: {item.get('url', 'N/A')}")
            lines.append(f"Content: {item.get('content', '')}")
            lines.append("\n---")
        
        content = "\n".join(lines)
        temp_path = Path(__file__).parent.parent / "data" / f"ingest_{int(time.time())}.md"
        temp_path.parent.mkdir(exist_ok=True)
        temp_path.write_text(content, encoding='utf-8')

        try:
            logger.info(f"Uploading {len(data_samples)} signals to Gemini File Search...")
            operation = self.client.file_search_stores.upload_to_file_search_store(
                file=str(temp_path),
                file_search_store_name=store.name,
                config={'mime_type': 'text/plain'}
            )
            # Wait for processing
            while not operation.done:
                time.sleep(1)
                operation = self.client.operations.get(operation)
            return store.name
        except Exception as e:
            logger.error(f"Ingestion failed: {e}")
            return None
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def analyze_scraped_data(self, data_samples: list):
        """
        Analyze anomalies using True RAG (Gemini File Search).
        """
        if not self.client:
            return []

        # 1. Ingest latest data into the store (Bug 5: Real RAG)
        store_name = self.ingest_data(data_samples)
        if not store_name:
            logger.warning("Falling back to basic prompt due to ingestion failure.")
            # Basic fallback if needed, but we aim for RAG
            return []

        # 2. Query with File Search tool
        system_instruction = """Sei il Direttore Sportivo del sistema OB1 Radar. Analizzi intelligence calcistica globale per identificare talenti Under 20 con MASSIMA ASIMMETRIA INFORMATIVA — giocatori il cui valore reale supera di gran lunga la loro visibilità mediatica.

Hai accesso a documenti nel File Search Store. Alcuni sono articoli completi [FULL ARTICLE], altri sono snippet brevi [SNIPPET]. Dai MOLTO PIU' PESO agli articoli completi perché contengono dettagli tattici, statistiche e contesto che gli snippet non hanno.

Ragiona come un DS che cerca valore nascosto, non hype."""

        prompt = """Analizza i documenti. Cerca giocatori Under 20 promettenti.

PRIORITA' DI ANALISI:
1. Dai precedenza ai documenti [FULL ARTICLE] — contengono dettagli reali.
2. Cerca menzioni di statistiche concrete (gol, assist, minuti, prestazioni).
3. Valuta il CONTESTO: sovraperformare in una squadra debole o lega minore vale di piu'.

SCORING:
- 85-100: Prestazioni eccezionali + zero presenza mainstream. "Fantasma totale".
- 70-84: Talento confermato da dati concreti ma ancora sotto il radar internazionale.
- 50-69: Menzione interessante ma dati insufficienti per confermare.
- 0-49: Gia' noto al mainstream, nessuna asimmetria.

PENALIZZAZIONI:
- Giocatori gia' in club top (Real Madrid, Man City, PSG, Bayern): score <= 20
- Giocatori con trasferimenti > 10M gia' completati: score <= 30

Restituisci SOLO un JSON array:
[
    {
        "player_name": "Nome Completo",
        "age": 17,
        "position": "CB/LW/CM/ST/GK/...",
        "club": "Nome Club Attuale",
        "league": "Nome Lega",
        "score": 0-100,
        "reason": "Analisi tecnica basata sui dati trovati negli articoli...",
        "is_ghost": true/false,
        "region": "Area geografica",
        "sources": ["URL1", "URL2"]
    }
]
Se eta'/posizione/club non sono menzionati, usa null."""

        try:
            # Bug 5: Transition to gemini-2.5-flash as suggested in official snippet
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[types.Tool(file_search=types.FileSearch(file_search_store_names=[store_name]))],
                    temperature=0.2,
                    max_output_tokens=16384
                )
            )
            
            # Parse the first complete JSON array using balanced brackets
            text = response.text.replace('```json', '').replace('```', '').strip()
            results = self._extract_first_json_array(text)

            if not results:
                logger.warning(f"Could not parse JSON from response ({len(text)} chars)")
                return []

            return results
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
