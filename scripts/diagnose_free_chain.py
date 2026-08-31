#!/usr/bin/env python3
"""
OB1 Global — stato reale della catena LLM gratuita

Perché
------
Nel run del 31 ago 2026 (05:57 UTC) due anelli su tre erano morti e il log
lo diceva solo di sfuggita, in mezzo a tutto il resto:

    openrouter: modello non trovato → escluso dal run. HTTP 404
      "This model is unavailable for free..."
    nvidia: rete/timeout ... (x6, 120s l'una)   <- 12 minuti su 24 di run
    nvidia: modello non trovato → escluso dal run. HTTP 404

Groq ha retto tutte e 15 le chiamate da solo. Va bene finché regge: il
giorno che Groq cade, la catena non ha nessun anello vivo dietro.

Questo script chiede a ogni provider il proprio CATALOGO (GET /models,
standard OpenAI) e poi prova davvero ogni rotta. Tiene separate due
domande che è facile confondere, e confonderle è quello che ha fatto
sostituire due volte un nome con un altro nome sbagliato:

  1. il MODELLO esiste ancora nel catalogo del provider?
     -> se no, il nome in FREE_PROVIDER_SPECS è stantio, e il catalogo
        dice come si chiama adesso.
  2. la CHIAVE funziona e la rotta risponde entro un tempo utile?
     -> un modello può essere in catalogo ed essere comunque irraggiungibile
        col piano corrente ("unavailable for free"), o rispondere così
        lentamente da non essere usabile (i 120s di nemotron-ultra).

Un 404 sul catalogo e un 404 su una chiamata vogliono dire cose opposte.

CANDIDATI: OB1_PROBE_EXTRA="label=modello,label=modello" prova nomi NON
ancora in configurazione, riusando la chiave di quel provider. Serve a
scegliere un rimpiazzo dopo averlo visto rispondere, invece di metterlo in
produzione e scoprirlo al cron dopo. Nota: le misure fatte su un ALTRO
account dello stesso provider non valgono qui — la disponibilità free è
per account, e i due repo OB1 usano chiavi OpenRouter diverse (user_id
differenti nei rispettivi 404).

Nessuna scrittura: niente database, niente feed. Le prove sono da poche
decine di token in uscita.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm_free_chain import FREE_PROVIDER_SPECS  # noqa: E402

PROBE_PROMPT = 'Rispondi in json: {"esito":"ok"}'
# I modelli reasoning spendono token in ragionamento PRIMA del contenuto:
# con un tetto basso il budget finisce lì e `content` torna vuoto, facendo
# risultare morta una rotta viva. Errore già commesso e corretto sull'altro
# repo OB1 — qui si parte col tetto giusto.
PROBE_MAX_TOKENS = 512
# nemotron-3-ultra ha sfondato i 120s in produzione. 45s è la soglia oltre
# la quale una rotta non è "lenta", è inutilizzabile per un run con budget.
PROBE_TIMEOUT_S = 45
CATALOG_TIMEOUT_S = 30
ERR_SNIPPET = 400


def _mask(key: str) -> str:
    """Forma della chiave, mai il valore: distingue 'assente' da 'presente
    ma rifiutata' leggendo un log pubblico di GitHub Actions."""
    return f"(impostata, {len(key)} char)" if key else "(assente)"


def catalogo(base_url: str, api_key: str) -> Tuple[Optional[List[str]], str]:
    """Lista modelli via GET /models. None = catalogo non interrogabile —
    che NON vuol dire provider morto: alcuni endpoint compatibili non
    espongono /models pur servendo /chat/completions."""
    try:
        r = requests.get(f"{base_url.rstrip('/')}/models",
                         headers={"Authorization": f"Bearer {api_key}"},
                         timeout=CATALOG_TIMEOUT_S)
    except Exception as e:
        return None, f"irraggiungibile ({type(e).__name__})"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        data = r.json().get("data")
    except ValueError:
        return None, "risposta non JSON"
    if not isinstance(data, list):
        return None, "formato inatteso"
    ids = sorted(m.get("id", "") for m in data if isinstance(m, dict) and m.get("id"))
    return [i for i in ids if i], f"{len(ids)} modelli"


def prova(base_url: str, api_key: str, model: str) -> Tuple[bool, str, float]:
    """Una chiamata vera. Ritorna (ok, dettaglio, secondi)."""
    t0 = time.time()
    try:
        r = requests.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": model, "temperature": 0.0,
                  "max_tokens": PROBE_MAX_TOKENS,
                  "messages": [{"role": "user", "content": PROBE_PROMPT}]},
            timeout=PROBE_TIMEOUT_S)
    except Exception as e:
        return False, f"trasporto: {type(e).__name__}", time.time() - t0
    dt = time.time() - t0
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:ERR_SNIPPET]}", dt
    try:
        msg = r.json()["choices"][0]["message"]
    except Exception:
        return False, f"risposta illeggibile: {r.text[:ERR_SNIPPET]}", dt
    contenuto = (msg.get("content") or "").strip()
    if contenuto:
        return True, contenuto[:60].replace("\n", " "), dt
    # Contenuto vuoto ma ragionamento presente: la rotta è viva, è il tetto
    # di token della PROVA a essere finito prima del testo finale.
    if str(msg.get("reasoning_content") or msg.get("reasoning") or "").strip():
        return True, "(solo reasoning: rotta viva, tetto token della prova)", dt
    return False, "risposta vuota (200 ma nessun contenuto)", dt


def main() -> int:
    env = os.environ
    print("=== Catena LLM gratuita: stato reale ===\n")
    vivi: List[str] = []
    morti: List[str] = []
    per_label: Dict[str, Tuple[str, str]] = {}   # label -> (base_url, key)

    for key_env, base_url, model_env, default_model, label in FREE_PROVIDER_SPECS:
        api_key = (env.get(key_env) or "").strip()
        model = (env.get(model_env) or default_model).strip()
        per_label[label] = (base_url, api_key)
        print(f"--- {label} · {base_url} · chiave {_mask(api_key)}")
        if not api_key:
            print("    saltato: nessuna chiave in ambiente\n")
            continue

        cat, nota = catalogo(base_url, api_key)
        print(f"    catalogo /models: {nota}")
        in_cat = ""
        if cat is not None:
            in_cat = "in catalogo" if model in cat else "NON in catalogo"

        ok, dettaglio, dt = prova(base_url, api_key, model)
        riga = f"    {'OK ' if ok else 'KO '} {model} ({dt:.1f}s)"
        if in_cat:
            riga += f" [{in_cat}]"
        print(riga)
        if not ok:
            print(f"         -> {dettaglio}")
        (vivi if ok else morti).append(f"{label}/{model}")

        # Il catalogo serve a chi deve scegliere un rimpiazzo: senza, si
        # torna a indovinare. Mostrato solo quando serve davvero, e solo i
        # gratuiti, per non seppellire il log.
        if cat is not None and model not in cat:
            free = [m for m in cat if m.endswith(":free")]
            if free:
                print(f"    modelli :free nel catalogo di {label} ({len(free)}):")
                for m in free:
                    print(f"      · {m}")
            else:
                print(f"    catalogo di {label} ({len(cat)} modelli, nessuno :free):")
                for m in cat[:40]:
                    print(f"      · {m}")
        print()

    print("=== riepilogo ===")
    print(f"anelli vivi: {len(vivi)}")
    for v in vivi:
        print(f"  OK  {v}")
    print(f"anelli morti: {len(morti)}")
    for m in morti:
        print(f"  KO  {m}")
    if not vivi and not morti:
        print("\n  Nessun anello configurato: nessuna chiave in ambiente.")
    elif not vivi:
        print("\n  ATTENZIONE: nessun anello vivo. L'ingest non ha provider.")
    elif len(vivi) == 1:
        print("\n  ATTENZIONE: la catena regge su un anello solo. Il giorno che")
        print("  cade, l'ingest non ha nessun provider dietro.")

    # --- candidati non ancora in configurazione ---
    raw = (env.get("OB1_PROBE_EXTRA") or "").strip()
    if raw:
        print("\n=== candidati (non in configurazione) ===")
        for chunk in raw.split(","):
            if "=" not in chunk:
                continue
            label, _, model = chunk.strip().partition("=")
            label, model = label.strip(), model.strip()
            if label not in per_label:
                print(f"  ??  {label}: provider sconosciuto")
                continue
            base_url, api_key = per_label[label]
            if not api_key:
                print(f"  ??  {label}/{model}: nessuna chiave per questo provider")
                continue
            ok, dettaglio, dt = prova(base_url, api_key, model)
            print(f"  {'OK ' if ok else 'KO '} {label}/{model} ({dt:.1f}s)")
            print(f"       -> {dettaglio}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
