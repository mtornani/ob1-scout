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
import os
import sqlite3
import sys
import unicodedata
from datetime import datetime
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


_SOURCE_TIERS_CACHE = None


def _load_source_tiers() -> dict:
    """
    dominio -> tier ("primary"/"secondary"), da config/sources.json.

    ARCH-003 (Eccellenza, ob1-serie-c) Fase 1: il campo tier esiste già nel
    registro ma finora era letto solo da sources_v2.py per escludere i
    secondary dalla discovery — il gate a due fonti contava domini distinti
    senza guardare il grado. Due aggregatori che si copiano valevano quanto
    federazione + stampa. Caricato una volta, in cache di modulo: è un file
    piccolo (18 fonti) letto ad ogni ricalcolo giocatore, non ha senso
    riaprirlo ogni volta. Non deve mai poter rompere il ricalcolo: qualunque
    errore ripiega su mappa vuota (nessun dominio noto = nessun primary
    accertato, coerente con "non inventare fiducia che non abbiamo").
    """
    global _SOURCE_TIERS_CACHE
    if _SOURCE_TIERS_CACHE is not None:
        return _SOURCE_TIERS_CACHE
    tiers = {}
    try:
        path = Path(__file__).parent.parent / "config" / "sources.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("sources") if isinstance(data, dict) else data
        for entry in entries or []:
            tier = entry.get("tier", "secondary")
            dom = domain_of(entry.get("url", ""))
            if dom:
                tiers[dom] = tier
            # Stesso editore, TLD/sottodominio diverso (es. Transfermarkt
            # .us/.de/.pl/.pe oltre a .com) — senza, il match esatto perdeva
            # queste varianti anche con la fonte già registrata.
            for alias in entry.get("aliases", []) or []:
                if alias:
                    tiers[alias.lower()] = tier
    except Exception:
        pass
    _SOURCE_TIERS_CACHE = tiers
    return tiers


def has_primary_source(domains) -> bool:
    """True se almeno uno dei domini-prova è una fonte 'primary' nel registro.
    Domini non registrati (non in config/sources.json) non contano come
    primary — solo perché non li conosciamo non significa che siano
    affidabili, è l'opposto: nessuna fiducia dichiarata finché non li
    aggiungiamo al registro con un tier esplicito."""
    tiers = _load_source_tiers()
    return any(tiers.get(d) == "primary" for d in domains)


# Algoritmo "copertura bassa" (solo Global, non Lega Pro): l'ostacolo non è il
# talento, è l'infrastruttura editoriale. La regola "serve una fonte primary"
# appena attivata (ARCH-003 Fase 1) è corretta dove la stampa sportiva
# digitale è densa (Brasile, Argentina, Europa) — ma in Africa subsahariana,
# Nord Africa e Asia Sud/Sudest/Centrale il registro è ancora agli inizi (13
# paesi seminati su ~70 potenziali al 2026-08-19: vedi config/sources.json,
# note_2026_08_19b) e per la maggioranza dei paesi qui sotto NON esiste ancora
# nessuna fonte primary registrata. Applicare lo stesso gate userebbe "fonte
# non ancora nel registro" come se fosse "fonte inaffidabile" — non è quello
# che intendiamo dire. Elenco per NOME PAESE (il campo `region` dei giocatori
# è di fatto un nome paese/regione in inglese, come estratto dall'LLM — non
# un codice ISO), deliberatamente ampio: copre l'intero continente/sub-area
# scelta, non solo i 13 paesi già seminati, così i paesi non ancora coperti
# dal registro entrano comunque nella regola alternativa invece di sparire.
LOW_COVERAGE_REGIONS = frozenset({
    # Nord Africa
    "Morocco", "Algeria", "Tunisia", "Libya", "Egypt", "Sudan",
    # Africa subsahariana (Ovest, Est, Centrale, Sud)
    "Nigeria", "Ghana", "Senegal", "Ivory Coast", "Cote d'Ivoire",
    "Guinea", "Mali", "Cameroon", "DR Congo", "Congo", "Benin", "Togo",
    "Burkina Faso", "Sierra Leone", "Liberia", "Gambia", "Guinea-Bissau",
    "Niger", "Chad", "Central African Republic", "Gabon",
    "Equatorial Guinea", "Kenya", "Ethiopia", "Uganda", "Tanzania",
    "Rwanda", "Burundi", "Somalia", "South Sudan", "Zambia", "Zimbabwe",
    "Malawi", "Mozambique", "Angola", "Namibia", "Botswana",
    "South Africa", "Eswatini", "Lesotho", "Madagascar", "Comoros",
    "Cape Verde", "Mauritius", "Mauritania",
    # Asia meridionale
    "India", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal", "Bhutan",
    "Maldives", "Afghanistan",
    # Sud-est asiatico
    "Indonesia", "Vietnam", "Thailand", "Philippines", "Malaysia",
    "Myanmar", "Cambodia", "Laos", "Singapore", "Brunei", "Timor-Leste",
    # Asia centrale
    "Uzbekistan", "Kazakhstan", "Kyrgyzstan", "Tajikistan", "Turkmenistan",
    # America Centrale e Caraibi (CONCACAF, 2026-08-19d — esclusi Stati Uniti,
    # Canada e Messico: stampa densa, non nel perimetro). Guatemala e Panama
    # erano già nel registro dalla Fase B2 come "Sud America ispanofono" ma
    # senza la deroga di gate: aggiunti qui per coerenza, stesso tipo di
    # mercato dei nuovi vicini caraibici.
    "Guatemala", "Honduras", "Costa Rica", "Panama", "Nicaragua",
    "El Salvador", "Belize", "Jamaica", "Haiti", "Trinidad and Tobago",
    "Bahamas", "Barbados", "Cuba", "Dominican Republic", "Suriname",
    "Guyana", "Grenada", "Saint Lucia", "Saint Vincent and the Grenadines",
    "Antigua and Barbuda", "Dominica", "Saint Kitts and Nevis", "Bermuda",
    "Curacao", "Aruba",
    # Oceania/Pacifico (OFC, 2026-08-19d — esclusi Australia, in AFC dal 2006
    # con stampa densa, e Nuova Zelanda, in OFC ma con stampa densa in
    # inglese: nessuna delle due ha bisogno della deroga).
    "Fiji", "Vanuatu", "Solomon Islands", "Papua New Guinea", "Tahiti",
    "New Caledonia", "Samoa", "American Samoa", "Tonga", "Cook Islands",
    "Tuvalu", "Kiribati",
})


def is_low_coverage_region(region) -> bool:
    """True se il paese/regione del giocatore rientra nel perimetro a bassa
    copertura editoriale (Africa subsahariana+Nord Africa, Asia Sud/Sudest/
    Centrale). Confronto esatto sulla stringa `region` così com'è estratta —
    niente inferenza da continente/lingua, per restare auditable."""
    return bool(region) and str(region).strip() in LOW_COVERAGE_REGIONS


# Serve per le fonti multi-paese (confederazioni: CAF, AFC) aggiunte
# nell'algoritmo copertura bassa (2026-08-19c). Il resto del registro sono
# fonti mono-paese: obs["region"] eredita src["region"] senza ambiguità
# (scripts/ingest_v2.py). Una fonte confederale invece copre decine di
# paesi — taggare ogni giocatore estratto con un unico region statico
# sarebbe sbagliato per la maggioranza. L'estrattore non dà "region" (solo
# "nationality", spesso un aggettivo: "Ghanaian", non "Ghana"): questa mappa
# traduce il gentilizio nel nome paese usato da LOW_COVERAGE_REGIONS, così
# is_low_coverage_region() continua a funzionare anche per chi arriva da
# CAF/AFC invece che da una fonte nazionale. Copre solo i 13 paesi seme
# 2026-08-19b + varianti comuni: da allargare insieme al registro fonti.
NATIONALITY_TO_REGION = {
    "kenya": "Kenya", "kenyan": "Kenya",
    "ethiopia": "Ethiopia", "ethiopian": "Ethiopia",
    "south africa": "South Africa", "south african": "South Africa",
    "morocco": "Morocco", "moroccan": "Morocco",
    "egypt": "Egypt", "egyptian": "Egypt",
    "tunisia": "Tunisia", "tunisian": "Tunisia",
    "india": "India", "indian": "India",
    "bangladesh": "Bangladesh", "bangladeshi": "Bangladesh",
    "indonesia": "Indonesia", "indonesian": "Indonesia",
    "vietnam": "Vietnam", "vietnamese": "Vietnam",
    "thailand": "Thailand", "thai": "Thailand",
    "philippines": "Philippines", "filipino": "Philippines", "philippine": "Philippines",
    "uzbekistan": "Uzbekistan", "uzbek": "Uzbekistan",
    # Già seminati (Fase B2), utili anche loro se un giorno arriva una
    # confederazione/hub multi-paese che li tocca (es. WAFU, CAF stessa).
    "ghana": "Ghana", "ghanaian": "Ghana",
    "senegal": "Senegal", "senegalese": "Senegal",
    "nigeria": "Nigeria", "nigerian": "Nigeria",
    "ivory coast": "Ivory Coast", "ivorian": "Ivory Coast", "côte d'ivoire": "Ivory Coast",
    "guinea": "Guinea", "guinean": "Guinea",
    "kazakhstan": "Kazakhstan", "kazakh": "Kazakhstan",
    # CONCACAF (Caraibi/America Centrale) e OFC (Pacifico), 2026-08-19d:
    # stesso motivo delle confederazioni CAF/AFC — servono per attribuire
    # il paese giusto ai giocatori estratti da fonti multi-paese.
    "guatemala": "Guatemala", "guatemalan": "Guatemala",
    "panama": "Panama", "panamanian": "Panama",
    "honduras": "Honduras", "honduran": "Honduras",
    "costa rica": "Costa Rica", "costa rican": "Costa Rica",
    "nicaragua": "Nicaragua", "nicaraguan": "Nicaragua",
    "el salvador": "El Salvador", "salvadoran": "El Salvador",
    "jamaica": "Jamaica", "jamaican": "Jamaica",
    "haiti": "Haiti", "haitian": "Haiti",
    "trinidad and tobago": "Trinidad and Tobago", "trinidadian": "Trinidad and Tobago",
    "cuba": "Cuba", "cuban": "Cuba",
    "dominican republic": "Dominican Republic", "dominican": "Dominican Republic",
    "suriname": "Suriname", "surinamese": "Suriname",
    "guyana": "Guyana", "guyanese": "Guyana",
    "fiji": "Fiji", "fijian": "Fiji",
    "vanuatu": "Vanuatu", "ni-vanuatu": "Vanuatu",
    "solomon islands": "Solomon Islands", "solomon islander": "Solomon Islands",
    "papua new guinea": "Papua New Guinea", "papua new guinean": "Papua New Guinea",
    "tahiti": "Tahiti", "tahitian": "Tahiti",
    "new caledonia": "New Caledonia", "new caledonian": "New Caledonia",
    "samoa": "Samoa", "samoan": "Samoa",
    "tonga": "Tonga", "tongan": "Tonga",
}


def region_from_nationality(nationality) -> str:
    """Nome paese (per LOW_COVERAGE_REGIONS) da un gentilizio/nome paese
    estratto dall'LLM, o '' se non riconosciuto. Non inventa: un gentilizio
    non in mappa resta senza region da qui, ricade sul region della fonte."""
    if not nationality:
        return ""
    return NATIONALITY_TO_REGION.get(str(nationality).strip().lower(), "")


def assess_identity(name: str, club: str, age, source_count: int, has_primary: bool,
                     low_coverage: bool = False) -> dict:
    """
    Valuta la qualità dell'identità di un giocatore e il gate di pubblicazione.
    Ritorna dict con i flag e i motivi di review. NON decide il genere (scelta
    di prodotto ancora aperta): quel campo resta 'unknown'.

    ARCH-003 Fase 1: due fonti non bastano più da sole. Misurato su
    data/ob1_v2.db (90 giocatori corroborati oggi): richiedere "≥1 fonte
    primary" da subito, col registro iniziale di 18 domini, faceva cadere
    39/90 — ma 30 di quei 39 erano falsi positivi da registro incompleto
    (Transfermarkt/Soccerway non riconosciuti sotto TLD alternativi, Globo
    Esporte e stampa nazionale brasiliana/messicana/cilena assenti dal seed).
    Allargato il registro (18 -> 45 domini, + alias di dominio) prima di
    attivare la regola: la sopravvivenza sale a 69/90, e i 21 rimasti sono
    genuinamente deboli (2 aggregatori che si copiano, nessuna fonte con
    cronaca vera). has_primary è calcolato dal chiamante via
    has_primary_source() sui domini-prova distinti del giocatore.

    Algoritmo "copertura bassa" (2026-08-19b, solo Global): per i paesi in
    LOW_COVERAGE_REGIONS (Africa subsahariana+Nord Africa, Asia Sud/Sudest/
    Centrale) il registro fonti è troppo giovane per esigere una fonte
    primary senza penalizzare l'infrastruttura invece del talento — lì
    "serve 1 fonte primary" tornerebbe a bocciare per "fonte sconosciuta",
    lo stesso errore misurato e corretto nel lotto precedente, solo spostato
    su un'altra fetta di mondo. low_coverage è calcolato dal chiamante via
    is_low_coverage_region() sulla region del giocatore: quando True, due
    fonti indipendenti bastano anche senza primary, ma il caso è sempre
    segnalato in review_flags — non si finge che regga lo stesso standard.
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
    # Due fonti che si copiano a vicenda (es. due aggregatori) non reggono
    # una telefonata di verifica quanto due fonti indipendenti con almeno
    # una cronaca vera. Segnalato separato da "fonte_singola": qui le fonti
    # numericamente ci sono, manca il grado. Nei paesi a bassa copertura la
    # regola primary è sospesa (registro troppo giovane): il flag cambia da
    # "bloccante" a "sperimentale", ma resta visibile.
    elif not has_primary:
        flags.append("copertura_bassa_sperimentale" if low_coverage else "senza_fonte_primary")

    identity_complete = (
        token_count >= 2
        and not looks_like_handle
        and bool(club and str(club).strip())
        and age is not None
    )
    corroborated = (source_count or 0) >= 2 and (has_primary or low_coverage)
    publishable = identity_complete and corroborated

    return {
        "name_token_count": token_count,
        "identity_complete": identity_complete,
        "corroborated": corroborated,
        "publishable": publishable,
        "review_flags": ",".join(flags),
        "coverage_tier": "low_coverage" if low_coverage else "standard",
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
                    created_at TEXT,
                    coverage_tier TEXT DEFAULT 'standard',
                    corr_attempts INTEGER DEFAULT 0,
                    last_corr_attempt_at TEXT
                )
            """)
            # Migrazione leggera per DB v2 creati prima delle colonne notified
            # e coverage_tier (algoritmo "copertura bassa", 2026-08-19b), poi
            # corr_attempts/last_corr_attempt_at (memoria dei tentativi di
            # corroborazione, 2026-08-21 — vedi players_to_corroborate).
            cols = {row[1] for row in c.execute("PRAGMA table_info(players)")}
            if "notified" not in cols:
                c.execute("ALTER TABLE players ADD COLUMN notified INTEGER DEFAULT 0")
            if "coverage_tier" not in cols:
                c.execute("ALTER TABLE players ADD COLUMN coverage_tier TEXT DEFAULT 'standard'")
            if "corr_attempts" not in cols:
                c.execute("ALTER TABLE players ADD COLUMN corr_attempts INTEGER DEFAULT 0")
            if "last_corr_attempt_at" not in cols:
                c.execute("ALTER TABLE players ADD COLUMN last_corr_attempt_at TEXT")
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
        # add_player non conosce ancora i domini-prova (l'evidence si
        # aggiunge dopo, via add_evidence): nessun primary accertato a
        # questo punto. Usato solo dalla migrazione legacy one-off
        # (scripts/migrate_to_v2.py); la pipeline live passa da
        # ingest_observation -> _recompute, che ricalcola has_primary sui
        # domini veri non appena c'è evidence.
        idn = assess_identity(canonical_name, club, age, source_count, has_primary=False)
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
        p = conn.execute("""SELECT canonical_name, age, club, league, region, is_ghost, stats_json
                            FROM players WHERE id=?""", (pid,)).fetchone()
        name, age, club, league, region, is_ghost, stats_json = p
        domains = [row[0] for row in conn.execute(
            "SELECT DISTINCT source_domain FROM evidences WHERE player_id=? AND source_domain!=''",
            (pid,)).fetchall()]
        n_sources = len(domains) or 1
        ev_count = conn.execute(
            "SELECT COUNT(*) FROM evidences WHERE player_id=?", (pid,)).fetchone()[0]
        stats = json.loads(stats_json) if stats_json else {}

        has_primary = has_primary_source(domains)
        low_coverage = is_low_coverage_region(region)
        idn = assess_identity(name, club, age, n_sources, has_primary, low_coverage)
        sc = score_player(age=age, is_ghost=bool(is_ghost), club=club, league=league,
                          stats=stats, n_sources=n_sources, detection_count=ev_count)
        conn.execute("""UPDATE players SET evidence_count=?, identity_complete=?,
                        corroborated=?, publishable=?, review_flags=?, score=?, confidence=?,
                        coverage_tier=?
                        WHERE id=?""",
                     (ev_count, 1 if idn["identity_complete"] else 0,
                      1 if idn["corroborated"] else 0, 1 if idn["publishable"] else 0,
                      idn["review_flags"], sc["score"], sc["confidence"],
                      idn["coverage_tier"], pid))

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

    def players_to_corroborate(self, limit: int = 100,
                               cooldown_hours: int = None) -> list:
        """
        Dict {id, name, age, club} dei giocatori con nome completo ma < 2 fonti.

        Trovato il 21 ago 2026 confrontando la coda reale (133 candidati) col
        tetto per run (CORR_MAX_SEARCH_ATTEMPTS=20, scripts/ingest_v2.py):
        l'ordinamento precedente (identity_complete → score → last_seen) è
        STABILE da un run all'altro finché nulla cambia per quei giocatori —
        quindi ogni ciclo di 6h ripescava e ri-cercava GLI STESSI ~20 in
        cima, mentre gli altri ~113 non venivano mai nemmeno tentati una
        prima volta. Nessun 422/timeout di ricerca c'entra: è la coda stessa
        a non avere memoria di cosa è già stato provato.

        Fix: escludere chi ha un tentativo recente (cooldown, default 24h —
        un candidato fallito oggi non vale la pena riprovarlo al prossimo
        ciclo di 6h, ma nemmeno aspettare giorni se la ricerca torna a
        funzionare) e mettere PRIMA chi non è mai stato tentato, poi chi
        aspetta da più tempo — solo a parità di quello, identity_complete e
        score restano gli spareggi. Così la coda intera viene attraversata
        nel tempo invece di ripetere sempre la stessa testa.
        """
        if cooldown_hours is None:
            cooldown_hours = int(os.getenv("CORR_COOLDOWN_HOURS", "24"))
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT p.id, p.canonical_name, p.age, p.club
                FROM players p
                WHERE p.name_token_count >= 2
                  AND (SELECT COUNT(DISTINCT e.source_domain) FROM evidences e
                       WHERE e.player_id = p.id AND e.source_domain != '') < 2
                  AND (p.last_corr_attempt_at IS NULL
                       OR julianday('now') - julianday(p.last_corr_attempt_at)
                          >= ? / 24.0)
                ORDER BY (p.last_corr_attempt_at IS NOT NULL),
                         p.last_corr_attempt_at ASC,
                         p.identity_complete DESC,
                         COALESCE(p.score, 0) DESC,
                         p.last_seen DESC
                LIMIT ?
            """, (cooldown_hours, limit)).fetchall()
        return [{"id": r[0], "name": r[1], "age": r[2], "club": r[3]} for r in rows]

    def record_corr_attempt(self, player_id: int) -> None:
        """
        Segna che questo giocatore è stato appena tentato in corroborazione
        (trovato o no — anche un tentativo fallito è memoria utile: evita di
        ripeterlo subito). Chiamata da _corroborate() in ingest_v2.py dopo
        OGNI find_profile(), a prescindere dall'esito.
        """
        with self._conn() as conn:
            conn.execute("""
                UPDATE players
                SET corr_attempts = COALESCE(corr_attempts, 0) + 1,
                    last_corr_attempt_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), player_id))


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

    # ARCH-003 Fase 1: grading fonti nel registro reale (config/sources.json,
    # 45 domini dopo l'allargamento del 2026-08-19). promiedos.com.ar è tier
    # primary lì dentro; un dominio non registrato non deve mai risultare
    # primary per default.
    _tiers = _load_source_tiers()
    assert _tiers.get("promiedos.com.ar") == "primary", _tiers.get("promiedos.com.ar")
    assert has_primary_source(["promiedos.com.ar", "un-dominio-mai-visto.xyz"])
    assert not has_primary_source(["un-dominio-mai-visto.xyz", "un-altro-ignoto.xyz"])
    assert not has_primary_source([])
    # Alias: stesso editore (Transfermarkt), TLD diverso da quello registrato
    # come url principale (.com) — misurato nel DB reale: transfermarkt.us
    # (6 osservazioni), .de/.pl/.pe trattati come domini ignoti prima di questo.
    assert _tiers.get("transfermarkt.us") == "secondary", _tiers.get("transfermarkt.us")
    assert _tiers.get("soccerway.com") == "secondary", _tiers.get("soccerway.com")
    # Allargamento registro 2026-08-19 (ARCH-003 Fase 1): testate note come
    # primary, non solo la piccola lista di federazioni originale.
    assert _tiers.get("ge.globo.com") == "primary", _tiers.get("ge.globo.com")
    print("OK grading fonti (has_primary_source)")

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

    # ARCH-003 Fase 1: la regola è ora collegata al gate vero (_recompute),
    # non solo disponibile come helper. Due fonti che si copiano (entrambe
    # aggregatori secondary) non bastano più; una primary + una secondary sì.
    with _tempfile.TemporaryDirectory() as _tmp2:
        _gate_db = OB1DatabaseV2(str(Path(_tmp2) / "test_gate.db"))
        pid, _ = _gate_db.ingest_observation({
            "name": "Nome Cognome Test", "club": "Club Test", "age": 18,
            "source_url": "https://sofascore.com/player/nome-cognome-test",
            "observed_at": "2026-03-01T00:00:00",
        })
        _gate_db.ingest_observation({
            "name": "Nome Cognome Test", "club": "Club Test", "age": 18,
            "source_url": "https://fbref.com/en/players/nome-cognome-test",
            "observed_at": "2026-03-05T00:00:00",
        })
        row = _gate_db._conn().execute(
            "SELECT corroborated, review_flags FROM players WHERE id=?", (pid,)).fetchone()
        assert row[0] == 0, f"2 fonti secondary non devono corroborare: {row}"
        assert "senza_fonte_primary" in row[1], row[1]

        # Terza fonte, questa volta primary: ora deve corroborare.
        _gate_db.ingest_observation({
            "name": "Nome Cognome Test", "club": "Club Test", "age": 18,
            "source_url": "https://ge.globo.com/futebol/noticia/nome-cognome-test.ghtml",
            "observed_at": "2026-03-10T00:00:00",
        })
        row2 = _gate_db._conn().execute(
            "SELECT corroborated, publishable FROM players WHERE id=?", (pid,)).fetchone()
        assert row2 == (1, 1), f"primary + secondary deve corroborare e pubblicare: {row2}"
    print("OK gate collegato: serve fonte primary per corroborare")

    # Algoritmo copertura bassa (2026-08-19b, solo Global): in un paese del
    # perimetro (Kenya) due fonti secondary bastano, ma il caso resta
    # segnalato — non è lo stesso standard di un paese a stampa densa.
    assert is_low_coverage_region("Kenya")
    assert is_low_coverage_region("Uzbekistan")
    assert not is_low_coverage_region("Brazil")
    assert not is_low_coverage_region(None)
    with _tempfile.TemporaryDirectory() as _tmp3:
        _lc_db = OB1DatabaseV2(str(Path(_tmp3) / "test_low_coverage.db"))
        pid, _ = _lc_db.ingest_observation({
            "name": "Jomo Otieno Test", "club": "Gor Mahia", "age": 17,
            "region": "Kenya",
            "source_url": "https://sofascore.com/player/jomo-otieno-test",
            "observed_at": "2026-03-01T00:00:00",
        })
        _lc_db.ingest_observation({
            "name": "Jomo Otieno Test", "club": "Gor Mahia", "age": 17,
            "region": "Kenya",
            "source_url": "https://fbref.com/en/players/jomo-otieno-test",
            "observed_at": "2026-03-05T00:00:00",
        })
        row = _lc_db._conn().execute(
            "SELECT corroborated, publishable, review_flags, coverage_tier "
            "FROM players WHERE id=?", (pid,)).fetchone()
        assert row[0] == 1 and row[1] == 1, \
            f"Kenya, 2 fonti secondary: deve corroborare sotto la regola alternativa: {row}"
        assert "copertura_bassa_sperimentale" in row[2], row[2]
        assert row[3] == "low_coverage", row[3]

        # Stesso identico caso ma senza region -> regola standard, NON deve
        # passare: la deroga è per paese dichiarato, non un default globale.
        pid_std, _ = _lc_db.ingest_observation({
            "name": "Altro Nome Test", "club": "Altro Club", "age": 17,
            "source_url": "https://sofascore.com/player/altro-nome-test",
            "observed_at": "2026-03-01T00:00:00",
        })
        _lc_db.ingest_observation({
            "name": "Altro Nome Test", "club": "Altro Club", "age": 17,
            "source_url": "https://fbref.com/en/players/altro-nome-test",
            "observed_at": "2026-03-05T00:00:00",
        })
        row_std = _lc_db._conn().execute(
            "SELECT corroborated, coverage_tier FROM players WHERE id=?", (pid_std,)).fetchone()
        assert row_std == (0, "standard"), \
            f"senza region nel perimetro deve restare sullo standard: {row_std}"
    print("OK algoritmo copertura bassa: deroga per paese, sempre segnalata")

    # Discovery per fonti multi-paese (2026-08-19c): una confederazione come
    # CAF/AFC non può ereditare un region statico, serve dedurlo dalla
    # nationality del testo.
    assert region_from_nationality("Kenyan") == "Kenya"
    assert region_from_nationality("kenya") == "Kenya"
    assert region_from_nationality("Ghanaian") == "Ghana"
    assert region_from_nationality("Brazilian") == "", \
        "gentilizio fuori mappa non deve inventare un paese"
    assert region_from_nationality(None) == ""
    assert region_from_nationality("") == ""
    print("OK region_from_nationality: gentilizio -> paese per fonti multi-paese")

    # 2026-08-19d: CONCACAF (Caraibi/America Centrale) e OFC (Pacifico) nel
    # perimetro; Australia/Nuova Zelanda restano FUORI apposta (stampa
    # densa in inglese, non serve allentare il gate per loro anche se
    # tecnicamente nella stessa confederazione di paesi che ce l'hanno).
    assert is_low_coverage_region("Haiti")
    assert is_low_coverage_region("Fiji")
    assert is_low_coverage_region("Kazakhstan")
    assert not is_low_coverage_region("Australia")
    assert not is_low_coverage_region("New Zealand")
    assert region_from_nationality("Haitian") == "Haiti"
    assert region_from_nationality("Fijian") == "Fiji"
    assert region_from_nationality("Kazakh") == "Kazakhstan"
    print("OK perimetro esteso: CONCACAF/OFC dentro, Australia/Nuova Zelanda fuori")

    # Memoria dei tentativi di corroborazione (2026-08-21): trovato con la
    # coda reale a 133 candidati e un tetto di 20 tentativi/run — senza
    # questo, i primi ~20 per identity_complete/score/last_seen restano
    # SEMPRE gli stessi da un run all'altro, gli altri ~113 non vengono mai
    # nemmeno provati una prima volta. Verificato qui con scenari minimi,
    # non solo assunto.
    with _tempfile.TemporaryDirectory() as _tmp4:
        _cq_db = OB1DatabaseV2(str(Path(_tmp4) / "test_corr_queue.db"))
        # Nomi senza token in comune tra loro: se condividono una parola
        # (successo di prima stesura: tutti e tre avevano "Tried") il
        # matcher di identità li fonde in UNA persona sola, e il test perde
        # senso senza avvisare (i tre pid tornavano tutti uguali).
        pid_never, _ = _cq_db.ingest_observation({
            "name": "Amara Diallo Test", "club": "Club A", "age": 17,
            "source_url": "https://sofascore.com/player/amara-diallo-test",
            "observed_at": "2026-03-01T00:00:00",
        })
        pid_recent, _ = _cq_db.ingest_observation({
            "name": "Baptiste Rousseau Test", "club": "Club B", "age": 17,
            "source_url": "https://sofascore.com/player/baptiste-rousseau-test",
            "observed_at": "2026-03-01T00:00:00",
        })
        pid_stale, _ = _cq_db.ingest_observation({
            "name": "Camila Duarte Test", "club": "Club C", "age": 17,
            "source_url": "https://sofascore.com/player/camila-duarte-test",
            "observed_at": "2026-03-01T00:00:00",
        })
        assert len({pid_never, pid_recent, pid_stale}) == 3, \
            "i tre giocatori di test si sono fusi in uno: nomi non abbastanza distinti"
        # pid_recent: tentato 1 ora fa -> dentro il cooldown di 24h, escluso.
        # pid_stale: tentato 48 ore fa -> fuori cooldown, rientra in coda.
        with _cq_db._conn() as _c:
            _c.execute("UPDATE players SET last_corr_attempt_at = "
                       "datetime('now', '-1 hours') WHERE id = ?", (pid_recent,))
            _c.execute("UPDATE players SET last_corr_attempt_at = "
                       "datetime('now', '-48 hours') WHERE id = ?", (pid_stale,))

        queue = [r["id"] for r in _cq_db.players_to_corroborate(cooldown_hours=24)]
        assert pid_recent not in queue, \
            "tentato 1h fa (cooldown 24h): non deve rientrare subito"
        assert pid_never in queue and pid_stale in queue, \
            f"mai tentato e tentato-da-tempo devono essere in coda: {queue}"
        # Mai tentato prima di chi ha già un tentativo (anche vecchio): dare
        # priorità a chi non è mai stato nemmeno guardato una volta.
        assert queue.index(pid_never) < queue.index(pid_stale), \
            f"mai tentato deve venire prima di tentato-da-tempo: {queue}"

        # record_corr_attempt: un tentativo (trovato o no) aggiorna la
        # memoria e lo fa sparire dalla coda fino al prossimo cooldown.
        _cq_db.record_corr_attempt(pid_never)
        row = _cq_db._conn().execute(
            "SELECT corr_attempts, last_corr_attempt_at FROM players "
            "WHERE id=?", (pid_never,)).fetchone()
        assert row[0] == 1 and row[1], f"record_corr_attempt non ha scritto: {row}"
        queue_after = [r["id"] for r in _cq_db.players_to_corroborate(cooldown_hours=24)]
        assert pid_never not in queue_after, \
            "appena tentato: deve uscire dalla coda fino al prossimo cooldown"
    print("OK memoria corroborazione: cooldown + priorità a chi non è mai stato tentato")

    db = _db
    print(f"Schema v2 inizializzato: {db.db_path}")
