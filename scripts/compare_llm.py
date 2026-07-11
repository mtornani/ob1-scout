#!/usr/bin/env python3
"""
OB1 Scout - LLM Comparison (diagnostic, off-pipeline)

Confronto onesto fra il modello in produzione (Gemini) e uno o piu' modelli
candidati a costo zero, USANDO LO STESSO IDENTICO PROMPT/RUBRICA.

Scopo: decidere, guardando dati veri e non a naso, SE la qualita' dello
scoring regge con un modello piu' economico — PRIMA di prendere qualsiasi
decisione sul backend LLM.

IMPORTANTE — rispetto del FREEZE PILOTA K-SPORT:
- Questo script NON fa parte della pipeline automatica.
- NON modifica src/intelligence.py ne' alcun file frozen.
- NON scrive sul database, NON manda alert Telegram, NON gira a schedule.
- Il prompt/rubrica usati sono ESATTAMENTE quelli di produzione: vengono
  "catturati" a runtime dal codice reale (intercettando la chiamata che
  intelligence.py farebbe a Gemini), non ricopiati a mano. Cosi' il confronto
  e' fedele e non puo' "barare".

Uso:
    # confronto su input di esempio (offline, immediato):
    python scripts/compare_llm.py --no-baseline

    # confronto su dati reali presi live dallo scraper di produzione:
    python scripts/compare_llm.py --scrape 6

    # confronto su un file di input salvato (lista di {title,url,content,deep_read}):
    python scripts/compare_llm.py --input output/sample_signals.json

Configurazione modelli candidati (a costo zero) — basta UNA di queste:
    GROQ_API_KEY=...            (consigliato: free tier, Llama 3.3 70B)
        GROQ_MODELS=llama-3.3-70b-versatile,llama-3.1-8b-instant   (opzionale)
    OPENROUTER_API_KEY=...      (free tier, modelli con suffisso ":free")
        OPENROUTER_MODELS=meta-llama/llama-3.3-70b-instruct:free   (opzionale)
    COMPARE_BASE_URL=... COMPARE_API_KEY=... COMPARE_MODEL=...      (generico OpenAI-compatible)

Il baseline Gemini usa la GEMINI_API_KEY gia' presente in .env
(saltato automaticamente se la chiave manca o con --no-baseline).
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.intelligence import OB1Intelligence
from config.ob1_config import GEMINI_API_KEY

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "llm_compare"

# Input di esempio (offline) — segnali realistici per un test immediato.
# NON e' la lista di query di produzione; serve solo a far girare il confronto
# senza rete. Per un test fedele usare --scrape o --input.
FIXTURE_SAMPLES = [
    {
        "title": "Joven mediocampista brilla en la Primera B colombiana",
        "url": "https://example-news.co/joven-mediocampista-2026",
        "content": ("El volante de 17 anos disputo 22 partidos con su club de la "
                    "segunda division, marcando 6 goles y dando 5 asistencias en "
                    "1800 minutos. Aun sin convocatorias a la seleccion ni interes "
                    "internacional reportado. Zurdo, juega de interior y media punta."),
        "deep_read": True,
    },
    {
        "title": "Nigerian U17 forward scores 14 in domestic league",
        "url": "https://example-africa.ng/u17-forward-2026",
        "content": ("The 16-year-old striker has netted 14 goals in 19 appearances "
                    "for a mid-table side. No European scouts reported, no agent "
                    "listed. Quick, two-footed, plays as a central striker."),
        "deep_read": True,
    },
    {
        "title": "Real Madrid Castilla wonderkid linked with first team",
        "url": "https://example-sports.es/castilla-wonderkid",
        "content": ("The 18-year-old has been training with the senior squad and is "
                    "valued at over 20 million euros. Multiple top clubs tracking him. "
                    "Extensive media coverage across Europe."),
        "deep_read": False,
    },
    {
        "title": "J-League rookie debut",
        "url": "https://example-jp.jp/rookie-2026",
        "content": ("Young midfielder made his debut. Played 12 minutes."),
        "deep_read": False,
    },
]


def capture_production_prompt(samples: list) -> dict:
    """
    Cattura il prompt/sistema/config ESATTI che src/intelligence.py invierebbe
    a Gemini per questi samples, senza modificare intelligence.py e senza fare
    la chiamata reale (la intercettiamo e ci fermiamo subito).
    """
    captured: dict = {}

    class _Stop(Exception):
        pass

    class _CaptureModels:
        def generate_content(self, model, contents, config):
            captured["model"] = model
            captured["prompt"] = contents
            captured["system_instruction"] = getattr(config, "system_instruction", None)
            captured["temperature"] = getattr(config, "temperature", None)
            captured["max_output_tokens"] = getattr(config, "max_output_tokens", None)
            raise _Stop()

    class _CaptureClient:
        models = _CaptureModels()

    intel = OB1Intelligence()
    intel.client = _CaptureClient()  # sovrascriviamo (anche se None per chiave mancante)
    try:
        intel.analyze_scraped_data(samples)
    except _Stop:
        pass

    if "prompt" not in captured:
        raise RuntimeError(
            "Impossibile catturare il prompt di produzione. "
            "Verifica che src/intelligence.py costruisca il prompt prima della chiamata a Gemini."
        )
    return captured


def parse_players(text: str) -> list:
    """Pulisce e fa il parse del JSON ESATTAMENTE come fa intelligence.py."""
    cleaned = text.replace("```json", "").replace("```", "").strip()
    return OB1Intelligence._extract_first_json_array(cleaned)


def call_gemini(spec: dict) -> dict:
    """
    Chiamata reale a Gemini col prompt/config catturati. Errori gestiti
    localmente (NON manda admin_alert, a differenza della pipeline).
    """
    out = {"label": f"Gemini (baseline) · {spec['model']}", "model": spec["model"],
           "text": "", "error": None, "latency": None}
    if not GEMINI_API_KEY:
        out["error"] = "GEMINI_API_KEY mancante — baseline saltato."
        return out
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        t0 = time.time()
        resp = client.models.generate_content(
            model=spec["model"],
            contents=spec["prompt"],
            config=types.GenerateContentConfig(
                system_instruction=spec["system_instruction"],
                temperature=spec["temperature"],
                max_output_tokens=spec["max_output_tokens"],
            ),
        )
        out["latency"] = time.time() - t0
        out["text"] = (resp.text or "")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def call_openai_compatible(label: str, base_url: str, api_key: str, model: str,
                           spec: dict, max_tokens: int) -> dict:
    """Chiamata a un endpoint OpenAI-compatible (Groq, OpenRouter, ecc.)."""
    out = {"label": label, "model": model, "text": "", "error": None, "latency": None}
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # OpenRouter consiglia questi header; innocui altrove.
        "HTTP-Referer": "https://github.com/mtornani/ob1-scout",
        "X-Title": "OB1 Scout LLM compare",
    }
    payload = {
        "model": model,
        "temperature": spec["temperature"],
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": spec["system_instruction"]},
            {"role": "user", "content": spec["prompt"]},
        ],
    }
    try:
        t0 = time.time()
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        out["latency"] = time.time() - t0
        if resp.status_code != 200:
            out["error"] = f"HTTP {resp.status_code}: {resp.text[:400]}"
            return out
        data = resp.json()
        out["text"] = data["choices"][0]["message"]["content"] or ""
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def build_candidates(spec: dict, max_tokens: int) -> list:
    """Costruisce la lista dei modelli candidati dalle env var disponibili."""
    import os
    candidates = []

    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        models = os.getenv("GROQ_MODELS", "llama-3.3-70b-versatile,llama-3.1-8b-instant")
        for m in [x.strip() for x in models.split(",") if x.strip()]:
            candidates.append(("Groq · " + m, "https://api.groq.com/openai/v1", groq_key, m))

    or_key = os.getenv("OPENROUTER_API_KEY", "")
    if or_key:
        models = os.getenv("OPENROUTER_MODELS", "meta-llama/llama-3.3-70b-instruct:free")
        for m in [x.strip() for x in models.split(",") if x.strip()]:
            candidates.append(("OpenRouter · " + m, "https://openrouter.ai/api/v1", or_key, m))

    base = os.getenv("COMPARE_BASE_URL", "")
    key = os.getenv("COMPARE_API_KEY", "")
    model = os.getenv("COMPARE_MODEL", "")
    if base and key and model:
        candidates.append(("Custom · " + model, base, key, model))

    return [(lambda lbl=l, b=bu, k=ky, m=mo:
             call_openai_compatible(lbl, b, k, m, spec, max_tokens))
            for (l, bu, ky, mo) in candidates], [c[0] for c in candidates]


async def scrape_samples(n_queries: int, max_items: int) -> list:
    """
    Input REALE dallo scraper di produzione (stesso codice: search + deep-read).
    Sottoinsieme di query di esempio solo per generare materiale di test —
    NON e' la lista di produzione e non la sostituisce.
    """
    sample_queries = [
        "joven promesa futbol sudamericano debut goles 2026",
        "revelação brasileirão série B menor clube 2026 scout",
        "Nigeria Ghana Senegal young football talent breakthrough 2026",
        "Japan J-League U23 young talent breakthrough 2026",
        "Ligue 2 lower league France young prospect debut 2026",
        "U17 U19 breakout performance lesser-known club 2026",
    ][:max(1, n_queries)]

    from src.scraper_global import AsyncGlobalScraper  # lazy: serve solo qui
    scraper = AsyncGlobalScraper()
    raw = await scraper.run_batch(sample_queries)
    urls = [r.get("url") for r in raw[:max_items] if r.get("url")]
    deep = await scraper.deep_read_urls(urls, max_urls=min(10, max_items))
    out = []
    for r in raw[:max_items]:
        item = dict(r)
        if item.get("url") in deep:
            item["content"] = deep[item["url"]]
            item["deep_read"] = True
        out.append(item)
    return out


def _players_summary(players: list) -> str:
    if not players:
        return "_(nessun giocatore estratto)_"
    rows = ["| Giocatore | Età | Club | Score |", "|---|---|---|---|"]
    for p in sorted(players, key=lambda x: x.get("score", 0) or 0, reverse=True):
        rows.append("| {} | {} | {} | {} |".format(
            p.get("player_name", "?"), p.get("age", "—"),
            p.get("club", "—"), p.get("score", "—")))
    return "\n".join(rows)


def render_report(spec: dict, samples: list, results: list) -> str:
    md = ["# OB1 — Confronto LLM (stesso prompt di produzione)",
          f"Generato: {datetime.now().isoformat()}",
          f"Segnali in input: {len(samples)} · "
          f"temp {spec['temperature']} · max_tokens {spec['max_output_tokens']}",
          ""]

    # Tabella riassuntiva
    md.append("## Sintesi")
    md.append("| Modello | Parse OK | # giocatori | Score max | Latenza | Errore |")
    md.append("|---|---|---|---|---|---|")
    parsed_by_model = {}
    for r in results:
        players = parse_players(r["text"]) if r["text"] else []
        parsed_by_model[r["label"]] = players
        ok = "✅" if players else ("—" if r["error"] else "⚠️ no JSON")
        top = max((p.get("score", 0) or 0 for p in players), default="—")
        lat = f"{r['latency']:.1f}s" if r["latency"] else "—"
        err = (r["error"][:60] + "…") if r["error"] and len(r["error"]) > 60 else (r["error"] or "")
        md.append(f"| {r['label']} | {ok} | {len(players)} | {top} | {lat} | {err} |")
    md.append("")

    # Dettaglio per modello
    for r in results:
        players = parsed_by_model[r["label"]]
        md.append(f"## {r['label']}")
        if r["error"]:
            md.append(f"**Errore:** `{r['error']}`")
        md.append(f"\n**Giocatori estratti ({len(players)}):**\n")
        md.append(_players_summary(players))
        md.append("\n<details><summary>Output grezzo</summary>\n")
        md.append("```\n" + (r["text"][:6000] if r["text"] else "(vuoto)") + "\n```")
        md.append("</details>\n")

    md.append("---")
    md.append("> Confronto diagnostico off-pipeline. Nessuna modifica a produzione, "
              "scoring o database. La decisione sul backend resta una scelta esplicita.")
    return "\n".join(md)


async def main():
    ap = argparse.ArgumentParser(description="Confronto LLM con prompt di produzione")
    ap.add_argument("--input", help="File JSON con lista di samples")
    ap.add_argument("--scrape", type=int, metavar="N", default=0,
                    help="Genera input reale con N query live dallo scraper")
    ap.add_argument("--no-baseline", action="store_true", help="Salta la chiamata Gemini")
    ap.add_argument("--max-items", type=int, default=20, help="Max segnali (come pipeline)")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="Override max_tokens per i candidati (default = come produzione)")
    args = ap.parse_args()

    # 1. Input
    if args.input:
        samples = json.loads(Path(args.input).read_text(encoding="utf-8"))[:args.max_items]
        src = f"file {args.input}"
    elif args.scrape > 0:
        print(f"Scraping live ({args.scrape} query)…")
        samples = await scrape_samples(args.scrape, args.max_items)
        src = f"scrape live ({args.scrape} query)"
    else:
        samples = FIXTURE_SAMPLES
        src = "fixture di esempio (offline)"
    print(f"Input: {src} — {len(samples)} segnali")

    # 2. Cattura prompt/config esatti di produzione
    spec = capture_production_prompt(samples)
    max_tokens = args.max_tokens or spec["max_output_tokens"]

    # 3. Costruisci la lista delle chiamate
    candidate_thunks, candidate_labels = build_candidates(spec, max_tokens)
    if not candidate_thunks:
        print("\n⚠️  Nessun modello candidato configurato.")
        print("   Imposta una di queste env var e rilancia:")
        print("     GROQ_API_KEY=...        (consigliato)")
        print("     OPENROUTER_API_KEY=...")
        print("     COMPARE_BASE_URL=... COMPARE_API_KEY=... COMPARE_MODEL=...")
        if args.no_baseline:
            return

    results = []
    if not args.no_baseline:
        print("→ Gemini (baseline)…")
        results.append(call_gemini(spec))
    for thunk, label in zip(candidate_thunks, candidate_labels):
        print(f"→ {label}…")
        results.append(thunk())

    if not results:
        print("Niente da confrontare. Esco.")
        return

    # 4. Report
    report = render_report(spec, samples, results)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(report, encoding="utf-8")

    # Riepilogo a console
    print("\n" + "=" * 60)
    for r in results:
        players = parse_players(r["text"]) if r["text"] else []
        status = r["error"] if r["error"] else f"{len(players)} giocatori"
        print(f"  {r['label']}: {status}")
    print("=" * 60)
    print(f"\nReport completo: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
