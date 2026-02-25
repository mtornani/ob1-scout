#!/usr/bin/env python3
"""
OB1 Global Scout - Dashboard Data Generator
Exports database content to JSON for the frontend HUD.
"""

import sqlite3
import json
from pathlib import Path

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
        # Fetch high-score anomalies (High Purity Filter)
        c.execute('''
            SELECT a.*, l.lead_time_days, l.ob1_detection_date 
            FROM anomalies a
            JOIN lead_times l ON a.player_name = l.player_name
            WHERE a.score >= 70
            ORDER BY a.score DESC, a.detection_date DESC
        ''')
        rows = c.fetchall()
        
        data = [dict(row) for row in rows]
        
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        print(f"✅ Dashboard data updated: {len(data)} items.")
    except Exception as e:
        print(f"Error generating dashboard JSON: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    generate_json()
