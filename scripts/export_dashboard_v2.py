#!/usr/bin/env python3
"""
OB1 v2 — Export dashboard (cutover)

Legge lo store v2 (ob1_v2.db) e scrive docs/data/players_v2.json per la
dashboard pubblica. Ogni giocatore esce con le sue PROVE (fonti linkate),
il punteggio trasparente (merito × confidenza, con breakdown ricalcolato)
e lo stato del gate — la promessa "ogni nome con le sue prove" resa dato.

Uso:
    python scripts/export_dashboard_v2.py [--db data/ob1_v2.db] [--out docs/data/players_v2.json]
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.scoring_v2 import score_player
from src.claims_v2 import stabilisci, registro, DICHIARATO, DEDOTTO, ASSENTE
from src.anomalie_v2 import come_dict as anomalie_dict
from src.anomalie_v2 import leggi as leggi_anomalie
from src.anomalie_v2 import scala_osservata

ROOT = Path(__file__).parent.parent


def _version_and_build() -> tuple:
    """
    (version, build) per il footer "è aggiornato al deploy giusto?" — stesso
    scopo del footer Sentinel (VERSION + K_REVISION), ma questo prodotto è
    statico su GitHub Pages: non esiste una revision iniettata dalla
    piattaforma, quindi build = short SHA del commit che ha generato
    l'export. Mai deve poter rompere l'export: qualunque errore (repo non
    git, comando assente) ripiega su "dev" invece di sollevare.
    version viene da VERSION in root, bumpato a mano — non ad ogni commit,
    è per un umano che guarda il footer, non un hash.
    """
    version = "0.0.0"
    try:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip() or version
    except OSError:
        pass
    build = "dev"
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=5,
        )
        if sha.returncode == 0 and sha.stdout.strip():
            build = sha.stdout.strip()
    except Exception:
        pass
    return version, build


def _selezione_di(p) -> dict:
    """
    Il blocco `selection_json` scritto da database_v2._recompute, più il nome
    leggibile della federazione (una sola, nella pratica: se un giorno ce ne
    fossero due la frase lo dice già da sé).

    Riga vuota o colonna assente su un DB vecchio = {}: la scheda non mostra
    la sezione, non mostra uno zero.
    """
    try:
        grezzo = p["selection_json"]
    except (KeyError, IndexError):
        return {}
    if not grezzo:
        return {}
    try:
        sel = json.loads(grezzo)
    except (ValueError, TypeError):
        return {}
    eventi = sel.get("eventi") or []
    if eventi:
        sel["fonte"] = eventi[0].get("fonte", "")
    sel["frase_en"] = _selection_en(sel) + "." if sel.get("quante") else ""
    return sel


def _come_persistenza(sel: dict):
    """
    Il dict salvato in colonna, riportato alla forma che `punti()` sa pesare.
    Solo i campi che contano per il punteggio: il resto serve alla scheda.
    """
    if not sel or not sel.get("quante"):
        return None
    from src.selezione_v2 import Persistenza
    return Persistenza(
        quante=sel.get("quante", 0),
        progressione=bool(sel.get("progressione")),
        mesi_di_arco=sel.get("mesi_di_arco", 0),
        categorie=sel.get("categorie") or [],
    )


_MESI_EN = {"gennaio": "January", "febbraio": "February", "marzo": "March",
            "aprile": "April", "maggio": "May", "giugno": "June",
            "luglio": "July", "agosto": "August", "settembre": "September",
            "ottobre": "October", "novembre": "November", "dicembre": "December"}


def _selection_en(sel: dict) -> str:
    """La stessa frase in inglese. Costruita dai campi, non tradotta a pezzi."""
    chi = sel.get("fonte") or "a national federation"
    testo = f"Called up {sel['quante']} times by {chi}"
    dal, al = sel.get("dal", ""), sel.get("al", "")
    if dal and al and dal[:7] != al[:7]:
        testo += f" between {_data_en(dal)} and {_data_en(al)}"
    cats = sel.get("categorie") or []
    if sel.get("progressione") and len(cats) >= 2:
        testo += f", from {cats[0].replace('sub-', 'U')} to {cats[-1].replace('sub-', 'U')}"
    elif len(cats) == 1:
        testo += f", {cats[0].replace('sub-', 'U')}"
    return testo


def _mesi_da(iso: str):
    """Quanti mesi sono passati da questa data. None se la data non c'è."""
    from datetime import datetime
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    oggi = datetime.now()
    return (oggi.year - d.year) * 12 + (oggi.month - d.month)


def _mese_it(iso: str) -> str:
    from src.selezione_v2 import _mese_leggibile
    return _mese_leggibile(iso)


def _data_en(iso: str) -> str:
    from datetime import datetime
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    return d.strftime("%B %Y")


def assess_player(p: dict, evidence_count: int = 1) -> dict:
    """
    "Perché sì / cautele / prossimi passi" per la scheda giocatore.
    Derivato in CODICE dagli stessi segnali del punteggio (mai da un LLM):
    non può contraddire il numero, non costa nulla, ed è testabile.

    Ogni riga è una coppia (it, en): a differenza dei verdetti di uno swarm
    AI (vedi Sentinel), qui non c'è un modello da rilanciare in due lingue —
    sono template Python deterministici, tradurli non costa una chiamata in
    più. L'export porta entrambe le lingue; il client sceglie a runtime
    (docs/index.html), IT resta il default/fallback se manca la EN.
    """
    pros, cautions, steps = [], [], []
    flags = {f for f in (p.get("review_flags") or "").split(",") if f}
    stats = p.get("stats") or {}
    has_stats = any(stats.values())
    bd = p.get("breakdown") or {}
    age = p.get("age")
    n_src = p.get("n_sources") or 0

    # --- Perché sì ---
    if age is not None and age <= 17:
        pros.append((f"Molto giovane ({age} anni): ampio margine di sviluppo",
                      f"Very young ({age} yo): plenty of room to develop"))
    elif age is not None and age <= 19:
        pros.append((f"Giovane ({age} anni)", f"Young ({age} yo)"))
    if stats.get("goals") or stats.get("assists"):
        det_it = " e ".join(x for x in [
            f"{stats['goals']} gol" if stats.get("goals") else "",
            f"{stats['assists']} assist" if stats.get("assists") else ""] if x)
        det_en = " and ".join(x for x in [
            f"{stats['goals']} goals" if stats.get("goals") else "",
            f"{stats['assists']} assists" if stats.get("assists") else ""] if x)
        suffix_it = f" in {stats['apps']} presenze" if stats.get("apps") else ""
        suffix_en = f" in {stats['apps']} appearances" if stats.get("apps") else ""
        pros.append((f"Produzione documentata: {det_it}{suffix_it}",
                      f"Documented output: {det_en}{suffix_en}"))
    elif stats.get("apps"):
        pros.append((f"Continuità documentata: {stats['apps']} presenze",
                      f"Documented continuity: {stats['apps']} appearances"))
    asym = bd.get("asymmetry") or 0  # 'or' voluto: coerce anche un eventuale None
    if asym >= 12:
        pros.append(("Fuori dai radar mainstream: alta asimmetria informativa",
                      "Off the mainstream radar: high information asymmetry"))
    elif asym >= 6:
        pros.append(("Contesto minore: valore potenzialmente sottoprezzato",
                      "Minor context: potentially underpriced"))
    # "Confermata da N fonti" solo se quelle fonti dicono davvero qualcosa.
    # 26 ago 2026: questa riga contava DOMINI. Un elenco di convocazione
    # ripetuto su cinque date piu' un titolo di pagina Transfermarkt vuoto
    # diventava "Identità confermata da 2 fonti indipendenti" — vero alla
    # lettera, falso nella sostanza. Il rilievo dell'avvocato del diavolo
    # (src/challenge_v2.py) arriva qui dentro via review_flags.
    if n_src >= 2 and "una_sola_fonte_sostanziale" not in flags \
            and "nessuna_fonte_sostanziale" not in flags:
        pros.append((f"Identità confermata da {n_src} fonti indipendenti",
                      f"Identity confirmed by {n_src} independent sources"))
    # "Rilevamenti" è una parola del nostro tubo, non del mestiere di chi
    # legge: dice quante volte lo scraper ha visto una pagina, e a un
    # direttore sportivo non serve a niente. Dove c'è una storia di
    # convocazioni la si dice per quello che è — quante volte una
    # federazione lo ha scelto, quando, e se è salito di categoria — e i
    # documenti stanno qui sotto, apribili. Vedi src/selezione_v2.py.
    sel = p.get("selezione") or {}
    if sel.get("quante", 0) >= 2:
        pass          # la storia di convocazioni sta in chiaro sulla scheda,
                      # con i documenti: ripeterla qui la direbbe due volte
    elif evidence_count >= 5:
        pros.append((f"Segnale persistente: {evidence_count} rilevamenti",
                      f"Persistent signal: {evidence_count} detections"))

    # --- Cautele ---
    # In cima apposta: è la cautela che cambia lo STANDARD di verifica, non
    # solo un dettaglio del profilo. "copertura_bassa_sperimentale" vuol dire
    # che questo nome è pubblicato senza una fonte di stampa primaria — non
    # perché la trascuriamo, ma perché nella sua regione (Africa subsahariana/
    # Nord Africa, Asia Sud/Sudest/Centrale, Caraibi, Pacifico) il registro non
    # ne ha ancora una registrata: la regola normale bocciava per "fonte non
    # ancora nel registro", non per prova debole. Ogni lettore deve saperlo
    # prima di leggere il resto della scheda, non scoprirlo dopo.
    if "copertura_bassa_sperimentale" in flags:
        cautions.append((
            "Regione a bassa copertura mediatica: verificato con criteri adattati, senza fonte di stampa primaria — non lo stesso standard degli altri profili",
            "Low media-coverage region: verified under adapted criteria, without a primary press source — not the same standard as other profiles"))
    # Caso diverso: qui il gate NON ha ceduto (source_count>=2 ma non
    # low_coverage), quindi il profilo resta in tracking, non pubblicato — ma
    # vale comunque dirlo esplicitamente invece di lasciarlo capire dal solo
    # "non pubblicabile".
    elif "senza_fonte_primary" in flags:
        cautions.append((
            "2+ fonti ma nessuna di stampa primaria: non basta ancora per il gate standard",
            "2+ sources but none is a primary press outlet: not enough yet for the standard gate"))
    # Rilievi dell'avvocato del diavolo (src/challenge_v2.py). Sono in
    # review_flags accanto ai flag storici. Quelli BLOCCANTI non arrivano qui
    # (la scheda non e' pubblicata); questi sono i rilievi di cautela, e
    # devono VEDERSI: un profilo che esce con una debolezza nota deve
    # dichiararla, altrimenti torniamo al problema di partenza.
    if "una_sola_fonte_sostanziale" in flags:
        cautions.append((
            "Un solo dominio dice qualcosa di concreto: le altre fonti confermano solo che il nome esiste",
            "Only one domain says anything concrete: the other sources merely confirm the name exists"))
    if "eta_dedotta_dalla_categoria" in flags:
        cautions.append((
            f"L'età ({age}) non è scritta da nessuna fonte: è dedotta dalla categoria del torneo (Sub-{age})",
            f"The age ({age}) is not stated by any source: it is inferred from the tournament category (U-{age})"))
    if "prove_che_non_lo_nominano" in flags:
        cautions.append((
            "Alcune fonti non lo nominano in modo identificante: potrebbero riferirsi a un'altra persona",
            "Some sources do not name him identifiably: they may refer to a different person"))
    if "club_non_scritto_da_nessuna_fonte" in flags:
        cautions.append((
            "Il club non compare in nessuna fonte: da riverificare prima di usarlo",
            "The club appears in no source: re-verify before relying on it"))
    if n_src < 2:
        cautions.append(("Una sola fonte: non ancora corroborato",
                          "Single source: not yet corroborated"))
    # Una storia di convocazioni che si ferma è essa stessa un'informazione, e
    # per chi deve telefonare è quella che cambia la telefonata: un ragazzo
    # scelto l'ultima volta a febbraio 2025 può essersi infortunato, aver
    # cambiato paese, o essere semplicemente uscito dal giro. Il sistema non
    # sa quale delle tre — e lo dice invece di far finta che la convocazione
    # sia di ieri.
    mesi = _mesi_da(sel.get("al", "")) if sel else None
    if sel.get("quante") and mesi is not None and mesi >= 12:
        cautions.append((
            f"Ultima convocazione {mesi} mesi fa ({_mese_it(sel['al'])}): "
            f"la serie si è interrotta, da capire perché",
            f"Last call-up {mesi} months ago ({_data_en(sel['al'])}): "
            f"the run has stopped — worth finding out why"))
    if not has_stats:
        cautions.append(("Nessuna statistica di rendimento documentata",
                          "No documented performance stats"))
    if "eta_mancante" in flags or age is None:
        cautions.append(("Età non confermata", "Age not confirmed"))
    if "club_mancante" in flags:
        cautions.append(("Club non identificato", "Club not identified"))
    if "nome_singolo" in flags or "handle_o_soprannome" in flags:
        cautions.append(("Identità debole: nome incompleto o soprannome",
                          "Weak identity: incomplete name or nickname"))
    if asym < 0:
        cautions.append(("Club/lega ad alta visibilità: poca asimmetria, concorrenza probabile",
                          "High-visibility club/league: low asymmetry, competition likely"))

    # --- Prossimi passi (azioni da scout, in ordine di blocco) ---
    if "copertura_bassa_sperimentale" in flags or "senza_fonte_primary" in flags:
        steps.append((
            "Cercare una fonte di stampa/federazione primaria per portarlo allo standard pieno",
            "Look for a primary press/federation source to bring it to the full standard"))
    if "nome_singolo" in flags or "handle_o_soprannome" in flags:
        steps.append(("Risolvere l'identità: serve un nome completo verificabile",
                       "Resolve the identity: needs a verifiable full name"))
    if "club_mancante" in flags:
        steps.append(("Verificare il club attuale", "Verify the current club"))
    if "eta_mancante" in flags or age is None:
        steps.append(("Confermare l'anno di nascita con la società",
                       "Confirm the birth year with the club"))
    if n_src < 2:
        steps.append(("Trovare una seconda fonte indipendente (aggregatori, stampa locale)",
                       "Find a second independent source (aggregators, local press)"))
    if not has_stats:
        steps.append(("Recuperare statistiche di rendimento (referti gara)",
                       "Get performance stats (match reports)"))
    if p.get("publishable"):
        steps.append(("Richiedere video o programmare una visione diretta",
                       "Request video or schedule a live viewing"))
        steps.append(("Telefonata al club: conferma anagrafica e status contrattuale",
                       "Call the club: confirm identity and contract status"))

    def _split(pairs, cap):
        pairs = pairs[:cap]
        return [x[0] for x in pairs], [x[1] for x in pairs]

    pros_it, pros_en = _split(pros, 4)
    cautions_it, cautions_en = _split(cautions, 4)
    steps_it, steps_en = _split(steps, 3)
    return {
        "pros": pros_it, "cautions": cautions_it, "next_steps": steps_it,
        "pros_en": pros_en, "cautions_en": cautions_en, "next_steps_en": steps_en,
    }


def export(db_path: Path, out_path: Path) -> dict:
    # Heal score NULL così priorità tracking e rank non restano a zero
    try:
        from src.database_v2 import OB1DatabaseV2
        OB1DatabaseV2(str(db_path)).heal_scores()
    except Exception as e:
        print(f"Warning: heal_scores failed: {e}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Pre-carica le fonti in una sola query (evita N+1 col crescere del DB)
    sources_by_player = {}
    for r in conn.execute(
            """SELECT player_id, source_domain, source_url,
                      MIN(observed_at) AS observed_at
               FROM evidences WHERE source_domain != ''
               GROUP BY player_id, source_domain ORDER BY observed_at"""):
        sources_by_player.setdefault(r["player_id"], []).append(
            {"domain": r["source_domain"], "url": r["source_url"],
             "seen": r["observed_at"]})

    # Le evidenze INTERE (testo citato + origin), non solo i domini: servono a
    # stabilisci() per dire quale fonte prova quale campo. Una query sola,
    # stesso motivo dell'altra: niente N+1 al crescere del database.
    evidences_by_player = {}
    for r in conn.execute(
            """SELECT player_id, raw_content, source_domain, source_url,
                      observed_at, origin FROM evidences"""):
        evidences_by_player.setdefault(r["player_id"], []).append(dict(r))

    # La scala delle categorie di ogni federazione, ricavata dai suoi stessi
    # comunicati: senza, "ha saltato una categoria" sarebbe un'assunzione
    # (in Colombia Sub-17 e Sub-19 sono attaccate, non c'e' niente in mezzo).
    # Un giro solo sulla colonna, prima del ciclo. Vedi src/anomalie_v2.py.
    _selezioni = []
    for r in conn.execute("SELECT selection_json FROM players "
                          "WHERE selection_json IS NOT NULL "
                          "AND selection_json != ''"):
        try:
            _selezioni.append(json.loads(r["selection_json"]))
        except (ValueError, TypeError):
            continue
    scala_categorie = scala_osservata(_selezioni)

    _registro = registro()

    def _tipi_fonte(pid):
        """I `type` del registro per le fonti che citano questo giocatore.
        Servono all'asimmetria di copertura: federazione sì, stampa no."""
        tipi = set()
        for e in evidences_by_player.get(pid, []):
            d = (e.get("source_domain") or "").lower()
            d = d[4:] if d.startswith("www.") else d
            t = _registro.get(d, {}).get("type")
            if t:
                tipi.add(t)
        return sorted(tipi)

    def _clean_sources(raw):
        """Domain unici; URL http se c'è, altrimenti domain-only (chip senza link)."""
        out, seen = [], set()
        for s in raw or []:
            url = (s.get("url") or "").strip()
            dom = (s.get("domain") or "").strip()
            if not dom:
                continue
            key = dom.lower()
            if key in seen:
                continue
            seen.add(key)
            if not url.startswith(("http://", "https://")):
                url = ""
            out.append({"domain": dom, "url": url, "seen": s.get("seen")})
            if len(out) >= 4:
                break
        return out

    def _tracking_rank(p: dict) -> tuple:
        """Prima identity quasi-gate, poi fonti, poi score. Rumore in coda."""
        flags = p.get("review_flags") or ""
        weak = ("nome_singolo" in flags) or ("handle_o_soprannome" in flags)
        return (
            0 if p.get("identity_complete") else 1,
            0 if (p.get("n_sources") or 0) >= 1 else 1,
            1 if weak else 0,
            -(p.get("score") or 0),
        )

    players = []
    for p in conn.execute("SELECT * FROM players ORDER BY score DESC"):
        pid = p["id"]
        sources = _clean_sources(sources_by_player.get(pid, []))
        n_sources = len(sources)
        stats = json.loads(p["stats_json"]) if p["stats_json"] else {}
        # La persistenza di selezione entra nel merito: senza, l'export
        # ricalcolerebbe un punteggio diverso da quello in database, e la
        # dashboard mostrerebbe una classifica che non è quella su cui il
        # gate ha lavorato. Vedi src/selezione_v2.py.
        selezione = _selezione_di(p)
        sc = score_player(
            age=p["age"], is_ghost=bool(p["is_ghost"]), club=p["club"],
            league=p["league"], stats=stats, n_sources=max(n_sources, 1),
            detection_count=p["evidence_count"] or 1,
            selezione=_come_persistenza(selezione),
        )
        # Nome senza contenuto utile → non in dashboard pubblica
        name = (p["canonical_name"] or "").strip()
        if len(name) < 3:
            continue

        # Provenienza per campo (src/claims_v2.py). L'età esce SOLO se una
        # fonte competente la scrive o se è dichiaratamente dedotta dalla
        # categoria del torneo: un valore che nessuno ha scritto non si
        # mostra come se fosse un fatto. È il difetto che ha prodotto
        # "Yan Diomande, 15 anni" (ne ha 19).
        claims = stabilisci(
            {"canonical_name": name, "club": p["club"], "age": p["age"],
             "stats": stats},
            evidences_by_player.get(pid, []))
        eta_claim = claims.get("eta", {})
        eta_mostrabile = p["age"] if eta_claim.get("stato") in (DICHIARATO, DEDOTTO) else None

        entry = {
            "name": name,
            "age": eta_mostrabile, "age_stato": eta_claim.get("stato"),
            "age_nota": eta_claim.get("nota", ""),
            "claims": {k: {"stato": v.get("stato"),
                           "fonte": (v.get("prova") or {}).get("nome_fonte", ""),
                           "tipo": (v.get("prova") or {}).get("tipo", ""),
                           "citazione": (v.get("prova") or {}).get("citazione", ""),
                           "url": (v.get("prova") or {}).get("url", "")}
                       for k, v in claims.items()},
            "position": p["position"], "club": p["club"],
            "league": p["league"], "region": p["region"],
            "gender": p["gender"],
            "score": sc["score"], "confidence": sc["confidence"],
            "breakdown": sc["breakdown"],
            "n_sources": n_sources, "sources": sources,
            "stats": stats,
            "publishable": bool(p["publishable"]),
            "identity_complete": bool(p["identity_complete"]),
            "review_flags": p["review_flags"] or "",
            # Algoritmo copertura bassa (2026-08-19b, src/database_v2.py):
            # 'low_coverage' = pubblicato senza fonte primary perché il
            # paese è nel perimetro a stampa digitale debole (Africa
            # subsahariana+Nord Africa, Asia Sud/Sudest/Centrale). Passthrough
            # puro: nessuna UI/etichetta dashboard decisa qui, è un cambio
            # di prodotto a parte.
            "coverage_tier": p["coverage_tier"] or "standard",
            "first_detected": p["first_detected"], "last_seen": p["last_seen"],
            # Persistenza di selezione: quante volte una federazione lo ha
            # scelto, quando, in che categoria, con i documenti. È la ragione
            # per cui questo nome è in lista, ed è verificabile aprendo i
            # link — la scheda porta la prova, non l'affermazione.
            "selezione": selezione,
            # Perché QUESTO nome e non un altro: la ragione in una riga, con
            # i documenti sotto. È la merce che un agente può usare — noi
            # diciamo cosa risulta anomalo e lo dimostriamo, l'ultimo passo
            # è suo. Lista vuota = niente che sappiamo dimostrare, che non
            # è la stessa cosa di "giocatore normale". Vedi src/anomalie_v2.py.
            #
            # L'età passata è solo quella DICHIARATA — non `eta_mostrabile`,
            # che include anche DEDOTTO. Un'età dedotta dalla categoria del
            # torneo, data in pasto a "è giovane per la categoria", chiude un
            # cerchio su se stessa e risponde sempre di sì: al primo giro
            # l'unico anticipo esportato era esattamente quello.
            "anomalie": anomalie_dict(leggi_anomalie(
                selezione,
                p["age"] if eta_claim.get("stato") == DICHIARATO else None,
                _tipi_fonte(pid), scala_categorie)),
        }
        entry["assessment"] = assess_player(entry, p["evidence_count"] or 1)
        players.append(entry)
    conn.close()

    players.sort(key=lambda x: (not x["publishable"], -x["score"]))
    pub = [x for x in players if x["publishable"]]
    trk = sorted([x for x in players if not x["publishable"]], key=_tracking_rank)
    # Prima un tetto a 15 nascondeva 176 dei 191 in tracking senza modo di
    # cercarli — un partner che chiedeva "avete copertura in Africa?" non
    # trovava i 16 nomi che c'erano davvero, ne usciva 1 solo (misurato
    # 2026-08-19: 17 giocatori in region africane, 1 solo publishable).
    # La dashboard ora ha un filtro per regione (docs/index.html), quindi il
    # tetto non serve più a "stare leggeri": tutti sono esportati, filtrabili.
    shown = pub + trk
    try:
        from src.database_v2 import OB1DatabaseV2
        outcomes = OB1DatabaseV2(str(db_path)).outcomes_summary()
    except Exception as e:
        print(f"Warning: outcomes_summary failed: {e}")
        outcomes = {"checked": 0, "avg_lead_time_days": None}

    version, build = _version_and_build()
    doc = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": version,
        "build": build,
        "total": len(players),
        "publishable": len(pub),
        "tracking": len(trk),
        "shown": len(shown),
        "tracking_capped": 0,  # storico: campo tenuto per compatibilità col client, non tronca più nulla
        "outcomes": outcomes,
        "players": shown,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "ob1_v2.db"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "data" / "players_v2.json"))
    args = ap.parse_args()
    if not Path(args.db).exists():
        print(f"DB v2 non trovato: {args.db} — esporto struttura vuota.")
        version, build = _version_and_build()
        doc = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "version": version, "build": build,
               "total": 0, "publishable": 0, "tracking": 0, "players": []}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(doc), encoding="utf-8")
        return
    doc = export(Path(args.db), Path(args.out))
    print(f"Export: {doc['total']} giocatori ({doc['publishable']} pubblicabili) → {args.out}")


def _selftest_assess_player():
    """
    "Ogni lettore deve sapere che il profilo ha dei pro e dei contro" — non
    solo un principio, verificato: un profilo pubblicato sotto la deroga
    copertura-bassa DEVE portare la cautela che lo dice, in entrambe le
    lingue, prima ancora di parlare del resto. Puro/testabile senza DB.
    """
    p_low = {"age": 17, "stats": {}, "breakdown": {}, "n_sources": 2,
              "publishable": True, "review_flags": "copertura_bassa_sperimentale"}
    a_low = assess_player(p_low, evidence_count=2)
    assert any("bassa copertura" in c for c in a_low["cautions"]), a_low
    assert any("Low media-coverage" in c for c in a_low["cautions_en"]), a_low
    assert any("fonte di stampa" in s for s in a_low["next_steps"]), a_low

    p_noprimary = {"age": 18, "stats": {}, "breakdown": {}, "n_sources": 2,
                    "publishable": False, "review_flags": "senza_fonte_primary"}
    a_noprimary = assess_player(p_noprimary, evidence_count=2)
    assert any("nessuna di stampa primaria" in c for c in a_noprimary["cautions"]), a_noprimary

    p_standard = {"age": 17, "stats": {"goals": 5}, "breakdown": {}, "n_sources": 3,
                   "publishable": True, "review_flags": ""}
    a_standard = assess_player(p_standard, evidence_count=3)
    assert not any("copertura" in c.lower() for c in a_standard["cautions"]), \
        "un profilo standard non deve portare una cautela di copertura inesistente"

    # I rilievi dell'avvocato del diavolo devono VEDERSI sulla scheda: un
    # profilo che esce con una debolezza nota la dichiara, altrimenti siamo
    # tornati a "VERIFICATO" su una prova che non regge.
    p_sfida = {"age": 17, "stats": {}, "breakdown": {}, "n_sources": 2,
               "publishable": True,
               "review_flags": "una_sola_fonte_sostanziale,eta_dedotta_dalla_categoria"}
    a_sfida = assess_player(p_sfida, evidence_count=2)
    assert any("dice qualcosa di concreto" in c for c in a_sfida["cautions"]), a_sfida
    assert any("dedotta dalla categoria" in c for c in a_sfida["cautions"]), a_sfida
    # ...e in quel caso NON si vanta di "2 fonti indipendenti".
    assert not any("fonti indipendenti" in p for p in a_sfida["pros"]), a_sfida["pros"]

    print("OK assess_player: cautela copertura-bassa e rilievi dell'avvocato "
          "del diavolo presenti quando servono, assenti quando no")


if __name__ == "__main__":
    _selftest_assess_player()
    main()
