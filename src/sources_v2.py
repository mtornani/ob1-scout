#!/usr/bin/env python3
"""
OB1 v2 — Monitor fonti (Fase B2)

Discovery SOURCE-FIRST: invece di cercare giocatori a caso, si monitorano le
fonti curate del registro (config/sources.json), si scoprono gli articoli NUOVI
(delta), e solo quelli finiscono all'estrattore. La ricerca generica resta
declassata a scoperta di fonti nuove, non lavoro quotidiano.

discover_item_urls() è codice puro e testabile senza rete.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

CONFIG = Path(__file__).parent.parent / "config" / "sources.json"

# Link plausibilmente "articolo/profilo" (hanno un path con slug lungo o data),
# non home/tag/social.
_ARTICLE_HINT = re.compile(r"/(\d{4}|noticias?|news|jugador|player|spieler|"
                           r"giocatore|profil|notizie|artic|story|match)", re.I)
_SKIP_HINT = re.compile(r"(facebook|twitter|instagram|youtube|tiktok|whatsapp|"
                        r"linkedin|/tag/|/category/|mailto:|"
                        r"//api\.|/images?/|/img/|/assets/|/static/|/uploads/|"
                        r"/portaldeclubes|/socios|/tienda|/mayores|/senior|"
                        r"\.(jpg|jpeg|png|gif|webp|svg|pdf|css|js|ico)(\?|$))", re.I)


def load_registry(path: Path = CONFIG, only_active: bool = True) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    src = data.get("sources", [])
    return [s for s in src if s.get("active")] if only_active else src


def _domain(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def discover_item_urls(markdown: str, source_url: str = "", max_items: int = 25) -> list:
    """
    Estrae dai contenuti di una pagina-fonte (markdown di Jina Reader) i link
    plausibili ad articoli/profili. Preferisce lo stesso dominio della fonte.
    Codice puro: testabile passando testo, senza rete.
    """
    if not markdown:
        return []
    base_dom = _domain(source_url)
    urls = re.findall(r"\((https?://[^)\s]+)\)", markdown)      # link markdown
    urls += re.findall(r"(?<![(\w])(https?://[^\s)]+)", markdown)  # link nudi

    seen, out = set(), []
    for u in urls:
        u = u.rstrip(".,);]")
        if u in seen or _SKIP_HINT.search(u):
            continue
        seen.add(u)
        # Scarta le homepage / root di sezione (path vuoto o troppo corto):
        # un articolo/profilo ha uno slug, non "dominio.it/".
        path = urlparse(u).path.strip("/")
        if len(path) < 8:
            continue
        same_dom = base_dom and base_dom in _domain(u)
        if same_dom or _ARTICLE_HINT.search(u):
            out.append(u)
        if len(out) >= max_items:
            break
    return out


# Termini "giovanili" per lingua, per la ricerca ristretta al dominio.
YOUTH_TERMS = {
    "es": "juvenil sub-20 sub-17 cantera promesa",
    "pt": "juvenil sub-20 sub-17 base revelação",
    "en": "youth U20 U17 academy prospect",
    "fr": "jeune U20 U17 espoir formation",
    "sr": "omladinac U19 U17 talenat",
    "hr": "mladi U19 U17 talent",
}


class SourceMonitor:
    """Scoperta articoli nuovi per una fonte (delta) + fetch via Jina."""

    JINA = "https://r.jina.ai/"

    def __init__(self, db, scraper=None):
        self.db = db            # OB1DatabaseV2 (per il delta seen_items)
        self.scraper = scraper  # AsyncGlobalScraper (search + deep_read), opzionale

    async def new_items(self, source: dict) -> list:
        """
        URL articolo NUOVI per questa fonte. Discovery robusta: ricerca ristretta
        al dominio (site:) con termini giovanili nella lingua della fonte — così
        si trovano gli articoli veri anche su siti JS-pesanti dove lo scraping
        della homepage non elenca i link. Le pagine di homepage/asset restano
        filtrate. Gli aggregatori (tier secondary) non si spazzano in discovery.
        """
        if self.scraper is None or source.get("tier") == "secondary":
            return []
        dom = _domain(source["url"])
        terms = YOUTH_TERMS.get(source.get("lang"), YOUTH_TERMS["en"])
        query = f"site:{dom} {terms} 2026"

        results = await self.scraper.search_query(query)
        found = []
        for r in results:
            u = (r.get("url") or "").rstrip(".,);]")
            if not u or _SKIP_HINT.search(u):
                continue
            if dom not in _domain(u):
                continue
            if len(urlparse(u).path.strip("/")) < 8:   # scarta root/homepage
                continue
            found.append(u)
        found = list(dict.fromkeys(found))
        return self.db.filter_new_items(source["id"], found)


if __name__ == "__main__":
    reg = load_registry()
    from collections import Counter
    by_region = Counter(s["region"] for s in reg)
    print(f"Fonti attive: {len(reg)}")
    for region, n in by_region.most_common():
        print(f"  {region:14s} {n}")
