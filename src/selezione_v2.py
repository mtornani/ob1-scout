#!/usr/bin/env python3
"""
OB1 v2 — Persistenza di selezione: il segnale che Global aveva già in mano.

Il problema
-----------
Global assegna il merito quasi tutto alla produzione (gol, assist, presenze:
40 punti su ~85 possibili). Ma Global non ha statistiche — i suoi giocatori
non stanno su Transfermarkt, non c'è una lega che pubblichi i tabellini delle
giovanili colombiane, e la pagina rendimento di TM ha i numeri dentro le
immagini. Misurato il 26 ago 2026 sugli 86 pubblicabili: `production` vale 0
per tutti tranne una manciata.

Quindi il sistema stava dando il punteggio per uno sport che non riesce a
vedere, e ignorando l'unico che vede benissimo.

Cosa vede benissimo
-------------------
Sugli 86 pubblicabili:

    12 giocatori con 0 convocazioni federali
    43 con 1
    16 con 2
     8 con 3
     4 con 4
     3 con 5

31 su 86 sono stati convocati DUE O PIÙ VOLTE da una federazione nazionale, e
sette di loro quattro o cinque volte. Esempio reale (Edmilson Yosue Herazo
Torres, tutte fcf.com.co):

    2024/09  Sub-17, Conmebol
    2025/01  Sub-17, microciclo
    2025/02  Sub-17, microciclo
    2026/07  Sub-19          <- salito di categoria

Una federazione che sceglie lo stesso ragazzo quattro volte in due anni, e poi
lo porta in una categoria più alta, sta dicendo qualcosa di molto più solido di
"8 gol" detti da un aggregatore. E lo dice PRIMA della stampa: la convocazione
esce mesi prima che qualunque giornale scriva quel nome. È esattamente la
merce di OB1 — un nome verificabile prima che sia ovvio.

Perché il gate lo buttava via
-----------------------------
`corroborated = (fonti distinte) >= 2`, dove le fonti si contano per DOMINIO.
Cinque convocazioni su fcf.com.co sono un dominio: uno. Così il giocatore con
l'evidenza più forte del database era anche il più difficile da pubblicare.

Il conteggio per dominio nasce da una paura giusta — tre blog che ricopiano lo
stesso comunicato non sono tre fonti — ma la applica al caso sbagliato. Due
convocazioni non sono due racconti dello stesso fatto: sono due FATTI, due
decisioni prese in momenti diversi da chi ha l'autorità di prenderle. Ciò che
va deduplicato è l'evento, non il dominio.

Cosa fa questo file
-------------------
Ricostruisce gli eventi di selezione dalle evidenze già in database — nessuna
fonte nuova, nessuna chiamata in più, nessun problema di licenza — e li rende
dicibili in una frase che regge una telefonata:

    "Convocato 4 volte dalla Federación Colombiana de Fútbol tra settembre
     2024 e luglio 2026, dal Sub-17 al Sub-19."

con i quattro link sotto. Chi la legge può aprirli e controllare.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

# Le categorie giovanili scritte in tutti i modi in cui le scrivono le
# federazioni: "sub17", "sub-17", "Sub 17", "U17", "under-17".
_CATEGORIA = re.compile(r"\b(?:sub|u|under)[\s\-_]?(\d{1,2})\b", re.IGNORECASE)
# Nazionale maggiore: per un sedicenne è il segnale più forte che esista.
_MAGGIORE = re.compile(r"\b(?:absoluta|mayor|seniors?|maggiore|a-national)\b",
                       re.IGNORECASE)
# Data nel percorso: /2026/07/19/... È la data del DOCUMENTO, cioè di quando
# la convocazione è stata pubblicata. Non va confusa con observed_at, che dice
# solo quando l'abbiamo scaricata: tutte le nostre evidenze sono state raccolte
# fra luglio e agosto 2026, quindi observed_at schiaccerebbe due anni di storia
# in tre settimane.
_DATA_NEL_PATH = re.compile(r"/(20\d\d)/(\d{1,2})/(\d{1,2})/")

MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre")


@dataclass
class Evento:
    """Una convocazione: un atto, con la sua data e il documento che lo prova."""
    data: str          # "YYYY-MM-DD", o "" se il documento non la porta
    categoria: str     # "sub-17", "maggiore", o "" se non dichiarata
    federazione: str   # nome leggibile della fonte
    dominio: str
    url: str

    @property
    def chiave(self) -> str:
        """
        Due documenti sono lo stesso evento se sono della stessa federazione,
        stessa categoria e stesso mese. Due microcicli a gennaio e febbraio
        sono due selezioni; due pagine dello stesso raduno di febbraio, una.
        """
        return f"{self.dominio}|{self.categoria}|{self.data[:7]}" if self.data \
            else f"{self.dominio}|{self.url}"


@dataclass
class Persistenza:
    quante: int = 0
    eventi: list = field(default_factory=list)
    federazioni: list = field(default_factory=list)
    dal: str = ""
    al: str = ""
    categorie: list = field(default_factory=list)
    progressione: bool = False      # è salito di categoria nel tempo
    mesi_di_arco: int = 0

    def __bool__(self) -> bool:
        return self.quante > 0


def _numero_categoria(cat: str):
    """'sub-17' -> 17, 'maggiore' -> 99, '' -> None. Per ordinare e confrontare."""
    if cat == "maggiore":
        return 99
    m = re.search(r"(\d+)", cat or "")
    return int(m.group(1)) if m else None


def _categoria_da(testo: str) -> str:
    if _MAGGIORE.search(testo or ""):
        return "maggiore"
    m = _CATEGORIA.search(testo or "")
    if not m:
        return ""
    n = int(m.group(1))
    # Un "sub-2026" non esiste: quello è un anno finito nella regex.
    return f"sub-{n}" if 10 <= n <= 23 else ""


def _data_da(url: str) -> str:
    m = _DATA_NEL_PATH.search(url or "")
    if not m:
        return ""
    a, me, g = (int(x) for x in m.groups())
    try:
        return datetime(a, me, g).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _mese_leggibile(iso: str) -> str:
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    return f"{MESI[d.month - 1]} {d.year}"


def leggi(evidenze, e_federazione, nome_fonte=None) -> Persistenza:
    """
    Gli eventi di selezione dentro le evidenze di un giocatore.

    `evidenze`      iterabile di dict/Row con source_url, source_domain,
                    raw_content, origin
    `e_federazione` dominio -> bool: la fonte ha l'autorità di convocare?
                    Passata da fuori perché il registro delle fonti vive in
                    config/sources.json, non qui.
    `nome_fonte`    dominio -> nome leggibile (per la frase). Opzionale.

    Conta solo l'origin 'extractor': una convocazione la prova il testo del
    comunicato, non il riassunto che un modello ne ha fatto. È la stessa regola
    di ORIGIN_CHE_PROVANO in src/claims_v2.py.
    """
    nome_fonte = nome_fonte or {}
    grezzi = []
    for e in evidenze:
        dom = (e["source_domain"] if not isinstance(e, dict) else e.get("source_domain")) or ""
        url = (e["source_url"] if not isinstance(e, dict) else e.get("source_url")) or ""
        testo = (e["raw_content"] if not isinstance(e, dict) else e.get("raw_content")) or ""
        origin = (e["origin"] if not isinstance(e, dict) else e.get("origin")) or ""
        if origin != "extractor":
            continue
        if not e_federazione(dom):
            continue
        grezzi.append(Evento(
            data=_data_da(url),
            # Il titolo sta nell'URL (le federazioni usano slug parlanti) e il
            # testo lo ripete: si guardano tutti e due, l'URL per primo perché
            # è meno rumoroso.
            categoria=_categoria_da(url) or _categoria_da(testo[:600]),
            federazione=nome_fonte.get(dom, dom),
            dominio=dom,
            url=url,
        ))

    unici = {}
    for ev in grezzi:
        # A parità di evento tiene quello con la data: serve per l'arco.
        vecchio = unici.get(ev.chiave)
        if vecchio is None or (not vecchio.data and ev.data):
            unici[ev.chiave] = ev
    eventi = sorted(unici.values(), key=lambda e: (e.data or "9999", e.url))
    if not eventi:
        return Persistenza()

    con_data = [e for e in eventi if e.data]
    categorie = []
    for e in eventi:
        if e.categoria and e.categoria not in categorie:
            categorie.append(e.categoria)
    categorie.sort(key=lambda c: _numero_categoria(c) or 0)

    # Progressione: la categoria dell'ultimo evento datato è più alta di quella
    # del primo. Non "ha due categorie diverse" — salire conta, scendere no.
    progressione = False
    datati_con_cat = [e for e in con_data if _numero_categoria(e.categoria)]
    if len(datati_con_cat) >= 2:
        progressione = (_numero_categoria(datati_con_cat[-1].categoria)
                        > _numero_categoria(datati_con_cat[0].categoria))

    arco = 0
    if len(con_data) >= 2:
        a = datetime.strptime(con_data[0].data, "%Y-%m-%d")
        b = datetime.strptime(con_data[-1].data, "%Y-%m-%d")
        arco = (b.year - a.year) * 12 + (b.month - a.month)

    federazioni = []
    for e in eventi:
        if e.federazione not in federazioni:
            federazioni.append(e.federazione)

    return Persistenza(
        quante=len(eventi),
        eventi=eventi,
        federazioni=federazioni,
        dal=con_data[0].data if con_data else "",
        al=con_data[-1].data if con_data else "",
        categorie=categorie,
        progressione=progressione,
        mesi_di_arco=arco,
    )


def leggi_dal_registro(evidenze) -> Persistenza:
    """
    Come `leggi()`, ma prendendo autorità e nomi dal registro fonti
    (config/sources.json via claims_v2.registro). È la forma che serve alla
    pipeline; `leggi()` resta a due argomenti perché così è testabile senza
    toccare il registro.
    """
    from src.claims_v2 import registro
    reg = registro()

    def _e_federazione(dominio: str) -> bool:
        d = (dominio or "").lower()
        d = d[4:] if d.startswith("www.") else d
        return reg.get(d, {}).get("type") == "federation"

    nomi = {d: (v.get("name") or d) for d, v in reg.items()}
    return leggi(evidenze, _e_federazione, nomi)


def frase(p: Persistenza) -> str:
    """
    La riga che va in cima alla scheda. Dice solo cose che i link sotto
    dimostrano: quante volte, da chi, quando, in che categoria. Nessun
    aggettivo, nessun giudizio — quelli non li possiamo provare.
    """
    if not p:
        return ""
    chi = p.federazioni[0] if len(p.federazioni) == 1 else "più federazioni"
    if p.quante == 1:
        quando = f" ({_mese_leggibile(p.dal)})" if p.dal else ""
        cat = f", {p.categorie[0].replace('sub-', 'Sub-')}" if p.categorie else ""
        return f"Convocato una volta da {chi}{cat}{quando}."

    testo = f"Convocato {p.quante} volte da {chi}"
    if p.dal and p.al and p.dal[:7] != p.al[:7]:
        testo += f" tra {_mese_leggibile(p.dal)} e {_mese_leggibile(p.al)}"
    elif p.dal:
        testo += f" ({_mese_leggibile(p.dal)})"
    if p.progressione and len(p.categorie) >= 2:
        primo = p.categorie[0].replace("sub-", "Sub-").replace("maggiore", "Nazionale A")
        ultimo = p.categorie[-1].replace("sub-", "Sub-").replace("maggiore", "Nazionale A")
        testo += f", dal {primo} al {ultimo}"
    elif len(p.categorie) == 1:
        testo += f", {p.categorie[0].replace('sub-', 'Sub-').replace('maggiore', 'Nazionale A')}"
    return testo + "."


def punti(p: Persistenza) -> float:
    """
    Quanto vale la persistenza di selezione nel merito. Max 32.

    Tarato per stare accanto a `_production_points` (max 40) senza superarlo:
    una stagione documentata con gol e presenze resta il segnale più forte se
    c'è. Ma dove non c'è — cioè quasi sempre, su Global — questo è ciò che
    resta, ed è molto meglio di zero.

    La scala non è lineare: la seconda convocazione è il salto informativo
    grosso (da "è stato preso una volta" a "continuano a prenderlo"), la
    quinta aggiunge poco.
    """
    if not p:
        return 0.0
    base = {0: 0.0, 1: 4.0, 2: 14.0, 3: 19.0, 4: 22.0}.get(p.quante, 24.0)
    if p.progressione:
        base += 5.0            # salire di categoria è la conferma piu' pulita
    if p.mesi_di_arco >= 12:
        base += 3.0            # ricorre da piu' di una stagione
    return min(base, 32.0)


# --------------------------------------------------------------- test

def _test() -> None:
    fcf = "fcf.com.co"
    e_fed = lambda d: d in {fcf, "thenff.com"}
    nomi = {fcf: "Federación Colombiana de Fútbol"}

    def ev(url, testo="", origin="extractor", dom=fcf):
        return {"source_url": url, "source_domain": dom,
                "raw_content": testo, "origin": origin}

    # 1. Caso reale: Edmilson Yosue Herazo Torres, 4 convocazioni, sale a Sub-19.
    herazo = [
        ev(f"https://{fcf}/2025/02/06/convocatoria-de-la-seleccion-colombia-masculina-sub-17-para-microciclo/"),
        ev(f"https://{fcf}/2025/01/14/convocatoria-de-la-seleccion-colombia-masculina-sub-17-para-microciclo/"),
        ev(f"http://www.{fcf}/2024/09/17/convocatoria-seleccion-colombia-masculina-sub-17-para-el-conmebol/"),
        ev(f"http://www.{fcf}/2026/07/27/convocatoria-de-la-seleccion-colombia-masculina-sub19-para-los-juegos/"),
    ]
    p = leggi(herazo, e_fed, nomi)
    assert p.quante == 4, p.quante
    assert p.dal.startswith("2024-09") and p.al.startswith("2026-07")
    assert p.categorie == ["sub-17", "sub-19"], p.categorie
    assert p.progressione is True
    assert p.mesi_di_arco == 22, p.mesi_di_arco
    assert frase(p) == ("Convocato 4 volte da Federación Colombiana de Fútbol "
                        "tra settembre 2024 e luglio 2026, dal Sub-17 al Sub-19."), frase(p)
    assert punti(p) == 30.0, punti(p)

    # 2. Due pagine dello stesso raduno non sono due selezioni.
    stesso = [
        ev(f"https://{fcf}/2026/02/17/convocatoria-sub17-microciclo-febrero-2026/"),
        ev(f"https://{fcf}/2026/02/19/convocatoria-sub17-microciclo-febrero-2026-actualizada/"),
    ]
    assert leggi(stesso, e_fed, nomi).quante == 1

    # 3. Un mese diverso sì: sono due decisioni.
    diversi = [
        ev(f"https://{fcf}/2026/01/13/convocatoria-sub17-microciclo/"),
        ev(f"https://{fcf}/2026/02/17/convocatoria-sub17-microciclo/"),
    ]
    assert leggi(diversi, e_fed, nomi).quante == 2

    # 4. Il riassunto di un modello non prova una convocazione: solo il testo
    #    della fonte lo fa (stessa regola di claims_v2).
    solo_llm = [ev(f"https://{fcf}/2026/01/13/convocatoria-sub17/", origin="llm_summary")]
    assert not leggi(solo_llm, e_fed, nomi)

    # 5. Una fonte senza autorità di convocare non genera eventi, per quanto
    #    il titolo somigli.
    blog = [ev("https://blogcalcio.example/2026/01/13/convocatoria-sub17/",
               dom="blogcalcio.example")]
    assert not leggi(blog, e_fed, nomi)

    # 6. Scendere di categoria non è progressione.
    giu = [
        ev(f"https://{fcf}/2025/01/14/convocatoria-sub-19-microciclo/"),
        ev(f"https://{fcf}/2026/01/14/convocatoria-sub-17-microciclo/"),
    ]
    assert leggi(giu, e_fed, nomi).progressione is False

    # 7. Una convocazione sola resta una convocazione sola: si dice, non si
    #    gonfia.
    una = leggi([ev(f"https://{fcf}/2026/07/19/convocatoria-sub-17-microciclo/")],
                e_fed, nomi)
    assert una.quante == 1 and punti(una) == 4.0
    assert frase(una) == ("Convocato una volta da Federación Colombiana de "
                          "Fútbol, Sub-17 (luglio 2026).") , frase(una)

    # 8. L'anno nell'URL non deve diventare una categoria ("sub-2026").
    assert _categoria_da("https://x/2026/07/19/convocatoria-2026/") == ""

    print("selezione_v2: ok")


if __name__ == "__main__":
    _test()
