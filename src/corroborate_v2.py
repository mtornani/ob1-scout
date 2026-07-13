#!/usr/bin/env python3
"""
OB1 v2 — Corroborazione attiva (Fase B3+)

Un giocatore trovato in UNA fonte primaria (es. una convocazione) resta "da
corroborare" finché non compare in una seconda fonte indipendente. Invece di
aspettare che ricompaia, lo cerchiamo attivamente su un aggregatore
(Transfermarkt / Soccerway / …): se troviamo il suo profilo, quella è la seconda
fonte — e leggendolo completiamo anche l'identità (età, club).

find_profile() usa solo la ricerca (gratis, no LLM) ed è testabile con uno
scraper finto.
"""

from urllib.parse import urlparse

AGGREGATORS = ("transfermarkt", "soccerway", "besoccer", "fbref")
PROFILE_PATHS = ("/profil/spieler/", "/spieler/", "/player/", "/players/",
                 "/giocatore/", "/jugador/", "/fiche/", "/footballer/")


def _domain(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


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
            if agg in _domain(u) and any(p in u.lower() for p in PROFILE_PATHS):
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

    async def main():
        u = await find_profile(FakeScraper(), "Tomás Martínez Rodríguez")
        print("Profilo trovato:", u)
        u2 = await find_profile(FakeScraper(), "Pirituba")  # nome singolo → None
        print("Nome singolo:", u2)

    asyncio.run(main())
