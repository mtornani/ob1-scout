#!/usr/bin/env python3
"""
OB1 Global Scout - True RAG Intelligence Engine
Powered by Gemini File Search (Google Generative AI).
"""

import os
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load env variables (API Keys)
load_dotenv()

# Setup logging
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("google-genai module not found. Run: pip install google-genai")

class GlobalRAGScorer:
    STORE_PREFIX = "ob1-global-radar-"
    MODEL = "gemini-2.5-flash"

    def __init__(self, db_path: str = "data/ob1_global.db"):
        self.db_path = Path(__file__).parent.parent / db_path
        self._init_db()

        if GEMINI_AVAILABLE:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                logger.warning("GEMINI_API_KEY missing.")
                self.client = None
            else:
                self.client = genai.Client(api_key=api_key)
        else:
            self.client = None

    def _init_db(self):
        """Initialize SQLite database for Lead Time tracking."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS anomalies
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      player_name TEXT,
                      region TEXT,
                      asymmetry_score INTEGER,
                      discovered_at TIMESTAMP,
                      lead_time_days INTEGER,
                      context TEXT,
                      source_url TEXT)''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS knowledge_base
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      content TEXT,
                      url TEXT,
                      date_added TIMESTAMP)''')
        conn.commit()
        conn.close()

    def _get_or_create_store(self, store_suffix: str = "kb"):
        store_display_name = f"{self.STORE_PREFIX}{store_suffix}"
        try:
            stores = list(self.client.file_search_stores.list())
            for s in stores:
                if store_display_name in s.display_name: 
                    return s
        except Exception as e:
            logger.debug(f"Error listing stores: {e}")
            pass
        logger.info(f"Creating new store: {store_display_name}")
        return self.client.file_search_stores.create(config={'display_name': store_display_name})

    def ingest_to_brain(self, scrape_results: list) -> str:
        """Feed new data into the persistent Gemini RAG File Search Store."""
        logger.info(f"🧠 Ingesting {len(scrape_results)} signals into the Global Brain...")
        
        # Also save to SQLite
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        for item in scrape_results:
            c.execute("INSERT INTO knowledge_base (content, url, date_added) VALUES (?, ?, ?)",
                      (item.get('title', '') + " | " + item.get('content', ''), 
                       item.get('url'), datetime.now().isoformat()))
        conn.commit()
        conn.close()

        if not self.client:
            return None

        store = self._get_or_create_store()
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Generate Markdown content for Gemini
        lines = [f"# OB1 GLOBAL INTELLIGENCE", f"Update: {today}", "", "---", ""]
        for res in scrape_results:
            lines.append(f"## {res.get('title', 'No Title')}")
            lines.append(f"Source: {res.get('source', 'Unknown')}")
            lines.append(f"URL: {res.get('url', 'N/A')}")
            lines.append(f"Content: {res.get('content', '')}")
            lines.append("\n---")
        
        content = "\n".join(lines)
        temp_dir = Path(__file__).parent.parent / 'data'
        temp_dir.mkdir(exist_ok=True)
        temp_path = temp_dir / f"ingest_global_{int(time.time())}.md"
        temp_path.write_text(content, encoding='utf-8')
        
        try:
            logger.info("Uploading knowledge to Gemini...")
            operation = self.client.file_search_stores.upload_to_file_search_store(
                file=str(temp_path),
                file_search_store_name=store.name
            )
            while not operation.done:
                time.sleep(2)
                operation = self.client.operations.get(operation)
            logger.info(f"Success! {len(scrape_results)} items indexed.")
            return store.name
        except Exception as e:
            logger.error(f"Ingest Error: {e}")
            return None
        finally:
            if temp_path.exists(): 
                temp_path.unlink()

    def get_existing_anomaly(self, player_name: str) -> dict:
        """Fetch existing anomaly from DB to calculate exact Lead Time."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM anomalies WHERE player_name LIKE ? ORDER BY discovered_at ASC LIMIT 1", (f"%{player_name}%",))
        row = c.fetchone()
        conn.close()
        return dict(row) if row else None

    def query_rag_engine(self, store_name: str) -> list:
        """
        Ask Gemini to analyze the RAG store and extract true anomalies.
        """
        if not self.client or not store_name:
            logger.warning("Using Mock RAG Scoring due to missing API key or store")
            return []

        system_instruction = """
        Sei il motore di intelligence OB1 Global Radar.
        Il tuo obiettivo è scovare "Anomalie" calcistiche (giocatori Under 20) in tutto il mondo
        basandoti sui documenti memorizzati nel tuo File Search Store.
        Cerca talenti di cui si inizia a parlare in contesti locali (Sud America, Africa, Est Europa, J-League).
        """

        query = """
        Analizza i documenti forniti. Estrai un array JSON di giocatori Under 20 promettenti menzionati.
        Calcola un "Information Asymmetry Score" (0-100).
        - 80-100: Sta facendo prestazioni eccezionali ma non è mainstream (non ci sono offerte da 20M+ o squadre come Real Madrid/City). E' un "Fantasma".
        - 0-40: Giocatore molto noto, il prezzo è già esploso.

        Rispondi ESCLUSIVAMENTE con un array JSON in questo formato:
        [
          {"player_name": "Nome", "region": "Regione (es. Sud America)", "asymmetry_score": 90, "context": "Perchè è un'anomalia"}
        ]
        """

        try:
            response = self.client.models.generate_content(
                model=self.MODEL,
                contents=query,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=[
                        types.Tool(
                            file_search=types.FileSearch(
                                file_search_store_names=[store_name]
                            )
                        )
                    ],
                    temperature=0.2,
                    max_output_tokens=2048
                )
            )
            
            text = response.text
            start = text.find('[')
            end = text.rfind(']') + 1
            if start != -1 and end != -1:
                return json.loads(text[start:end])
            return []
        except Exception as e:
            logger.error(f"RAG LLM Error: {e}")
            return []

    def evaluate_and_store(self, scrape_results: list):
        """Main pipeline sequence for scoring."""
        # 1. Ingest to RAG
        store_name = self.ingest_to_brain(scrape_results)
        
        # 2. Analyze with RAG LLM
        anomalies_raw = self.query_rag_engine(store_name)
        
        # 3. Store High Value Anomalies & Calculate Exact Lead Time
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        processed_anomalies = []
        
        for anomaly in anomalies_raw:
            if anomaly.get('asymmetry_score', 0) >= 60:
                player_name = anomaly['player_name']
                existing = self.get_existing_anomaly(player_name)
                
                lead_time_days = 0
                if existing:
                    # Calculate real lead time based on the DB timestamp difference
                    first_seen = datetime.fromisoformat(existing['discovered_at'])
                    lead_time_days = (datetime.now() - first_seen).days
                    anomaly['lead_time_days'] = lead_time_days
                else:
                    anomaly['lead_time_days'] = 0
                    
                processed_anomalies.append(anomaly)
                    
                logger.info(f"✨ ANOMALY: {player_name} (Score: {anomaly['asymmetry_score']}, Lead Time: {lead_time_days}d)")
                
                c.execute('''INSERT INTO anomalies (player_name, region, asymmetry_score, discovered_at, lead_time_days, context)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (player_name, anomaly.get('region'), anomaly['asymmetry_score'],
                           datetime.now().isoformat(), lead_time_days, anomaly.get('context')))
        
        conn.commit()
        conn.close()
        return processed_anomalies

if __name__ == "__main__":
    # Test
    scorer = GlobalRAGScorer()
    print("OB1 Global RAG initialized.")
