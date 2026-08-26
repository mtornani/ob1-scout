#!/usr/bin/env python3
"""
OB1 v2 — L'avvocato del diavolo del sistema su sé stesso.

Il problema che risolve, misurato il 26 agosto 2026 sul database di
produzione (252 giocatori, 64 pubblicati come "VERIFICATO"):

    profili con >= 2 fonti SOSTANZIALI ......  2 su 64
    profili con ZERO fonti sostanziali ...... 42 su 64
    profili la cui età non è scritta da
      nessuna fonte .......................... 32 su 64
    evidenze che non contengono nemmeno un
      token identificante del giocatore ...... 25

Il gate esistente conta DOMINI DISTINTI. Ma "Kevin Angulo Angulo – América
S.A." ripetuto su cinque pagine della federazione colombiana più una pagina
Transfermarkt che contiene solo il titolo ("Kévin Angulo - Player profile")
fa due domini, supera il gate, e finisce in dashboard come "VERIFICATO — 2
FONTI INDIPENDENTI". Nessuna delle due fonti dice quanti anni ha: il 17 è
DEDOTTO dal fatto che la convocazione è Sub-17. È un'inferenza ragionevole
presentata come un fatto osservato.

Il caso che ha fatto scoppiare tutto: Yan Diomande, primo "in evidenza",
score 53, "3 fonti indipendenti" — 15 anni, RB Leipzig. Ne ha 19 (14 nov
2006) ed è al Real Madrid dal 6 agosto 2026 per una cifra record. Un
giocatore da 140 milioni pubblicato come scoperta early non è un dettaglio
sbagliato: è la negazione della promessa del prodotto.

La differenza di approccio:

    gate classico     "ho abbastanza prove per pubblicare?"
    avvocato del      "riesco a demolire questa scheda? se sì,
    diavolo            non la pubblico — o la pubblico dicendo
                       esattamente dove è debole"

Ogni contestazione qui è deterministica e pura: niente rete, niente LLM,
niente DB. Si scrive una volta, si testa, e non può contraddire sé stessa
run dopo run. Un LLM che giudica le proprie estrazioni sarebbe di nuovo il
problema, non la soluzione.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List, Optional

# Contestazione BLOCCANTE: la scheda non si pubblica finché regge questo
# rilievo. Contestazione di CAUTELA: si pubblica, ma il rilievo si vede.
BLOCCANTE = "bloccante"
CAUTELA = "cautela"

# Nomi di battesimo così comuni da non identificare nessuno: se l'unica cosa
# che un'evidenza condivide col giocatore è "Juan" o "José", quell'evidenza
# può benissimo parlare di un'altra persona. Caso reale: a "Juan José Fori
# Viveros" era attaccata una riga su "Juan José Cataño Vahos – Envigado F.C.",
# che è un altro ragazzo, e contava come fonte.
NOMI_COMUNI = {
    "juan", "jose", "luis", "carlos", "david", "daniel", "jhon", "john",
    "miguel", "angel", "santiago", "andres", "cristian", "camilo", "sebastian",
    "alejandro", "felipe", "diego", "mateus", "gabriel", "pedro", "joao",
    "bruno", "lucas", "matheus", "kevin", "brayan", "marco", "mario", "paulo",
    "rafael", "victor", "eduardo", "fernando", "antonio", "francisco", "jorge",
    "silva", "santos", "souza", "oliveira", "rodriguez", "gonzalez", "martinez",
    "garcia", "lopez", "perez", "sanchez", "ramirez", "torres", "flores",
}

# Club la cui sola presenza smentisce la premessa "early / poco coperto".
# Non è un giudizio sulla qualità: è che di questi parlano tutti, quindi
# arrivarci "primi" è impossibile e dichiararlo è un autogol. Lista corta e
# volutamente conservativa — meglio pochi nomi certi che una tassonomia.
CLUB_GIA_COPERTI = {
    "real madrid", "fc barcelona", "barcelona", "manchester city",
    "manchester united", "liverpool", "chelsea", "arsenal", "tottenham",
    "paris saint-germain", "psg", "bayern", "bayern munich", "bayern münchen",
    "borussia dortmund", "juventus", "inter", "internazionale", "ac milan",
    "milan", "napoli", "atletico madrid", "atlético madrid", "ajax",
    "benfica", "porto", "sporting cp", "rb leipzig", "bayer leverkusen",
    "as roma", "roma", "lazio", "newcastle united", "aston villa",
}

# Categoria del torneo nell'URL: "sub-17", "u17", "sub17". Se l'età salvata
# coincide col numero della categoria e nessun testo la scrive, non è stata
# osservata: è stata dedotta dal titolo del torneo.
_CATEGORIA_RE = re.compile(r"sub-?(\d{2})|\bu-?(\d{2})\b", re.IGNORECASE)

# Un club che è in realtà una descrizione generica prodotta dall'estrattore
# quando il testo non dava un nome: "Unspecified Portuguese Club", "Brazilian
# Serie A Club", "club di Serie B non specificato". Entrambi gli esempi sono
# reali, presi da schede pubblicate.
_CLUB_DESCRITTIVO_RE = re.compile(
    r"\bunspecified\b|\bnon specificat|\bunknown\b|\bsconosciut|"
    r"\b(?:brazilian|portuguese|italian|spanish|argentine|club)\s+"
    r"(?:serie|liga|primera|league|top|major)?\s*\w*\s*club\b", re.IGNORECASE)


def _norm(testo: Any) -> str:
    t = unicodedata.normalize("NFKD", str(testo or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _token(testo: Any) -> set:
    return set(_norm(testo).split())


def token_identificanti(nome: Any) -> set:
    """
    I token di un nome che identificano davvero una persona: né corti né
    comunissimi. Se restano vuoti (es. "Mora", "Chukwueze" da soli) si
    ripiega su tutti i token >= 4, perché è meglio un controllo debole che
    nessun controllo.
    """
    tutti = {t for t in _norm(nome).split() if len(t) >= 4}
    rari = tutti - NOMI_COMUNI
    return rari or tutti


def evidenza_parla_del_giocatore(raw_content: Any, nome: Any) -> bool:
    """
    Un'evidenza vale come prova SU QUESTA PERSONA solo se la nomina in modo
    identificante. Un testo che descrive un giocatore senza mai chiamarlo per
    nome non è verificabile contro nessuno — e infatti 25 evidenze in
    produzione erano riassunti valutativi ("Capocannoniere del Brasileirão
    Série B 2026 con 11 gol") senza il nome dentro.
    """
    identificanti = token_identificanti(nome)
    if not identificanti:
        return True          # nessun criterio: non si può accusare
    return bool(identificanti & _token(raw_content))


def _spoglia(raw_content: Any) -> str:
    """
    Toglie dal testo tutto ciò che il lettore non ha scritto: URL e boilerplate
    dello scraper. Senza questo, "Douglas Telles - Player profile / URL Source:
    https://www.transfermarkt.com/douglas-telles/profil/spieler/1220787" conta
    come una fonte sostanziale, perché l'URL da solo porta abbastanza parole
    da superare la soglia. È un titolo di pagina, non un fatto.
    """
    testo = str(raw_content or "")
    testo = re.sub(r"https?://\S+", " ", testo)
    testo = re.sub(
        r"\b(title|url source|markdown content|published time|player profile|"
        r"profilo giocatore|stats|statistiche)\b\s*:?", " ", testo, flags=re.I)
    return testo


def evidenza_e_sostanziale(raw_content: Any, nome: Any) -> bool:
    """
    Una fonte è sostanziale se dice QUALCOSA oltre al nome. "Kévin Angulo -
    Player profile" e "Nome Cognome – Nome Club" non lo sono: confermano che
    la persona esiste in un elenco, non un fatto su di lei.
    """
    if not evidenza_parla_del_giocatore(raw_content, nome):
        return False
    parole = _token(_spoglia(raw_content)) - _token(nome)
    return len(parole) >= 6


def _eta_nel_testo(eta: Optional[int], blob: str) -> bool:
    return bool(eta) and bool(re.search(rf"\b{int(eta)}\b", blob))


def _eta_deducibile_da_categoria(eta: Optional[int], urls: str) -> bool:
    if not eta:
        return False
    for m in _CATEGORIA_RE.finditer(urls):
        cat = m.group(1) or m.group(2)
        if cat and int(cat) == int(eta):
            return True
    return False


def contesta(giocatore: Dict[str, Any], evidenze: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Prova a demolire una scheda. Ritorna la lista dei rilievi che reggono —
    vuota se la scheda sopravvive a tutti.

    `giocatore`: dict con almeno canonical_name, age, club (opz. last_seen).
    `evidenze`:  lista di dict con raw_content, source_domain, source_url.
    """
    nome = giocatore.get("canonical_name") or giocatore.get("name") or ""
    eta = giocatore.get("age")
    club = giocatore.get("club") or ""
    rilievi: List[Dict[str, str]] = []

    blob = _norm(" ".join(str(e.get("raw_content") or "") for e in evidenze))
    urls = " ".join(str(e.get("source_url") or "") for e in evidenze)

    parlanti = [e for e in evidenze
                if evidenza_parla_del_giocatore(e.get("raw_content"), nome)]
    sostanziali = [e for e in parlanti
                   if evidenza_e_sostanziale(e.get("raw_content"), nome)]
    domini_sostanziali = {e.get("source_domain") for e in sostanziali if e.get("source_domain")}

    # --- identità: le prove parlano davvero di questa persona? -------------
    estranee = len(evidenze) - len(parlanti)
    if evidenze and not parlanti:
        rilievi.append({
            "codice": "nessuna_prova_nomina_il_giocatore",
            "gravita": BLOCCANTE,
            "detta": f"nessuna delle {len(evidenze)} fonti contiene il nome in "
                     f"forma identificante: non sono verificabili su di lui",
        })
    elif estranee:
        rilievi.append({
            "codice": "prove_che_non_lo_nominano",
            "gravita": CAUTELA,
            "detta": f"{estranee} fonti su {len(evidenze)} non lo nominano in "
                     f"forma identificante: potrebbero parlare di un'altra persona",
        })

    # --- corroborazione: quante fonti dicono qualcosa, non solo il nome ----
    if not domini_sostanziali:
        rilievi.append({
            "codice": "nessuna_fonte_sostanziale",
            "gravita": BLOCCANTE,
            "detta": "nessuna fonte dice nulla oltre al nome: elenchi e titoli "
                     "di pagina provano che esiste, non un fatto su di lui",
        })
    elif len(domini_sostanziali) == 1:
        rilievi.append({
            "codice": "una_sola_fonte_sostanziale",
            "gravita": CAUTELA,
            "detta": f"un solo dominio dice qualcosa di concreto "
                     f"({sorted(domini_sostanziali)[0]}): le altre confermano "
                     f"solo che il nome esiste",
        })

    # --- i campi che pubblichiamo sono scritti da qualcuno? ----------------
    if eta is not None and not _eta_nel_testo(eta, blob):
        if _eta_deducibile_da_categoria(eta, urls):
            rilievi.append({
                "codice": "eta_dedotta_dalla_categoria",
                "gravita": CAUTELA,
                "detta": f"i {eta} anni non sono scritti da nessuna fonte: "
                         f"coincidono con la categoria del torneo (Sub-{eta}), "
                         f"quindi sono dedotti, non osservati",
            })
        else:
            rilievi.append({
                "codice": "eta_non_scritta_da_nessuna_fonte",
                "gravita": BLOCCANTE,
                "detta": f"i {eta} anni non compaiono in nessuna fonte e non "
                         f"sono deducibili: valore senza origine",
            })

    # Club che è una DESCRIZIONE, non un nome: "Unspecified Portuguese Club",
    # "Brazilian Serie A Club". Sono entrambi reali, in produzione, su schede
    # pubblicate. Un club così non si può telefonare — ed è il gesto che il
    # prodotto promette di rendere possibile.
    if club and _CLUB_DESCRITTIVO_RE.search(_norm(club)):
        rilievi.append({
            "codice": "club_e_una_descrizione_non_un_nome",
            "gravita": BLOCCANTE,
            "detta": f"'{club}' non è un club: è una descrizione. Non esiste "
                     f"un numero da chiamare",
        })
    elif club and not (_token(club) & set(blob.split())):
        rilievi.append({
            "codice": "club_non_scritto_da_nessuna_fonte",
            "gravita": CAUTELA,
            "detta": f"'{club}' non compare in nessuna fonte: da riverificare "
                     f"prima di usarlo",
        })

    # --- la premessa del prodotto regge ancora? ---------------------------
    # OB1 Global vende ANTICIPO su nomi poco coperti. Un giocatore di un club
    # di cui scrive tutto il mondo non è una scoperta: pubblicarlo come tale
    # è un autogol, indipendentemente dal fatto che i dati siano giusti.
    if _norm(club) in {_norm(c) for c in CLUB_GIA_COPERTI}:
        rilievi.append({
            "codice": "gia_coperto_da_tutti",
            "gravita": BLOCCANTE,
            "detta": f"gioca nel {club}: di questo club scrivono tutti, quindi "
                     f"non è una scoperta early — la premessa del prodotto non "
                     f"regge, anche se i dati fossero esatti",
        })

    return rilievi


def bloccanti(rilievi: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [r for r in rilievi if r.get("gravita") == BLOCCANTE]


def sopravvive(rilievi: List[Dict[str, str]]) -> bool:
    """True se la scheda può essere pubblicata (nessun rilievo bloccante)."""
    return not bloccanti(rilievi)


# --------------------------------------------------------------- self-test
if __name__ == "__main__":
    # I casi vengono dal database di produzione reale, non inventati.

    # 1) Kevin Angulo: 2 domini, nessuna sostanza, età dedotta da "sub-17".
    ev_angulo = [
        {"raw_content": "Kevin Angulo Angulo – América S.A. (Cat Formación)",
         "source_domain": "fcf.com.co",
         "source_url": "https://fcf.com.co/2025/02/06/convocatoria-de-la-seleccion-colombia-masculina-sub-17-para-microciclo/"},
        {"raw_content": "Kévin Angulo - Player profile 26/27",
         "source_domain": "transfermarkt.com",
         "source_url": "https://www.transfermarkt.com/kevin-angulo/profil/spieler/659787"},
    ]
    r = contesta({"canonical_name": "Kevin Angulo Angulo", "age": 17,
                  "club": "América S.A."}, ev_angulo)
    codici = {x["codice"] for x in r}
    assert "nessuna_fonte_sostanziale" in codici, codici
    assert "eta_dedotta_dalla_categoria" in codici, codici
    assert not sopravvive(r), "due elenchi non possono valere come verifica"

    # 2) Yan Diomande: il club da solo smentisce la premessa del prodotto.
    r = contesta({"canonical_name": "Yan Diomande", "age": 15, "club": "RB Leipzig"},
                 [{"raw_content": "Na atual temporada foram 12 gols e nove assistências.",
                   "source_domain": "placar.com.br", "source_url": "https://placar.com.br/x"}])
    codici = {x["codice"] for x in r}
    assert "gia_coperto_da_tutti" in codici, codici
    # e la fonte non lo nomina nemmeno
    assert "nessuna_prova_nomina_il_giocatore" in codici, codici
    assert not sopravvive(r)

    # 3) Contaminazione tra persone: "Juan José" non identifica nessuno.
    assert not evidenza_parla_del_giocatore(
        "Juan José Cataño Vahos – Envigado F.C.- Inferiores", "Juan José Fori Viveros")
    assert evidenza_parla_del_giocatore(
        "Juan José Fori Viveros – C.D Estudiantil", "Juan José Fori Viveros")

    # 4) Il lato opposto — una scheda buona non deve essere bocciata.
    ev_buone = [
        {"raw_content": "Il difensore Mattia Verdi, 18 anni, ha esordito con la "
                        "prima squadra del Pescara segnando il gol del pareggio "
                        "nella sfida di ieri contro il Rimini.",
         "source_domain": "corrieredellosport.it", "source_url": "https://x.it/a"},
        {"raw_content": "Mattia Verdi resta il profilo piu' seguito del vivaio: "
                        "18 anni, quattro presenze in Serie C con il Pescara e una "
                        "convocazione in nazionale giovanile.",
         "source_domain": "tuttomercatoweb.com", "source_url": "https://y.it/b"},
    ]
    r = contesta({"canonical_name": "Mattia Verdi", "age": 18, "club": "Pescara"}, ev_buone)
    assert sopravvive(r), [x["codice"] for x in r]
    assert not r, [x["codice"] for x in r]

    # 5) Una sola fonte sostanziale: si pubblica, ma il rilievo si vede.
    r = contesta({"canonical_name": "Mattia Verdi", "age": 18, "club": "Pescara"},
                 ev_buone[:1] + [{"raw_content": "Mattia Verdi – Pescara",
                                  "source_domain": "lega-pro.com",
                                  "source_url": "https://z.it/c"}])
    assert sopravvive(r)
    assert {x["codice"] for x in r} == {"una_sola_fonte_sostanziale"}, r

    # 6) Titolo di pagina + URL non sono una fonte sostanziale: senza spogliare
    #    l'URL, le sue parole da sole superavano la soglia (caso Douglas Telles).
    assert not evidenza_e_sostanziale(
        "Douglas Telles - Player profile\nURL Source: "
        "https://www.transfermarkt.com/douglas-telles/profil/spieler/1220787",
        "Douglas Telles")

    # 7) Club che è una descrizione, non un nome (casi reali in produzione).
    for finto in ("Unspecified Portuguese Club", "Brazilian Serie A Club"):
        r = contesta({"canonical_name": "Tizio Caio", "age": 17, "club": finto},
                     [{"raw_content": f"Tizio Caio, 17 anni, ha esordito ieri "
                                      f"segnando una doppietta nel torneo giovanile.",
                       "source_domain": "a.it", "source_url": "https://a.it/1"},
                      {"raw_content": "Tizio Caio resta il piu' seguito del vivaio "
                                      "con 17 anni e quattro presenze stagionali.",
                       "source_domain": "b.it", "source_url": "https://b.it/2"}])
        assert "club_e_una_descrizione_non_un_nome" in {x["codice"] for x in r}, (finto, r)
        assert not sopravvive(r), finto

    print("OK challenge_v2: l'avvocato del diavolo boccia i casi reali di "
          "produzione e lascia passare una scheda che regge")
