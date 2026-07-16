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

import json
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
                    stats_json TEXT,
                    score INTEGER,
                    confidence REAL,
                    notified INTEGER DEFAULT 0,
                    legacy_id INTEGER,
                    created_at TEXT
                )
            """)
            # Migrazione leggera per DB v2 creati prima della colonna notified
            cols = {row[1] for row in c.execute("PRAGMA table_info(players)")}
            if "notified" not in cols:
                c.execute("ALTER TABLE players ADD COLUMN notified INTEGER DEFAULT 0")
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
            # Delta tracking: item (articolo/URL) già visti per fonte, così a
            # ogni run si estrae SOLO il nuovo.
            c.execute("""
                CREATE TABLE IF NOT EXISTS seen_items (
                    source_id TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    first_seen TEXT,
                    PRIMARY KEY (source_id, item_key)
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


    # ---- Delta tracking (Fase B2) ----
    def filter_new_items(self, source_id: str, keys: list) -> list:
        """Ritorna solo le item_key non ancora viste per questa fonte."""
        if not keys:
            return []
        with self._conn() as conn:
            seen = {r[0] for r in conn.execute(
                "SELECT item_key FROM seen_items WHERE source_id=?", (source_id,))}
        return [k for k in keys if k not in seen]

    def mark_seen(self, source_id: str, keys: list, when: str = None):
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen_items (source_id, item_key, first_seen) VALUES (?, ?, ?)",
                [(source_id, k, when) for k in keys])
            conn.commit()

    # ---- Risoluzione entità (Fase B2) ----
    def _names_match(self, a: str, b: str) -> bool:
        """Match prudente (evita di fondere omonimi diversi)."""
        a, b = normalize_name(a), normalize_name(b)
        if not a or not b:
            return False
        if a == b or a in b or b in a:
            return True
        pa = {t for t in a.split() if len(t) > 2}
        pb = {t for t in b.split() if len(t) > 2}
        return bool(pa and pb and len(pa & pb) >= 2)

    def find_player(self, name: str):
        with self._conn() as conn:
            rows = conn.execute("SELECT id, canonical_name FROM players").fetchall()
        for pid, cname in rows:
            if self._names_match(name, cname):
                return pid
        return None

    def _recompute(self, conn, pid: int):
        """Ricalcola fonti-distinte, gate e punteggio v2 per un giocatore."""
        from src.scoring_v2 import score_player  # lazy: evita cicli d'import
        p = conn.execute("""SELECT canonical_name, age, club, league, is_ghost, stats_json
                            FROM players WHERE id=?""", (pid,)).fetchone()
        name, age, club, league, is_ghost, stats_json = p
        n_sources = conn.execute(
            "SELECT COUNT(DISTINCT source_domain) FROM evidences WHERE player_id=? AND source_domain!=''",
            (pid,)).fetchone()[0] or 1
        ev_count = conn.execute(
            "SELECT COUNT(*) FROM evidences WHERE player_id=?", (pid,)).fetchone()[0]
        stats = json.loads(stats_json) if stats_json else {}

        idn = assess_identity(name, club, age, n_sources)
        sc = score_player(age=age, is_ghost=bool(is_ghost), club=club, league=league,
                          stats=stats, n_sources=n_sources, detection_count=ev_count)
        conn.execute("""UPDATE players SET evidence_count=?, identity_complete=?,
                        corroborated=?, publishable=?, review_flags=?, score=?, confidence=?
                        WHERE id=?""",
                     (ev_count, 1 if idn["identity_complete"] else 0,
                      1 if idn["corroborated"] else 0, 1 if idn["publishable"] else 0,
                      idn["review_flags"], sc["score"], sc["confidence"], pid))

    def ingest_observation(self, obs: dict) -> tuple:
        """
        Assorbe un'osservazione (dall'estrattore) nello store entità-centrico:
        risolve l'entità (fuzzy per nome), aggiunge la prova, riempie i campi
        mancanti, fonde le statistiche, e ricalcola gate + punteggio.
        Ritorna (player_id, 'new'|'updated'|'skipped').
        """
        name = (obs.get("name") or "").strip()
        if not name:
            return None, "skipped"
        src_url = obs.get("source_url") or ""
        dom = domain_of(src_url)
        new_stats = obs.get("stats") or {}

        pid = self.find_player(name)
        with self._conn() as conn:
            status = "updated"
            if pid is None:
                cur = conn.execute("""
                    INSERT INTO players
                    (canonical_name, normalized_name, name_token_count, gender, age,
                     position, club, league, region, is_ghost, detection_count,
                     evidence_count, stats_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 0, ?, ?)
                """, (name, normalize_name(name), len([t for t in name.split() if t]),
                      obs.get("gender") or "unknown", obs.get("age"),
                      obs.get("position"), obs.get("club"), obs.get("league"),
                      obs.get("region"), json.dumps(new_stats) if new_stats else None,
                      obs.get("observed_at")))
                pid = cur.lastrowid
                status = "new"
            else:
                # riempi solo i campi mancanti + fondi le statistiche (max per chiave)
                cur_stats = conn.execute("SELECT stats_json FROM players WHERE id=?", (pid,)).fetchone()[0]
                merged = json.loads(cur_stats) if cur_stats else {}
                for k, v in new_stats.items():
                    if isinstance(v, (int, float)):
                        merged[k] = max(merged.get(k, 0), v)
                conn.execute("""
                    UPDATE players SET
                        age = COALESCE(age, ?), position = COALESCE(position, ?),
                        club = COALESCE(club, ?), league = COALESCE(league, ?),
                        region = COALESCE(region, ?),
                        gender = CASE WHEN gender='unknown' THEN ? ELSE gender END,
                        stats_json = ?
                    WHERE id=?
                """, (obs.get("age"), obs.get("position"), obs.get("club"),
                      obs.get("league"), obs.get("region"),
                      obs.get("gender") or "unknown",
                      json.dumps(merged) if merged else None, pid))

            conn.execute("""INSERT INTO evidences
                (player_id, source_url, source_domain, observed_at, raw_content, origin)
                VALUES (?, ?, ?, ?, ?, 'extractor')""",
                (pid, src_url, dom, obs.get("observed_at"),
                 obs.get("evidence_quote"), ))
            self._recompute(conn, pid)
            conn.commit()
        return pid, status


    # ---- Notifiche (cutover) ----
    def publishable_to_notify(self) -> list:
        """Giocatori pubblicabili non ancora notificati: lista di dict."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, canonical_name, age, position, club, league, region, score
                FROM players WHERE publishable=1 AND COALESCE(notified,0)=0
                ORDER BY score DESC""").fetchall()
            return [dict(r) for r in rows]

    def mark_notified(self, ids: list):
        if not ids:
            return
        with self._conn() as conn:
            conn.executemany("UPDATE players SET notified=1 WHERE id=?",
                             [(i,) for i in ids])
            conn.commit()

    # ---- Corroborazione attiva (Fase B3+) ----
    def player_domains(self, pid: int) -> set:
        """Domini-fonte già associati a un giocatore."""
        with self._conn() as conn:
            return {r[0] for r in conn.execute(
                "SELECT DISTINCT source_domain FROM evidences WHERE player_id=? AND source_domain!=''",
                (pid,))}

    def players_to_corroborate(self, limit: int = 100) -> list:
        """(id, nome) dei giocatori con nome completo ma < 2 fonti distinte."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT p.id, p.canonical_name,
                       (SELECT COUNT(DISTINCT e.source_domain) FROM evidences e
                        WHERE e.player_id = p.id AND e.source_domain != '') AS nsrc
                FROM players p WHERE p.name_token_count >= 2
            """).fetchall()
        return [(r[0], r[1]) for r in rows if (r[2] or 0) < 2][:limit]


if __name__ == "__main__":
    db = OB1DatabaseV2()
    print(f"Schema v2 inizializzato: {db.db_path}")
