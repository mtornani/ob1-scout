#!/usr/bin/env python3
"""
OB1 v2 — Corroborazione attiva (Fase B3+)

Un giocatore trovato in UNA fonte primaria (es. una convocazione) resta "da
corroborare" finché non compare in una seconda fonte indipendente. Invece di
aspettare che ricompaia, lo cerchiamo attivamente su un aggregatore
(Transfermarkt / Soccerway / …): se troviamo il suo profilo, quella è la seconda
fonte — e leggendolo completiamo anche l'identità (età, club).

find_profile() usa solo la ricerca (gratis, no LLM) ed è testabile con uno
scraper finto. I guardrail su slug URL + età evitano omonimi adulti (es.
"Diego Reyes" pro) spacciati per under.
"""

import sys
from pathlib import Path
from urllib.parse import urlparse

# importabile sia come package sia da `python src/corroborate_v2.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.database_v2 import normalize_name

AGGREGATORS = ("transfermarkt", "soccerway", "besoccer", "fbref")
PROFILE_PATHS = ("/profil/spieler/", "/spieler/", "/player/", "/players/",
                 "/giocatore/", "/jugador/", "/fiche/", "/footballer/")

# Scarto se età estratta e età nota divergono oltre questa soglia.
AGE_TOLERANCE = 2
# Oltre questa età non è un target early-warning (profilo aggregatore sbagliato).
MAX_YOUTH_AGE = 23


def _domain(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def url_matches_name(url: str, name: str) -> bool:
    """
    True se lo slug dell'URL contiene il cognome (o ≥2 token del nome).
    Evita di aprire il primo hit generico della SERP.
    """
    name = normalize_name(name)
    tokens = [t for t in name.split() if len(t) > 2]
    if not tokens:
        return False
    try:
        path = urlparse(url).path.lower().replace("-", " ").replace("_", " ")
    except Exception:
        return False
    # Cognome (ultimo token) obbligatorio nello slug, oppure ≥2 token presenti.
    surname = tokens[-1]
    if surname in path:
        return True
    hits = sum(1 for t in tokens if t in path)
    return hits >= 2


def observation_fits_target(obs: dict, name: str, age=None, club: str = None,
                            names_match_fn=None) -> bool:
    """
    Guardrail post-estrazione: nome compatibile + età non contraddittoria.
    names_match_fn: opzionale (default match normalizzato soft).
    """
    if not obs or not isinstance(obs, dict):
        return False
    obs_name = (obs.get("name") or "").strip()
    if not obs_name:
        return False

    if names_match_fn is not None:
        if not names_match_fn(obs_name, name):
            return False
    else:
        a, b = normalize_name(obs_name), normalize_name(name)
        if not a or not b:
            return False
        if a != b and a not in b and b not in a:
            pa = {t for t in a.split() if len(t) > 2}
            pb = {t for t in b.split() if len(t) > 2}
            if not (pa and pb and len(pa & pb) >= 2):
                return False

    obs_age = obs.get("age")
    if isinstance(obs_age, int) and obs_age > MAX_YOUTH_AGE:
        # Aggregatore ha restituito un pro omonimo: non corroborare.
        if age is None or age <= MAX_YOUTH_AGE:
            return False
    if isinstance(age, int) and isinstance(obs_age, int):
        if abs(age - obs_age) > AGE_TOLERANCE:
            return False

    # Club non è hard-reject: varianti U20 / lingue / nick generano falsi negativi.
    # Nome + età bastano a scartare i pro omonimi tipici.
    return True


async def find_profile(scraper, name: str, aggregators=AGGREGATORS,
                       exclude_domains=()) -> str | None:
    """
    Cerca il profilo del giocatore su un aggregatore. Ritorna l'URL del primo
    profilo plausibile trovato, o None. `exclude_domains`: aggregatori già usati
    come fonte per questo giocatore (non ricorroborare sullo stesso dominio).
    """
    name = (name or "").strip()
    if len(name.split()) < 2:
        return None  # serve un nome completo per una ricerca affidabile
    excl = " ".join(exclude_domains).lower()
    for agg in aggregators:
        if agg in excl:
            continue
        try:
            results = await scraper.search_query(f'"{name}" {agg}')
        except Exception:
            continue
        for r in results or []:
            u = (r.get("url") or "").rstrip(".,);]")
            if agg not in _domain(u):
                continue
            if not any(p in u.lower() for p in PROFILE_PATHS):
                continue
            if not url_matches_name(u, name):
                continue
            return u
    return None


if __name__ == "__main__":
    import asyncio

    class FakeScraper:
        async def search_query(self, q):
            print("  q:", q)
            if "transfermarkt" in q:
                return [{"url": "https://www.transfermarkt.com/tomas-martinez/profil/spieler/998877"},
                        {"url": "https://www.transfermarkt.com/tomas-martinez/leistung/998877"}]
            return []

    assert url_matches_name(
        "https://www.transfermarkt.com/bruno-baldini/profil/spieler/1362669",
        "Bruno Baldini")
    assert not url_matches_name(
        "https://www.transfermarkt.com/random-player/profil/spieler/1",
        "Bruno Baldini")
    assert observation_fits_target(
        {"name": "Bruno Baldini", "age": 19, "club": "Londrina"},
        "Bruno Baldini", age=19, club="Londrina")
    assert not observation_fits_target(
        {"name": "Diego Reyes", "age": 32, "club": "Tigres"},
        "Diego Reyes", age=18, club="Flamengo U20")
    assert not observation_fits_target(
        {"name": "Gustavo Gómez", "age": 32, "club": "Palmeiras"},
        "Gustavo Gomes", age=17, club="Raio Amado")

    async def main():
        u = await find_profile(FakeScraper(), "Tomás Martínez Rodríguez")
        print("Profilo trovato:", u)
        u2 = await find_profile(FakeScraper(), "Pirituba")  # nome singolo → None
        print("Nome singolo:", u2)

    asyncio.run(main())
    print("OK guards")
