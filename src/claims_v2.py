#!/usr/bin/env python3
"""
OB1 v2 — Ogni CAMPO con la sua prova (non ogni scheda con un bollino).

Perché questo modulo esiste
---------------------------
Il 26 agosto 2026, misurando il database di produzione, il sistema
pubblicava 64 profili col bollino "VERIFICATO — N FONTI INDIPENDENTI".
Ne reggevano 2. Il primo in vetrina, Yan Diomande, diceva "15 anni, RB
Leipzig": ne ha 19 ed è al Real Madrid da 140 milioni.

Il primo rimedio è stato un cancello più severo (src/challenge_v2.py):
64 -> 15 pubblicati. Ma quel cancello conteneva un errore di progetto mio,
trovato subito dopo: bocciava come "fonte non sostanziale" la riga

    "Juan José Fori Viveros – C.D Estudiantil"          [fcf.com.co]

perché è corta. Non è corta: è la **Federazione Colombiana** che certifica
in una convocazione ufficiale che quel ragazzo esiste e gioca in quel club.
È la prova d'identità più forte che possiamo desiderare — più di un
articolo di giornale. Confondere "testo breve" con "prova debole" buttava
via esattamente il materiale migliore che abbiamo.

Il vero difetto non era la severità del cancello: era che il cancello è
BINARIO. Decide se una SCHEDA esce, quando la domanda giusta è cosa quella
scheda può DIRE. Quella convocazione stabilisce nome e club; non stabilisce
l'età, e non conosce nessuna statistica. Il sistema pubblicava lo stesso
un'età (17) dedotta dal fatto che il torneo era Sub-17, presentandola come
osservata.

Il modello
----------
L'unità atomica non è il giocatore: è la SINGOLA AFFERMAZIONE.

    campo (club) + valore ("C.D Estudiantil") + fonte (fcf.com.co,
    federazione) + stato (dichiarato) + citazione + quando

Ogni campo esce con il suo stato, e lo stato si vede:

    DICHIARATO  una fonte competente per quel campo lo scrive davvero
    DEDOTTO     ricavabile dal contesto ma non scritto (età da "Sub-17")
    ASSENTE     nessuno lo dice: il campo non si mostra, non si inventa

Competenza per tipo di fonte
----------------------------
Cosa una fonte può stabilire dipende da COS'È, non da quanto è lunga.
Una convocazione federale prova identità e club, non le statistiche.
Un titolo di pagina Transfermarkt non prova nulla, per quanto sia un
dominio autorevole: è un titolo.

Tutto qui dentro è deterministico e puro: niente rete, niente LLM, niente
DB. Un LLM che giudica le proprie estrazioni sarebbe di nuovo il problema.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

DICHIARATO = "dichiarato"
DEDOTTO = "dedotto"
ASSENTE = "assente"

# Cosa ogni tipo di fonte è competente a stabilire. La chiave è il campo
# `type` del registro (config/sources.json).
#
#  federation/confederation  una convocazione o una lista ufficiale prova
#                            CHI È e PER CHI GIOCA. Non prova quanti anni ha
#                            (la categoria dà una fascia, non una data) né
#                            quanto ha segnato.
#  results_stats             database strutturati (Promiedos, Soccerway):
#                            nascono per anagrafica e numeri, li stabiliscono.
#  *_press                   una cronaca dice quello che dice: se scrive
#                            l'età, la stabilisce; se scrive i gol, li
#                            stabilisce.
#  academy                   il club parla dei propri tesserati: identità e
#                            club sì, il resto è comunicazione.
#  aggregator                Transfermarkt, Soccerway, Sofascore, FBref. Il
#                            registro li marca "secondary", ma quel tier vuol
#                            dire "non è stampa primaria", non "non vale
#                            nulla": sono database di riferimento con
#                            un'anagrafica propria e un ID stabile per
#                            persona, non testate che ricopiano un'agenzia.
#                            Per identità, club e data di nascita sono
#                            competenti — è esattamente il motivo per cui
#                            l'intero prodotto Lega Pro usa una scheda
#                            Transfermarkt come verifica.
#                            Questo NON riapre la porta al caso Diomande: la
#                            competenza dice solo CHI può stabilire un campo,
#                            resta obbligatorio che il valore compaia davvero
#                            nel testo citato. Un titolo di pagina ("Kévin
#                            Angulo - Player profile") non contiene il club,
#                            quindi non stabilisce il club, per quanto il
#                            dominio sia autorevole.
#  fan_site                  nessuna competenza: non ha un'anagrafica propria
#                            né una redazione che risponda di quel che scrive.
COMPETENZE = {
    "federation":     {"nome", "club"},
    "confederation":  {"nome", "club"},
    "academy":        {"nome", "club"},
    "results_stats":  {"nome", "club", "eta", "stats"},
    "national_press": {"nome", "club", "eta", "stats"},
    "local_press":    {"nome", "club", "eta", "stats"},
    "aggregator":     {"nome", "club", "eta", "stats"},
    "fan_site":       set(),
}

# Categoria giovanile nell'URL o nel testo: da "Sub-17" si ricava una
# FASCIA ("under 17 al momento della convocazione"), non un'età.
_CATEGORIA_RE = re.compile(r"sub-?(\d{2})|\bu-?(\d{2})\b", re.IGNORECASE)

# Solo un testo CITATO dalla fonte può stabilire qualcosa. `origin` dice come
# è nata un'evidenza:
#
#   extractor          citazione letterale, verificata carattere per carattere
#                      contro il testo della pagina (ground_observations in
#                      src/extractor_v2.py, attivo dal 19 ago 2026)
#   legacy_migration   riassunto scritto dall'LLM SULLA fonte, migrato dalla v1
#
# La distinzione non è formale. Yan Diomande risultava «15 anni DICHIARATO da
# Los Angeles Times»: quel testo non l'ha scritto il LA Times, l'ha scritto il
# nostro estrattore v1 leggendolo ("Età molto giovane (15 anni)... questo
# suggerisce un talento grezzo"). Ne ha 19. Trattare una nostra conclusione
# passata come se fosse la voce della fonte è il modo esatto in cui
# un'allucinazione diventa un fatto verificato: il sistema cita sé stesso e
# chiama il risultato prova.
#
# Le evidenze legacy restano nel database — sono ottime PISTE, dicono dove
# guardare. Semplicemente non provano niente da sole.
ORIGIN_CHE_PROVANO = frozenset({"extractor"})

_REGISTRO_CACHE: Optional[Dict[str, Dict[str, str]]] = None


def _norm(t: Any) -> str:
    s = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _tok(t: Any) -> set:
    return set(_norm(t).split())


def _dominio(url: str) -> str:
    from urllib.parse import urlparse
    try:
        host = urlparse(url or "").netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def registro() -> Dict[str, Dict[str, str]]:
    """dominio -> {'type', 'tier', 'name'} da config/sources.json."""
    global _REGISTRO_CACHE
    if _REGISTRO_CACHE is not None:
        return _REGISTRO_CACHE
    mappa: Dict[str, Dict[str, str]] = {}
    try:
        path = Path(__file__).parent.parent / "config" / "sources.json"
        dati = json.loads(path.read_text(encoding="utf-8"))
        voci = dati.get("sources") if isinstance(dati, dict) else dati
        for v in voci or []:
            meta = {"type": v.get("type", ""), "tier": v.get("tier", "secondary"),
                    "name": v.get("name", "")}
            d = _dominio(v.get("url", ""))
            if d:
                mappa[d] = meta
            for alias in v.get("aliases", []) or []:
                if alias:
                    mappa[alias.lower()] = meta
    except Exception:
        pass
    _REGISTRO_CACHE = mappa
    return mappa


def competenze_di(dominio: str) -> set:
    """
    Campi che questo dominio può STABILIRE. Un dominio non nel registro non
    stabilisce nulla: non lo conosciamo, e non conoscerlo non è un motivo
    per fidarsi — è il contrario.
    """
    meta = registro().get((dominio or "").lower())
    if not meta:
        # Dominio non nel registro: non lo conosciamo, e non conoscerlo non è
        # un motivo per fidarsi. È l'unica protezione contro una fonte
        # qualunque trovata da una ricerca web.
        return set()
    return COMPETENZE.get(meta.get("type", ""), set())


def _spoglia(testo: Any) -> str:
    """Via URL e boilerplate dello scraper: non li ha scritti la fonte."""
    t = re.sub(r"https?://\S+", " ", str(testo or ""))
    return re.sub(r"\b(title|url source|markdown content|published time|"
                  r"player profile|profilo giocatore)\b\s*:?", " ", t, flags=re.I)


def _nomina(testo: Any, nome: Any, nomi_comuni: set) -> bool:
    """L'evidenza nomina questa persona in modo identificante?"""
    tutti = {t for t in _norm(nome).split() if len(t) >= 4}
    identificanti = (tutti - nomi_comuni) or tutti
    return not identificanti or bool(identificanti & _tok(testo))


# Nomi troppo comuni per identificare qualcuno da soli. Caso reale: a "Juan
# José Fori Viveros" era attaccata la riga di "Juan José Cataño Vahos", che
# è un altro ragazzo, e contava come seconda fonte.
NOMI_COMUNI = {
    "juan", "jose", "luis", "carlos", "david", "daniel", "jhon", "john",
    "miguel", "angel", "santiago", "andres", "cristian", "camilo", "sebastian",
    "alejandro", "felipe", "diego", "mateus", "gabriel", "pedro", "joao",
    "bruno", "lucas", "matheus", "kevin", "brayan", "marco", "mario", "paulo",
    "rafael", "victor", "eduardo", "fernando", "antonio", "francisco", "jorge",
    "silva", "santos", "souza", "oliveira", "rodriguez", "gonzalez", "martinez",
    "garcia", "lopez", "perez", "sanchez", "ramirez", "torres", "flores",
}


def _prova(campo: str, valore: Any, evidenze: List[Dict[str, Any]],
           nome: Any) -> Optional[Dict[str, Any]]:
    """
    Cerca una fonte COMPETENTE per `campo` il cui testo contenga davvero
    `valore` e che nomini il giocatore. Ritorna la prova, o None.
    """
    if valore in (None, "", 0):
        return None
    for e in evidenze:
        # Un riassunto che abbiamo scritto noi non è la voce della fonte.
        if (e.get("origin") or "extractor") not in ORIGIN_CHE_PROVANO:
            continue
        dom = e.get("source_domain") or _dominio(e.get("source_url", ""))
        if campo not in competenze_di(dom):
            continue
        testo = _spoglia(e.get("raw_content"))
        if not _nomina(testo, nome, NOMI_COMUNI):
            continue
        if campo == "eta":
            trovato = bool(re.search(rf"\b{int(valore)}\b", _norm(testo)))
        else:
            attesi = {t for t in _tok(valore) if len(t) >= 4}
            trovato = bool(attesi) and attesi.issubset(_tok(testo))
        if trovato:
            frase = str(e.get("raw_content") or "").strip()
            return {"fonte": dom,
                    "nome_fonte": registro().get(dom, {}).get("name", dom),
                    "tipo": registro().get(dom, {}).get("type", ""),
                    "citazione": frase[:220],
                    "url": e.get("source_url", ""),
                    "quando": e.get("observed_at", "")}
    return None


def _fascia_eta(evidenze: List[Dict[str, Any]]) -> Optional[int]:
    """La categoria più bassa vista nelle fonti: 'Sub-17' -> 17 (fascia)."""
    fasce = []
    for e in evidenze:
        blob = f"{e.get('source_url','')} {e.get('raw_content','')}"
        for m in _CATEGORIA_RE.finditer(blob):
            c = m.group(1) or m.group(2)
            if c and 12 <= int(c) <= 23:
                fasce.append(int(c))
    return min(fasce) if fasce else None


def stabilisci(giocatore: Dict[str, Any],
               evidenze: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Per ogni campo pubblicabile dice COSA sappiamo e COME lo sappiamo.

    Ritorna {campo: {valore, stato, prova?, nota?}}. Un campo ASSENTE non va
    mostrato: il silenzio è un'informazione onesta, un valore inventato no.
    """
    nome = giocatore.get("canonical_name") or giocatore.get("name") or ""
    out: Dict[str, Dict[str, Any]] = {}

    for campo, valore in (("nome", nome),
                          ("club", giocatore.get("club")),
                          ("eta", giocatore.get("age"))):
        prova = _prova(campo, valore, evidenze, nome)
        if prova:
            out[campo] = {"valore": valore, "stato": DICHIARATO, "prova": prova}
        elif campo == "eta" and valore is not None:
            fascia = _fascia_eta(evidenze)
            if fascia is not None and int(valore) == fascia:
                out[campo] = {
                    "valore": valore, "stato": DEDOTTO,
                    "nota": f"nessuna fonte scrive l'età: ricavata dalla "
                            f"categoria del torneo (Sub-{fascia})",
                }
            else:
                out[campo] = {"valore": None, "stato": ASSENTE,
                              "nota": f"il valore {valore} non è scritto da "
                                      f"nessuna fonte competente"}
        else:
            out[campo] = {"valore": None, "stato": ASSENTE}

    stats = giocatore.get("stats") or {}
    if any(stats.values()):
        prova = None
        for k, v in stats.items():
            if v:
                prova = _prova("stats", v, evidenze, nome)
                if prova:
                    break
        out["stats"] = ({"valore": stats, "stato": DICHIARATO, "prova": prova}
                        if prova else
                        {"valore": None, "stato": ASSENTE,
                         "nota": "nessuna fonte competente riporta questi numeri"})
    else:
        out["stats"] = {"valore": None, "stato": ASSENTE}

    return out


def fonti_che_stabiliscono(claims: Dict[str, Dict[str, Any]]) -> set:
    return {c["prova"]["fonte"] for c in claims.values()
            if c.get("stato") == DICHIARATO and c.get("prova")}


def pubblicabile(claims: Dict[str, Dict[str, Any]]) -> tuple:
    """
    La soglia di pubblicazione, dichiarata qui e in un posto solo.

    Si pubblica quando **l'identità è stabilita**: nome e club scritti da
    almeno una fonte primary competente. È esattamente la promessa del
    prodotto — "verifica che esista davvero" — né più né meno.

    NON serve un'età, e non servono due fonti: una convocazione della
    federazione nazionale è una prova d'identità migliore di due articoli
    che si copiano. Quel che manca non viene finto: esce come ASSENTE, e
    la scheda lo dice.
    """
    motivi = []
    if claims.get("nome", {}).get("stato") != DICHIARATO:
        motivi.append("nessuna fonte competente scrive il nome di questa persona")
    if claims.get("club", {}).get("stato") != DICHIARATO:
        motivi.append("nessuna fonte competente scrive per quale club gioca")
    return (not motivi), motivi


# --------------------------------------------------------------- self-test
if __name__ == "__main__":
    # Caso 1 — Fori Viveros: la convocazione federale che il cancello
    # precedente buttava via. Deve pubblicare, con l'età DEDOTTA, non finta.
    ev = [
        {"raw_content": "Juan José Fori Viveros – C.D Estudiantil",
         "source_domain": "fcf.com.co",
         "source_url": "https://fcf.com.co/2026/05/14/convocatoria-de-la-seleccion-colombia-masculina-sub-17-para-microciclo/",
         "observed_at": "2026-05-14"},
        {"raw_content": "Juan Fori - Player profile",
         "source_domain": "transfermarkt.com",
         "source_url": "https://www.transfermarkt.com/juan-fori/profil/spieler/1531673",
         "observed_at": "2026-06-01"},
    ]
    c = stabilisci({"canonical_name": "Juan José Fori Viveros",
                    "club": "C.D Estudiantil", "age": 17}, ev)
    assert c["nome"]["stato"] == DICHIARATO, c["nome"]
    assert c["club"]["stato"] == DICHIARATO, c["club"]
    assert c["nome"]["prova"]["tipo"] == "federation"
    assert c["eta"]["stato"] == DEDOTTO, c["eta"]
    ok, motivi = pubblicabile(c)
    assert ok, motivi

    # Caso 2 — un titolo di pagina non stabilisce niente, per quanto il
    # dominio sia autorevole.
    solo_tm = [ev[1]]
    c2 = stabilisci({"canonical_name": "Juan Fori", "club": "C.D Estudiantil",
                     "age": 17}, solo_tm)
    assert c2["club"]["stato"] == ASSENTE, c2["club"]
    assert not pubblicabile(c2)[0]

    # Caso 3 — Diomande: l'età 15 non è scritta da nessuno. Non si pubblica
    # un'età inventata: il campo sparisce e la scheda lo dichiara.
    c3 = stabilisci(
        {"canonical_name": "Yan Diomande", "club": "RB Leipzig", "age": 15},
        [{"raw_content": "Na atual temporada foram 12 gols e nove assistências.",
          "source_domain": "placar.com.br", "source_url": "https://placar.com.br/x",
          "observed_at": "2026-08-23"}])
    assert c3["eta"]["stato"] == ASSENTE, c3["eta"]
    assert c3["nome"]["stato"] == ASSENTE, "quel testo non lo nomina nemmeno"

    # Caso 4 — un dominio NON nel registro non stabilisce nulla, per quanto
    # completo sia il testo: e' l'unica difesa contro una fonte qualunque
    # pescata da una ricerca web.
    c4 = stabilisci({"canonical_name": "Tizio Caio", "club": "Club Vero", "age": 18},
                    [{"raw_content": "Tizio Caio, 18 anni, gioca nel Club Vero ed "
                                     "è stato decisivo ieri con una doppietta.",
                      "source_domain": "un-aggregatore-qualunque.com",
                      "source_url": "https://un-aggregatore-qualunque.com/x",
                      "observed_at": "2026-08-01"}])
    assert c4["club"]["stato"] == ASSENTE
    assert not pubblicabile(c4)[0]

    # Caso 4b — un database di riferimento (Transfermarkt/Sofascore) SI'.
    # Il registro li marca "secondary", ma hanno un'anagrafica propria: e' il
    # motivo per cui tutto il prodotto Lega Pro usa una scheda TM come prova.
    c4b = stabilisci({"canonical_name": "Tizio Caio", "club": "Club Vero", "age": 18},
                     [{"raw_content": "Tizio Caio, 18 anni, Club Vero, "
                                      "centrocampista, 12 presenze stagionali.",
                       "source_domain": "sofascore.com",
                       "source_url": "https://www.sofascore.com/player/tizio-caio",
                       "observed_at": "2026-08-01", "origin": "extractor"}])
    assert c4b["club"]["stato"] == DICHIARATO, c4b["club"]
    assert c4b["eta"]["stato"] == DICHIARATO, c4b["eta"]
    assert pubblicabile(c4b)[0]

    # Caso 5 — la stampa primaria che scrive l'età LA stabilisce davvero.
    c5 = stabilisci({"canonical_name": "Eidy Ruiz", "club": "Dep. Cali", "age": 17},
                    [{"raw_content": "El gol para las nuestras fue anotado por la "
                                     "delantera Eidy Ruiz, de 17 años, del Dep. Cali.",
                      "source_domain": "fcf.com.co",
                      "source_url": "https://fcf.com.co/nota", "observed_at": "2026-04-02"}])
    assert c5["nome"]["stato"] == DICHIARATO
    assert c5["club"]["stato"] == DICHIARATO
    # fcf è federation: competente per nome/club, NON per l'età.
    assert c5["eta"]["stato"] == ASSENTE, c5["eta"]

    # Caso 6 — il caso Diomande vero: un riassunto scritto dal NOSTRO
    # estrattore v1, salvato sotto un dominio autorevole. Contiene "15 anni",
    # ma quelle parole non le ha scritte il Los Angeles Times: le abbiamo
    # scritte noi. Non deve stabilire NULLA, o il sistema cita sé stesso.
    blob_legacy = ("Età molto giovane (15 anni) e una storia di un percorso "
                   "'loco' dal calcio scolastico in Florida al potenziale "
                   "Mondiale, proveniente dalla Costa d'Avorio. Questo "
                   "suggerisce un talento grezzo con un'alta asimmetria.")
    c6 = stabilisci(
        {"canonical_name": "Yan Diomande", "club": "RB Leipzig", "age": 15},
        [{"raw_content": blob_legacy, "source_domain": "latimes.com",
          "source_url": "https://www.latimes.com/espanol/deportes/articulo/x",
          "observed_at": "2026-06-02", "origin": "legacy_migration"}])
    assert c6["eta"]["stato"] == ASSENTE, c6["eta"]
    assert c6["nome"]["stato"] == ASSENTE, c6["nome"]
    assert not pubblicabile(c6)[0]
    # ...ma la STESSA frase, se fosse una citazione vera della fonte, varrebbe.
    c6b = stabilisci(
        {"canonical_name": "Yan Diomande", "club": "RB Leipzig", "age": 15},
        [{"raw_content": "Yan Diomande, 15 anni, gioca nel RB Leipzig.",
          "source_domain": "latimes.com", "source_url": "https://x",
          "observed_at": "2026-06-02", "origin": "extractor"}])
    assert c6b["eta"]["stato"] == DICHIARATO

    print("OK claims_v2: ogni campo esce col suo stato — dichiarato, dedotto "
          "o assente. Una convocazione federale pubblica; un titolo di pagina "
          "no; un nostro vecchio riassunto non prova niente.")
