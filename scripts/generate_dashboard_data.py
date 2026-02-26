#!/usr/bin/env python3
"""
OB1 Global Scout - Dashboard Data Generator
Exports database content to JSON for the frontend HUD.
"""

import sqlite3
import json
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "ob1_global.db"
OUTPUT_PATH = BASE_DIR / "docs" / "data" / "anomalies.json"

def generate_json():
    (BASE_DIR / "docs" / "data").mkdir(parents=True, exist_ok=True)
    
    if not DB_PATH.exists():
        logger.warning("Database not found. Generating empty dashboard.")
        # Create empty placeholder if no DB
        with open(OUTPUT_PATH, 'w') as f:
            json.dump([], f)
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    try:
        # Diagnostic: check total counts
        c.execute("SELECT COUNT(*) FROM anomalies")
        total_anomalies = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM lead_times")
        total_leads = c.fetchone()[0]
        logger.info(f"DB Check: {total_anomalies} anomalies, {total_leads} lead_time entries.")

        # Fetch high-score anomalies (High Purity Filter)
        c.execute('''
            SELECT a.*, l.lead_time_days, l.ob1_detection_date 
            FROM anomalies a
            JOIN lead_times l ON TRIM(a.player_name) = TRIM(l.player_name)
            WHERE a.score >= 70
            ORDER BY a.score DESC, a.detection_date DESC
        ''')
        rows = c.fetchall()
        
        data = [dict(row) for row in rows]
        
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        logger.info(f"✅ Dashboard data updated: {len(data)} items (filtered from {total_anomalies}).")
    except Exception as e:
        logger.error(f"Error generating dashboard JSON: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    generate_json()
