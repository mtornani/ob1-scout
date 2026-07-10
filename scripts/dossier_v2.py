#!/usr/bin/env python3
"""
OB1 v2 — Dossier automatico (Fase B3)

Il dossier verificato multi-fonte (fatto a mano per Callegari) diventa un
generatore: dato un giocatore, raccoglie le fonti, legge integralmente (Jina),
estrae i dati (LLM) e produce una scheda condivisibile nell'estetica v2
("Quiet Intelligence"). È il servizio per cui un club paga giorni di scout.

render_dossier() è codice puro e testabile senza rete.

Uso:
    python scripts/dossier_v2.py "Edoardo Callegari" "Cremonese"
"""

import asyncio
import html
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

OUT_DIR = Path(__file__).parent.parent / "output" / "dossiers"

CSS = """
:root{--bg:#f4f3ef;--surface:#fff;--ink:#181a1d;--muted:#697079;--faint:#a7abb2;
--line:#e7e4dd;--line-soft:#f0eee8;--accent:#0f6f5c;--accent-mid:#3f9683;
--accent-lo:#9ecabe;--accent-wash:#eaf3f0;--warn-bg:#fbf7ec;--warn-bd:#e7d9b0;
--sans:'Inter',system-ui,-apple-system,sans-serif;--mono:'IBM Plex Mono',ui-monospace,monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.5;
-webkit-font-smoothing:antialiased;max-width:520px;margin:0 auto;padding:30px 20px 50px}
.brand{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);font-weight:600}
h1{font-size:26px;font-weight:700;letter-spacing:-0.02em;margin:8px 0 3px}
.sub{font-size:14px;color:var(--muted)}
.hero{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;
border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:8px}
.score{text-align:right;font-family:var(--mono);line-height:1}
.score .v{font-size:32px;font-weight:600}
.score .m{font-size:11px;color:var(--faint)}
.merit{display:flex;height:5px;border-radius:3px;overflow:hidden;margin:14px 0 10px;background:var(--line-soft)}
.merit i{display:block;height:100%}.merit .y{background:var(--accent-lo)}.merit .p{background:var(--accent-mid)}.merit .a{background:var(--accent)}
.foot{display:flex;align-items:center;gap:10px;font-size:12px;flex-wrap:wrap}
.conf{display:inline-flex;align-items:center;gap:5px;color:var(--muted)}
.dots{display:inline-flex;gap:2px}.dots i{width:5px;height:5px;border-radius:50%;background:var(--line)}.dots i.on{background:var(--accent)}
.gate{margin-left:auto;font-size:11px;font-weight:600;padding:3px 10px;border-radius:20px;color:var(--accent);background:var(--accent-wash)}
h2{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:26px 0 10px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{text-align:left;padding:7px 2px;border-bottom:1px solid var(--line-soft)}
th{color:var(--muted);font-weight:500;width:42%}
ul{margin:0;padding-left:18px}li{margin-bottom:5px;font-size:14px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.box{border:1px solid var(--line);border-radius:10px;padding:10px 6px;text-align:center;background:var(--surface)}
.box .n{font-family:var(--mono);font-size:19px;font-weight:600}
.box .l{font-size:10px;color:var(--faint);text-transform:uppercase;letter-spacing:.05em;margin-top:2px}
.why{font-size:14px;color:#33383e;line-height:1.6}
.note{background:var(--warn-bg);border:1px solid var(--warn-bd);border-radius:10px;padding:12px 14px;font-size:12.5px;color:#6b5d38;margin-top:10px}
.srcs{font-size:12.5px}.srcs a{color:var(--accent);text-decoration:none;word-break:break-all}.srcs li{margin-bottom:6px}
footer{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);font-size:11px;color:var(--faint);text-align:center}
"""


def _esc(v):
    return html.escape(str(v)) if v is not None else "—"


def render_dossier(d: dict) -> str:
    m = d.get("merit", {"youth": 33, "production": 34, "asymmetry": 33})
    dots = "".join("<i class='on'></i>" if i < d.get("conf_dots", 0) else "<i></i>"
                   for i in range(4))
    anag = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>"
                   for k, v in d.get("anagrafica", []))
    perc = "".join(f"<li>{v}</li>" for v in d.get("percorso", []))
    grid = "".join(f"<div class='box'><div class='n'>{_esc(v)}</div><div class='l'>{_esc(k)}</div></div>"
                   for k, v in d.get("stats", []))
    srcs = "".join(f"<li><a href='{_esc(u)}' target='_blank'>{_esc(t)}</a></li>"
                   for t, u in d.get("sources", []))
    gate = ("<span class='gate'>Pubblicabile</span>"
            if d.get("publishable") else "")

    parts = [f"""<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dossier — {_esc(d.get('name'))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<div class="brand">OB1 Scout · Dossier</div>
<div class="hero">
  <div><h1>{_esc(d.get('name'))}</h1><div class="sub">{_esc(d.get('subtitle'))}</div></div>
  <div class="score"><div class="v">{_esc(d.get('score'))}</div><div class="m">/100</div></div>
</div>
<div class="merit"><i class="y" style="width:{m['youth']}%"></i><i class="p" style="width:{m['production']}%"></i><i class="a" style="width:{m['asymmetry']}%"></i></div>
<div class="foot"><span class="conf">confidenza <span class="dots">{dots}</span></span>
<span style="color:var(--muted)">◆ {_esc(d.get('n_sources'))} fonti</span>{gate}</div>
"""]
    if anag:
        parts.append(f"<h2>Anagrafica</h2><table>{anag}</table>")
    if perc:
        parts.append(f"<h2>Percorso</h2><ul>{perc}</ul>")
    if grid:
        parts.append(f"<h2>Statistiche</h2><div class='grid'>{grid}</div>")
    if d.get("why"):
        parts.append(f"<h2>Perché</h2><div class='why'>{d['why']}</div>")
    if d.get("note"):
        parts.append(f"<div class='note'>{d['note']}</div>")
    if srcs:
        parts.append(f"<h2>Fonti consultate</h2><ul class='srcs'>{srcs}</ul>")
    parts.append(f"<footer>Generato il {_esc(d.get('generated'))} · OB1 Scout · ricerca automatica multi-fonte</footer></body></html>")
    return "\n".join(parts)


async def gather(name: str, club: str = "") -> dict:
    """Raccoglie fonti reali e costruisce i dati del dossier (rete + Jina)."""
    from src.scraper_global import AsyncGlobalScraper
    scraper = AsyncGlobalScraper()
    cp = f" {club}" if club else ""
    results = await scraper.run_batch([
        f'"{name}"{cp} transfermarkt', f'"{name}"{cp} soccerway statistics',
        f'"{name}"{cp} news',
    ])
    sources = [(r.get("title", "")[:70], r.get("url", "")) for r in results if r.get("url")][:8]
    return {
        "name": name, "subtitle": club, "score": "—", "conf_dots": min(len(sources), 4),
        "n_sources": len(set(u for _, u in sources)),
        "sources": sources, "generated": datetime.now().strftime("%d/%m/%Y"),
        "note": "Dati pubblici disponibili alla data. Nessun accesso a report scout "
                "interni o video. Confermare anagrafica e status con la società.",
    }


async def main():
    if len(sys.argv) < 2:
        print('Uso: python scripts/dossier_v2.py "Nome Cognome" ["Club"]')
        return
    name, club = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
    data = await gather(name, club)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name.lower().replace(' ', '_')}.html"
    path.write_text(render_dossier(data), encoding="utf-8")
    print(f"Dossier: {path}")


if __name__ == "__main__":
    asyncio.run(main())
