#!/usr/bin/env python3
"""
OB1 v2 — Jina Search ricaricato aiuta le fonti generaliste/confederazioni?

Perché (27 ago 2026)
---------------------
La ricarica del credito Jina ha spento il 402 (saldo esaurito): il run di
produzione #184, il primo dopo la ricarica, è tornato 422 ("nessun
risultato per questa query"), non più 402. Ma l'unica query tentata in
quel run (site:afa.com.ar ...) non ha prodotto niente, e i pubblicabili
sono rimasti identici al run precedente (87). Non basta per concludere che
la ricarica non serve a niente: quella query era già stata provata prima
della ricarica (stesso esito nullo), quindi non è un test dell'ipotesi che
serve davvero verificare.

L'ipotesi da testare è un'altra. impronta_fonti.py (PR #38) ha misurato
che alcune fonti hanno un indice leggibile ma NON filtrato topicamente:
restituiscono tutto il sito (menu, pagine istituzionali), non solo calcio
giovanile. cafonline.com (confederazione), nation.africa e
astanatimes.com (stampa generalista) sono in questo gruppo. Per loro
l'indice grezzo (INDEX_PATHS via Jina Reader) non è lo strumento giusto:
serve proprio la ricerca per rilevanza (site: + termini giovanili) che
_da_ricerca() usa già per le fonti senza indice — ma prima della ricarica
Jina Search era morto al 100%, quindi l'ipotesi non era testabile.

Cosa fa questo script
----------------------
Chiama search_query() — STESSO metodo, STESSA chiave, STESSO codice della
pipeline (src/scraper_global.py, la via primaria di _da_ricerca() in
src/sources_v2.py) — sulle query esatte che la pipeline genererebbe per
questi tre domini, e stampa cosa torna: quanti risultati, quali URL/titoli.

NON scrive nel DB, NON tocca la pipeline di produzione, NON consuma
budget LLM: è una ricognizione manuale una tantum, non un run di ingest.
Va lanciato dove JINA_API_KEY è disponibile (CI, non in locale).

Uso
---
    python scripts/test_ricerca_generaliste.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.scraper_global import AsyncGlobalScraper
from src.sources_v2 import YOUTH_TERMS

# Le tre fonti segnalate da impronta_fonti.py (PR #38) come "leggibili ma
# non filtrate topicamente": hanno un indice via INDEX_PATHS, ma quell'
# indice riporta il sito intero, non solo contenuto calcistico giovanile.
# Stessa lang="en" per tutte e tre in config/sources.json -> stessi termini.
DOMINI = [
    ("cafonline.com", "en"),
    ("nation.africa", "en"),
    ("astanatimes.com", "en"),
]


async def main() -> int:
    scraper = AsyncGlobalScraper()
    if not scraper.jina_api_key:
        print("JINA_API_KEY assente: qui questo test non dice niente "
              "sul canale Jina Search (ripiegherebbe su ddgs/SearXNG, "
              "canali diversi da quello in produzione). Va lanciato dove "
              "la chiave c'è.")
        return 1

    print(f"Jina Search: chiave presente. {len(DOMINI)} domini da testare.\n")

    for dom, lang in DOMINI:
        terms = YOUTH_TERMS.get(lang, YOUTH_TERMS["en"])
        query = f"site:{dom} {terms} 2026"
        print(f"--- {dom} ---")
        print(f"query: {query!r}")
        results = await scraper.search_query(query)
        print(f"risultati: {len(results)}")
        for r in results[:5]:
            print(f"  · {r.get('title', '')[:80]!r}  {r.get('url', '')}")
        if scraper.last_jina_http_error:
            print(f"  ultimo errore HTTP Jina Search: "
                  f"{scraper.last_jina_http_error[:200]}")
            scraper.last_jina_http_error = None  # non ripetere fra un dominio e l'altro
        print()

    print("=== riepilogo ===")
    print(f"  jina_attempts: {scraper.jina_attempts}")
    print(f"  jina_failures: {scraper.jina_failures}")
    print(f"  jina_empty: {scraper.jina_empty}")
    print(f"  distribuzione HTTP: {dict(scraper.jina_status_counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
