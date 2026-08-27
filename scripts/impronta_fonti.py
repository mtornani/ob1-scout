#!/usr/bin/env python3
"""
OB1 v2 — L'impronta di ogni fonte: quanto produce, non solo se è registrata

Perché serve
------------
Il registro (config/sources.json) è cresciuto per accumulo: 83 fonti,
aggiunte in più giri e per lo più "classificate a memoria" o verificate una
volta con una ricerca web, mai ricontrollate contro un fetch vero (le note
_meta lo dicono da sole: "validated: false su tutte"). Misurato contro il
database di produzione il 26 ago 2026:

    52 fonti su 83 non hanno MAI prodotto un'evidenza
    180 evidenze su ~450 vengono da un dominio solo (fcf.com.co)

Prima di aggiungere fonti nuove (la tentazione naturale — "copriamo più
paesi") va misurato cosa producono quelle che già ci sono. Aggiungerne senza
misurare è quello che è già successo il 19 agosto: +34 fonti per 13 paesi,
zero evidenze aggiuntive.

Cosa misura
-----------
Per ognuna delle fonti nel registro, in ordine:

    1. Ha un indice leggibile? (INDEX_PATHS di src/sources_v2.py — la
       stessa strada che la pipeline usa ora per la discovery reale, non
       una sonda separata che potrebbe rispondere diversamente)
    2. Se sì: quanti articoli propone, con che aspetto (i primi 2, per
       giudicare a colpo d'occhio se sono davvero notizie o rumore)
    3. Storico: quante evidenze ha prodotto finora nel DB locale (se
       presente) — un'impronta storica zero + nessun indice oggi è la
       combinazione che dice "questa fonte non sta lavorando per noi"

Non decide niente da solo: scrive un rapporto (testo + JSON) che un umano
legge per decidere cosa tenere, cosa verificare a mano, cosa togliere.

Uso
---
    python scripts/impronta_fonti.py                  # tutte le fonti
    python scripts/impronta_fonti.py --regione Africa  # solo una regione
    python scripts/impronta_fonti.py --limit 10        # le prime N (prova)

Costo
-----
Un fetch via Jina Reader per fonte nella maggior parte dei casi (si ferma
al primo INDEX_PATHS che risponde), fino a 4 se nessuno risponde. Senza
JINA_API_KEY il tetto è ~20 richieste/minuto: questo script fa una pausa
fra le fonti apposta, e NON gira dentro la pipeline — è una ricognizione
manuale, da rilanciare quando si vuole ricontrollare il registro, non a
ogni ciclo di 6h.
"""

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.sources_v2 import load_registry, parse_index, INDEX_PATHS
from src.scraper_global import AsyncGlobalScraper
from src.database_v2 import DEFAULT_DB

# Pausa fra una fonte e l'altra: senza chiave Jina il tetto è basso, e ogni
# fonte può costare fino a 4 richieste (un tentativo per ogni INDEX_PATHS
# prima di arrendersi). Volutamente conservativo — questo non è un ciclo
# di produzione con un tetto per-run, è una ricognizione che può permettersi
# di essere lenta.
PAUSA_SEC = float(__import__("os").environ.get("IMPRONTA_PAUSA_SEC", "4"))


def _dominio(url: str) -> str:
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _evidenze_storiche() -> dict:
    """dominio -> n. evidenze nel DB locale. {} se il DB non c'è (niente
    da confrontare, non un errore: capita su un checkout pulito)."""
    if not Path(DEFAULT_DB).exists():
        return {}
    try:
        db = sqlite3.connect(DEFAULT_DB)
        return dict(Counter(
            r[0] for r in db.execute(
                "SELECT source_domain FROM evidences WHERE source_domain != ''")))
    except sqlite3.Error:
        return {}


async def impronta_di(scraper: AsyncGlobalScraper, source: dict) -> dict:
    """Un fetch per volta, fermandosi al primo INDEX_PATHS che risponde —
    stessa logica di SourceMonitor._da_indice_sito, per misurare esattamente
    quello che la pipeline vedrebbe."""
    dom = _dominio(source["url"])
    base = source["url"].rstrip("/")
    for path in INDEX_PATHS:
        text = await scraper.read_raw(base + path)
        trovati = parse_index(text, dom)
        if trovati:
            return {"ha_indice": True, "percorso": path,
                    "n_articoli": len(trovati), "campione": trovati[:2]}
    return {"ha_indice": False, "percorso": None, "n_articoli": 0, "campione": []}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regione", default=None,
                    help="Filtra per regione (match esatto, es. 'Colombia')")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="data/impronta_fonti.json")
    args = ap.parse_args()

    fonti = load_registry()
    if args.regione:
        fonti = [s for s in fonti if s.get("region") == args.regione]
    if args.limit:
        fonti = fonti[:args.limit]

    storiche = _evidenze_storiche()
    scraper = AsyncGlobalScraper()

    righe = []
    print(f"Fonti da misurare: {len(fonti)}\n")
    for i, s in enumerate(fonti):
        if i:
            await asyncio.sleep(PAUSA_SEC)
        dom = _dominio(s["url"])
        imp = await impronta_di(scraper, s)
        storico = storiche.get(dom, 0)
        for alias in (s.get("aliases") or []):
            storico += storiche.get(alias.lower(), 0)
        riga = {**{k: s.get(k) for k in
                   ("id", "name", "region", "type", "tier", "url")},
                "dominio": dom, "evidenze_storiche": storico, **imp}
        righe.append(riga)
        segno = "✓ indice" if imp["ha_indice"] else "  ricerca"
        print(f"  {segno}  {dom:32s} {s.get('region','')[:16]:18s} "
              f"storico={storico:4d}  ora={imp['n_articoli']:3d}")

    # --- riepilogo ---
    con_indice = [r for r in righe if r["ha_indice"]]
    senza_nulla = [r for r in righe
                   if not r["ha_indice"] and r["evidenze_storiche"] == 0]
    print(f"\n=== riepilogo ({len(righe)} fonti) ===")
    print(f"  con indice leggibile oggi: {len(con_indice)}")
    print(f"  senza indice E senza storico (candidate a ricontrollo/rimozione): "
          f"{len(senza_nulla)}")
    if senza_nulla:
        print("\n  --- non producono niente, in nessun modo, mai ---")
        for r in senza_nulla:
            print(f"    {r['dominio']:32s} {r['region']:18s} {r['type']}")

    per_regione = Counter(r["region"] for r in con_indice)
    if per_regione:
        print("\n  regioni con almeno una fonte leggibile via indice oggi:")
        for reg, n in per_regione.most_common():
            print(f"    {n:3d}  {reg}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generato_il": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "fonti": righe,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nScritto {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
