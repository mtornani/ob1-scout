#!/usr/bin/env python3
"""
OB1 v2 — Grafo delle fonti: cosa sappiamo, da chi, e chi vince quando
litigano.

Da dove viene
-------------
Portato da OuroborosCouncil (`discovery_engine.record_observation` /
`resolve_field`). Là serve a un radar che pesca da Wikidata, dalla stampa e
dalle pagine-rosa dei tornei; qui la forma è la stessa perché il problema è
lo stesso, e tenerla riconoscibile vale più di adattarla.

Il problema che risolve, misurato qui
-------------------------------------
Fino al 31 ago 2026 `database_v2.ingest_observation` scriveva
`age = COALESCE(age, ?)`: la PRIMA età arrivata vinceva per sempre. Jorman
Camilo Mendoza Garrido è rimasto "16 anni" anche dopo che la sua scheda
Transfermarkt, letta per intero, diceva "nato il 14/01/2008" — cioè 18. Su
quel 16 sbagliato il sistema aveva costruito un'anomalia ("convocato in
Sub-17 a 14 anni") che era falsa.

La toppa di quel giorno — "aggiorna se il valore attuale non è ancora
provato" — è questa idea fatta a mano, per un campo solo, senza memoria di
chi aveva detto cosa. Questo file è la forma generale.

Le tre regole
-------------
1. **La conferma umana batte tutto.** Livello 0. Oggi nessun canale la
   produce (non c'è UI di conferma): il livello esiste, ed è vuoto. È
   dichiarato apposta invece che omesso — il giorno che qualcuno guarda una
   scheda e dice "no, il club è un altro", quel giudizio deve avere un
   posto dove atterrare più in alto di qualunque scraper.

2. **Se le fonti concordano, vince il valore condiviso.** Nessuna gerarchia
   da applicare: non c'è lite.

3. **In disaccordo, la piramide NON si legge sempre nello stesso verso.**
   È il punto che rende utile tutto il resto:

       club  -> DAL BASSO   un club cambia. Vince l'osservazione DATATA più
                            fresca: un comunicato di ieri batte una scheda
                            aggregata che non dice a quando si riferisce.
       eta   -> DALL'ALTO   una data di nascita non cambia. Vince la fonte
                            consolidata: Transfermarkt batte il numero
                            pescato da un articolo.

   Applicare un solo verso a entrambi i campi sbaglia sempre uno dei due.

Livelli, per questo prodotto
----------------------------
L'asse NON è "quanto è affidabile" ma "quanto è fresca contro quanto è
consolidata" — è la stessa scelta di OuroborosCouncil (news 2, wikidata 4)
e va letta così, altrimenti sembra che stiamo dicendo che un aggregatore è
più autorevole di una federazione, che non è quello che dice.

Puro: nessuna rete, nessun database. Il grafo è un dict che passa il
chiamante, il salvataggio è affar suo.

Test: python -m src.piramide_v2
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

# Livello 0 = la voce umana. Vuoto oggi, dichiarato apposta (vedi regola 1).
UMANO = "umano"

# Dai `type` di config/sources.json a una posizione sull'asse
# fresco -> consolidato. Chi non è qui dentro non entra nel grafo: mai un
# livello indovinato, che è la stessa regola di "mai un numero inventato".
LIVELLI: Dict[str, int] = {
    UMANO: 0,
    # cronaca: la cosa più fresca che abbiamo
    "national_press": 2,
    "local_press": 2,
    "niche_scouting": 2,
    "fan_site": 2,
    # atti datati: una convocazione è un fatto con una data sopra
    "federation": 3,
    "confederation": 3,
    "academy": 3,
    # anagrafiche consolidate: lente sul presente, solide sull'identità
    "aggregator": 4,
    "results_stats": 4,
    "encyclopedic": 4,
}

# Come si legge la piramide, campo per campo.
REGOLE_CAMPO: Dict[str, str] = {
    "club": "dal_basso",     # fatto veloce
    "eta": "dall_alto",      # fatto lento
    "position": "dal_basso",
    "league": "dal_basso",
}
REGOLA_DI_DEFAULT = "dal_basso"

# Quante osservazioni per campo si tengono. Serve solo a non far crescere il
# grafo senza fine su un giocatore molto citato: le più vecchie della stessa
# fonte non aggiungono niente, perché la risoluzione guarda l'ULTIMA di
# ciascuna fonte.
MAX_OSSERVAZIONI_PER_CAMPO = 24


def _ora() -> str:
    return datetime.now(timezone.utc).isoformat()


def registra(grafo: dict, chiave: str, campo: str, valore, fonte: str,
             datato_al: str = "", url: str = "", nota: str = "") -> bool:
    """
    Aggiunge un'osservazione al grafo (in memoria). True se il grafo cambia.

    `fonte` è un TIPO del registro (federation, aggregator, ...), non un
    dominio: la piramide ragiona per genere di fonte, e due giornali diversi
    stanno allo stesso livello.

    Dedup: se l'ultima osservazione della STESSA fonte porta lo stesso
    valore è una conferma, non una riga nuova — si aggiorna quando l'abbiamo
    vista e basta. Se porta un valore diverso, è una riga nuova: il
    cambiamento è il dato.
    """
    valore = str(valore).strip() if valore is not None else ""
    if not valore or not chiave or fonte not in LIVELLI:
        return False
    osservazioni = grafo.setdefault(chiave, {}).setdefault(campo, [])
    adesso = _ora()
    for obs in reversed(osservazioni):
        if obs["fonte"] != fonte:
            continue
        if obs["valore"] == valore:
            obs["osservato_il"] = adesso
            if datato_al and not obs.get("datato_al"):
                obs["datato_al"] = datato_al
            return True
        break                      # l'ultima della stessa fonte dice altro
    voce = {"valore": valore, "fonte": fonte, "osservato_il": adesso}
    for k, v in (("datato_al", datato_al), ("url", url), ("nota", nota)):
        if v:
            voce[k] = v
    osservazioni.append(voce)
    del osservazioni[:-MAX_OSSERVAZIONI_PER_CAMPO]
    return True


def _ts(obs: dict, chiave: str = "osservato_il") -> str:
    return obs.get(chiave) or ""


def risolvi(grafo: dict, chiave: str, campo: str,
            ripiego=None, oggi: Optional[datetime] = None) -> Optional[dict]:
    """
    Il valore corrente di un campo secondo il grafo, con la spiegazione.
    Deterministico: stesse osservazioni, stessa risposta.

    `ripiego` è il valore che il chiamante ha già fuori dal grafo (la
    colonna in tabella): usato SOLO se il grafo non sa nulla, e dichiarato
    come tale nella spiegazione — così non si confonde "lo dice una fonte"
    con "ce l'avevamo scritto".

    Ritorna None quando non c'è né grafo né ripiego: l'assenza resta
    un'assenza, non diventa un valore.
    """
    osservazioni = (grafo.get(chiave) or {}).get(campo) or []
    if not osservazioni:
        if ripiego in (None, ""):
            return None
        return {"valore": ripiego, "fonte": None, "livello": None,
                "spiegazione": "valore di partenza, nessuna fonte lo osserva",
                "conflitto": False, "alternativa": None,
                "alternativa_fonte": None, "datato_al": "", "url": ""}

    # L'ultima osservazione di ciascuna fonte: le precedenti della stessa
    # fonte sono storia, non voci in più.
    per_fonte = {}
    for obs in osservazioni:
        per_fonte[obs["fonte"]] = obs
    voci = list(per_fonte.values())

    def esito(migliore: dict, spiegazione: str, conflitto: bool) -> dict:
        altre = sorted([o for o in voci if o["valore"] != migliore["valore"]],
                       key=_ts, reverse=True)
        return {
            "valore": migliore["valore"],
            "fonte": migliore["fonte"],
            "livello": LIVELLI.get(migliore["fonte"]),
            "datato_al": migliore.get("datato_al", ""),
            "url": migliore.get("url", ""),
            "spiegazione": spiegazione,
            "conflitto": conflitto,
            "alternativa": altre[0]["valore"] if altre else None,
            "alternativa_fonte": altre[0]["fonte"] if altre else None,
        }

    # 1. la voce umana batte tutto
    umane = [o for o in voci if LIVELLI.get(o["fonte"]) == 0]
    if umane:
        migliore = max(umane, key=_ts)
        discordi = any(o["valore"] != migliore["valore"] for o in voci)
        return esito(migliore,
                     f"confermato a mano il {migliore['osservato_il'][:10]}",
                     discordi)

    # 2. accordo pieno
    if len({o["valore"] for o in voci}) == 1:
        migliore = max(voci, key=_ts)
        spiegazione = (f"{len(voci)} fonti concordano" if len(voci) > 1
                       else f"unica fonte: {migliore['fonte']}")
        return esito(migliore, spiegazione, False)

    # 3. disaccordo: il verso dipende dal campo
    if REGOLE_CAMPO.get(campo, REGOLA_DI_DEFAULT) == "dall_alto":
        migliore = max(voci, key=lambda o: (LIVELLI.get(o["fonte"], -1), _ts(o)))
        return esito(migliore,
                     f"\"{migliore['valore']}\" secondo {migliore['fonte']} — "
                     f"su questo campo la fonte consolidata batte quella fresca",
                     True)

    datate = [o for o in voci if o.get("datato_al")]
    if datate:
        migliore = max(datate, key=lambda o: _ts(o, "datato_al"))
        return esito(migliore,
                     f"\"{migliore['valore']}\" secondo {migliore['fonte']} "
                     f"(datato {migliore['datato_al'][:10]}) — un'osservazione "
                     f"con una data batte un valore senza data",
                     True)
    migliore = min(voci, key=lambda o: (LIVELLI.get(o["fonte"], 9), _inverso(_ts(o))))
    return esito(migliore,
                 f"\"{migliore['valore']}\" secondo {migliore['fonte']} — nessuna "
                 f"fonte porta una data, vince quella più vicina al campo",
                 True)


def _inverso(iso: str) -> float:
    """Chiave d'ordinamento per "il più recente vince" dentro un min():
    timestamp negato. Una data assente o malformata torna 0.0, che è
    maggiore di ogni timestamp negato valido — quindi perde contro
    qualunque osservazione ben datata, e non solleva mai."""
    try:
        return -datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return 0.0


def conflitti(grafo: dict, chiave: str,
              campi: Sequence[str] = ("club", "eta")) -> List[dict]:
    """I campi su cui le fonti non vanno d'accordo, già risolti. Serve a
    misurare quanto litigano prima di lasciar decidere il grafo, e a
    mostrarlo: un conflitto dichiarato vale più di uno risolto in silenzio."""
    fuori = []
    for campo in campi:
        r = risolvi(grafo, chiave, campo)
        if r and r["conflitto"]:
            fuori.append(dict(r, campo=campo))
    return fuori


# --------------------------------------------------------------- test

def _test() -> None:
    G: dict = {}

    # 1. Una fonte sola: si dice, senza gerarchie da applicare.
    assert registra(G, "p1", "club", "Envigado F.C.", "aggregator")
    r = risolvi(G, "p1", "club")
    assert r["valore"] == "Envigado F.C." and not r["conflitto"]
    assert r["spiegazione"] == "unica fonte: aggregator", r["spiegazione"]

    # 2. Stessa fonte, stesso valore: conferma, non riga nuova.
    assert registra(G, "p1", "club", "Envigado F.C.", "aggregator")
    assert len(G["p1"]["club"]) == 1

    # 3. Stessa fonte, valore diverso: riga nuova, il cambiamento è il dato.
    assert registra(G, "p1", "club", "Envigado FC U20", "aggregator")
    assert len(G["p1"]["club"]) == 2

    # 4. CLUB, disaccordo: vince chi porta una data. È il caso vero per cui
    #    la regola esiste — una scheda aggregata non dice a quando si
    #    riferisce, un comunicato sì.
    G2: dict = {}
    registra(G2, "p2", "club", "Vecchio Club", "aggregator")
    registra(G2, "p2", "club", "Nuovo Club", "federation", datato_al="2026-07-19")
    r = risolvi(G2, "p2", "club")
    assert r["valore"] == "Nuovo Club", r
    assert r["conflitto"] and r["alternativa"] == "Vecchio Club"
    assert "datato 2026-07-19" in r["spiegazione"], r["spiegazione"]

    # 5. ETA, stesso disaccordo, verso OPPOSTO: vince il consolidato. È il
    #    caso Mendoza — l'aggregatore dice 18 leggendo la data di nascita,
    #    un articolo diceva 16, e il numero giusto è quello dell'anagrafica.
    G3: dict = {}
    registra(G3, "p3", "eta", "16", "national_press", datato_al="2026-08-30")
    registra(G3, "p3", "eta", "18", "aggregator")
    r = risolvi(G3, "p3", "eta")
    assert r["valore"] == "18", r
    assert "consolidata batte quella fresca" in r["spiegazione"]
    assert r["conflitto"] and r["alternativa"] == "16"
    #    ...e con lo stesso identico grafo, sul CLUB avrebbe vinto l'altro:
    #    è esattamente ciò che una piramide a verso unico sbaglia.

    # 6. La voce umana batte tutto, anche il consolidato.
    registra(G3, "p3", "eta", "17", UMANO)
    r = risolvi(G3, "p3", "eta")
    assert r["valore"] == "17" and "confermato a mano" in r["spiegazione"]
    assert r["conflitto"], "se l'umano smentisce, il conflitto resta detto"

    # 7. Accordo pieno fra fonti diverse: nessun conflitto.
    G4: dict = {}
    registra(G4, "p4", "club", "Atl. Nacional", "federation")
    registra(G4, "p4", "club", "Atl. Nacional", "aggregator")
    r = risolvi(G4, "p4", "club")
    assert not r["conflitto"] and r["spiegazione"] == "2 fonti concordano"

    # 8. Una fonte non censita non entra: mai un livello indovinato.
    assert not registra(G4, "p4", "club", "Chissà", "blog_di_tizio")
    assert len(G4["p4"]["club"]) == 2

    # 9. Grafo vuoto: il ripiego si usa, ma si dichiara che è un ripiego.
    r = risolvi({}, "ignoto", "club", ripiego="Club In Tabella")
    assert r["valore"] == "Club In Tabella" and r["fonte"] is None
    assert "nessuna fonte lo osserva" in r["spiegazione"]
    #    ...e senza ripiego resta un'assenza, non diventa un valore.
    assert risolvi({}, "ignoto", "club") is None

    # 10. Valori vuoti non entrano nel grafo.
    assert not registra(G4, "p4", "club", "", "federation")
    assert not registra(G4, "p4", "club", None, "federation")

    # 11. I conflitti si possono chiedere tutti insieme, per misurarli.
    c = conflitti(G3, "p3", campi=("club", "eta"))
    assert [x["campo"] for x in c] == ["eta"], c

    # 12. Un timestamp malformato non fa saltare la risoluzione.
    G5: dict = {}
    registra(G5, "p5", "club", "A", "national_press")
    registra(G5, "p5", "club", "B", "local_press")
    G5["p5"]["club"][0]["osservato_il"] = "non-una-data"
    assert risolvi(G5, "p5", "club") is not None

    print("piramide_v2: ok")


if __name__ == "__main__":
    _test()
