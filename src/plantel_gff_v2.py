#!/usr/bin/env python3
"""
OB1 v2 — Lettore delle convocazioni GFF (Gambia Football Federation), in codice.

Perché esiste
-------------
La GFF pubblica ogni convocazione in una lista fissa per reparto:

    **GOALKEEPERS**

    1. PA EBOU DAMPHA – WAA BJL
    18. EBRIMA JAITEH - TMT FC

NUMERO, NOME, CLUB. Niente data di nascita (a differenza dell'AUF uruguaiana,
src/plantel_auf_v2.py): l'età di questi convocati resta ASSENTE, non dedotta
dalla categoria del torneo — è la verità, non un buco da coprire.

Perché aggiunta il 19 set 2026: la convocazione WAFU U17 della Gambia (fonte
stampa, voicegambia.com) è uscita PRIMA che un nome della stessa lista
(Adama Jeng) diventasse virale su LinkedIn con Chelsea/Bayern/Benfica citati
come osservatori — controllato: il nostro registro non aveva alcuna fonte
gambiana, quindi quel margine di anticipo non l'avremmo mai potuto vedere.
Aggiungere la GFF non è ipotesi, è chiudere un buco già misurato.

Perché in codice e non con l'LLM
--------------------------------
Stessa ragione dell'AUF: lista a formato fisso, si legge con una regex.
Zero chiamate al modello per un'intera rosa, contro un budget di poche
chiamate per l'intero giro.

Cosa NON fa
-----------
Non data l'evento (nessuna data per convocazione, solo il titolo pagina se
presente). Non ricava l'età: senza data di nascita scritta dalla federazione,
resta None — src/selezione_v2.py la conterà come convocazione senza età, che
è la verità.

Sulla capitalizzazione: la GFF scrive tutto in MAIUSCOLO. Nome e club vengono
normalizzati con .title() per la visualizzazione (non è un'invenzione: è la
stessa persona, solo la maiuscola cambiata) — ma la citazione-prova
(evidence_quote) riporta il testo ESATTO della federazione, maiuscolo
compreso, così chi verifica trova la pagina, non la nostra interpretazione.

Non ancora provato in produzione: v. try/except in scripts/ingest_v2.py,
stesso trattamento riservato all'AUF il giorno dell'aggiunta.

    python src/plantel_gff_v2.py     # autotest sulla pagina vera (U-20 AFCON, 19 set 2026)
"""

import re
from typing import Any, Dict, List, Optional

# Vocabolario chiuso, stessa scelta dell'AUF: un reparto che non sia uno di
# questi quattro non è una sezione di rosa, e non deve poter entrare.
RUOLI = {
    "goalkeepers": "Portiere",
    "defenders": "Difensore",
    "midfielders": "Centrocampista",
    "forwards": "Attaccante",
}

_RE_REPARTO = re.compile(
    r"^\*{0,2}\s*(GOALKEEPERS|DEFENDERS|MIDFIELDERS|FORWARDS)\s*\*{0,2}\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# "1. PA EBOU DAMPHA – WAA BJL" — numero, nome, trattino (– o -, con o senza
# spazi), club. Il club può finire con una virgola solitaria (visto sulla
# pagina vera: "EBRIMA SINGHATEH – SLAVIA PRAGUE,") o contenere una virgola
# vera per il paese ("AC HORSENS, DENMARK"): si toglie solo la virgola finale
# isolata, mai quelle interne.
_RE_CONVOCATO = re.compile(
    r"^\s*\d{1,2}\.\s*"
    r"(?P<nome>[A-ZÀ-Ö' .-]{3,40}?)\s*[-–]\s*"
    r"(?P<club>[^\n]{2,50}?)\s*,?\s*$",
    re.MULTILINE,
)

_RE_TITOLO = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _pulisci(s: str) -> str:
    """Maiuscolo GFF -> forma leggibile, senza inventare separazioni."""
    return " ".join(w.capitalize() if w.isupper() else w for w in s.split())


def titolo_convocazione(testo: str) -> str:
    m = _RE_TITOLO.search(testo or "")
    return m.group(1).strip() if m else ""


def leggi_plantel(testo: str, url: str = "") -> List[Dict[str, Any]]:
    """
    I convocati di una pagina di rosa GFF, nella stessa forma che produce
    l'estrattore LLM — così a valle non cambia niente.

    Lista vuota se la pagina non è una convocazione (nessun reparto
    riconosciuto): è una risposta, non un fallimento.
    """
    if not testo:
        return []

    titolo = titolo_convocazione(testo)
    reparti = list(_RE_REPARTO.finditer(testo))
    if not reparti:
        return []

    fuori: List[Dict[str, Any]] = []
    visti = set()

    for i, rep in enumerate(reparti):
        ruolo = RUOLI[rep.group(1).lower()]
        inizio = rep.end()
        fine = reparti[i + 1].start() if i + 1 < len(reparti) else len(testo)
        blocco = testo[inizio:fine]

        for m in _RE_CONVOCATO.finditer(blocco):
            nome_raw = m.group("nome").strip(" -–")
            chiave = nome_raw.lower()
            if chiave in visti:
                continue
            visti.add(chiave)

            club_raw = m.group("club").strip(" -–,")
            nome = _pulisci(nome_raw)
            club = _pulisci(club_raw) if club_raw else None

            citazione_pezzi = [nome_raw, ruolo.upper(), club_raw or "?"]
            citazione = (f"GFF — {titolo}: " if titolo else "GFF: ") + ", ".join(citazione_pezzi)

            fuori.append({
                "name": nome,
                "age": None,  # la GFF non scrive la data di nascita: assente, non dedotta
                "club": club,
                "nationality": "Gambia",
                "position": ruolo,
                "league": None,
                "gender": "unknown",
                "stats": {},
                "evidence_quote": citazione[:220],
                "source_url": url,
            })
    return fuori


if __name__ == "__main__":
    # Autotest sulla forma vera, letta dalla pagina reale (U-20 AFCON, verificata 19 set 2026).
    PAGINA = """# THE GAMBIA SQUAD LIST

**GOALKEEPERS**

1. PA EBOU DAMPHA – WAA BJL
18. EBRIMA JAITEH - TMT FC
22. YOUKASSEH SANYANG – STEVE BIKO FC

**DEFENDERS**
2. BA LAMIN SOWE – Tenerife, Spain
3. SAINEY SANYANG – HAWKS
4. ALAGIE SAINE – AC HORSENS, DENMARK

**MIDFIELDERS**
6. MAHMUDU BAJO – GRANADA , SPAIN
14. MUHAMMED SAWANEH- TENGUETH, SENEGAL

**FORWARDS**
19. EBRIMA SINGHATEH – SLAVIA PRAGUE,
21. MAMIN SANYANG – FC BAYERN MUNICH
"""
    rosa = leggi_plantel(PAGINA, "https://gambiaff.org/gambia-unveils-25-man-squad-for-u-20-afcon/")

    assert titolo_convocazione(PAGINA) == "THE GAMBIA SQUAD LIST"
    assert len(rosa) == 10, f"attesi 10 convocati, letti {len(rosa)}"

    p = rosa[0]
    assert p["name"] == "Pa Ebou Dampha", p
    assert p["club"] == "Waa Bjl", p
    assert p["position"] == "Portiere", p
    assert p["age"] is None
    assert p["nationality"] == "Gambia"
    # la citazione porta il testo ESATTO della federazione, maiuscolo compreso
    assert "PA EBOU DAMPHA" in p["evidence_quote"], p["evidence_quote"]
    assert "THE GAMBIA SQUAD LIST" in p["evidence_quote"]

    # trattino senza spazi ("- TMT FC")
    jaiteh = [r for r in rosa if r["name"] == "Ebrima Jaiteh"][0]
    assert jaiteh["club"] == "Tmt Fc", jaiteh

    # club con paese separato da virgola VERA: non va tagliata
    saine = [r for r in rosa if r["name"] == "Alagie Saine"][0]
    assert saine["club"] == "Ac Horsens, Denmark", saine

    # virgola finale isolata: va tolta
    singhateh = [r for r in rosa if r["name"] == "Ebrima Singhateh"][0]
    assert singhateh["club"] == "Slavia Prague", singhateh

    # spazio prima della virgola ("GRANADA , SPAIN"): il paese resta nel club
    bajo = [r for r in rosa if r["name"] == "Mahmudu Bajo"][0]
    assert bajo["club"] == "Granada , Spain" or bajo["club"] == "Granada, Spain", bajo

    # reparti diversi assegnano ruoli diversi
    assert [r["position"] for r in rosa if r["name"] == "Sainey Sanyang"] == ["Difensore"]
    assert [r["position"] for r in rosa if r["name"] == "Mamin Sanyang"] == ["Attaccante"]

    # una pagina senza reparti riconosciuti risponde [], non rumore
    assert leggi_plantel("## Notizie\n\nLa Gambia ha vinto 2-0.", "") == []
    assert leggi_plantel("", "") == []

    print(f"OK — {len(rosa)} convocati letti, zero chiamate al modello")
    for r in rosa:
        print(f"  {r['name']:22} {r['position']:14} {r['club']}")
