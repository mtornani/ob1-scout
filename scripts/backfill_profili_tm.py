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


# La firma che il parser lascia quando ha letto DAVVERO la scheda
# (src/profilo_tm_v2.leggi_profilo). Un'evidenza TM che non ce l'ha è una
# cattura vecchia: il titolo della pagina e basta.
_FIRMA_PARSER = "Scheda Transfermarkt"


def _candidati(db: OB1DatabaseV2, limit: int = 0) -> list:
    """
    (player_id, nome, età, club, url) per ogni scheda TM già nota che vale la
    pena rileggere.

    Il criterio era "il giocatore non è pubblicabile". Copriva il caso per cui
    lo script è nato — sbloccare chi il gate ferma — ma ne lasciava fuori uno
    che conta quanto: un giocatore GIÀ pubblicato la cui unica evidenza TM è
    ancora il titolo della pagina. Lì l'evidenza stantia non gli impedisce di
    uscire, gli toglie l'ETÀ: claims_v2 non trova nessuna fonte competente che
    la scriva, la scheda la nasconde, e con lei sparisce l'anomalia di
    anticipo di categoria — che è il segnale più forte che abbiamo.
    Misurato il 31 ago 2026: tre dei quattro casi di anticipo sono pubblicati
    e muti per questo motivo.

    Quindi si rilegge se il giocatore non è pubblicabile, OPPURE se nessuna
    evidenza TM porta la firma del parser — cioè se quel che abbiamo è ancora
    la cattura vecchia, troncata ai 1500 caratteri di menu.
    """
    with db._conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT p.id, p.canonical_name, p.age, p.club,
                            e.source_url, p.publishable
            FROM players p JOIN evidences e ON e.player_id = p.id
            WHERE e.source_url LIKE '%transfermarkt%'
        """).fetchall()
        letti = {r[0] for r in conn.execute(
            "SELECT DISTINCT player_id FROM evidences "
            "WHERE source_url LIKE '%transfermarkt%' AND raw_content LIKE ?",
            (f"%{_FIRMA_PARSER}%",))}
    cand = [(r[0], r[1], r[2], r[3], r[4], bool(r[5])) for r in rows
            if e_scheda_tm(r[4]) and (not r[5] or r[0] not in letti)]
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
    eta_ora_provata = 0
    for i, (pid, name, age, club, url, era_pubblicabile) in enumerate(candidati):
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
            # L'età va stampata insieme al nome: observation_fits_target
            # rifiuta anche per età (la guardia contro il professionista
            # omonimo), e un messaggio coi soli nomi produceva righe come
            # "'Rian Santana' vs 'Rian Santana' — salto", che sembrano un bug
            # e non lo sono.
            print(f"  ! non combacia più: {name!r} ({age}) vs "
                  f"{letto.get('name')!r} ({letto.get('age')}) — salto")
            continue

        prima_club, prima_eta = club, age
        letto["source_url"] = url
        db.ingest_observation(letto)
        with db._conn() as conn:
            dopo = conn.execute("SELECT club, age, publishable FROM players WHERE id=?",
                                (pid,)).fetchone()
            # L'esito che conta per chi era GIÀ pubblicato: prima l'unica
            # evidenza TM era il titolo della pagina, e claims_v2 non trovava
            # nessuna fonte competente che scrivesse l'età — quindi la scheda
            # non la mostrava e l'anomalia di anticipo restava muta. Ora c'è
            # una citazione che l'età ce l'ha dentro.
            if conn.execute(
                    "SELECT 1 FROM evidences WHERE player_id=? AND "
                    "source_url LIKE '%transfermarkt%' AND raw_content LIKE ? "
                    "LIMIT 1", (pid, f"%{_FIRMA_PARSER}%")).fetchone():
                eta_ora_provata += 1
        # "Nuovo" solo se il gate è passato ADESSO. Da quando i candidati
        # includono anche i già pubblicati (per rileggerne l'età), un
        # controllo sul solo stato finale li contava tutti come sbloccati:
        # un numero lusinghiero e falso.
        if dopo[2] and not era_pubblicabile:
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
    print(f"  età ora dimostrabile da una fonte competente: {eta_ora_provata}")
    print(f"  non combaciano più: {non_combacia}")
    print(f"  non leggibili oggi: {illeggibili}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
