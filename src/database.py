#!/usr/bin/env python3
"""
OB1 Global Scout - SQLite Database Module
Handles storage of detected anomalies and tracks Lead Time vs Mainstream Media.
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime

# Setup logging
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger(__name__)

class OB1Database:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "data" / "ob1_global.db")
        
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        """Initialize database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Table for detected anomalies
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS anomalies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL,
                    detection_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    source_url TEXT,
                    score REAL,
                    raw_content TEXT,
                    region TEXT,
                    status TEXT DEFAULT 'detected',
                    UNIQUE(player_name, source_url)
                )
            ''')
            
            # Table for tracking Lead Time (OB1 vs Mainstream)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS lead_times (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_name TEXT NOT NULL,
                    ob1_detection_date TIMESTAMP NOT NULL,
                    mainstream_hype_date TIMESTAMP,
                    lead_time_days INTEGER,
                    status TEXT DEFAULT 'tracking',
                    UNIQUE(player_name)
                )
            ''')
            
            conn.commit()
            logger.info("Database initialized successfully.")

    def add_anomaly(self, player_name, source_url, score, raw_content, region):
        """Add a new anomaly or update existing one."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO anomalies (player_name, source_url, score, raw_content, region)
                    VALUES (?, ?, ?, ?, ?)
                ''', (player_name, source_url, score, raw_content, region))
                
                # Also ensure player is in lead_times table for tracking
                cursor.execute('''
                    INSERT OR IGNORE INTO lead_times (player_name, ob1_detection_date)
                    VALUES (?, ?)
                ''', (player_name, datetime.now().isoformat()))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding anomaly: {e}")
            return False

    def get_tracked_players(self):
        """Get all players currently being tracked for Lead Time."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT player_name, ob1_detection_date FROM lead_times WHERE status = "tracking"')
            return cursor.fetchall()

    def record_mainstream_hype(self, player_name, hype_date=None):
        """Record when mainstream media started talking about a player."""
        if hype_date is None:
            hype_date = datetime.now()
        
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Get OB1 detection date
                cursor.execute('SELECT ob1_detection_date FROM lead_times WHERE player_name = ?', (player_name,))
                result = cursor.fetchone()
                
                if result:
                    ob1_date = datetime.fromisoformat(result[0])
                    delta = (hype_date - ob1_date).days
                    
                    cursor.execute('''
                        UPDATE lead_times 
                        SET mainstream_hype_date = ?, lead_time_days = ?, status = 'completed'
                        WHERE player_name = ?
                    ''', (hype_date.isoformat(), delta, player_name))
                    conn.commit()
                    logger.info(f"Recorded Lead Time for {player_name}: {delta} days.")
                    return delta
                return None
        except Exception as e:
            logger.error(f"Error recording hype: {e}")
            return None

if __name__ == "__main__":
    db = OB1Database()
    db.add_anomaly("Gilberto Mora", "https://example.com/mora", 85.5, "Rising star in Mexico", "north_america")
    print("Database test run complete.")
