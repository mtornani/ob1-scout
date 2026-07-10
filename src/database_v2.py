#!/usr/bin/env python3
"""
OB1 v2 — Store entità-centrico (Fase B0)

Un giocatore è un'ENTITÀ che accumula PROVE (evidences) nel tempo, non "un nome
in un articolo". Su ogni entità si valuta l'identità e il gate di pubblicazione;
gli OUTCOME (trasferimenti, convocazioni, hype mainstream) sono la ground truth
per calibrare lo scoring.

DB separato (`data/ob1_v2.db`): NON tocca la produzione (`ob1_global.db`).

Schema:
  players   — l'entità, con flag di qualità identità e gate di pubblicazione
  evidences — ogni osservazione da una fonte (source_url + testo + estrazione)
  outcomes  — eventi verificabili (mainstream_hype / transfer / call_up / debut)
"""

import sqlite3
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_DB = Path(__file__).parent.parent / "data" / "ob1_v2.db"


def normalize_name(name: str) -> str:
    """Nome normalizzato per matching (accenti via, minuscolo, trim)."""
    if not name:
        return ""
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_name = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_name.lower().strip()


def domain_of(url: str) -> str:
    """Dominio di una URL, senza www."""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def assess_identity(name: str, club: str, age, source_count: int) -> dict:
    """
    Valuta la qualità dell'identità di un giocatore e il gate di pubblicazione.
    Ritorna dict con i flag e i motivi di review. NON decide il genere (scelta
    di prodotto ancora aperta): quel campo resta 'unknown'.
    """
    name = (name or "").strip()
    tokens = [t for t in name.split() if t]
    token_count = len(tokens)

    # Handle/soprannome: contiene cifre o underscore, oppure token singolo
    # concatenato in camelCase (es. "Gustavogoes", "Cauazinn_.08").
    looks_like_handle = any(ch.isdigit() for ch in name) or "_" in name

    flags = []
    if token_count <= 1:
        flags.append("nome_singolo")
    if looks_like_handle:
        flags.append("handle_o_soprannome")
    if not (club and str(club).strip()):
        flags.append("club_mancante")
    if age is None:
        flags.append("eta_mancante")
    # La produzione legacy salvava 1 sola source_url per giocatore, a
    # prescindere dalle ri-detection: la corroborazione multi-fonte non è
    # verificabile sui dati vecchi. Va segnalato, non finto.
    if (source_count or 0) < 2:
        flags.append("fonte_singola")

    identity_complete = (
        token_count >= 2
        and not looks_like_handle
        and bool(club and str(club).strip())
        and age is not None
    )
    corroborated = (source_count or 0) >= 2
    publishable = identity_complete and corroborated

    return {
        "name_token_count": token_count,
        "identity_complete": identity_complete,
        "corroborated": corroborated,
        "publishable": publishable,
        "review_flags": ",".join(flags),
    }


class OB1DatabaseV2:
    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or DEFAULT_DB)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._conn() as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_name TEXT NOT NULL,
                    normalized_name TEXT,
                    name_token_count INTEGER,
                    gender TEXT DEFAULT 'unknown',
                    age INTEGER,
                    position TEXT,
                    club TEXT,
                    league TEXT,
                    region TEXT,
                    is_ghost INTEGER DEFAULT 0,
                    legacy_score REAL,
                    detection_count INTEGER DEFAULT 1,
                    evidence_count INTEGER DEFAULT 0,
                    first_detected TEXT,
                    last_seen TEXT,
                    identity_complete INTEGER DEFAULT 0,
                    corroborated INTEGER DEFAULT 0,
                    publishable INTEGER DEFAULT 0,
                    review_flags TEXT,
                    legacy_id INTEGER,
                    created_at TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS evidences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    source_url TEXT,
                    source_domain TEXT,
                    observed_at TEXT,
                    raw_content TEXT,
                    origin TEXT,
                    FOREIGN KEY (player_id) REFERENCES players(id)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    outcome_type TEXT,
                    outcome_date TEXT,
                    source_url TEXT,
                    source_domain TEXT,
                    lead_time_days INTEGER,
                    suspect INTEGER DEFAULT 0,
                    note TEXT,
                    FOREIGN KEY (player_id) REFERENCES players(id)
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_ev_player ON evidences(player_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_out_player ON outcomes(player_id)")
            conn.commit()

    def add_player(self, canonical_name, age=None, position=None, club=None,
                   league=None, region=None, is_ghost=False, legacy_score=None,
                   detection_count=1, source_count=1, first_detected=None,
                   last_seen=None, legacy_id=None, created_at=None) -> int:
        idn = assess_identity(canonical_name, club, age, source_count)
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO players
                (canonical_name, normalized_name, name_token_count, gender, age,
                 position, club, league, region, is_ghost, legacy_score,
                 detection_count, evidence_count, first_detected, last_seen,
                 identity_complete, corroborated, publishable, review_flags,
                 legacy_id, created_at)
                VALUES (?, ?, ?, 'unknown', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                canonical_name, normalize_name(canonical_name),
                idn["name_token_count"], age, position, club, league, region,
                1 if is_ghost else 0, legacy_score, detection_count,
                first_detected, last_seen,
                1 if idn["identity_complete"] else 0,
                1 if idn["corroborated"] else 0,
                1 if idn["publishable"] else 0,
                idn["review_flags"], legacy_id, created_at,
            ))
            conn.commit()
            return cur.lastrowid

    def add_evidence(self, player_id, source_url=None, observed_at=None,
                     raw_content=None, origin="pipeline") -> int:
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO evidences
                (player_id, source_url, source_domain, observed_at, raw_content, origin)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (player_id, source_url, domain_of(source_url), observed_at,
                  raw_content, origin))
            conn.execute("""
                UPDATE players SET evidence_count =
                    (SELECT COUNT(*) FROM evidences WHERE player_id = ?)
                WHERE id = ?
            """, (player_id, player_id))
            conn.commit()
            return cur.lastrowid

    def add_outcome(self, player_id, outcome_type, outcome_date=None,
                    source_url=None, lead_time_days=None, suspect=False,
                    note=None) -> int:
        with self._conn() as conn:
            cur = conn.execute("""
                INSERT INTO outcomes
                (player_id, outcome_type, outcome_date, source_url, source_domain,
                 lead_time_days, suspect, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (player_id, outcome_type, outcome_date, source_url,
                  domain_of(source_url), lead_time_days,
                  1 if suspect else 0, note))
            conn.commit()
            return cur.lastrowid


if __name__ == "__main__":
    db = OB1DatabaseV2()
    print(f"Schema v2 inizializzato: {db.db_path}")
