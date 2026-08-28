#!/usr/bin/env python3
"""
OB1 v2 — Rileggere le schede Transfermarkt già note, senza aspettare la coda

Perché (misurato il 28 ago 2026 sul DB di produzione)
--------------------------------------------------------
La ragione numero uno per cui un giocatore NON è pubblicabile, in ogni
regione compresa la Colombia, è la stessa: claims_v2.pubblicabile() dice
"nessuna fonte competente scrive per quale club gioca". Per Brasile e
Argentina il quadro è più preciso: 27/37 (Brasile) e 16/63 (Argentina) hanno
GIÀ un valore di club in tabella E una fonte di tipo competente fra le loro
evidenze — eppure la prova fallisce.

Causa, verificata caso per caso, non supposta. Esempio reale (Bruno
Baldini): l'unica evidenza Transfermarkt dice

    "Bruno Baldini - Player profile"

Non un errore di trascrizione: è tutto quel che _c'era_ da citare. Questa
evidenza è stata catturata PRIMA del fix della giuntura (#42/#43), quando la
corroborazione leggeva la scheda con deep_read_urls() troncata a 1500
caratteri — sulla scheda TM i primi 1500 sono tutti menu di navigazione, il
blocco dati comincia verso il carattere 4900 (vedi src/profilo_tm_v2.py).
Restava solo il titolo della pagina: nome sì, età e club no.

Il fix è già in produzione, ma aiuta solo i tentativi NUOVI. Le evidenze
vecchie restano lì finché la coda di corroborazione non ripesca lo stesso
giocatore — al ritmo attuale (budget LLM condiviso da decine di candidati)
può volerci giorni.

Questo script non aspetta la coda: gli URL Transfermarkt li ABBIAMO GIÀ,
sono nel database. Li rilegge con lo stesso parser, sulla pagina INTERA
invece che troncata. Zero chiamate LLM — il parser non ne usa, quindi
questo script non tocca il budget della pipeline né i secret a pagamento.

Stessa guardia della corroborazione vera (observation_fits_target) prima di
accettare l'osservazione: un URL riletto che oggi restituisse una persona
diversa (TM riassegna raramente un ID, ma è successo) non deve fondersi nel
giocatore sbagliato.

Uso
---
    python scripts/backfill_profili_tm.py              # tutti i candidati
    python scripts/backfill_profili_tm.py --limit 20   # prova su 20

Costo
-----
Un fetch Jina Reader anonimo per candidato (20 richieste/minuto): la pausa
fra un candidato e l'altro è tarata su quello, non sulla pipeline. Non gira
nel cron delle 6h — è una ricognizione da rilanciare quando serve, come
impronta_fonti.py e valida_prefiltro.py.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.database_v2 import OB1DatabaseV2, DEFAULT_DB
from src.scraper_global import AsyncGlobalScraper
from src.profilo_tm_v2 import leggi_profilo, e_scheda_tm
from src.corroborate_v2 import observation_fits_target

# Jina Reader anonimo: 20 richieste/minuto. 4s sta comodamente sotto.
PAUSA_SEC = 4.0


def _candidati(db: OB1DatabaseV2, limit: int = 0) -> list:
    """(player_id, nome, età, club, url) per ogni scheda TM già nota di un
    giocatore non ancora pubblicabile — chi è già passato non va ritoccato."""
    with db._conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT p.id, p.canonical_name, p.age, p.club, e.source_url
            FROM players p JOIN evidences e ON e.player_id = p.id
            WHERE p.publishable = 0 AND e.source_url LIKE '%transfermarkt%'
        """).fetchall()
    cand = [tuple(r) for r in rows if e_scheda_tm(r[4])]
    return cand[:limit] if limit else cand


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    db = OB1DatabaseV2(args.db)
    scraper = AsyncGlobalScraper()
    candidati = _candidati(db, args.limit)
    print(f"Schede TM da rileggere: {len(candidati)}\n")

    migliorati = nuovi_pubblicabili = invariati = illeggibili = non_combacia = 0
    for i, (pid, name, age, club, url) in enumerate(candidati):
        if i:
            await asyncio.sleep(PAUSA_SEC)
        testo = await scraper.read_raw(url)
        letto = leggi_profilo(testo, url) if testo else None
        if not letto:
            illeggibili += 1
            print(f"  ? non leggibile oggi: {name} — {url[:70]}")
            continue
        # Stessa guardia della corroborazione vera: non fondere nel
        # giocatore sbagliato se l'URL oggi punta a qualcun altro.
        if not observation_fits_target(letto, name, age=age, club=club,
                                       names_match_fn=db._names_match):
            non_combacia += 1
            print(f"  ! non combacia più: {name!r} vs {letto.get('name')!r} — salto")
            continue

        prima_club, prima_eta = club, age
        letto["source_url"] = url
        db.ingest_observation(letto)
        with db._conn() as conn:
            dopo = conn.execute("SELECT club, age, publishable FROM players WHERE id=?",
                                (pid,)).fetchone()
        if dopo[2]:
            nuovi_pubblicabili += 1
            print(f"  + {name} -> ORA PUBBLICABILE (club={dopo[0]!r}, età={dopo[1]})")
        elif dopo[0] != prima_club or dopo[1] != prima_eta:
            migliorati += 1
            print(f"    {name}: club {prima_club!r} -> {dopo[0]!r}, "
                  f"età {prima_eta} -> {dopo[1]}")
        else:
            invariati += 1

    print(f"\n=== esito ===")
    print(f"candidati: {len(candidati)}")
    print(f"  nuovi pubblicabili: {nuovi_pubblicabili}")
    print(f"  campo migliorato ma non ancora pubblicabile: {migliorati}")
    print(f"  invariati (parser non ha aggiunto nulla di nuovo): {invariati}")
    print(f"  non combaciano più: {non_combacia}")
    print(f"  non leggibili oggi: {illeggibili}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
