#!/usr/bin/env python3
"""
OB1 v2 — L'avvocato del diavolo: perché questa scheda NON dovrebbe esistere.

Divisione del lavoro con src/claims_v2.py, che è arrivato dopo:

    claims_v2     cosa possiamo DIRE, e con quale prova, campo per campo
                  ("il club lo scrive la federazione", "l'età non la scrive
                  nessuno") — la parte affermativa
    challenge_v2  perché la scheda non dovrebbe uscire COMUNQUE, anche se
                  ogni singolo campo fosse provato — la parte negativa

Prima versione (26 ago 2026) faceva entrambe le cose, e sbagliava la prima:
bocciava come "fonte non sostanziale" la riga

    "Juan José Fori Viveros – C.D Estudiantil"          [fcf.com.co]

perché corta. È la Federazione Colombiana che certifica in una convocazione
ufficiale che quel ragazzo esiste e gioca lì: la prova d'identità più forte
che possiamo avere. Confondere "testo breve" con "prova debole" buttava via
il materiale migliore. Quei controlli sono passati a claims_v2, che guarda
la COMPETENZA della fonte invece della lunghezza del testo.

Qui restano solo i rilievi che nessuna prova per-campo può sollevare, perché
riguardano la PREMESSA della scheda, non i suoi dati:

  - un giocatore di un club di cui scrive tutto il mondo non è una scoperta
    "early", per quanto i suoi dati siano esatti (caso Yan Diomande: primo in
    vetrina, e nel frattempo era passato al Real Madrid per 140 milioni);
  - un club che è una descrizione e non un nome ("Unspecified Portuguese
    Club") non si può telefonare — ed è il gesto che il prodotto promette.

Deterministico e puro: niente rete, niente LLM, niente DB.
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
    club = giocatore.get("club") or ""
    rilievi: List[Dict[str, str]] = []

    # I rilievi su fonti e campi (quante fonti dicono qualcosa, se l'età è
    # scritta o dedotta, se il club compare nei testi) NON stanno più qui:
    # li fa src/claims_v2.py, che valuta la COMPETENZA della fonte invece
    # della lunghezza del testo. Tenerne una seconda versione qui produceva
    # il falso positivo che ha motivato lo split — una convocazione della
    # federazione bocciata come "non sostanziale" perché è una riga sola.

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
    # Dopo lo split con claims_v2, qui restano SOLO i rilievi di premessa.

    # 1) Il caso Diomande: il club da solo smentisce la promessa "early".
    #    Nessun controllo sui dati poteva prenderlo — i dati non c'entrano.
    r = contesta({"canonical_name": "Yan Diomande", "age": 19, "club": "RB Leipzig"},
                 [{"raw_content": "Yan Diomande, 19 anni, ha lasciato il RB Leipzig.",
                   "source_domain": "latimes.com", "source_url": "https://x"}])
    assert {x["codice"] for x in r} == {"gia_coperto_da_tutti"}, r
    assert not sopravvive(r)

    # 2) Club che è una descrizione: non c'è un numero da chiamare, ed è il
    #    gesto che il prodotto promette di rendere possibile.
    for finto in ("Unspecified Portuguese Club", "Brazilian Serie A Club"):
        r = contesta({"canonical_name": "Tizio Caio", "age": 17, "club": finto}, [])
        assert {x["codice"] for x in r} == {"club_e_una_descrizione_non_un_nome"}, (finto, r)
        assert not sopravvive(r)

    # 3) Il lato opposto, quello che la PRIMA versione sbagliava: una
    #    convocazione della federazione è una riga sola, e deve passare.
    #    Se questo test si rompe, siamo tornati a confondere "testo breve"
    #    con "prova debole".
    r = contesta({"canonical_name": "Juan José Fori Viveros", "age": 17,
                  "club": "C.D Estudiantil"},
                 [{"raw_content": "Juan José Fori Viveros – C.D Estudiantil",
                   "source_domain": "fcf.com.co",
                   "source_url": "https://fcf.com.co/convocatoria-sub-17"}])
    assert r == [], r
    assert sopravvive(r)

    # 4) Un club normale non fa scattare nulla.
    r = contesta({"canonical_name": "Mattia Verdi", "age": 18, "club": "Pescara"}, [])
    assert r == [], r

    print("OK challenge_v2: restano i rilievi di PREMESSA (club gia' coperto "
          "da tutti, club che e' una descrizione). Una convocazione federale "
          "passa: la qualita' della prova la valuta claims_v2.")
