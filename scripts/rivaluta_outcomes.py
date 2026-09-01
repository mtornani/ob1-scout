#!/usr/bin/env python3
"""
Rivaluta i 4 outcome "mainstream_lead_time" scritti prima del fix del 1 set
2026 (src/outcomes_v2.py, regola 0 e "fonte_non_editoriale").

Non cancella niente: chi ha scritto un outcome invalido resta visibile, con
`suspect=1` e una `note` che dice perché — stessa scelta già fatta per il
reperto storico della migrazione v1 (scripts/migrate_to_v2.py). Cancellare
avrebbe fatto sparire in silenzio l'errore; questo lo rende leggibile.

Misurato il 1 set 2026: zero dei 4 outcome vivi reggeva.
  - 3 erano fcf.com.co (federazione) che riconvocava lo stesso ragazzo in un
    microciclo successivo — non stampa, la stessa bocca due volte.
  - 1 era "Enzo Fernández" al Benfica: il gate lo blocca altrove come
    gia_coperto_da_tutti (non pubblicabile), eppure contava come "anticipo
    confermato" di 2 giorni.

    python scripts/rivaluta_outcomes.py --prova   # mostra e basta
    python scripts/rivaluta_outcomes.py           # scrive
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database_v2 import OB1DatabaseV2, domain_of
from src.claims_v2 import registro
from src.outcomes_v2 import evaluate_mainstream


def main() -> int:
    prova = "--prova" in sys.argv
    db = OB1DatabaseV2()
    c = sqlite3.connect(db.db_path)
    c.row_factory = sqlite3.Row

    righe = list(c.execute("""
        SELECT o.id, o.lead_time_days, o.outcome_date, o.source_url, o.suspect,
               p.canonical_name, p.club, p.first_detected
        FROM outcomes o JOIN players p ON p.id = o.player_id
        WHERE o.outcome_type = 'mainstream_lead_time'
    """))
    print(f"outcome vivi da rivalutare: {len(righe)}\n")

    cambiati = 0
    for r in righe:
        tipo = registro().get(domain_of(r["source_url"]), {}).get("type")
        v = evaluate_mainstream(
            r["canonical_name"], r["club"], r["first_detected"],
            hype_url=r["source_url"], hype_date=r["outcome_date"],
            hype_source_type=tipo)
        stato = "resta valido" if v["valid"] else f"NON regge più ({v['reason']})"
        print(f"  [{r['id']}] {r['canonical_name']:32} {r['club'] or '':28} -> {stato}")
        if not v["valid"] and not r["suspect"]:
            cambiati += 1
            if not prova:
                c.execute("UPDATE outcomes SET suspect=1, note=? WHERE id=?",
                          (v["reason"], r["id"]))
    if not prova:
        c.commit()
    c.close()
    print(f"\n{cambiati} outcome rietichettati sospetti"
          + ("  (--prova: niente scritto)" if prova else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
