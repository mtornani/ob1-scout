#!/usr/bin/env python3
"""
Restituisce un profilo proprio ai giocatori finiti dentro il record di un altro.

Il match per sottostringa (src/database_v2._names_match, corretto il 1 set
2026) faceva combaciare un nome di un solo token con chiunque lo portasse
dentro: il record "Felipe" ha assorbito quattro colombiani diversi piu' un
cileno, il record "Eduardo" tre colombiani piu' un brasiliano. Il fix
impedisce che succeda ancora, ma non tocca cio' che era gia' scritto.

Questo script sposta ogni prova al giocatore che la prova stessa nomina.
Le convocazioni della FCF hanno un formato fisso — "Nome Completo – Club" —
quindi il nome vero e' scritto li' dentro e non va indovinato.

Tre forme diverse, tre rimedi diversi
------------------------------------
1. PROVE DI ALTRI, da spostare (record "Felipe" e "Eduardo"). Ogni prova
   nomina per intero il giocatore a cui appartiene: si sposta li'.

2. OMONIMI DENTRO UNA PROVA SOLA (record "Saviolo", "Josmar", "Paulinho").
   Sono schede della v1 in cui un blocco "Stats:" incolla le statistiche di
   piu' persone con lo stesso nome — tre Paulinho diversi, uno dei quali
   gioca in Danish Superliga. Non c'e' niente da riassegnare: non sono due
   prove da separare, e' una prova sola che mescola gente. Si toglie il
   blocco, che non e' una prova su questo giocatore, e resta la valutazione
   originale, che parla di uno solo.

3. NIENTE DA FARE ("Mora"). La sua unica prova descrive un giocatore solo
   (record della Liga MX, Gold Cup 2025): e' il nome a essere troncato, non
   le prove a essere sbagliate. Un record a nome mozzo non pubblica — il
   gate lo marca `nome_singolo` — e inventargli un cognome sarebbe peggio.

Il conteggio delle rilevazioni si ricalcola SOLO dove e' uscita davvero una
persona (1). Dove si e' tolto un blocco di stats altrui (2) il record non ha
perso nessuno, e azzerarlo butterebbe via informazione vera.

Le date restano quelle originali: `observed_at` viene ricopiato dalla prova
di partenza. Spostare una prova non cambia QUANDO l'abbiamo vista, e
metterci oggi falserebbe first_detected e l'arco delle convocazioni.

    python scripts/spacchetta_record_fusi.py --prova   # mostra e basta
    python scripts/spacchetta_record_fusi.py           # scrive
"""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database_v2 import OB1DatabaseV2

# I record da svuotare, con il dominio delle prove che non gli appartengono.
# Elencati a mano e non cercati: sono sei, li ho letti tutti, e una regola
# automatica su dati di produzione sbaglierebbe piu' di quanto aggiusti.
DA_SPACCHETTARE = {
    7: "fcf.com.co",    # "Felipe" — il cileno del Colo Colo resta, i colombiani no
    47: "fcf.com.co",   # "Eduardo" — il brasiliano del Palmeiras resta
}

# "SANCIONADO LUIS FELIPE MARQUINEZ VALVERDE": la prima parola e' il verdetto
# del comunicato, non parte del nome.
_PREFISSI = ("SANCIONADO", "SUSPENDIDO", "AMONESTADO")

# Prove in prosa, dove il nome c'e' ma non in un formato regolare:
#   "escalou Felipe Morais em vitoria do Cruzeiro sobre Coritiba"
# Una mappa esplicita invece di un parser per il portoghese giornalistico:
# sono una riga, e leggerla e' piu' affidabile che indovinarla.
A_MANO = {
    477: ("Felipe Morais", "Cruzeiro", "Brazil"),
}

# Prove che mescolano omonimi DENTRO se stesse: schede della v1 in cui un
# blocco "Stats:" incolla i dati di piu' persone con lo stesso nome. Qui non
# c'e' niente da riassegnare — si toglie il blocco, che non e' una prova su
# questo giocatore, e resta la valutazione originale, che parla di uno solo.
#
#   3  "Saviolo"  -> stats di Noah Saviolo E Luigi Saviolo
#   21 "Josmar"   -> Josmar Galea E Josmar Palacios
#   41 "Paulinho" -> tre Paulinho diversi, uno in Danish Superliga
DA_SPOGLIARE = (3, 21, 41)


def nome_e_club(testo: str):
    """Da 'Nome Completo – Club' al nome e al club. Club None se non c'e'."""
    t = (testo or "").strip()
    for p in _PREFISSI:
        if t.upper().startswith(p):
            t = t[len(p):].strip()
    pezzi = re.split(r"\s+[–|]\s+|\s+\|\s+", t, maxsplit=1)
    nome = pezzi[0].strip(" -–—|")
    club = pezzi[1].strip() if len(pezzi) > 1 else None
    if nome.isupper():          # i comunicati di sanzione urlano
        nome = nome.title()
    return nome, (club or None)


def _leggi(db, pid: int, dominio: str):
    """Le prove da spostare, lette e la connessione chiusa subito.

    ingest_observation() apre una connessione sua: tenerne aperta un'altra
    con una transazione in corso da' "database is locked" a meta' lavoro, e
    lascia una prova ingerita nel record nuovo e ancora presente in quello
    vecchio. Qui si legge tutto prima, si chiude, e si scrive dopo.
    """
    c = sqlite3.connect(db.db_path)
    c.row_factory = sqlite3.Row
    try:
        p = c.execute("SELECT canonical_name FROM players WHERE id=?", (pid,)).fetchone()
        righe = [dict(r) for r in c.execute(
            "SELECT id, raw_content, source_url, observed_at "
            "FROM evidences WHERE player_id=? AND source_domain=? ORDER BY id",
            (pid, dominio))]
    finally:
        c.close()
    return (p["canonical_name"] if p else str(pid)), righe


def _scrivi(db, sql: str, args=()):
    c = sqlite3.connect(db.db_path)
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def main() -> int:
    prova = "--prova" in sys.argv
    db = OB1DatabaseV2()

    spostate, toccati, svuotati = 0, set(), set()
    for pid, dominio in DA_SPACCHETTARE.items():
        nome_vecchio, righe = _leggi(db, pid, dominio)
        print(f"\n=== {nome_vecchio!r} (id {pid})")
        for e in righe:
            nome, club = nome_e_club(e["raw_content"])
            if len(nome.split()) < 2:
                print(f"  [{e['id']}] nome non affidabile ({nome!r}): lasciata dov'e'")
                continue
            print(f"  [{e['id']}] -> {nome!r}" + (f"  ({club})" if club else ""))
            spostate += 1
            if prova:
                continue
            # L'eta' NON si inventa: la FCF non la scrive nel comunicato.
            db.ingest_observation({
                "name": nome, "age": None, "club": club,
                "nationality": "Colombia", "position": None, "league": None,
                "gender": "unknown", "stats": {},
                "evidence_quote": e["raw_content"],
                "source_url": e["source_url"],
                "region": "Colombia",
                "observed_at": e["observed_at"],
            })
            _scrivi(db, "DELETE FROM evidences WHERE id=?", (e["id"],))
            _scrivi(db, "DELETE FROM observations WHERE player_id=? AND source_url=?",
                    (pid, e["source_url"]))
            toccati.add(pid)
            svuotati.add(pid)

    # Prove in prosa, indicate una per una.
    for eid, (nome, club, regione) in A_MANO.items():
        c = sqlite3.connect(db.db_path)
        c.row_factory = sqlite3.Row
        e = c.execute("SELECT player_id, raw_content, source_url, observed_at "
                      "FROM evidences WHERE id=?", (eid,)).fetchone()
        c.close()
        if not e:
            continue
        print(f"\n=== a mano: prova [{eid}] -> {nome!r} ({club})")
        spostate += 1
        if prova:
            continue
        db.ingest_observation({
            "name": nome, "age": None, "club": club, "nationality": None,
            "position": None, "league": None, "gender": "unknown", "stats": {},
            "evidence_quote": e["raw_content"], "source_url": e["source_url"],
            "region": regione, "observed_at": e["observed_at"],
        })
        _scrivi(db, "DELETE FROM evidences WHERE id=?", (eid,))
        _scrivi(db, "DELETE FROM observations WHERE player_id=? AND source_url=?",
                (e["player_id"], e["source_url"]))
        toccati.add(e["player_id"])
        svuotati.add(e["player_id"])

    # Blocchi "Stats:" che mescolano omonimi dentro una prova sola.
    for pid in DA_SPOGLIARE:
        c = sqlite3.connect(db.db_path)
        c.row_factory = sqlite3.Row
        righe = [dict(r) for r in c.execute(
            "SELECT id, raw_content FROM evidences WHERE player_id=?", (pid,))]
        c.close()
        for e in righe:
            testo = e["raw_content"] or ""
            i = testo.find("\n\nStats:")
            if i < 0:
                continue
            print(f"\n=== spogliata: prova [{e['id']}] del record {pid} "
                  f"({len(testo) - i} caratteri di stats di omonimi tolti)")
            spostate += 1
            if prova:
                continue
            _scrivi(db, "UPDATE evidences SET raw_content=? WHERE id=?",
                    (testo[:i].strip(), e["id"]))
            toccati.add(pid)

    # Solo i record da cui e' USCITA una persona: dove ho tolto un blocco di
    # stats altrui il conteggio delle rilevazioni resta valido com'era, e
    # azzerarlo perderebbe informazione vera.
    for pid in sorted(svuotati):
        # detection_count contava anche le ri-osservazioni delle persone appena
        # uscite: 26 su un record che ne conteneva cinque. Non so dividerle, so
        # contare le prove rimaste — contare e' piu' onesto che tenere un numero
        # gonfiato da qualcun altro.
        c = sqlite3.connect(db.db_path)
        try:
            n = c.execute("SELECT COUNT(*) FROM evidences WHERE player_id=?",
                          (pid,)).fetchone()[0]
            c.execute("UPDATE players SET detection_count=? WHERE id=?", (max(n, 1), pid))
            c.commit()
        finally:
            c.close()
        with db._conn() as conn:
            db._recompute(conn, pid)
            conn.commit()

    for pid in sorted(toccati - svuotati):
        with db._conn() as conn:
            db._recompute(conn, pid)
            conn.commit()

    print(f"\n{spostate} prove spostate" + ("  (--prova: niente scritto)" if prova else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
