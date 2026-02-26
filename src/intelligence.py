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

    def ingest_data(self, data_samples: list):
        """Upload raw scraped data as a markdown file to the RAG store."""
        if not self.client or not data_samples:
            return None

        store = self._get_or_create_store()
        
        # Prepare content
        lines = [f"# OB1 Radar Intelligence Update - {datetime.now().isoformat()}", ""]
        for item in data_samples:
            lines.append(f"## {item.get('title', 'Unknown Title')}")
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
                file_search_store_name=store.name
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
        system_instruction = """
        Sei l'Analista Senior del sistema OB1 Radar, un'intelligence calcistica specilaizzata in Anomalie Globali U20.
        Hai accesso a documenti grezzi nel tuo File Search Store.
        Il tuo compito è identificare giocatori che mostrano MASSIMA ASIMMETRIA INFORMATIVA.
        """

        prompt = """
        Analizza i nuovi documenti. Restituisci un JSON array di giocatori Under 20 promettenti menzionati.
        
        REGOLE CRITICHE:
        1. PENALIZZA DURAMENTE (> -50 punti) giocatori già famosi.
        2. PREMIA (> 80 punti) "Fantasmi" (debutti in leghe esotiche, news solo locali).
        3. Identifica se è un "Ghost" (ZERO presenza mainstream).
        
        Restituisci SOLO un JSON array:
        [
            {
                "player_name": "Nome",
                "score": 0-100,
                "reason": "Spiegazione tecnica...",
                "is_ghost": true/false,
                "region": "Area geografica",
                "sources": ["URL1", "URL2"]
            }
        ]
        """

        try:
            # Bug 5: Transition to gemini-2.5-flash as suggested in official snippet
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[types.Tool(file_search=types.FileSearch(file_search_store_names=[store_name]))],
                    temperature=0.2,
                    max_output_tokens=2048
                )
            )
            
            # Extract citations/grounding metadata if available
            grounding = response.candidates[0].grounding_metadata
            found_sources = []
            if grounding and grounding.grounding_chunks:
                found_sources = list({c.retrieved_context.title for c in grounding.grounding_chunks if c.retrieved_context})
            
            text = response.text.replace('```json', '').replace('```', '').strip()
            import re
            match = re.search(r'\[.*\]', text, re.DOTALL)
            if match:
                results = json.loads(match.group())
                # Enrich with grounding sources if missing
                for res in results:
                    if 'sources' not in res or not res['sources']:
                        res['sources'] = found_sources
                return results
            return []
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
