#!/usr/bin/env python3
"""
OB1 Global Scout - SQLite Database Module
Handles storage of detected anomalies, player deduplication, tracking, and Lead Time.
"""

import sqlite3
import json
import logging
import unicodedata
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def _normalize_name(name: str) -> str:
    """Normalize a player name for fuzzy matching."""
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_name = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_name.lower().strip()


def _names_match(name_a: str, name_b: str) -> bool:
    """Fuzzy match two player names (handles accents, partial names)."""
    a = _normalize_name(name_a)
    b = _normalize_name(name_b)
    if a == b:
        return True
    if a in b or b in a:
        return True
    parts_a = set(p for p in a.split() if len(p) > 2)
    parts_b = set(p for p in b.split() if len(p) > 2)
    if parts_a and parts_b and parts_a & parts_b:
        return True
    return False


class OB1Database:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "data" / "ob1_global.db")

        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        """Initialize database tables and migrate schema if needed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

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

            # Migrate anomalies
            existing = {row[1] for row in cursor.execute("PRAGMA table_info(anomalies)").fetchall()}
            anomaly_migrations = {
                'age': 'INTEGER',
                'position': 'TEXT',
                'club': 'TEXT',
                'league': 'TEXT',
                'stats_summary': 'TEXT',
                'is_ghost': 'INTEGER DEFAULT 0',
                'first_detected': 'TIMESTAMP',
                'last_seen': 'TIMESTAMP',
                'detection_count': 'INTEGER DEFAULT 1',
                'score_history': 'TEXT',
                'source_count': 'INTEGER DEFAULT 1',
            }
            for col, col_type in anomaly_migrations.items():
                if col not in existing:
                    cursor.execute(f'ALTER TABLE anomalies ADD COLUMN {col} {col_type}')
                    logger.info(f"Migrated DB: added column '{col}'")

            # Migrate lead_times
            lt_existing = {row[1] for row in cursor.execute("PRAGMA table_info(lead_times)").fetchall()}
            if 'mainstream_source' not in lt_existing:
                cursor.execute("ALTER TABLE lead_times ADD COLUMN mainstream_source TEXT")
                logger.info("Migrated DB: added column 'mainstream_source' to lead_times")

            conn.commit()
            logger.info("Database initialized successfully.")

    def _find_existing_player(self, cursor, player_name: str):
        """Find an existing player by fuzzy name match. Returns dict or None."""
        cursor.execute('SELECT id, player_name, score, detection_count, score_history, source_url FROM anomalies')
        for row in cursor.fetchall():
            if _names_match(player_name, row[1]):
                return {
                    'id': row[0], 'player_name': row[1], 'score': row[2],
                    'detection_count': row[3] or 1, 'score_history': row[4],
                    'source_url': row[5]
                }
        return None

    def add_anomaly(self, player_name, source_url, score, raw_content, region,
                    age=None, position=None, club=None, league=None,
                    stats_summary=None, is_ghost=False):
        """Add or update an anomaly with deduplication and tracking."""
        now = datetime.now().isoformat()
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                existing = self._find_existing_player(cursor, player_name)

                if existing:
                    # UPDATE existing player — merge data, track history
                    old_history = existing.get('score_history') or '[]'
                    try:
                        history = json.loads(old_history)
                    except (json.JSONDecodeError, TypeError):
                        history = []
                    history.append({'score': score, 'date': now})
                    history = history[-20:]

                    new_count = (existing.get('detection_count') or 1) + 1
                    best_score = max(score, existing.get('score') or 0)

                    cursor.execute('''
                        UPDATE anomalies SET
                            score = ?,
                            raw_content = ?,
                            region = COALESCE(?, region),
                            last_seen = ?,
                            detection_count = ?,
                            score_history = ?,
                            age = COALESCE(?, age),
                            position = COALESCE(?, position),
                            club = COALESCE(?, club),
                            league = COALESCE(?, league),
                            stats_summary = COALESCE(?, stats_summary),
                            is_ghost = ?
                        WHERE id = ?
                    ''', (best_score, raw_content, region, now, new_count,
                          json.dumps(history),
                          age, position, club, league, stats_summary,
                          1 if is_ghost else 0, existing['id']))

                    logger.info(f"[Dedup] Updated {player_name} (#{new_count}, best={best_score:.0f})")
                    conn.commit()
                    # Return 'updated' so pipeline knows this is not a new detection
                    return 'updated'
                else:
                    # INSERT new player
                    history = json.dumps([{'score': score, 'date': now}])
                    cursor.execute('''
                        INSERT OR IGNORE INTO anomalies
                        (player_name, source_url, score, raw_content, region,
                         age, position, club, league, stats_summary, is_ghost,
                         first_detected, last_seen, detection_count, score_history, source_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 1)
                    ''', (player_name, source_url, score, raw_content, region,
                          age, position, club, league, stats_summary,
                          1 if is_ghost else 0, now, now, history))

                    cursor.execute('''
                        INSERT OR IGNORE INTO lead_times (player_name, ob1_detection_date)
                        VALUES (?, ?)
                    ''', (player_name, now))

                    conn.commit()
                    logger.info(f"[New] Added {player_name} (score={score:.0f})")
                    return 'new'
        except Exception as e:
            logger.error(f"Error adding anomaly: {e}")
            return False

    def get_top_anomalies(self, limit=5):
        """Return top anomalies by score for digest messages."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT player_name, score, region, detection_count
                FROM anomalies
                ORDER BY score DESC, detection_count DESC
                LIMIT ?
            ''', (limit,))
            cols = ['player_name', 'score', 'region', 'detection_count']
            return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def get_tracked_players(self):
        """Get all players currently being tracked for Lead Time."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT l.player_name, l.ob1_detection_date, a.score, a.detection_count
                FROM lead_times l
                LEFT JOIN anomalies a ON TRIM(l.player_name) = TRIM(a.player_name)
                WHERE l.status = 'tracking'
            ''')
            return cursor.fetchall()

    def record_mainstream_hype(self, player_name, hype_date=None, source_url=None):
        """Record when mainstream media started talking about a player."""
        if hype_date is None:
            hype_date = datetime.now()

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT ob1_detection_date FROM lead_times WHERE player_name = ?', (player_name,))
                result = cursor.fetchone()

                if result:
                    ob1_date = datetime.fromisoformat(result[0])
                    delta = (hype_date - ob1_date).days
                    cursor.execute('''
                        UPDATE lead_times
                        SET mainstream_hype_date = ?, lead_time_days = ?,
                            status = 'completed', mainstream_source = ?
                        WHERE player_name = ?
                    ''', (hype_date.isoformat(), delta, source_url, player_name))
                    conn.commit()
                    logger.info(f"Lead Time for {player_name}: {delta} days (source: {source_url})")
                    return delta
                return None
        except Exception as e:
            logger.error(f"Error recording hype: {e}")
            return None


if __name__ == "__main__":
    db = OB1Database()
    db.add_anomaly("Gilberto Mora", "https://example.com/mora", 85.5,
                   "Rising star in Mexico", "north_america",
                   age=15, position="ST", club="Tijuana U17")
    print("Database test run complete.")
