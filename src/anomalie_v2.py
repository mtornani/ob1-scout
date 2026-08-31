#!/usr/bin/env python3
"""
OB1 v2 — Anomalie: perché QUESTO nome, detto in una riga che regge una
telefonata.

Il problema commerciale
-----------------------
Un agente non compra una classifica. Compra una frase che può ripetere al
telefono senza fare brutta figura: "il tuo sistema dice che questo ragazzo è
strano, per quale motivo?". Se la risposta è "ha punteggio 71" la telefonata
finisce lì. Se la risposta è "a settembre 2024 la federazione lo ha convocato
in Sub-17 quando aveva 14 o 15 anni, ecco il comunicato", la telefonata
continua — e l'ultimo passo, quello che vale, lo fa lui.

Questo file calcola solo il secondo tipo di frase. Zero rete, zero modello,
zero scritture: legge il blocco `selection_json` che la pipeline ha gia' in
database (src/selezione_v2.py) e i tipi delle fonti dal registro.

Cosa abbiamo misurato prima di scrivere questo file
---------------------------------------------------
Erano cinque i segnali candidati. Misurati sui 308 giocatori in
data/ob1_v2.db il 31 ago 2026, TRE sono risultati artefatti di raccolta, non
proprieta' del giocatore. Sono documentati qui perche' il prossimo che li
riproporra' (probabilmente io) trovi gia' il conto fatto:

  · PROGRESSIONE ("e' salito di categoria") — 12 giocatori la mostrano, ma
    10 su 12 sono Sub-16 -> Sub-17 in 10-17 mesi. Non e' salire: e' compiere
    un anno. Dire "e' progredito" di un ragazzo che ha semplicemente
    festeggiato il compleanno e' esattamente il tipo di frase che fa
    chiudere la telefonata. (Nota a parte, fuori dallo scopo di questo file:
    `selezione_v2.punti()` regala +5 di merito a tutti e 12. Dieci di quei
    dodici bonus premiano un compleanno. Va guardato, con i dati, a parte.)

  · DENSITA' (convocazioni all'anno) — misurabile solo su 31 giocatori su
    142, e gli archi temporali si ammucchiano su due valori soli (5 mesi e
    17 mesi): non e' la carriera del ragazzo che ha quella forma, sono i due
    cicli di convocazioni che abbiamo scaricato. La densita' misura noi.

  · SERIE INTERROTTA (>=3 convocazioni, poi >=12 mesi di silenzio) — tre
    giocatori, e tutti e tre con la stessa ultima data, 2025-02-06. Non sono
    tre segnali: e' una rosa sola che non abbiamo piu' seguito. Con un corpus
    piu' profondo tornera' dicibile; oggi direbbe una bugia.

Restano tre. Sono qui sotto, e ognuno porta i documenti con se'.

La scala delle categorie non e' inventata
-----------------------------------------
Sub-17 -> Sub-19 sembra un salto di due, ma nella scala che usa davvero la
Federacion Colombiana (osservata nei suoi stessi comunicati: Sub-15, Sub-16,
Sub-17, Sub-19, Sub-20) sono due gradini attaccati. Una scala universale
inventata a tavolino avrebbe segnalato come anomalo mezzo database.
Quindi la scala si costruisce dai dati, per federazione: `scala_osservata()`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence

# Fonti che raccontano: un giornale, un sito di scouting, una tifoseria che
# scrive. NON ci sono dentro gli aggregatori (Transfermarkt, promiedos): una
# scheda in un database non e' qualcuno che ha deciso di scrivere di lui, ed
# e' la differenza su cui poggia tutta l'anomalia di copertura.
TIPI_STAMPA = frozenset({"national_press", "local_press", "niche_scouting",
                         "fan_site"})

# Soglie. Non scelte a intuito: vedi il conto nel docstring e
# scripts/valida_anomalie.py, che le rimisura sui dati veri.
#
# ANTICIPO_MINIMO e' partito a 2 (cioe' "almeno un anno sotto la categoria") e
# lo script di validazione l'ha bocciato subito: 21 segnalati, ma da 4 soli
# documenti — e 15 dei 21 erano "un anno sotto". Non e' un'anomalia, e' come
# funzionano le convocazioni: una Sub-17 convocata a settembre 2024 gioca il
# torneo nel 2025, quindi al momento della chiamata quei ragazzi hanno 16 anni
# per costruzione. Segnalare la normalita' avrebbe riempito la lista di un
# agente di niente. A 3 restano 4 casi, quelli davvero due anni sotto.
ANTICIPO_MINIMO = 3        # scarto grezzo; sono >= 2 anni sotto, garantiti
ASIMMETRIA_MINIMO = 2      # convocazioni federali sotto zero righe di stampa

_NUM = re.compile(r"(\d+)")


@dataclass
class Anomalia:
    """Una ragione, la frase che la dice, e i documenti che la reggono.

    Le due lingue si costruiscono dai campi, non si traducono a pezzi: e' la
    stessa scelta di `selezione_v2` / `_selection_en` nell'export, e serve a
    non far divergere le due versioni la prima volta che una soglia cambia.
    """
    codice: str
    titolo: str
    frase: str
    forza: str                       # "forte" | "indicativa"
    frase_en: str = ""
    prove: List[str] = field(default_factory=list)
    dati: Dict = field(default_factory=dict)


def _num(categoria: str) -> Optional[int]:
    if categoria == "maggiore":
        return 99
    m = _NUM.search(categoria or "")
    return int(m.group(1)) if m else None


def scala_osservata(selezioni: Iterable[dict]) -> Dict[str, List[int]]:
    """
    La scala delle categorie giovanili di ogni federazione, ricavata dai suoi
    comunicati invece che da un'assunzione.

    `selezioni` sono i blocchi `selection_json` gia' in database. Ritorna
    federazione -> lista ordinata dei numeri di categoria che quella
    federazione usa davvero (es. Colombia: [15, 16, 17, 19, 20]).

    Serve a rispondere alla sola domanda che conta per il salto: fra queste
    due convocazioni, quali categorie ESISTONO e non sono state fatte?
    """
    scala: Dict[str, set] = {}
    for sel in selezioni:
        for ev in (sel or {}).get("eventi") or []:
            n = _num(ev.get("categoria") or "")
            if n and n != 99:
                scala.setdefault(ev.get("fonte") or "?", set()).add(n)
    return {f: sorted(v) for f, v in scala.items()}


def _eta_all_evento(eta_oggi: int, anno_evento: int, anno_corrente: int) -> int:
    """
    Quanti anni aveva al momento della convocazione.

    L'eta' in database e' quella di OGGI (viene dalla scheda Transfermarkt,
    aggiornata). L'anno di nascita si ricava per sottrazione, ma non sappiamo
    il mese: l'anno di nascita e' quindi uno di due, e l'eta' al momento della
    convocazione e' incerta di un anno in su e uno in giu'.

    Questa funzione ritorna il valore centrale. Chi scrive la frase usa il
    BORDO ALTO (`+1`): dire "aveva al massimo 15 anni" e' vero comunque sia
    caduto il compleanno, mentre dire "ne aveva 14" e' vero solo se abbiamo
    indovinato. Un'anomalia che si regge su un compleanno non regge una
    telefonata.
    """
    return anno_evento - (anno_corrente - eta_oggi)


def _anticipo(sel: dict, eta_oggi: Optional[int], anno_corrente: int):
    """L'evento in cui era piu' giovane rispetto alla categoria. None se non
    calcolabile: senza eta' dichiarata, o senza data, non si finge."""
    if not eta_oggi:
        return None
    migliore = None
    for ev in sel.get("eventi") or []:
        n = _num(ev.get("categoria") or "")
        data = ev.get("data") or ""
        if not n or n == 99 or len(data) < 4:
            continue
        eta_ev = _eta_all_evento(eta_oggi, int(data[:4]), anno_corrente)
        if eta_ev < 0:
            continue                       # dati incoerenti: si tace
        scarto = n - eta_ev
        if migliore is None or scarto > migliore[0]:
            migliore = (scarto, ev, eta_ev)
    return migliore


def _salto(sel: dict, scala: Dict[str, List[int]]):
    """Due convocazioni consecutive con almeno una categoria REALE saltata in
    mezzo. Ritorna (gradini_saltati, evento_prima, evento_dopo, saltate)."""
    eventi = [e for e in (sel.get("eventi") or [])
              if e.get("data") and _num(e.get("categoria") or "")
              and _num(e["categoria"]) != 99]
    eventi.sort(key=lambda e: e["data"])
    peggio = None
    for prima, dopo in zip(eventi, eventi[1:]):
        a, b = _num(prima["categoria"]), _num(dopo["categoria"])
        if b <= a:
            continue
        gradini = scala.get(dopo.get("fonte") or "?") or []
        saltate = [c for c in gradini if a < c < b]
        if saltate and (peggio is None or len(saltate) > len(peggio[0])):
            peggio = (saltate, prima, dopo)
    if not peggio:
        return None
    saltate, prima, dopo = peggio
    return len(saltate), prima, dopo, saltate


def _cat(categoria: str, en: bool = False) -> str:
    return (categoria or "").replace("sub-", "U" if en else "Sub-").replace(
        "maggiore", "the senior side" if en else "Nazionale A")


_MESI_IT = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
            "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre")
_MESI_EN = ("January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December")


def _mese_anno(iso: str, en: bool = False) -> str:
    try:
        mesi = _MESI_EN if en else _MESI_IT
        return f"{mesi[int(iso[5:7]) - 1]} {iso[:4]}"
    except (ValueError, IndexError):
        return iso[:7]


def leggi(selezione: dict,
          eta: Optional[int],
          tipi_fonte: Sequence[str],
          scala: Optional[Dict[str, List[int]]] = None,
          oggi: Optional[date] = None) -> List[Anomalia]:
    """
    Le anomalie di un giocatore, forti per prime.

    `selezione`  il blocco `selection_json` (src/selezione_v2.py)
    `eta`        l'eta' oggi, SOLO se una fonte la SCRIVE (claims_v2.DICHIARATO).
                 None altrimenti — e in particolare None se l'eta' e' DEDOTTA
                 dalla categoria del torneo, perche' allora
                 `anticipo_categoria` misurerebbe la categoria contro se
                 stessa e direbbe sempre di sì. Il primo giro di export lo ha
                 fatto davvero: l'unico anticipo pubblicato era di un
                 giocatore la cui eta' veniva dalla Sub-19 in cui era stato
                 convocato.
    `tipi_fonte` i `type` del registro per le fonti che lo citano
    `scala`      federazione -> categorie osservate (scala_osservata())
    `oggi`       iniettabile per i test

    Lista vuota non vuol dire "giocatore normale": vuol dire "niente che
    questo sistema sappia dimostrare". La differenza va tenuta, ed e' il
    motivo per cui nessuna anomalia dice mai "talento" o "predestinato".
    """
    sel = selezione or {}
    if not sel.get("quante"):
        return []
    anno = (oggi or date.today()).year
    scala = scala if scala is not None else {}
    fuori: List[Anomalia] = []

    # 1. Convocato in una categoria molto sopra la sua eta'.
    ant = _anticipo(sel, eta, anno)
    if ant and ant[0] >= ANTICIPO_MINIMO:
        scarto, ev, eta_ev = ant
        cat = _cat(ev["categoria"])
        # Il bordo alto dell'incertezza, non il centro: cosi' la frase resta
        # vera qualunque sia il mese di nascita. Vedi _eta_all_evento.
        eta_massima = eta_ev + 1
        anni_sotto = scarto - 1
        fuori.append(Anomalia(
            codice="anticipo_categoria",
            titolo="Convocato sopra la sua età",
            frase=(f"Convocato in {cat} nel {_mese_anno(ev['data'])}, quando "
                   f"aveva al massimo {eta_massima} anni: almeno {anni_sotto} "
                   f"{'anno' if anni_sotto == 1 else 'anni'} sotto la "
                   f"categoria. Fonte: {ev.get('fonte') or 'federazione'}."),
            frase_en=(f"Called up to {_cat(ev['categoria'], en=True)} in "
                      f"{_mese_anno(ev['data'], en=True)}, aged at most "
                      f"{eta_massima}: at least {anni_sotto} "
                      f"{'year' if anni_sotto == 1 else 'years'} below the "
                      f"age group. Source: "
                      f"{ev.get('fonte') or 'national federation'}."),
            forza="forte" if anni_sotto >= 2 else "indicativa",
            prove=[ev.get("url", "")],
            dati={"anni_sotto_categoria": anni_sotto,
                  "eta_massima_all_evento": eta_massima,
                  "categoria": ev["categoria"], "data": ev["data"]},
        ))

    # 2. Ha saltato una categoria che quella federazione usa davvero.
    salto = _salto(sel, scala)
    if salto:
        n_saltate, prima, dopo, saltate = salto
        elenco = ", ".join(_cat(f"sub-{c}") for c in saltate)
        fuori.append(Anomalia(
            codice="salto_categoria",
            titolo="Ha saltato una categoria",
            frase=(f"Da {_cat(prima['categoria'])} ({_mese_anno(prima['data'])}) "
                   f"a {_cat(dopo['categoria'])} ({_mese_anno(dopo['data'])}) "
                   f"senza passare da {elenco}, che la stessa federazione "
                   f"convoca."),
            frase_en=(f"From {_cat(prima['categoria'], en=True)} "
                      f"({_mese_anno(prima['data'], en=True)}) to "
                      f"{_cat(dopo['categoria'], en=True)} "
                      f"({_mese_anno(dopo['data'], en=True)}) without passing "
                      f"through "
                      f"{', '.join('U%d' % c for c in saltate)}, which the "
                      f"same federation does call up."),
            forza="forte",
            prove=[prima.get("url", ""), dopo.get("url", "")],
            dati={"saltate": saltate, "da": prima["categoria"],
                  "a": dopo["categoria"]},
        ))

    # 3. La federazione lo sceglie, la stampa non lo scrive.
    #    E' l'anomalia commercialmente piu' interessante e insieme quella da
    #    dire con piu' prudenza: "nessuna stampa" qui significa nessuna delle
    #    fonti di stampa DEL NOSTRO registro, non nessun giornale al mondo.
    #    La frase lo dice, perche' un agente che scopre da solo che il limite
    #    era nostro non ci richiama.
    if sel["quante"] >= ASIMMETRIA_MINIMO and not (set(tipi_fonte) & TIPI_STAMPA):
        chi = ((sel.get("eventi") or [{}])[0].get("fonte")
               or "la federazione")
        fuori.append(Anomalia(
            codice="asimmetria_copertura",
            titolo="Scelto dalla federazione, assente dalla stampa",
            frase=(f"{sel['quante']} convocazioni di {chi}, e nessuna delle "
                   f"fonti di stampa che seguiamo lo ha mai scritto. "
                   f"Chi decide lo conosce, chi racconta no."),
            frase_en=(f"{sel['quante']} call-ups by {chi}, and none of the "
                      f"press outlets we track has ever written about him. "
                      f"The people who pick him know him; the people who "
                      f"report don't."),
            forza="indicativa",
            prove=[e.get("url", "") for e in (sel.get("eventi") or [])][:4],
            dati={"convocazioni": sel["quante"],
                  "tipi_fonte": sorted(set(tipi_fonte))},
        ))

    fuori.sort(key=lambda a: 0 if a.forza == "forte" else 1)
    return fuori


def come_dict(anomalie: Sequence[Anomalia]) -> List[dict]:
    """Forma serializzabile per players_v2.json."""
    return [{"codice": a.codice, "titolo": a.titolo, "frase": a.frase,
             "frase_en": a.frase_en, "forza": a.forza,
             "prove": [p for p in a.prove if p],
             "dati": a.dati} for a in anomalie]


# --------------------------------------------------------------- test

def _test() -> None:
    FCF = "Federación Colombiana de Fútbol"
    OGGI = date(2026, 8, 31)

    def ev(data, cat, fonte=FCF):
        return {"data": data, "categoria": cat, "fonte": fonte,
                "url": f"https://fcf.com.co/{data.replace('-', '/')}/conv-{cat}/"}

    def sel(*eventi):
        return {"quante": len(eventi), "eventi": list(eventi)}

    # La scala si ricava dai comunicati, non da un'assunzione.
    corpus = [sel(ev("2025-01-01", "sub-15"), ev("2025-02-01", "sub-16"),
                  ev("2025-03-01", "sub-17"), ev("2025-04-01", "sub-19"),
                  ev("2025-05-01", "sub-20"))]
    scala = scala_osservata(corpus)
    assert scala == {FCF: [15, 16, 17, 19, 20]}, scala

    # 1. Caso reale: Miguel Ángel Agámez Cabarcas, 16 anni oggi, convocato in
    #    Sub-17 a settembre 2024 — cioe' a 14 o 15 anni.
    agamez = sel(ev("2024-09-17", "sub-17"), ev("2025-01-14", "sub-17"),
                 ev("2026-05-14", "sub-17"), ev("2026-07-19", "sub-17"))
    a = leggi(agamez, eta=16, tipi_fonte=["federation", "aggregator"],
              scala=scala, oggi=OGGI)
    codici = [x.codice for x in a]
    assert "anticipo_categoria" in codici, codici
    ant = next(x for x in a if x.codice == "anticipo_categoria")
    assert ant.dati["anni_sotto_categoria"] == 2, ant.dati
    assert ant.dati["eta_massima_all_evento"] == 15, ant.dati
    assert "al massimo 15 anni" in ant.frase, ant.frase
    assert "almeno 2 anni sotto" in ant.frase, ant.frase
    assert ant.forza == "forte"
    # Transfermarkt e' un aggregatore, non stampa: l'asimmetria vale.
    assert "asimmetria_copertura" in codici, codici

    # 2. Sub-17 -> Sub-19 NON e' un salto: in Colombia sono attaccate.
    herazo = sel(ev("2024-09-17", "sub-17"), ev("2026-07-27", "sub-19"))
    assert not [x for x in leggi(herazo, 17, ["federation"], scala, OGGI)
                if x.codice == "salto_categoria"]

    # 3. Sub-16 -> Sub-19 salta la Sub-17, che la stessa federazione convoca.
    felipe = sel(ev("2025-07-31", "sub-16"), ev("2026-07-27", "sub-19"))
    s = next(x for x in leggi(felipe, 16, ["federation"], scala, OGGI)
             if x.codice == "salto_categoria")
    assert s.dati["saltate"] == [17], s.dati
    assert "senza passare da Sub-17" in s.frase, s.frase
    assert len([p for p in s.prove if p]) == 2

    # 4. Una riga di stampa spegne l'asimmetria: e' il punto dell'anomalia.
    assert not [x for x in leggi(agamez, 16, ["federation", "national_press"],
                                 scala, OGGI)
                if x.codice == "asimmetria_copertura"]

    # 5. Una convocazione sola non e' un'asimmetria, e' un documento solo.
    una = sel(ev("2026-07-19", "sub-17"))
    assert not [x for x in leggi(una, 17, ["federation"], scala, OGGI)
                if x.codice == "asimmetria_copertura"]

    # 6. Senza eta' dichiarata non si inventa un anticipo. Il gate dell'eta'
    #    (src/claims_v2.py) esiste proprio per non mostrare numeri che nessuno
    #    ha scritto: qui vale lo stesso.
    assert not [x for x in leggi(agamez, None, ["federation"], scala, OGGI)
                if x.codice == "anticipo_categoria"]

    # 7. Chi non ha convocazioni non ha anomalie di questo tipo. Zero, non
    #    "normale".
    assert leggi({}, 17, ["federation"], scala, OGGI) == []
    assert leggi({"quante": 0}, 17, ["national_press"], scala, OGGI) == []

    # 8. Eta' coerente con la categoria: nessun anticipo.
    normale = sel(ev("2026-07-19", "sub-17"), ev("2026-05-14", "sub-17"))
    assert not [x for x in leggi(normale, 17, ["federation", "local_press"],
                                 scala, OGGI)
                if x.codice == "anticipo_categoria"]

    # 8b. Il caso che ha alzato la soglia: convocato in Sub-17 a settembre
    #     2024 avendone 16, perche' il torneo si gioca nel 2025. E' la
    #     normalita' di come funzionano le convocazioni, e va taciuta —
    #     altrimenti la lista di un agente si riempie di niente. (Misurato:
    #     15 dei 21 segnalati alla soglia precedente erano di questo tipo.)
    tipico = sel(ev("2024-09-17", "sub-17"))
    assert not [x for x in leggi(tipico, 18, ["federation"], scala, OGGI)
                if x.codice == "anticipo_categoria"]

    # 9. Le forti vengono prima: e' l'ordine in cui un agente legge.
    ordinate = leggi(felipe, 15, ["federation"], scala, OGGI)
    assert [x.forza for x in ordinate] == sorted([x.forza for x in ordinate],
                                                 key=lambda f: 0 if f == "forte" else 1)

    # 10. Serializzabile senza perdere le prove, e in due lingue: la scheda
    #     ha un interruttore IT/EN e una meta' vuota si vedrebbe subito.
    d = come_dict(ordinate)
    assert d and all(set(x) == {"codice", "titolo", "frase", "frase_en",
                                "forza", "prove", "dati"} for x in d)
    assert all(x["frase"] and x["frase_en"] for x in d), d
    en = {x["codice"]: x["frase_en"] for x in d}
    assert "without passing through U17" in en["salto_categoria"], en
    assert "at least 3 years below the age group" in en["anticipo_categoria"], en
    solo_asim = come_dict(leggi(agamez, 16, ["federation", "aggregator"],
                                scala, OGGI))
    assert "none of the press outlets we track" in \
        next(x["frase_en"] for x in solo_asim
             if x["codice"] == "asimmetria_copertura")

    print("anomalie_v2: ok")


if __name__ == "__main__":
    _test()
