#!/usr/bin/env python3
"""
OB1 v2 — Le anomalie reggono sui dati veri, o sono artefatti di raccolta?

Perché
------
src/anomalie_v2.py nasce da una promessa commerciale precisa: "ti do questi
profili, al mio sistema risultano anomali per questo motivo, l'ultimo passo è
tuo". La promessa vale solo se il motivo è vero. Un'anomalia che in realtà
misura come abbiamo scaricato i dati, e non il giocatore, è peggio di nessuna
anomalia: la si scopre al telefono, davanti a chi paga.

Cinque segnali erano candidati. Questo script è il conto che ne ha bocciati
tre — e serve a rifarlo, perché con un corpus più profondo le risposte
cambiano e vanno riguardate, non ricordate.

    ANTICIPO DI CATEGORIA   convocato in una categoria sopra la sua età
    SALTO DI CATEGORIA      ha scavalcato una categoria che quella
                            federazione convoca davvero
    ASIMMETRIA DI COPERTURA la federazione lo sceglie, la stampa lo ignora
    ---- bocciati, e qui sotto si vede perché ----
    PROGRESSIONE            "è salito di categoria"
    DENSITÀ                 convocazioni all'anno
    SERIE INTERROTTA        convocato spesso, poi silenzio

Il test che separa un segnale da un artefatto è sempre lo stesso: se i
giocatori "anomali" condividono le stesse date, allora non è la loro carriera
ad avere quella forma — è la nostra raccolta. Un'anomalia vera è distribuita;
un artefatto si ammucchia.

Uso
---
    python scripts/valida_anomalie.py [--db data/ob1_v2.db] [--tutti]

Solo lettura: nessuna scrittura, nessuna rete, nessuna chiamata a modello.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.anomalie_v2 import (TIPI_STAMPA, come_dict, leggi,  # noqa: E402
                             scala_osservata)
from src.claims_v2 import DICHIARATO, registro, stabilisci  # noqa: E402


def _dominio(d: str) -> str:
    d = (d or "").lower()
    return d[4:] if d.startswith("www.") else d


def carica(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    reg = registro()
    giocatori = []
    for p in conn.execute("SELECT * FROM players"):
        grezzo = p["selection_json"]
        sel = {}
        if grezzo:
            try:
                sel = json.loads(grezzo)
            except (ValueError, TypeError):
                sel = {}
        domini = {_dominio(r["source_domain"]) for r in conn.execute(
            "SELECT source_domain FROM evidences "
            "WHERE player_id=? AND origin='extractor'", (p["id"],))}
        tipi = [reg.get(d, {}).get("type") for d in domini
                if reg.get(d, {}).get("type")]
        # L'età che l'export può davvero usare: solo DICHIARATO. Un'età
        # DEDOTTA viene dalla categoria del torneo, e darla in pasto a
        # "è giovane per la categoria" chiude un cerchio su se stessa.
        stats = json.loads(p["stats_json"]) if p["stats_json"] else {}
        claims = stabilisci(
            {"canonical_name": p["canonical_name"], "club": p["club"],
             "age": p["age"], "stats": stats},
            [dict(r) for r in conn.execute(
                "SELECT player_id, raw_content, source_domain, source_url, "
                "observed_at, origin FROM evidences WHERE player_id=?",
                (p["id"],))])
        dichiarata = claims.get("eta", {}).get("stato") == DICHIARATO
        giocatori.append({
            "nome": p["canonical_name"], "eta": p["age"], "club": p["club"],
            "eta_dichiarata": p["age"] if dichiarata else None,
            "publishable": bool(p["publishable"]), "selezione": sel,
            "tipi_fonte": tipi, "domini": sorted(domini),
        })
    conn.close()
    return giocatori


def _spread(date_iso, etichetta: str) -> str:
    """Un segnale distribuito o un artefatto? Se le date si ammucchiano su
    pochissimi valori distinti, i 'casi' sono lo stesso evento visto n volte."""
    pulite = [d for d in date_iso if d]
    if not pulite:
        return f"    {etichetta}: nessuna data"
    distinte = sorted(set(pulite))
    quota = len(distinte) / len(pulite)
    verdetto = "distribuito" if quota >= 0.5 else "AMMUCCHIATO (sospetto artefatto)"
    return (f"    {etichetta}: {len(pulite)} casi su {len(distinte)} date "
            f"distinte -> {verdetto}"
            + (f"  {distinte}" if len(distinte) <= 4 else ""))


def controprova_bocciati(giocatori, oggi: date) -> None:
    """I tre segnali scartati, con il numero che li ha scartati. Rifatto ogni
    volta invece che ricordato: se un giorno reggono, si vede qui."""
    print("\n=== I tre segnali bocciati (controprova) ===")

    # PROGRESSIONE — è salire, o è compiere un anno?
    passi = Counter()
    for g in giocatori:
        sel = g["selezione"]
        if not sel.get("progressione"):
            continue
        cat = [c for c in (sel.get("categorie") or [])]
        if len(cat) >= 2:
            passi[f"{cat[0]} -> {cat[-1]}"] += 1
    tot = sum(passi.values())
    print(f"\n  PROGRESSIONE: {tot} giocatori")
    for passo, n in passi.most_common():
        a, _, b = passo.partition(" -> ")
        try:
            delta = int(b.split("-")[1]) - int(a.split("-")[1])
        except (ValueError, IndexError):
            delta = None
        nota = "  <- un anno di età, non un salto" if delta == 1 else ""
        print(f"    {n:3}x  {passo}{nota}")
    banali = sum(n for p, n in passi.items()
                 if p in ("sub-16 -> sub-17", "sub-15 -> sub-16",
                          "sub-17 -> sub-18", "sub-19 -> sub-20"))
    if tot:
        print(f"    verdetto: {banali}/{tot} sono il gradino successivo. "
              f"Non è un'anomalia, è un compleanno.")

    # DENSITÀ — la forma è del giocatore o dei cicli che abbiamo scaricato?
    archi = [g["selezione"].get("mesi_di_arco", 0) for g in giocatori
             if g["selezione"].get("mesi_di_arco", 0) >= 3]
    print(f"\n  DENSITÀ: misurabile su {len(archi)} giocatori "
          f"(serve un arco >= 3 mesi)")
    if archi:
        c = Counter(archi)
        print(f"    archi temporali: {dict(sorted(c.items()))}")
        quota = sum(n for _, n in c.most_common(2)) / len(archi)
        verdetto = ("è la forma dei nostri cicli, non delle loro carriere"
                    if quota >= 0.5 else "distribuzione plausibile")
        print(f"    i due archi più frequenti coprono {quota:.0%} "
              f"dei casi: {verdetto}")

    # SERIE INTERROTTA — casi indipendenti o una rosa sola?
    interrotti = []
    for g in giocatori:
        sel = g["selezione"]
        eventi = [e for e in (sel.get("eventi") or []) if e.get("data")]
        if sel.get("quante", 0) < 3 or not eventi:
            continue
        ultima = max(e["data"] for e in eventi)
        mesi = (oggi.year - int(ultima[:4])) * 12 + (oggi.month - int(ultima[5:7]))
        if mesi >= 12:
            interrotti.append((g["nome"], ultima, mesi))
    print(f"\n  SERIE INTERROTTA: {len(interrotti)} giocatori")
    for nome, ultima, mesi in sorted(interrotti, key=lambda x: -x[2])[:8]:
        print(f"    {mesi:3} mesi di silenzio  {nome[:34]:34} ultima {ultima}")
    print(_spread([u for _, u, _ in interrotti], "ultime date"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "ob1_v2.db"))
    ap.add_argument("--tutti", action="store_true",
                    help="elenca ogni giocatore segnalato, non solo i primi")
    ap.add_argument("--oggi", default="", help="YYYY-MM-DD, per riprodurre "
                                               "una misura passata")
    args = ap.parse_args()

    oggi = (datetime.strptime(args.oggi, "%Y-%m-%d").date() if args.oggi
            else date.today())
    giocatori = carica(Path(args.db))
    scala = scala_osservata(g["selezione"] for g in giocatori)

    print(f"=== Anomalie su {args.db} · {oggi} ===")
    print(f"giocatori: {len(giocatori)} · pubblicabili: "
          f"{sum(1 for g in giocatori if g['publishable'])} · con convocazioni: "
          f"{sum(1 for g in giocatori if g['selezione'].get('quante'))}")

    print("\nscala delle categorie, ricavata dai comunicati stessi:")
    for fed, gradini in sorted(scala.items()):
        print(f"    {fed}: {gradini}")

    # Due passate sulla stessa base. La prima è quella che finisce davvero in
    # dashboard: usa solo l'età DICHIARATA da una fonte. La seconda usa l'età
    # grezza in colonna e serve a misurare una cosa sola — quanto segnale
    # esiste nei dati ma resta bloccato dalla provenienza dell'età. È il
    # numero che dice dove conviene lavorare dopo.
    per_codice = defaultdict(list)
    for g in giocatori:
        for a in leggi(g["selezione"], g["eta_dichiarata"], g["tipi_fonte"],
                       scala, oggi):
            per_codice[a.codice].append((g, a))

    bloccati = []
    for g in giocatori:
        if g["eta_dichiarata"] or not g["eta"]:
            continue
        for a in leggi(g["selezione"], g["eta"], g["tipi_fonte"], scala, oggi):
            if a.codice == "anticipo_categoria":
                bloccati.append((g, a))

    print("\n=== I tre segnali che restano ===")
    for codice in ("salto_categoria", "anticipo_categoria",
                   "asimmetria_copertura"):
        casi = per_codice.get(codice, [])
        pubbl = [c for c in casi if c[0]["publishable"]]
        forti = [c for c in casi if c[1].forza == "forte"]
        print(f"\n  {codice.upper()}: {len(casi)} giocatori "
              f"({len(pubbl)} pubblicabili, {len(forti)} 'forte')")
        if not casi:
            print("    nessuno: il segnale non ha ancora base nel corpus.")
            continue
        print(_spread([c[1].dati.get("data", "") for c in casi], "date"))
        quanti = len(casi) if args.tutti else 6
        for g, a in sorted(casi, key=lambda c: (c[1].forza != "forte",
                                                c[0]["nome"]))[:quanti]:
            marchio = "PUB" if g["publishable"] else "trk"
            print(f"    [{marchio}] {a.forza:10} {g['nome'][:30]:30} "
                  f"{g['club'] or '-'}")
            print(f"          {a.frase}")
            if a.prove:
                print(f"          {a.prove[0]}")
        if not args.tutti and len(casi) > quanti:
            print(f"    ... e altri {len(casi) - quanti} (--tutti per vederli)")

    # A chi si può davvero mandare un profilo: pubblicabile e con una ragione.
    consegnabili = {}
    for codice, casi in per_codice.items():
        for g, a in casi:
            if g["publishable"]:
                consegnabili.setdefault(g["nome"], []).append(a)
    print(f"\n=== Consegnabili a un agente: {len(consegnabili)} profili "
          f"pubblicabili con almeno una ragione ===")
    forti = {n: aa for n, aa in consegnabili.items()
             if any(a.forza == "forte" for a in aa)}
    print(f"    di cui con una ragione 'forte': {len(forti)}")
    for nome, aa in sorted(forti.items()):
        print(f"      {nome}: {', '.join(a.codice for a in aa)}")

    print(f"\n=== Bloccati dalla provenienza dell'età: {len(bloccati)} ===")
    if bloccati:
        print("  Questi risultano sotto età nei dati grezzi, ma nessuna fonte")
        print("  competente SCRIVE la loro età: viene da un aggregatore, o è")
        print("  dedotta dalla categoria stessa — e dedurla dalla categoria per")
        print("  poi dire che è basso per quella categoria è un cerchio.")
        print("  Non è un difetto del filtro: è la regola dell'età")
        print("  (src/claims_v2.py) che tiene. Lo sblocco non è allentarla — è")
        print("  estrarre la data di nascita dai comunicati di convocazione,")
        print("  che spesso ce l'hanno e hanno l'autorità per averla.")
        for g, a in sorted(bloccati, key=lambda c: c[0]["nome"]):
            marchio = "PUB" if g["publishable"] else "trk"
            print(f"    [{marchio}] {g['nome'][:32]:32} {a.frase}")

    controprova_bocciati(giocatori, oggi)

    # Copertura del registro fonti: l'asimmetria dice "assente dalla stampa
    # che seguiamo", e quanto valga dipende da quanta stampa seguiamo.
    reg = registro()
    stampa = sum(1 for v in reg.values() if v.get("type") in TIPI_STAMPA)
    fed = sum(1 for v in reg.values() if v.get("type") == "federation")
    print(f"\n=== Limite dichiarato ===")
    print(f"  registro fonti: {fed} federazioni, {stampa} fonti di stampa.")
    print("  'assente dalla stampa' significa assente da QUESTE. Con un")
    print("  registro di stampa più largo l'asimmetria si restringe: è la")
    print("  misura che va rifatta dopo ogni allargamento del registro.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
