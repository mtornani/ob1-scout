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
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

# importabile sia come package sia da `python src/database_v2.py` diretto —
# senza, il lazy import di src.scoring_v2/src.outcomes_v2 più sotto fallisce
# con ModuleNotFoundError perché sys.path[0] diventa src/, non la repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_DB = Path(__file__).parent.parent / "data" / "ob1_v2.db"


def normalize_name(name: str) -> str:
    """Nome normalizzato per matching (accenti via, minuscolo, trim)."""
    if not name:
        return ""
    nfkd = unicodedata.normalize('NFKD', name)
    ascii_name = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_name.lower().strip()


def surname_candidates(tokens: list) -> list:
    """
    Token che possono contare come cognome, non come nome di battesimo.

    Con 2 token è "Nome Cognome": solo l'ultimo conta. Con ≥3 token
    l'ultimo NON è sempre il solo cognome utile: l'anagrafica ispanica usa
    spesso due cognomi (paterno + materno), e in gran parte della pool
    sudamericana capita che una fonte usi solo il primo dei due — quindi
    si accettano gli ultimi due token come "zona cognome". Scarta comunque
    un match che cade solo nella parte nome-di-battesimo, dove vivono le
    collisioni tipo "Juan José" (comune in America Latina, non la stessa
    persona).
    """
    if len(tokens) >= 3:
        return tokens[-2:]
    return tokens[-1:]


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
        """
        Match prudente (evita di fondere omonimi diversi).

        Bug reale, trovato confrontando a mano l'overlap con un altro
        radar: "≥2 token in comune" senza toccare la zona cognome fondeva
        "Juan José Fori Viveros" con un "Juan José Camacho" qualunque —
        stesso nome di battesimo composto, cognome diverso, persone
        diverse. Questa è la funzione usata da find_player() per decidere
        se una nuova osservazione appartiene a un giocatore già in
        anagrafica: un falso positivo qui non produce solo una
        corroborazione fasulla, fonde le prove di due persone reali in un
        solo profilo. Vedi surname_candidates per perché sono due token
        con nomi a doppio cognome, non uno.
        """
        a, b = normalize_name(a), normalize_name(b)
        if not a or not b:
            return False
        if a == b or a in b or b in a:
            return True
        pa = {t for t in a.split() if len(t) > 2}
        b_tokens = [t for t in b.split() if len(t) > 2]
        pb = set(b_tokens)
        if not (pa and pb and len(pa & pb) >= 2):
            return False
        return bool(set(surname_candidates(b_tokens)) & pa)

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
        observed_at = obs.get("observed_at")

        pid = self.find_player(name)
        # Per il tabellone (outcomes_v2): serve il first_detected del
        # giocatore com'era PRIMA di questa osservazione, non dopo.
        prior_first_detected, prior_club, prior_name = None, None, name
        with self._conn() as conn:
            status = "updated"
            if pid is None:
                cur = conn.execute("""
                    INSERT INTO players
                    (canonical_name, normalized_name, name_token_count, gender, age,
                     position, club, league, region, is_ghost, detection_count,
                     evidence_count, stats_json, first_detected, last_seen, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 0, ?, ?, ?, ?)
                """, (name, normalize_name(name), len([t for t in name.split() if t]),
                      obs.get("gender") or "unknown", obs.get("age"),
                      obs.get("position"), obs.get("club"), obs.get("league"),
                      obs.get("region"), json.dumps(new_stats) if new_stats else None,
                      observed_at, observed_at, observed_at))
                pid = cur.lastrowid
                status = "new"
            else:
                row = conn.execute(
                    "SELECT stats_json, first_detected, club, canonical_name "
                    "FROM players WHERE id=?", (pid,)).fetchone()
                cur_stats, prior_first_detected, prior_club, prior_name = row
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
                        stats_json = ?,
                        first_detected = COALESCE(first_detected, ?),
                        last_seen = ?
                    WHERE id=?
                """, (obs.get("age"), obs.get("position"), obs.get("club"),
                      obs.get("league"), obs.get("region"),
                      obs.get("gender") or "unknown",
                      json.dumps(merged) if merged else None,
                      observed_at, observed_at, pid))

            conn.execute("""INSERT INTO evidences
                (player_id, source_url, source_domain, observed_at, raw_content, origin)
                VALUES (?, ?, ?, ?, ?, 'extractor')""",
                (pid, src_url, dom, observed_at,
                 obs.get("evidence_quote"), ))
            self._recompute(conn, pid)
            conn.commit()

        # Tabellone (outcomes_v2, Fase B3): una NUOVA fonte su un giocatore
        # già noto è il momento esatto in cui verificare se questa prova è
        # "hype" vero (stampa editoriale mainstream) e non solo un'altra
        # pagina-profilo — e se sì, quanto anticipo aveva OB1. Fuori dal
        # blocco `with` sopra apposta: add_outcome() apre una connessione
        # sua, e scrivere mentre l'altra è ancora aperta rischia il lock
        # SQLite invece di limitarsi a un'attesa innocua.
        if status == "updated" and prior_first_detected and src_url:
            try:
                from src.outcomes_v2 import evaluate_mainstream
            except ImportError:
                from outcomes_v2 import evaluate_mainstream
            verdict = evaluate_mainstream(
                prior_name, prior_club, prior_first_detected,
                hype_url=src_url, hype_date=observed_at,
            )
            if verdict["valid"]:
                self.add_outcome(
                    pid, "mainstream_lead_time", outcome_date=observed_at,
                    source_url=src_url, lead_time_days=verdict["lead_time_days"],
                    note=verdict["reason"])
        return pid, status


    # ---- Notifiche (cutover) ----
    def outcomes_summary(self) -> dict:
        """
        Il tabellone (outcomes_v2, Fase B3), in tre numeri: quante volte
        abbiamo verificato un anticipo, quante erano prove difendibili, e
        l'anticipo medio in giorni su quelle valide. Zero prove non è un
        errore — è la normalità finché il sistema non accumula storia.
        """
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*), AVG(lead_time_days)
                FROM outcomes WHERE outcome_type='mainstream_lead_time'
            """).fetchone()
        checked, avg_lead = row
        return {
            "checked": checked or 0,
            "avg_lead_time_days": round(avg_lead, 1) if avg_lead is not None else None,
        }

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

    def heal_scores(self) -> int:
        """Ricalcola score/gate per righe con score NULL (insert parziali / legacy)."""
        with self._conn() as conn:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM players WHERE score IS NULL")]
            for pid in ids:
                self._recompute(conn, pid)
            if ids:
                conn.commit()
            return len(ids)

    # ---- Corroborazione attiva (Fase B3+) ----
    def player_domains(self, pid: int) -> set:
        """Domini-fonte già associati a un giocatore."""
        with self._conn() as conn:
            return {r[0] for r in conn.execute(
                "SELECT DISTINCT source_domain FROM evidences WHERE player_id=? AND source_domain!=''",
                (pid,))}

    def players_to_corroborate(self, limit: int = 100) -> list:
        """
        Dict {id, name, age, club} dei giocatori con nome completo ma < 2 fonti.
        Priorità: identity_complete (un passo dal gate) → score alto → recenti.
        Filtro nsrc + LIMIT in SQL (niente full-scan in Python).
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT p.id, p.canonical_name, p.age, p.club
                FROM players p
                WHERE p.name_token_count >= 2
                  AND (SELECT COUNT(DISTINCT e.source_domain) FROM evidences e
                       WHERE e.player_id = p.id AND e.source_domain != '') < 2
                ORDER BY p.identity_complete DESC,
                         COALESCE(p.score, 0) DESC,
                         p.last_seen DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [{"id": r[0], "name": r[1], "age": r[2], "club": r[3]} for r in rows]


if __name__ == "__main__":
    _db = OB1DatabaseV2()

    # Falso positivo reale, trovato confrontando a mano l'overlap con un
    # altro radar: nome di battesimo composto comune in America Latina,
    # cognome diverso, non deve fondere due persone.
    assert not _db._names_match("Juan José Camacho", "Juan José Fori Viveros")
    assert _db._names_match("Juan Fori Viveros", "Juan José Fori Viveros")
    # Doppio cognome ispanico (paterno+materno): non deve rompersi solo
    # perché una fonte usa il primo cognome e l'altra il secondo.
    assert _db._names_match("Tomás Martínez", "Tomás Martínez Rodríguez")
    assert _db._names_match("Tomás Rodríguez", "Tomás Martínez Rodríguez")
    print("OK _names_match guards")

    # Tabellone (outcomes_v2): spento in produzione da sempre, non perché
    # disattivato apposta - la vera causa era first_detected mai scritto
    # da ingest_observation() (solo add_player, mai chiamato dalla
    # pipeline viva, lo popolava). Verifica end-to-end con un DB a parte.
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as _tmp:
        _test_db = OB1DatabaseV2(str(Path(_tmp) / "test_outcomes.db"))
        pid, status = _test_db.ingest_observation({
            "name": "Bruno Baldini", "club": "Londrina", "age": 19,
            "source_url": "https://transfermarkt.com/bruno-baldini/profil/spieler/1",
            "observed_at": "2026-03-01T00:00:00",
        })
        assert status == "new"
        row = _test_db._conn().execute(
            "SELECT first_detected, last_seen FROM players WHERE id=?", (pid,)).fetchone()
        assert row == ("2026-03-01T00:00:00", "2026-03-01T00:00:00"), \
            "first_detected/last_seen non scritti alla creazione"

        # Seconda fonte, più avanti nel tempo, editoriale (non pagina-profilo
        # di un aggregatore) -> deve produrre un outcome valido.
        pid2, status2 = _test_db.ingest_observation({
            "name": "Bruno Baldini", "club": "Londrina", "age": 19,
            "source_url": "https://goal.com/br/noticias/baldini-destaque",
            "observed_at": "2026-03-18T00:00:00",
        })
        assert status2 == "updated" and pid2 == pid
        summary = _test_db.outcomes_summary()
        assert summary["checked"] == 1, f"outcome atteso non registrato: {summary}"
        assert summary["avg_lead_time_days"] == 17.0, summary

        # Una pagina-profilo aggregatore come seconda fonte NON è hype:
        # non deve sporcare il tabellone.
        _test_db2 = OB1DatabaseV2(str(Path(_tmp) / "test_outcomes2.db"))
        _test_db2.ingest_observation({
            "name": "Gustavo Gomes", "club": "Raio Amado", "age": 17,
            "source_url": "https://sofascore.com/player/gustavo-gomes",
            "observed_at": "2026-01-01T00:00:00",
        })
        _test_db2.ingest_observation({
            "name": "Gustavo Gomes", "club": "Raio Amado", "age": 17,
            "source_url": "https://transfermarkt.com/gustavo-gomes/profil/spieler/2",
            "observed_at": "2026-01-15T00:00:00",
        })
        assert _test_db2.outcomes_summary()["checked"] == 0
    print("OK tabellone (outcomes_v2) collegato")

    db = _db
    print(f"Schema v2 inizializzato: {db.db_path}")
