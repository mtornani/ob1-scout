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
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.challenge_v2 import contesta, sopravvive
from src.claims_v2 import (stabilisci, pubblicabile as pubblicabile_da_claims,
                           fonti_che_stabiliscono, registro, DICHIARATO)
from src.selezione_v2 import leggi_dal_registro as leggi_selezione
from src.anomalie_v2 import scala_osservata

# La data dell'atto nel percorso dell'URL: /2026/07/19/... Stessa lettura di
# selezione_v2._DATA_NEL_PATH — è quando il documento è stato pubblicato, non
# quando l'abbiamo scaricato.
_DATA_NEL_PATH = re.compile(r"/(20\d\d)/(\d{1,2})/(\d{1,2})/")

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


def _selezione_json(p) -> str:
    """
    La persistenza di selezione, in una forma che l'export può leggere senza
    ricalcolare. Vuoto = nessuna convocazione, e resta NULL in colonna.
    """
    if not p:
        return ""
    from src.selezione_v2 import frase
    return json.dumps({
        "quante": p.quante,
        "frase": frase(p),
        "dal": p.dal,
        "al": p.al,
        "categorie": p.categorie,
        "progressione": p.progressione,
        "mesi_di_arco": p.mesi_di_arco,
        "eventi": [{"data": e.data, "categoria": e.categoria,
                    "fonte": e.federazione, "url": e.url} for e in p.eventi],
    }, ensure_ascii=False)


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
                    last_corr_attempt_at TEXT,
                    selection_json TEXT
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
            # Persistenza di selezione (2026-08-26): si ricalcola a ogni run
            # dalle evidenze, ma si salva perché la rilegge l'export — senza
            # colonna dovrebbe rifare il giro su tutte le evidenze di ogni
            # giocatore a ogni invocazione, e soprattutto potrebbe calcolarla
            # in modo diverso da qui, cioè mostrare una classifica che non è
            # quella su cui il gate ha lavorato.
            if "selection_json" not in cols:
                c.execute("ALTER TABLE players ADD COLUMN selection_json TEXT")
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
            # Grafo delle fonti (src/piramide_v2.py): OGNI osservazione con
            # chi l'ha detta, non solo il valore che ha vinto. La tabella
            # players tiene un valore per campo — utile, ma non ricorda che
            # una fonte ne diceva un altro, e senza quella memoria una
            # divergenza si scopre solo quando produce un'anomalia falsa
            # (caso Mendoza, 31 ago 2026: età 16 congelata dall'arrivo,
            # Transfermarkt diceva 18).
            #
            # Additiva: nessuna colonna esistente cambia, nessuna riga
            # esistente si tocca. Si riempie da qui in avanti — lo storico
            # non è ricostruibile, perché le evidenze vecchie conservano il
            # testo della fonte, non i campi che ne erano stati estratti.
            c.execute("""
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    campo TEXT NOT NULL,
                    valore TEXT NOT NULL,
                    fonte_tipo TEXT NOT NULL,
                    source_domain TEXT,
                    source_url TEXT,
                    datato_al TEXT,
                    observed_at TEXT,
                    FOREIGN KEY (player_id) REFERENCES players(id)
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_obs_player "
                      "ON observations(player_id, campo)")
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
            # Memoria delle scansioni di discovery (2026-08-27). Le fonti
            # vivono in config/sources.json, non in una tabella: qui si tiene
            # solo QUANDO ognuna è stata scansionata l'ultima volta, che è
            # l'unica cosa che serve a farle ruotare tutte invece di rifare
            # sempre la testa del registro. Vedi sources_in_scan_order.
            c.execute("""
                CREATE TABLE IF NOT EXISTS source_scans (
                    source_id TEXT PRIMARY KEY,
                    scans INTEGER DEFAULT 0,
                    last_scan_at TEXT
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
        if a == b:
            return True
        # Contenimento per TOKEN, mai per sottostringa, e mai partendo da un
        # nome di un solo token.
        #
        # Prima era `a in b or b in a` su tutta la stringa, e quella riga
        # annullava la prudenza scritta qui sotto: "mora" sta dentro "dylan
        # mora", quindi un record chiamato "Mora" catturava Dylan Mora E
        # Thiago Mora — due convocati diversi della stessa sub-17 uruguaiana,
        # fusi in un profilo messicano del Club Tijuana. Stando su stringa e
        # non su parole prendeva anche "morales" e "moraes".
        #
        # Misurato sul DB del 1 set 2026: sei record a un token contenevano
        # prove di piu' di una persona. Il peggiore, "Felipe", ne teneva sei
        # — fra cui un comunicato di squalifica ("SANCIONADO LUIS FELIPE
        # MARQUINEZ"), perche' Felipe e' un nome di battesimo e la
        # sottostringa pesca chiunque ce l'abbia in mezzo.
        #
        # Il minimo di due token e' il punto: un nome solo non identifica
        # nessuno. Meglio due profili separati che una persona inventata —
        # e infatti il gate marca gia' `nome_singolo` e non li pubblica.
        ta, tb = a.split(), b.split()
        if len(ta) >= 2 and set(ta) <= set(tb):
            return True
        if len(tb) >= 2 and set(tb) <= set(ta):
            return True
        pa = {t for t in a.split() if len(t) > 2}
        b_tokens = [t for t in b.split() if len(t) > 2]
        pb = set(b_tokens)
        if not (pa and pb and len(pa & pb) >= 2):
            return False
        # Il cognome vero e' l'ULTIMO token, e ne basta uno dei due dentro
        # l'altro nome. surname_candidates() ne restituisce due, per i doppi
        # cognomi ispanici, ma su un nome di tre token il penultimo e' ancora
        # un nome di battesimo: per "juan jose camacho" propone
        # ['jose', 'camacho'], e quel 'jose' bastava a far combaciare "Juan
        # Jose Fori Viveros" con "Juan Jose Camacho" — cioe' proprio il caso
        # che il commento qui sopra dava per risolto, e che invece passava
        # ancora (verificato il 1 set 2026, non era una regressione: la vecchia
        # riga della sottostringa restituiva False e si arrivava lo stesso qui).
        #
        # Impatto misurato sui 337 record di oggi: ZERO coppie cambiano esito.
        # Non e' una validazione a favore, e' l'assenza di rischio: il valore
        # e' preventivo, su un caso che il codice stesso documenta come gia'
        # visto una volta sui dati veri.
        return a.split()[-1] in set(b.split()) or b.split()[-1] in set(a.split())

    def find_player(self, name: str):
        with self._conn() as conn:
            rows = conn.execute("SELECT id, canonical_name FROM players").fetchall()
        for pid, cname in rows:
            if self._names_match(name, cname):
                return pid
        return None

    def _scala_categorie(self, conn) -> dict:
        """
        La scala reale di categorie di ogni federazione, ricavata dai
        comunicati stessi (anomalie_v2.scala_osservata) — non un'assunzione.

        Serve a selezione_v2.punti() per distinguere un vero sorpasso (una
        categoria saltata che la federazione usa davvero) da un compleanno
        (il gradino successivo): senza, Sub-17 -> Sub-19 in Colombia sembra
        un salto di due, quando nella scala vera della FCF ([15,16,17,19,20],
        nessun Sub-18) è il gradino successivo.

        Una query su tutta la tabella per ogni giocatore ricalcolato: costa
        pochi millisecondi anche sui volumi di oggi, e il budget di chiamate
        per run tiene comunque bassi i giocatori toccati a ogni giro.
        """
        selezioni = []
        for (grezzo,) in conn.execute(
                "SELECT selection_json FROM players "
                "WHERE selection_json IS NOT NULL AND selection_json != ''"):
            try:
                selezioni.append(json.loads(grezzo))
            except (ValueError, TypeError):
                continue
        return scala_osservata(selezioni)

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

        # Le evidenze servono a tre cose (persistenza di selezione, claim,
        # avvocato del diavolo): si leggono una volta sola, qui.
        evidenze = [{"raw_content": r[0], "source_domain": r[1], "source_url": r[2],
                     "observed_at": r[3], "origin": r[4]}
                    for r in conn.execute(
                        "SELECT raw_content, source_domain, source_url, "
                        "observed_at, origin FROM evidences WHERE player_id=?",
                        (pid,)).fetchall()]

        # Quante volte una federazione ha scelto questo ragazzo, e se nel
        # tempo è salito di categoria. È il segnale che Global vede davvero —
        # le statistiche no — e per 34 giocatori su 291 era già in database
        # senza che nessuno lo contasse. Vedi src/selezione_v2.py.
        persistenza = leggi_selezione(evidenze)

        sc = score_player(age=age, is_ghost=bool(is_ghost), club=club, league=league,
                          stats=stats, n_sources=n_sources, detection_count=ev_count,
                          selezione=persistenza,
                          scala_categorie=self._scala_categorie(conn))

        # L'avvocato del diavolo (src/challenge_v2.py) — l'ULTIMO cancello,
        # dopo il gate classico. Aggiunto il 26 ago 2026: assess_identity()
        # conta DOMINI distinti, e su 64 profili pubblicati solo 2 avevano
        # davvero >=2 fonti che dicessero qualcosa oltre al nome. Un elenco di
        # convocazione ripetuto su cinque date piu' un titolo di pagina
        # Transfermarkt vuoto faceva "2 fonti indipendenti", e la dashboard
        # scriveva VERIFICATO.
        #
        # Sta QUI e non nell'export perche' `publishable` decide anche chi
        # finisce nella notifica Telegram: un gate solo in dashboard lascerebbe
        # passare su Telegram esattamente cio' che la dashboard nasconde.
        #
        # I rilievi sopravvivono in review_flags accanto ai flag storici: chi
        # legge il DB vede PERCHE' una scheda non e' uscita, senza rieseguire.
        soggetto = {"canonical_name": name, "age": age, "club": club, "stats": stats}

        # La soglia di pubblicazione è quella di claims_v2: si esce quando
        # l'IDENTITÀ è stabilita — nome e club scritti da una fonte primary
        # competente. Non serve un'età e non servono due domini.
        #
        # Il gate storico (assess_identity: >=2 domini + fonte primary) NON
        # decide più: contava domini, e una convocazione della federazione
        # ripetuta su cinque date valeva quanto due aggregatori che si
        # copiano. I suoi flag restano perché descrivono ancora bene la
        # forma delle prove, ma la decisione è passata ai claim.
        claims = stabilisci(soggetto, evidenze)
        ok_claims, motivi = pubblicabile_da_claims(claims)
        rilievi = contesta(soggetto, evidenze)
        publishable = ok_claims and sopravvive(rilievi)
        flags = ",".join(f for f in (
            idn["review_flags"],
            f"eta_{claims['eta']['stato']}",
            ",".join(x["codice"] for x in rilievi)) if f)

        conn.execute("""UPDATE players SET evidence_count=?, identity_complete=?,
                        corroborated=?, publishable=?, review_flags=?, score=?, confidence=?,
                        coverage_tier=?, selection_json=?
                        WHERE id=?""",
                     (ev_count, 1 if idn["identity_complete"] else 0,
                      1 if idn["corroborated"] else 0, 1 if publishable else 0,
                      flags, sc["score"], sc["confidence"],
                      idn["coverage_tier"], _selezione_json(persistenza), pid))

    def _registra_osservazioni(self, conn, pid: int, obs: dict, dominio: str,
                               src_url: str, observed_at: str) -> None:
        """
        Scrive nel grafo (src/piramide_v2.py) cosa dice QUESTA fonte, campo
        per campo. Non decide niente: registra.

        Il tipo di fonte viene dal registro (config/sources.json). Un dominio
        che il registro non conosce NON entra: la piramide ragiona per genere
        di fonte, e un livello indovinato varrebbe meno di nessun livello.

        La data dell'atto, quando c'è, si legge dall'URL come già fa
        selezione_v2 — /2026/07/19/... è quando il documento è stato
        pubblicato, ed è l'unica cosa che distingue un'osservazione fresca da
        una che non dice a quando si riferisce.
        """
        from src.piramide_v2 import LIVELLI
        dom = (dominio or "").lower()
        dom = dom[4:] if dom.startswith("www.") else dom
        tipo = registro().get(dom, {}).get("type", "")
        if tipo not in LIVELLI:
            return
        m = _DATA_NEL_PATH.search(src_url or "")
        datato_al = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""
        for campo, valore in (("club", obs.get("club")),
                              ("eta", obs.get("age")),
                              ("position", obs.get("position")),
                              ("league", obs.get("league"))):
            if valore in (None, "", 0):
                continue
            # Stessa fonte, stesso valore, stesso campo: è una conferma, non
            # una riga nuova (stessa regola di piramide_v2.registra).
            gia = conn.execute(
                "SELECT id FROM observations WHERE player_id=? AND campo=? "
                "AND fonte_tipo=? ORDER BY id DESC LIMIT 1",
                (pid, campo, tipo)).fetchone()
            if gia:
                precedente = conn.execute(
                    "SELECT valore FROM observations WHERE id=?", (gia[0],)).fetchone()
                if precedente and precedente[0] == str(valore):
                    conn.execute("UPDATE observations SET observed_at=? WHERE id=?",
                                 (observed_at, gia[0]))
                    continue
            conn.execute(
                "INSERT INTO observations (player_id, campo, valore, fonte_tipo, "
                "source_domain, source_url, datato_al, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, campo, str(valore), tipo, dom, src_url, datato_al, observed_at))

    def grafo_di(self, player_id: int) -> dict:
        """Il grafo delle fonti di un giocatore, nella forma che
        piramide_v2.risolvi() sa leggere. Chiave: sempre "p"."""
        grafo: dict = {}
        with self._conn() as conn:
            for campo, valore, tipo, datato, quando, url in conn.execute(
                    "SELECT campo, valore, fonte_tipo, datato_al, observed_at, "
                    "source_url FROM observations WHERE player_id=? ORDER BY id",
                    (player_id,)):
                voce = {"valore": valore, "fonte": tipo,
                        "osservato_il": quando or "", "url": url or ""}
                if datato:
                    voce["datato_al"] = datato
                grafo.setdefault("p", {}).setdefault(campo, []).append(voce)
        return grafo

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
                    "SELECT stats_json, first_detected, club, canonical_name, age "
                    "FROM players WHERE id=?", (pid,)).fetchone()
                cur_stats, prior_first_detected, prior_club, prior_name, cur_age = row
                merged = json.loads(cur_stats) if cur_stats else {}
                for k, v in new_stats.items():
                    if isinstance(v, (int, float)):
                        merged[k] = max(merged.get(k, 0), v)

                # L'età non è più COALESCE(age, ?): quella regola la fissava
                # alla prima osservazione per sempre, prova o non prova.
                # Caso reale che l'ha tradita (31 ago 2026): Jorman Camilo
                # Mendoza Garrido è entrato con età 16 da un'estrazione
                # generica, ed è rimasto 16 anche dopo che la sua scheda
                # Transfermarkt, letta per intero e non più troncata al
                # titolo, diceva "nato il 14/01/2008" — cioè 18. Il 16 non è
                # mai stato smentito, solo mai messo alla prova: la card
                # mostrava un'età che nessuna fonte competente scriveva
                # (claims_v2 cerca "16" nel testo, e nel testo c'è "18"), e
                # l'anomalia di anticipo di categoria calcolata su quel 16
                # era falsa — un ragazzo convocato in Sub-17 a 16 anni, non
                # a 14.
                #
                # La regola nuova: un'età si aggiorna quando quella in
                # colonna non è ancora una prova, cioè quando claims_v2 (la
                # stessa funzione che decide cosa mostrare in scheda) non
                # trova una fonte competente che la scriva. Un'età già
                # PROVATA non si lascia scavalcare da un'osservazione
                # qualunque: altrimenti un aggregatore rumoroso potrebbe
                # smentire un fatto che una federazione ha già stabilito.
                nuova_eta = obs.get("age")
                eta_da_scrivere = cur_age
                if isinstance(nuova_eta, int) and nuova_eta != cur_age:
                    if cur_age is None:
                        eta_da_scrivere = nuova_eta
                    else:
                        evidenze_correnti = [
                            {"source_domain": d, "source_url": u,
                             "raw_content": c, "origin": o}
                            for d, u, c, o in conn.execute(
                                "SELECT source_domain, source_url, raw_content, "
                                "origin FROM evidences WHERE player_id=?", (pid,))]
                        eta_provata = stabilisci(
                            {"canonical_name": prior_name, "club": prior_club,
                             "age": cur_age}, evidenze_correnti
                        ).get("eta", {}).get("stato") == DICHIARATO
                        if not eta_provata:
                            eta_da_scrivere = nuova_eta

                conn.execute("""
                    UPDATE players SET
                        age = ?, position = COALESCE(position, ?),
                        club = COALESCE(club, ?), league = COALESCE(league, ?),
                        region = COALESCE(region, ?),
                        gender = CASE WHEN gender='unknown' THEN ? ELSE gender END,
                        stats_json = ?,
                        first_detected = COALESCE(first_detected, ?),
                        last_seen = ?
                    WHERE id=?
                """, (eta_da_scrivere, obs.get("position"), obs.get("club"),
                      obs.get("league"), obs.get("region"),
                      obs.get("gender") or "unknown",
                      json.dumps(merged) if merged else None,
                      observed_at, observed_at, pid))

            conn.execute("""INSERT INTO evidences
                (player_id, source_url, source_domain, observed_at, raw_content, origin)
                VALUES (?, ?, ?, ?, ?, 'extractor')""",
                (pid, src_url, dom, observed_at,
                 obs.get("evidence_quote"), ))
            # Il grafo delle fonti: cosa dice QUESTA fonte, a prescindere da
            # chi ha vinto in colonna. È l'unico posto dove i valori sono
            # ancora separati per fonte — dopo l'UPDATE resta un valore solo.
            self._registra_osservazioni(conn, pid, obs, dom, src_url, observed_at)
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

    # Versione del cancello. Va alzata ogni volta che una regola di
    # pubblicazione cambia in modo da poter BOCCIARE profili gia' pubblicati.
    #   1 = gate storico (identita' completa + >=2 domini + fonte primary)
    #   2 = 26 ago 2026, avvocato del diavolo (src/challenge_v2.py)
    GATE_VERSION = 2

    def reapply_gate(self, conn=None) -> dict:
        """
        Ripassa il cancello su TUTTI i giocatori, non solo su quelli toccati
        da una nuova osservazione.

        Serve perche' _recompute() gira solo dentro ingest_observation(): un
        profilo gia' pubblicato che non riceve piu' fonti non verrebbe mai
        rivalutato. Quando il 26 ago 2026 il gate e' diventato piu' severo,
        senza questo passaggio i 49 profili che non lo superavano piu'
        sarebbero rimasti online (e su Telegram) a tempo indefinito: una
        regola nuova che non tocca il passato non protegge nessuno.

        Ritorna {'esaminati', 'ritirati', 'ammessi'} — ritirati = erano
        pubblicati e ora non lo sono piu'.
        """
        proprio = conn is None
        conn = conn or self._conn()
        try:
            prima = {r[0]: r[1] for r in conn.execute(
                "SELECT id, COALESCE(publishable, 0) FROM players")}
            for pid in prima:
                self._recompute(conn, pid)
            dopo = {r[0]: r[1] for r in conn.execute(
                "SELECT id, COALESCE(publishable, 0) FROM players")}
            if proprio:
                conn.commit()
        finally:
            if proprio:
                conn.close()
        return {
            "esaminati": len(prima),
            "ritirati": sum(1 for k in prima if prima[k] and not dopo.get(k)),
            "ammessi": sum(1 for k in prima if not prima[k] and dopo.get(k)),
        }

    # ---- Corroborazione attiva (Fase B3+) ----
    def player_domains(self, pid: int) -> set:
        """Domini-fonte già associati a un giocatore, provino qualcosa o no.

        Usare per la deduplica display/conteggio. NON per decidere se vale
        la pena ricercare di nuovo su un dominio: vedi proven_domains().
        """
        with self._conn() as conn:
            return {r[0] for r in conn.execute(
                "SELECT DISTINCT source_domain FROM evidences WHERE player_id=? AND source_domain!=''",
                (pid,))}

    def proven_domains(self, pid: int) -> set:
        """
        Domini che hanno DAVVERO stabilito un claim (nome/club/età/stats) per
        questo giocatore secondo claims_v2 — non solo "presenti come evidenza".

        Trovato il 29 ago 2026 insieme al fix di players_to_corroborate():
        find_profile() usa exclude_domains per non riproporre un aggregatore
        già usato come fonte. Ma "già usato" veniva letto come player_domains
        (qualsiasi dominio con almeno un'evidenza) — e un'evidenza può essere
        presente senza aver mai provato nulla: una scheda Transfermarkt di un
        professionista adulto omonimo, scartata da claims_v2 ma già in
        tabella, faceva escludere Transfermarkt per sempre da una ricerca che
        in realtà non aveva mai davvero trovato il giocatore giusto.

        Riusa stabilisci()/fonti_che_stabiliscono() — la stessa identica
        logica del gate reale in _recompute() — invece di re-inventare un
        secondo criterio di "prova" che potrebbe divergere da quello.
        """
        with self._conn() as conn:
            p = conn.execute(
                "SELECT canonical_name, age, club, stats_json FROM players WHERE id=?",
                (pid,)).fetchone()
            if not p:
                return set()
            name, age, club, stats_json = p
            evidenze = [{"raw_content": r[0], "source_domain": r[1], "source_url": r[2],
                        "observed_at": r[3], "origin": r[4]}
                       for r in conn.execute(
                           "SELECT raw_content, source_domain, source_url, "
                           "observed_at, origin FROM evidences WHERE player_id=?",
                           (pid,)).fetchall()]
        soggetto = {"canonical_name": name, "age": age, "club": club,
                   "stats": json.loads(stats_json) if stats_json else {}}
        return fonti_che_stabiliscono(stabilisci(soggetto, evidenze))

    def players_to_corroborate(self, limit: int = 100,
                               cooldown_hours: int = None) -> list:
        """
        Dict {id, name, age, club} dei giocatori con nome completo e non
        ancora pubblicabili.

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

        Criterio di ingresso corretto il 29 ago 2026 (misurato sul DB di
        produzione, non a intuito): contava i DOMINI distinti di evidenza
        (< 2 = ancora da corroborare), non se il giocatore fosse davvero
        pubblicabile. Un dominio conta anche quando l'evidenza che porta non
        prova nulla — es. una scheda Transfermarkt di un professionista
        adulto omonimo, scartata da claims_v2 ma già presente come evidenza.
        Risultato misurato: 72 giocatori su 121 non pubblicabili (24 Brasile,
        21 Argentina) avevano già ≥2 domini e uscivano PER SEMPRE dalla coda,
        senza mai ricevere un secondo (o terzo) tentativo di ricerca — pur
        non essendo mai stati pubblicabili nemmeno un giorno. Il criterio ora
        è direttamente "non pubblicabile", che è anche più permissivo
        all'altro estremo: un giocatore con una sola fonte ma già
        pubblicabile (claims_v2 non richiede sempre 2 fonti, vedi commento in
        _recompute) smette di consumare budget di ricerca che non gli serve.
        """
        if cooldown_hours is None:
            cooldown_hours = int(os.getenv("CORR_COOLDOWN_HOURS", "24"))
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT p.id, p.canonical_name, p.age, p.club
                FROM players p
                WHERE p.name_token_count >= 2
                  AND p.publishable = 0
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

    # ---- Turno di scansione delle fonti (discovery) ----
    def sources_in_scan_order(self, sources: list) -> list:
        """
        Le stesse fonti, riordinate: prima quelle MAI scansionate, poi quelle
        che aspettano da più tempo. A parità resta l'ordine del registro.

        Perché (misurato il 27 ago 2026 sul DB di produzione). Il ciclo di
        discovery in scripts/ingest_v2.py scorre le fonti in ordine di
        registro e fa `break` quando il budget LLM finisce. L'ordine del
        registro è FISSO, il budget è piccolo (15 chiamate, ~1/3 riservate
        alla corroborazione): il ciclo non è mai arrivato oltre la posizione
        44 su 83. Risultato, contato sulle seen_items reali:

            67 fonti primary nel registro
            29 raggiunte almeno una volta
            38 MAI scansionate, nemmeno una volta

        Fra le mai scansionate: tutta l'Asia (India, Bangladesh, Indonesia,
        Vietnam, Thailandia, Filippine, Uzbekistan, Kazakistan), tutte e 4 le
        confederazioni, tutte e 4 le accademie, quasi tutto il Nord Africa.
        Delle 45 primary che non hanno mai prodotto un'evidenza, 38 non hanno
        mai prodotto perché non gli è mai stato CHIESTO: le fonti davvero
        interrogate-e-sterili sono 7.

        È lo stesso identico bug già trovato e corretto per la corroborazione
        il 21 ago 2026 (vedi players_to_corroborate): coda in ordine stabile +
        tetto per run = la testa sempre, il resto mai. Là fu risolto, qui no.

        Sicura al primo run dopo il deploy: nessuna fonte ha ancora un record
        di scansione, quindi sono tutte "mai scansionate", il criterio di
        ordinamento pareggia e sorted() — che è stabile — restituisce
        ESATTAMENTE l'ordine di registro di oggi. Nessuna disruzione
        immediata; la rotazione parte dal secondo run, quando last_scan_at
        comincia a popolarsi. Stesso pattern di migrazione verificato per
        corr_attempts.
        """
        # julianday() e non la stringa grezza: i timestamp possono arrivare
        # in due formati diversi ('2026-08-27T07:30:00.123' da isoformat,
        # '2026-08-27 06:30:00' da un datetime() SQL) e confrontarli come
        # testo li ordina per separatore invece che per data — ' ' < 'T',
        # quindi un record scritto in un formato precede SEMPRE l'altro a
        # prescindere dall'ora. Beccato dal self-test qui sotto. julianday
        # normalizza entrambi in un numero, come già fa la coda di
        # corroborazione (players_to_corroborate).
        with self._conn() as conn:
            stato = {r[0]: r[1] for r in conn.execute(
                "SELECT source_id, julianday(last_scan_at) FROM source_scans")}
        # Chiave: (già scansionata?, quando). False < True mette le mai
        # scansionate davanti; fra le altre vince il julianday più basso,
        # cioè quella che aspetta da più tempo.
        return sorted(sources,
                      key=lambda s: (stato.get(s.get("id")) is not None,
                                     stato.get(s.get("id")) or 0.0))

    def record_source_scan(self, source_id: str) -> None:
        """
        Segna che questa fonte è stata appena scansionata — abbia prodotto
        articoli nuovi o no. Anche una scansione a vuoto è memoria utile: dice
        "questa l'ho appena guardata, tocca alle altre", che è esattamente il
        punto. Chiamata da ingest_v2.py dopo OGNI monitor.new_items().
        """
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO source_scans (source_id, scans, last_scan_at)
                VALUES (?, 1, ?)
                ON CONFLICT(source_id) DO UPDATE
                SET scans = COALESCE(scans, 0) + 1, last_scan_at = excluded.last_scan_at
            """, (source_id, datetime.now().isoformat()))
            conn.commit()


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

    # L'età non è più COALESCE(age, ?) fissato per sempre: si aggiorna finché
    # non è ancora una prova. Caso reale, 31 ago 2026: Jorman Camilo Mendoza
    # Garrido è entrato con età 16 da un'estrazione generica (nessuna fonte
    # citava "16"), ed è rimasto 16 anche dopo che la sua scheda Transfermarkt,
    # letta per intero, diceva "nato il 14/01/2008" (18 anni) — perché il 16
    # non era mai stato smentito, solo mai messo alla prova.
    with _tempfile.TemporaryDirectory() as _tmp4:
        _age_db = OB1DatabaseV2(str(Path(_tmp4) / "test_age.db"))
        pid, _ = _age_db.ingest_observation({
            "name": "Jorman Camilo Mendoza Garrido", "club": "Envigado F.C.",
            "age": 16,
            "source_url": "https://transfermarkt.com/jorman-mendoza/profil/spieler/1",
            "observed_at": "2026-07-01T00:00:00",
            "evidence_quote": "Jorman Mendoza - Player profile",  # il 16 non è nel testo
        })
        assert _age_db._conn().execute(
            "SELECT age FROM players WHERE id=?", (pid,)).fetchone() == (16,)

        # La scheda letta per intero: l'età vera è 18, e lo dice.
        _age_db.ingest_observation({
            "name": "Jorman Camilo Mendoza Garrido", "club": "Envigado F.C.",
            "age": 18,
            "source_url": "https://transfermarkt.com/jorman-mendoza/profil/spieler/1",
            "observed_at": "2026-08-31T00:00:00",
            "evidence_quote": "Scheda Transfermarkt: Jorman Mendoza, 18 anni, "
                              "Envigado F.C.",
        })
        assert _age_db._conn().execute(
            "SELECT age FROM players WHERE id=?", (pid,)).fetchone() == (18,), \
            "un'età mai provata deve potersi correggere"

        # Ma un'età GIÀ provata non si lascia scavalcare da un'osservazione
        # qualunque: altrimenti un aggregatore rumoroso potrebbe smentire un
        # fatto che una fonte competente ha già stabilito.
        pid2, _ = _age_db.ingest_observation({
            "name": "Altra Persona Test", "club": "Club Test", "age": 17,
            "source_url": "https://sofascore.com/player/altra-persona-test",
            "observed_at": "2026-07-01T00:00:00",
            "evidence_quote": "Altra Persona Test, 17 anni, titolare del "
                              "Club Test.",
        })
        _age_db.ingest_observation({
            "name": "Altra Persona Test", "club": "Club Test", "age": 15,
            "source_url": "https://fbref.com/en/players/altra-persona-test",
            "observed_at": "2026-08-01T00:00:00",
            "evidence_quote": "Altra Persona Test compie gli anni: 15 anni "
                              "e già al Club Test.",  # numero diverso, stesso nome
        })
        assert _age_db._conn().execute(
            "SELECT age FROM players WHERE id=?", (pid2,)).fetchone() == (17,), \
            "un'età già provata da una fonte competente non va scavalcata"
    print("OK merge età: si corregge un segnaposto, non un fatto già provato")

    # Grafo delle fonti: l'ingest registra CHI ha detto cosa, non solo il
    # valore che ha vinto in colonna. È la memoria che mancava — senza,
    # una divergenza si scopre solo quando produce un'anomalia falsa.
    from src.piramide_v2 import risolvi
    with _tempfile.TemporaryDirectory() as _tmp5:
        _g_db = OB1DatabaseV2(str(Path(_tmp5) / "test_grafo.db"))
        # Una convocazione federale, datata nell'URL.
        _g_db.ingest_observation({
            "name": "Nome Grafo Test", "club": "Envigado F.C.", "age": 17,
            "source_url": "https://fcf.com.co/2026/07/19/convocatoria-sub-17/",
            "observed_at": "2026-07-20T00:00:00",
            "evidence_quote": "Nome Grafo Test – Envigado F.C.",
        })
        # La scheda dell'aggregatore dice un'altra età e un altro club.
        pid, _ = _g_db.ingest_observation({
            "name": "Nome Grafo Test", "club": "Envigado FC U20", "age": 19,
            "source_url": "https://www.transfermarkt.com/nome-grafo/profil/spieler/9",
            "observed_at": "2026-08-01T00:00:00",
            "evidence_quote": "Scheda Transfermarkt: Nome Grafo Test, 19 anni",
        })
        grafo = _g_db.grafo_di(pid)
        # ETÀ: fatto lento, vince il consolidato anche se è arrivato dopo.
        eta = risolvi(grafo, "p", "eta")
        assert eta["valore"] == "19" and eta["fonte"] == "aggregator", eta
        assert eta["conflitto"] and eta["alternativa"] == "17", eta
        # CLUB: stesso disaccordo, verso opposto — vince l'atto datato.
        club = risolvi(grafo, "p", "club")
        assert club["valore"] == "Envigado F.C.", club
        assert club["fonte"] == "federation" and club["datato_al"] == "2026-07-19", club
        assert club["conflitto"] and club["alternativa"] == "Envigado FC U20", club

        # Una fonte fuori registro non entra nel grafo: mai un livello indovinato.
        _g_db.ingest_observation({
            "name": "Nome Grafo Test", "club": "Club Inventato", "age": 21,
            "source_url": "https://blog-di-tizio.example/nome-grafo",
            "observed_at": "2026-08-02T00:00:00",
            "evidence_quote": "Nome Grafo Test gioca nel Club Inventato",
        })
        assert risolvi(_g_db.grafo_di(pid), "p", "club")["valore"] == "Envigado F.C."
    print("OK grafo fonti: l'ingest ricorda chi ha detto cosa, e il verso "
          "della piramide cambia per campo")

    # ARCH-003 Fase 1: la regola è ora collegata al gate vero (_recompute),
    # non solo disponibile come helper. Due fonti che si copiano (entrambe
    # aggregatori secondary) non bastano più; una primary + una secondary sì.
    with _tempfile.TemporaryDirectory() as _tmp2:
        _gate_db = OB1DatabaseV2(str(Path(_tmp2) / "test_gate.db"))
        pid, _ = _gate_db.ingest_observation({
            "name": "Nome Cognome Test", "club": "Club Test", "age": 18,
            "source_url": "https://sofascore.com/player/nome-cognome-test",
            "observed_at": "2026-03-01T00:00:00",
            "evidence_quote": "Nome Cognome Test, 18 anni, ha esordito con il "
                              "Club Test segnando una doppietta nel finale.",
        })
        _gate_db.ingest_observation({
            "name": "Nome Cognome Test", "club": "Club Test", "age": 18,
            "source_url": "https://fbref.com/en/players/nome-cognome-test",
            "observed_at": "2026-03-05T00:00:00",
            "evidence_quote": "Nome Cognome Test resta il profilo piu' seguito "
                              "del Club Test: 18 anni e quattro presenze.",
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
            "evidence_quote": "Il Club Test lancia Nome Cognome Test: 18 anni, "
                              "titolare per la prima volta in campionato.",
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
            "evidence_quote": "Jomo Otieno Test, 17 anni, ha firmato il gol "
                              "vittoria del Gor Mahia nel derby di ieri.",
        })
        _lc_db.ingest_observation({
            "name": "Jomo Otieno Test", "club": "Gor Mahia", "age": 17,
            "region": "Kenya",
            "source_url": "https://fbref.com/en/players/jomo-otieno-test",
            "observed_at": "2026-03-05T00:00:00",
            "evidence_quote": "Jomo Otieno Test del Gor Mahia, 17 anni, "
                              "convocato per la prima volta in nazionale.",
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
            "evidence_quote": "Altro Nome Test, 17 anni dell' Altro Club, "
                              "ha giocato titolare tutta la partita di ieri.",
        })
        _lc_db.ingest_observation({
            "name": "Altro Nome Test", "club": "Altro Club", "age": 17,
            "source_url": "https://fbref.com/en/players/altro-nome-test",
            "observed_at": "2026-03-05T00:00:00",
            "evidence_quote": "Altro Nome Test resta un punto fermo dell' "
                              "Altro Club: 17 anni, sei presenze stagionali.",
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

    # --- proven_domains: un dominio con un'evidenza che non prova nulla non
    # deve contare come "già usato" (29 ago 2026, vedi commento nel metodo) ---
    with _tempfile.TemporaryDirectory() as _tmp6:
        _pd_db = OB1DatabaseV2(str(Path(_tmp6) / "test_proven_domains.db"))
        pid, _ = _pd_db.ingest_observation({
            "name": "Paulo Ricardo Nakamura", "club": "Coritiba",
            "source_url": "https://ge.globo.com/pr/futebol/noticia/paulo-nakamura.ghtml",
            "observed_at": "2026-03-01T00:00:00",
            "evidence_quote": "O volante Paulo Ricardo Nakamura, do Coritiba, "
                              "foi convocado para a Sub-20.",
        })
        # Scheda Transfermarkt di un omonimo: nessun token in comune con
        # nome o club del nostro giocatore -> non prova nulla, ma resta
        # un'evidenza in tabella (esattamente come nel caso reale).
        _pd_db.ingest_observation({
            "name": "Paulo Ricardo Nakamura",
            "source_url": "https://transfermarkt.com/paulo-nakamura/profil/spieler/999",
            "observed_at": "2026-03-05T00:00:00",
            "evidence_quote": "Outro Jogador Qualquer - Player profile",
        })
        all_domains = _pd_db.player_domains(pid)
        assert all_domains == {"ge.globo.com", "transfermarkt.com"}, all_domains
        proven = _pd_db.proven_domains(pid)
        assert proven == {"ge.globo.com"}, \
            f"transfermarkt.com non prova nulla per questo giocatore, non deve restare in proven_domains: {proven}"
    print("OK proven_domains: un dominio presente ma che non prova nulla non blocca la ricerca")

    # --- Turno di scansione delle fonti (27 ago 2026) ---
    with _tempfile.TemporaryDirectory() as _tmp5:
        _sc_db = OB1DatabaseV2(str(Path(_tmp5) / "test_scan_order.db"))
        _reg = [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]

        # 1. Primo run dopo il deploy: nessuna fonte ha un record di
        #    scansione. DEVE tornare esattamente l'ordine del registro —
        #    è la garanzia di "nessuna disruzione immediata in produzione".
        assert [s["id"] for s in _sc_db.sources_in_scan_order(_reg)] == \
            ["a", "b", "c", "d"], \
            "primo run: l'ordine del registro non deve cambiare"

        # 2. Dopo che la testa del registro è stata scansionata (il caso
        #    reale: il ciclo si ferma sul budget dopo le prime fonti), il
        #    turno dopo deve toccare a chi non è mai stato guardato.
        _sc_db.record_source_scan("a")
        _sc_db.record_source_scan("b")
        ordine = [s["id"] for s in _sc_db.sources_in_scan_order(_reg)]
        assert ordine[:2] == ["c", "d"], \
            f"le mai scansionate devono passare davanti: {ordine}"
        assert set(ordine) == {"a", "b", "c", "d"}, \
            f"nessuna fonte va persa nel riordino: {ordine}"

        # 3. Quando TUTTE sono state scansionate almeno una volta, vince chi
        #    aspetta da più tempo — così la rotazione continua invece di
        #    ripiegare di nuovo sulla testa del registro.
        #
        #    I due formati di timestamp qui sotto sono VOLUTI, non una
        #    sciatteria: record_source_scan scrive in isoformat, con la 'T'
        #    ('2026-08-27T07:30:00.123'), mentre un datetime() SQL scrive con
        #    lo spazio ('2026-08-27 06:30:00'). Confrontati come TESTO, ' '
        #    viene prima di 'T': il FORMATO deciderebbe l'ordine al posto
        #    della data. Qui 'a' è vecchia di anni ma in formato 'T', 'b' è di
        #    un'ora fa in formato spazio: ordinando le stringhe grezze
        #    passerebbe 'b', che è sbagliato. julianday() normalizza entrambi.
        #    Le ore sono fissate a mano, stessa data, invece di usare l'ora
        #    corrente: solo così il separatore è davvero il carattere che
        #    decide il confronto testuale. (Prima stesura di questo test
        #    metteva 'a' nel 2020 — a quel punto a decidere era l'ANNO, e il
        #    test passava anche con l'implementazione sbagliata. Verificato
        #    rompendo apposta il codice: ora fallisce, prima no.)
        _sc_db.record_source_scan("c")
        _sc_db.record_source_scan("d")
        with _sc_db._conn() as _c:
            for _sid, _ts in (("a", "2026-08-27T00:00:00.000000"),  # 'T', la più vecchia
                              ("b", "2026-08-27 06:00:00"),          # spazio, seconda
                              ("c", "2026-08-27T12:00:00.000000"),
                              ("d", "2026-08-27T18:00:00.000000")):
                _c.execute("UPDATE source_scans SET last_scan_at = ? "
                           "WHERE source_id = ?", (_ts, _sid))
        ordine = [s["id"] for s in _sc_db.sources_in_scan_order(_reg)]
        assert ordine == ["a", "b", "c", "d"], \
            ("ordine per data, non per formato del timestamp: confrontando le "
             f"stringhe grezze 'b' passerebbe davanti ad 'a'. Ottenuto: {ordine}")

        # 4. record_source_scan conta i passaggi (una fonte scansionata due
        #    volte non deve ripartire da zero: serve a leggere nei log se una
        #    fonte gira davvero o se resta indietro).
        riga = _sc_db._conn().execute(
            "SELECT scans FROM source_scans WHERE source_id='a'").fetchone()
        assert riga[0] == 1, f"scans deve contare i passaggi: {riga}"
        _sc_db.record_source_scan("a")
        riga = _sc_db._conn().execute(
            "SELECT scans FROM source_scans WHERE source_id='a'").fetchone()
        assert riga[0] == 2, f"seconda scansione non contata: {riga}"
    print("OK turno fonti: ordine invariato al primo run, poi priorità a chi non è mai stato scansionato")

    db = _db
    print(f"Schema v2 inizializzato: {db.db_path}")
