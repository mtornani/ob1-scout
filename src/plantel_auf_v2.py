#!/usr/bin/env python3
"""
OB1 v2 — Lettore delle convocazioni AUF (Uruguay), in codice.

Perché esiste
-------------
La AUF pubblica ogni convocazione giovanile con quattro campi per convocato:

    **Luis Machín** - Golero - 06/02/2010 - Nacional (URU)

NOME, RUOLO, DATA DI NASCITA, CLUB. La data di nascita scritta dalla
federazione è il dato che alla FCF colombiana — oggi la fonte da cui viene
metà di tutto ciò che pubblichiamo — non c'è. Senza, l'età si può solo
dedurre dalla categoria del torneo, e allora dire "gioca due anni sopra la
sua età" diventa circolare: l'età viene dalla categoria che si vuole
commentare. Con la data scritta, la frase regge una telefonata.

Perché in codice e non con l'LLM
--------------------------------
È una tabella a etichette fisse, come le schede Transfermarkt
(src/profilo_tm_v2.py): si legge con una regex. Vale 23 convocati per zero
chiamate al modello, contro un budget di 15 chiamate per l'INTERO giro
(INGEST_LLM_BUDGET). Un estrattore LLM su questa pagina sarebbe la spesa
più alta della pipeline per il testo più regolare che incontra.

Cosa NON fa
-----------
Non data l'evento. Il titolo dice "Convocatoria al CONMEBOL sub-17 2026
(Del 3 al 19 de abril)" e l'URL è fisso (/sub-17/), quindi non c'è una data
per convocazione da cui costruire un arco temporale: la categoria si legge,
il mese no. La persistenza (src/selezione_v2.py) conterà queste come
convocazioni senza data, che è la verità.

    python src/plantel_auf_v2.py     # autotest sulla pagina vera del 1 set 2026
"""

import re
from datetime import date
from typing import Any, Dict, List, Optional

# Il vocabolario dei ruoli che la AUF usa davvero, misurato sulle tre pagine
# di categoria (sub-15, sub-17, sub-20) il 1 set 2026. È chiuso apposta: una
# riga in grassetto che non dichiari uno di questi quattro ruoli non è un
# convocato, e non deve poter entrare. Stessa scelta del controllo di forma
# sui nomi di club in Lega Pro — dire che forma ha un dato buono, invece di
# elencare le forme sbagliate viste finora.
RUOLI = {
    "golero": "Portiere",
    "defensa": "Difensore",
    "mediocampista": "Centrocampista",
    "delantero": "Attaccante",
}

# **Nome** - Ruolo - dd/mm/aaaa - Club (URU)
# La data è opzionale: sulla pagina sub-17 del 1 set 2026 un convocato
# (Vicente Pesce) non ce l'ha. Senza il gruppo opzionale quella riga si
# sarebbe letta storta invece di leggersi a metà.
_RE_CONVOCATO = re.compile(
    r"\*\*(?P<nome>[^*\n]{3,60}?)\*\*\s*-\s*"
    r"(?P<ruolo>Golero|Defensa|Mediocampista|Delantero)\s*-\s*"
    r"(?:(?P<nascita>\d{2}/\d{2}/\d{4})\s*-\s*)?"
    r"(?P<club>[^\n(]{2,40}?)\s*\((?P<paese>[A-Z]{3})\)",
    re.IGNORECASE,
)

_RE_TITOLO = re.compile(r"^##\s*Plantel\s*\n+([^\n]+)", re.MULTILINE)


def _eta(nascita: str, oggi: Optional[date] = None) -> Optional[int]:
    """Anni compiuti oggi da una data dd/mm/aaaa. None se non è una data."""
    try:
        g, m, a = (int(x) for x in nascita.split("/"))
        nato = date(a, m, g)
    except (ValueError, TypeError):
        return None
    oggi = oggi or date.today()
    anni = oggi.year - nato.year - ((oggi.month, oggi.day) < (nato.month, nato.day))
    return anni if 8 <= anni <= 60 else None


def titolo_convocazione(testo: str) -> str:
    """"Convocatoria al CONMEBOL sub-17 2026 (Del 3 al 19 de abril)", o ""."""
    m = _RE_TITOLO.search(testo or "")
    return m.group(1).strip() if m else ""


def leggi_plantel(testo: str, url: str = "",
                  oggi: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    I convocati di una pagina di selezione AUF, nella stessa forma che
    produce l'estrattore LLM — così a valle non cambia niente.

    Lista vuota se la pagina non è una convocazione: è una risposta, non un
    fallimento. Chi chiama non deve poter confondere "non ci sono convocati"
    con "non ho saputo leggere".
    """
    if not testo:
        return []
    titolo = titolo_convocazione(testo)
    fuori: List[Dict[str, Any]] = []
    visti = set()

    for m in _RE_CONVOCATO.finditer(testo):
        nome = m.group("nome").strip()
        chiave = nome.lower()
        # La pagina elenca la rosa due volte, in due formati diversi (schede
        # con foto e poi lista piatta). È lo stesso convocato: si tiene una
        # volta sola, o la persistenza conterebbe due convocazioni per una.
        if chiave in visti:
            continue
        visti.add(chiave)

        nascita = m.group("nascita")
        eta = _eta(nascita, oggi) if nascita else None
        club = m.group("club").strip(" -–—")
        ruolo = RUOLI[m.group("ruolo").lower()]

        # La citazione-prova deve CONTENERE la data di nascita: è il testo su
        # cui claims_v2._prova() verifica che la federazione l'abbia scritta
        # davvero, invece di fidarsi del numero che le abbiamo calcolato noi.
        pezzi = [nome, ruolo]
        if nascita:
            pezzi.append(f"nato il {nascita}")
        pezzi.append(club)
        citazione = (f"AUF — {titolo}: " if titolo else "AUF: ") + ", ".join(pezzi)

        fuori.append({
            "name": nome,
            "age": eta,
            "club": club or None,
            "nationality": "Uruguay",
            "position": ruolo,
            "league": None,
            "gender": "unknown",
            "stats": {},
            "evidence_quote": citazione[:220],
            "source_url": url,
        })
    return fuori


if __name__ == "__main__":
    # Autotest sulla forma vera, copiata dalla pagina /sub-17/ del 1 set 2026.
    PAGINA = """## Plantel

Convocatoria al CONMEBOL sub-17 2026 (Del 3 al 19 de abril)

### Luis Machín

#### Golero - 06/02/2010

##### [Nacional (URU)](https://www.nacional.uy/)

**Luis Machín** - Golero - 06/02/2010 - Nacional (URU)

* * *

**Anderson Luz** - Mediocampista - 19/09/2009 - M. City Torque (URU)

* * *

**Vicente Pesce** - Delantero - Paysandú F.C. (URU)

* * *

**Ignacio González** - Director Técnico - 14/03/1982 - AUF (URU)
"""
    OGGI = date(2026, 9, 1)
    rosa = leggi_plantel(PAGINA, "https://www.auf.org.uy/sub-17/", OGGI)

    assert titolo_convocazione(PAGINA).startswith("Convocatoria al CONMEBOL sub-17")
    assert len(rosa) == 3, f"attesi 3 convocati, letti {len(rosa)}"

    m = rosa[0]
    assert m["name"] == "Luis Machín" and m["age"] == 16, m
    assert m["club"] == "Nacional" and m["position"] == "Portiere", m
    # la data DEVE stare nella citazione: senza, claims_v2 non può provare l'età
    assert "06/02/2010" in m["evidence_quote"], m["evidence_quote"]
    assert "sub-17" in m["evidence_quote"]

    # il doppio elenco non deve contare due volte lo stesso convocato
    assert [r["name"] for r in rosa].count("Luis Machín") == 1

    # senza data di nascita: si legge lo stesso, ma l'età resta ignota
    p = [r for r in rosa if r["name"] == "Vicente Pesce"][0]
    assert p["age"] is None and p["club"] == "Paysandú F.C.", p
    assert "nato il" not in p["evidence_quote"]

    # il commissario tecnico non è un convocato
    assert not [r for r in rosa if "González" in r["name"]], "lo staff non entra"

    # una pagina che non è una convocazione risponde [], non rumore
    assert leggi_plantel("## Noticias\n\nUruguay ganó 2-0.", "") == []
    assert leggi_plantel("", "") == []

    print(f"OK — {len(rosa)} convocati letti, zero chiamate al modello")
    for r in rosa:
        print(f"  {r['name']:22} {str(r['age'] or '?'):>3}  {r['position']:14} {r['club']}")
