#!/usr/bin/env python3
"""
OB1 v2 — Il prefiltro scarterebbe articoli che invece hanno prodotto giocatori?

Perché
------
src/prefilter_v2.py è scritto e testato dalla Fase C ma non è mai stato
collegato alla pipeline. Prima di collegarlo va misurato su dati VERI, non
sui tre casi di esempio del suo self-test (regola 2 del CLAUDE.md: un cambio
diventa default solo dopo un confronto su dati reali).

La domanda esatta è asimmetrica, e conta saperlo:

    scartare un articolo che AVREBBE dato un giocatore  = un nome perso,
                                                          costo alto
    scartare un articolo che non avrebbe dato niente    = quota risparmiata,
                                                          costo zero

Quindi il numero che decide è il primo: quanti POSITIVI verrebbero scartati.
Il secondo dice solo quanto si guadagna.

Come
----
Due insiemi presi dal DB di produzione, non costruiti a mano:

  POSITIVI  url di evidenze legate a un giocatore PUBBLICABILE — sappiamo per
            certo che da lì è uscito un nome che ha passato il gate.
  NEGATIVI  url in seen_items che non compaiono in nessuna evidenza — lo
            scraper li ha letti e non ne è uscito niente.

Di ognuno si scarica il testo VERO (Jina Reader anonimo, la stessa strada di
scripts/impronta_fonti.py) e ci si passa should_extract().

Terza categoria, che è il punto delicato: una pagina che oggi non si scarica
(404, Cloudflare, sito spento) NON è un verdetto del filtro e non va contata
né come tenuta né come scartata. Confondere "non lo so" con "no" è
esattamente l'errore che in questa stessa base di codice aveva già gonfiato
una misura (verifica TM su Lega Pro, 26 ago 2026): qui si conta a parte.

Uso
---
    python scripts/valida_prefiltro.py                # 25 + 25
    python scripts/valida_prefiltro.py --campione 40
"""

import argparse
import asyncio
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.prefilter_v2 import should_extract, relevance, condense
from src.scraper_global import AsyncGlobalScraper
from src.database_v2 import DEFAULT_DB
from src.sources_v2 import _ERRORE_BERSAGLIO

# Anonimo su Jina Reader = 20 richieste/minuto. 4s tra una e l'altra sta
# comodamente sotto, e questo script non gira in pipeline: può permettersi
# di essere lento.
PAUSA_SEC = 4.0


def _e_aggregatore(url: str) -> bool:
    """True se l'url appartiene a una fonte tier=secondary del registro.
    I secondary sono esclusi dalla discovery per progetto (vedi
    SourceMonitor.new_items): se un'evidenza viene da lì, è arrivata dalla
    corroborazione — percorso diverso, con input diverso (pagine-profilo
    strutturate invece di articoli in prosa)."""
    from src.database_v2 import _load_source_tiers, domain_of
    return _load_source_tiers().get(domain_of(url)) == "secondary"


def _campioni(db_path: str, n: int, seed: int = 20260827):
    """(positivi, negativi) dal DB di produzione. Seed fisso: due esecuzioni
    sullo stesso DB devono dare lo stesso campione, altrimenti il numero non
    è confrontabile con quello di ieri."""
    db = sqlite3.connect(db_path)
    pos = [r[0] for r in db.execute("""
        SELECT DISTINCT e.source_url FROM evidences e
        JOIN players p ON p.id = e.player_id
        WHERE p.publishable = 1 AND e.source_url LIKE 'http%'""")]
    neg = [r[0] for r in db.execute("""
        SELECT s.item_key FROM seen_items s
        WHERE s.item_key LIKE 'http%'
          AND s.item_key NOT IN (
              SELECT source_url FROM evidences WHERE source_url IS NOT NULL)""")]
    rng = random.Random(seed)
    rng.shuffle(pos)
    rng.shuffle(neg)
    return pos[:n], neg[:n]


async def _misura(scraper, urls: list, etichetta: str) -> dict:
    tenuti, scartati, illeggibili = [], [], []
    risparmio_char = []
    for i, u in enumerate(urls):
        if i:
            await asyncio.sleep(PAUSA_SEC)
        testo = await scraper.read_raw(u)
        # Jina risponde 200 anche quando il sito di destinazione ha dato 404:
        # l'errore vero sta dentro il testo (vedi _ERRORE_BERSAGLIO in
        # sources_v2). Senza questo controllo si misurerebbe il filtro sulla
        # pagina d'errore del sito, non sull'articolo.
        if not testo or _ERRORE_BERSAGLIO.search(testo[:400]):
            illeggibili.append(u)
            print(f"  ? {etichetta} non leggibile oggi: {u[:88]}")
            continue
        r = relevance(testo)
        if r["keep"]:
            tenuti.append(u)
            risparmio_char.append((len(testo), len(condense(testo))))
        else:
            scartati.append((u, r.get("reason", "")))
            print(f"  - {etichetta} SCARTATO (score={r.get('score')}): {u[:80]}")
    return {"tenuti": tenuti, "scartati": scartati,
            "illeggibili": illeggibili, "risparmio": risparmio_char}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campione", type=int, default=25,
                    help="quanti url per gruppo (default 25)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    pos_tutti, neg = _campioni(args.db, args.campione)
    # I positivi vanno divisi per PERCORSO, non trattati come un blocco solo.
    # Misurato il 27 ago 2026 su 17 positivi giudicati: gli unici 3 scartati
    # erano pagine-profilo di transfermarkt.com e soccerway.com — cioè
    # aggregatori (tier secondary), che la discovery non tocca mai
    # (SourceMonitor.new_items ritorna [] per i secondary) e che quindi
    # arrivano SOLO dalla corroborazione. Sono tabelle di dati strutturati,
    # non prosa: un filtro che cerca "paragrafo con segnali giovanili +
    # nomi" li boccia per costruzione, ed è giusto che li bocci — ma è anche
    # la prova che NON va applicato a quel percorso. Separarli è la
    # differenza fra "il filtro perde il 18%" e "il filtro non perde nulla
    # dove verrebbe davvero usato".
    pos_disc = [u for u in pos_tutti if not _e_aggregatore(u)]
    pos_agg = [u for u in pos_tutti if _e_aggregatore(u)]
    print(f"Campione: {len(pos_tutti)} positivi "
          f"({len(pos_disc)} da discovery, {len(pos_agg)} da aggregatori/"
          f"corroborazione) · {len(neg)} negativi\n")

    scraper = AsyncGlobalScraper()
    print("--- POSITIVI da DISCOVERY (uno scarto qui è un giocatore perso) ---")
    p = await _misura(scraper, pos_disc, "POS-disc")
    print("\n--- POSITIVI da AGGREGATORI (percorso corroborazione, "
          "il filtro NON andrebbe applicato qui) ---")
    pa = await _misura(scraper, pos_agg, "POS-agg")
    print("\n--- NEGATIVI (uno scarto qui è quota risparmiata) ---")
    n = await _misura(scraper, neg, "NEG")

    def _riga(nome, d, commento=""):
        v, s, i = len(d["tenuti"]), len(d["scartati"]), len(d["illeggibili"])
        g = v + s
        print(f"{nome:<22} giudicati {g:>2} (+{i} non leggibili, esclusi)")
        if g:
            print(f"{'':<22} tenuti {v:>2} · scartati {s:>2}  "
                  f"→ {s / g * 100:.0f}% {commento}")
        return g, s

    print("\n=== ESITO ===")
    gp, sp = _riga("POSITIVI discovery", p, "PERSI — è questo il numero che decide")
    _riga("POSITIVI aggregatori", pa, "(atteso alto: pagine-profilo, non articoli)")
    _riga("NEGATIVI", n, "di chiamate risparmiate")

    tutti = p["risparmio"] + n["risparmio"]
    if tutti:
        import statistics
        quote = [1 - b / a for a, b in tutti if a]
        print(f"\ncondensazione sugli articoli TENUTI: mediana "
              f"{statistics.median(quote) * 100:.0f}% di caratteri in meno "
              f"(su {len(quote)} articoli)")

    print("\n--- verdetto ---")
    if gp and sp == 0:
        print("Nessun positivo da discovery scartato: applicato al SOLO percorso")
        print("di discovery il filtro non costa nomi, e il risparmio è netto.")
    elif gp:
        print(f"{sp}/{gp} positivi da discovery verrebbero persi: NON collegare")
        print("il filtro così com'è: prima va capito cosa hanno in comune.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
