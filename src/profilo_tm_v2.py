#!/usr/bin/env python3
"""
OB1 v2 — Leggere una scheda Transfermarkt senza chiamare un modello

Perché (misurato il 27 ago 2026 sul DB di produzione)
------------------------------------------------------
La corroborazione cerca una seconda fonte per i profili a una fonte sola,
apre la scheda che trova e la passa all'estrattore LLM. Contate le chiamate
vere (un articolo = una chiamata) su 301 totali in archivio:

    89 chiamate su pagine "a template"  →  di cui 70 su transfermarkt.*
    212 chiamate su prosa

Cioè il 23% di tutte le chiamate LLM mai fatte è servito a leggere una
scheda Transfermarkt. E si vede da cosa ne usciva: le citazioni-prova
salvate a fronte di quelle chiamate sono

    "Mateus Romero - Player profile"
    "Kauan Toledo - Player profile"

il titolo della pagina, ricopiato. Non c'è comprensione da fare: la scheda
è una tabella con etichette fisse. Questo modulo la legge in codice.

Cosa NON fa
-----------
Non sostituisce l'estrattore: se la pagina non ha i campi attesi (redesign
del sito, pagina d'errore, TLD con layout diverso) ritorna None e chi chiama
ripiega sull'LLM. Stessa rete di sicurezza di _da_indice_sito → _da_ricerca
in sources_v2: un parser che non aggancia deve degradare al comportamento di
prima, non a zero. È il prezzo di ammettere che i parser sono fragili.

Puro: nessuna rete. Il testo glielo passa chi l'ha già scaricato.

Test: python src/profilo_tm_v2.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Le etichette che delimitano un campo. Servono come "fermata" perché nel
# markdown di Jina i campi a volte si attaccano SENZA a capo — visto davvero
# su Fode Diallo: "Name in home country:Fode Diallo Conde Date of birth/Age:
# [06/03/2012 (14)]". Fermarsi al newline lì avrebbe inghiottito la data
# dentro il nome.
_ETICHETTE = (
    "Date of birth", "Place of birth", "Citizenship", "Height", "Position",
    "Foot", "Player agent", "Current club", "Joined", "Contract expires",
    "Name in home country", "Full name", "Outfitter", "Social",
)
_STOP = "|".join(re.escape(e) for e in _ETICHETTE)

# L'ETÀ è il campo che conta davvero: observation_fits_target la usa per
# scartare l'omonimo professionista (MAX_YOUTH_AGE). Su TM sta sempre fra
# parentesi subito dopo la data di nascita, e quello resta identico anche
# quando il FORMATO della data cambia col dominio — verificato il 27 ago
# 2026: "15/04/2006 (20)" su .com, "Jun 14, 2006 (20)" su .us. Ancorarsi
# all'età in parentesi invece che al formato data è ciò che rende il parser
# indipendente dal TLD.
_RE_ETA = re.compile(r"Date of birth/Age:\s*\[?[^()\[\]]{4,24}\((\d{1,2})\)")

# Nome ufficiale nel paese d'origine: quando c'è è migliore del titolo,
# perché porta il nome completo ("Óscar Gómez Marcos" invece di "Óscar
# Gómez") — e il gate d'identità di OB1 conta i token del nome.
_RE_NOME_LOCALE = re.compile(rf"Name in home country:\s*(.+?)\s*(?:{_STOP}|$)")

# Ripiego: il titolo della pagina. "Óscar Gómez - Player profile 26/27" —
# la coda con la stagione va tolta.
_RE_TITOLO = re.compile(r"Title:\s*(.+?)\s*[-–]\s*Player profile", re.I)

# Club e cittadinanza vivono nel testo alternativo di un'immagine
# (![Image 27: Atlético Mineiro U20]), non come testo semplice.
_RE_CLUB = re.compile(r"Current club:\s*\[?!?\[Image\s*\d+:\s*([^\]]+)\]")
_RE_NAZIONE = re.compile(r"Citizenship:\s*!?\[Image\s*\d+:\s*([^\]]+)\]")
_RE_RUOLO = re.compile(rf"Position:\s*(.+?)\s*(?:{_STOP}|$)", re.M)

# Jina Reader risponde 200 anche quando il sito ha dato 404: l'errore vero
# finisce dentro il testo. Stessa firma già gestita in sources_v2 — qui
# serve di nuovo perché una pagina d'errore NON deve diventare un profilo
# vuoto ma "plausibile".
_RE_ERRORE = re.compile(r"Warning: Target URL returned error \d")


def _pulisci(s: str) -> str:
    """Via i resti di markdown (link, immagini, grassetto) e gli spazi doppi."""
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s or "")
    s = s.replace("*", "").replace("[", "").replace("]", "")
    return re.sub(r"\s+", " ", s).strip(" :–-")


def leggi_profilo(testo: str, url: str = "") -> dict:
    """
    Un'osservazione nello stesso schema che produce l'estrattore LLM
    (name/age/club/nationality/position/evidence_quote/source_url), oppure
    None se la pagina non è una scheda giocatore leggibile.

    None significa "non lo so", e chi chiama deve trattarlo come tale:
    ripiegare sull'LLM, non concludere che il giocatore non esiste. È la
    distinzione che in questa base di codice era già costata una misura
    gonfiata (verifica TM su Lega Pro, 26 ago 2026).
    """
    if not testo or _RE_ERRORE.search(testo[:400]):
        return None

    m_eta = _RE_ETA.search(testo)
    nome = None
    m_nome = _RE_NOME_LOCALE.search(testo)
    if m_nome:
        nome = _pulisci(m_nome.group(1))
    if not nome:
        m_tit = _RE_TITOLO.search(testo)
        if m_tit:
            nome = _pulisci(m_tit.group(1))

    # Senza nome non c'è osservazione; senza età non si può scartare
    # l'omonimo professionista, che è l'unica cosa per cui la corroborazione
    # guarda questa pagina. In entrambi i casi meglio l'LLM che un'ipotesi.
    if not nome or not m_eta:
        return None

    club = _pulisci(_RE_CLUB.search(testo).group(1)) if _RE_CLUB.search(testo) else None
    naz = _pulisci(_RE_NAZIONE.search(testo).group(1)) if _RE_NAZIONE.search(testo) else None
    m_ruolo = _RE_RUOLO.search(testo)
    ruolo = _pulisci(m_ruolo.group(1)) if m_ruolo else None

    return {
        "name": nome,
        "age": int(m_eta.group(1)),
        "club": club or None,
        "nationality": naz or None,
        "position": ruolo or None,
        "league": None,
        "gender": "unknown",
        "stats": {},
        # La citazione-prova dice COSA abbiamo letto e dove, non ricopia il
        # titolo della pagina come faceva l'LLM su queste stesse schede.
        "evidence_quote": (f"Scheda Transfermarkt: {nome}, {m_eta.group(1)} anni"
                           + (f", {club}" if club else ""))[:200],
        "source_url": url,
    }


def e_scheda_tm(url: str) -> bool:
    """True per le URL di scheda giocatore Transfermarkt, su qualunque TLD
    (verificato in archivio: .com, .us, .es, .de, .pl, .pe — 70 chiamate LLM
    distribuite su sei domini diversi dello stesso sito)."""
    return bool(re.search(r"transfermarkt\.[a-z.]{2,6}/[^/]+/profil/spieler/\d+", url or ""))


# ------------------------------------------------------------------ test

def _test() -> None:
    # Frammenti CATTURATI DAL VIVO il 27 ago 2026 (via Jina Reader) dalle
    # schede che avevano davvero consumato chiamate LLM in produzione. Non
    # inventati: le differenze fra loro sono le differenze vere del sito.
    com = ("Title: Kauan Toledo - Player profile\n"
           "*   Date of birth/Age:  15/04/2006 (20)\n"
           "*   Citizenship: ![Image 24: Brazil](https://img.a/flagge.png)\n"
           "*   Position:  Right Winger\n"
           "*   Current club: [![Image 28: Botafogo FR U20](https://img.a/w.png)](/x)\n")
    o = leggi_profilo(com, "https://www.transfermarkt.com/kauan-toledo/profil/spieler/1096605")
    assert o["name"] == "Kauan Toledo", o
    assert o["age"] == 20, o
    assert o["club"] == "Botafogo FR U20", o
    assert o["nationality"] == "Brazil", o
    assert o["position"] == "Right Winger", o

    # Stesso sito, TLD .us: la DATA cambia formato (mese in lettere). Se il
    # regex si fosse ancorato a gg/mm/aaaa qui sarebbe tornato None e
    # avremmo continuato a pagare l'LLM su un dominio su sei.
    us = ("Title: Mateus Romero - Player profile\n"
          "*   Date of birth/Age:  Jun 14, 2006 (20)\n"
          "*   Citizenship: ![Image 24: Brazil](https://img.a/f.png)\n"
          "*   Current club: [![Image 27: Atlético Mineiro U20](https://img.a/w.png)](/x)\n")
    o = leggi_profilo(us, "https://www.transfermarkt.us/mateus-romero/profil/spieler/1224587")
    assert o["name"] == "Mateus Romero" and o["age"] == 20, o
    assert o["club"] == "Atlético Mineiro U20", o

    # Campi ATTACCATI senza a capo, e "Name in home country" presente: il
    # nome completo va preferito al titolo (il gate conta i token del nome),
    # ma non deve inghiottire la data che lo segue.
    attaccato = ("Title: Fode Diallo - Player profile\n"
                 "Name in home country:Fode Diallo Conde Date of birth/Age:"
                 "[06/03/2012 (14)](https://www.transfermarkt.com/x)\n"
                 "*   Citizenship: ![Image 28: Spain](https://img.a/f.png)\n"
                 "*   Current club: [![Image 33: FC Barcelona Youth](https://img.a/w.png)](/x)\n")
    o = leggi_profilo(attaccato, "https://www.transfermarkt.com/fode-diallo/profil/spieler/1527842")
    assert o["name"] == "Fode Diallo Conde", o
    assert o["age"] == 14, o

    # Titolo con la stagione in coda: va tolta, o il nome non combacia più.
    o = leggi_profilo("Title: Óscar Gómez - Player profile 26/27\n"
                      "*   Date of birth/Age:  13/07/2000 (26)\n", "")
    assert o["name"] == "Óscar Gómez" and o["age"] == 26, o

    # --- i casi in cui DEVE dire "non lo so" e lasciare la parola all'LLM ---
    assert leggi_profilo("", "") is None
    assert leggi_profilo("Warning: Target URL returned error 404: Not Found", "") is None, \
        "una pagina d'errore non è un profilo vuoto"
    # Nome ma nessuna età: senza età non si può scartare l'omonimo
    # professionista, che è l'unico motivo per cui apriamo questa pagina.
    assert leggi_profilo("Title: Tizio Caio - Player profile\n", "") is None, \
        "senza età meglio l'LLM che un'osservazione non verificabile"
    # Una pagina qualunque del sito che non è una scheda
    assert leggi_profilo("Title: Transfermarkt - Market values\n", "") is None

    # --- la GIUNTURA, non solo la parte (run #187, 27 ago 2026) ---
    # Questo parser ha bisogno della pagina INTERA. deep_read_urls in
    # scraper_global taglia a 1500 caratteri, perché quel testo è destinato
    # al modello; ma su Transfermarkt i primi 1500 sono tutti menu di
    # navigazione e il blocco dati comincia intorno al carattere 4900.
    # Collegato per sbaglio al testo troncato, il parser rispondeva None su
    # OGNI scheda: nel primo run dopo il merge corr_via_parser era 0 su 5,
    # con una scheda TM davvero corroborata in quel run. Il parser era
    # giusto e la giuntura sbagliata — testata la parte, non il punto in cui
    # si incastra. Chi lo ricollega deve passargli read_raw(), non
    # deep_read_urls().
    menu = "[NAVIGAZIONE](https://www.transfermarkt.com/x) " * 200   # ~2000 char
    assert len(menu) > 1500
    pagina = ("Title: Kauan Toledo - Player profile\n" + menu +
              "*   Date of birth/Age:  15/04/2006 (20)\n")
    assert leggi_profilo(pagina[:1500], "") is None, \
        "col taglio a 1500 il parser non vede i dati: e' il bug del run #187"
    o = leggi_profilo(pagina, "")
    assert o and o["age"] == 20, \
        f"sulla pagina intera deve leggere: {o}"

    # e_scheda_tm su tutti i TLD visti in archivio
    for tld in ("com", "us", "es", "de", "pl", "pe"):
        assert e_scheda_tm(f"https://www.transfermarkt.{tld}/x/profil/spieler/1"), tld
    assert not e_scheda_tm("https://www.transfermarkt.com/navigation/marktwerte")
    assert not e_scheda_tm("https://www.soccerway.com/player/x/")
    assert not e_scheda_tm("")

    print("OK profilo_tm_v2: legge le schede TM (.com/.us, campi attaccati, "
          "titolo con stagione) e dice 'non lo so' quando non può leggerle")


if __name__ == "__main__":
    _test()
