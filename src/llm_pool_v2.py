#!/usr/bin/env python3
"""
OB1 v2 — Pool di provider LLM con ledger di quota (Fase C1)

Il problema che risolve: oggi l'estrattore ha UN primario (Gemini) e UN
fallback, e lo stato "quota finita" vive dentro il processo. Su GitHub Actions
ogni run parte da zero: se ieri Gemini era esaurito, oggi alle 00:30 ci
riproviamo comunque, e prendiamo 429 uno dopo l'altro finché il budget del run
non finisce. Il budget stesso è una costante (INGEST_LLM_BUDGET=15) scelta a
mano, non ricavata da quanta capacità gratuita c'è davvero.

Qui il modello cambia:
  - i provider sono un REGISTRO (config/llm_providers.json), non due rami di if;
  - la quota è una RISORSA CONTABILIZZATA in un ledger persistito
    (data/llm_ledger.json), che sopravvive al singolo run;
  - il router sceglie per CLASSE DI COMPITO (fast / mid / frontier): l'estrazione
    di massa va sui provider capienti, i modelli di frontiera restano riserva
    per identità e dossier;
  - il budget di un run non si dichiara: si CHIEDE al pool (capacity()).

Nessuna dipendenza nuova: `requests` per gli endpoint OpenAI-compatible,
`google-genai` (già in requirements) solo se si usa il provider Gemini.

Tutto ciò che non è rete è codice puro e testabile: python src/llm_pool_v2.py
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG = Path(__file__).parent.parent / "config" / "llm_providers.json"
LEDGER = Path(__file__).parent.parent / "data" / "llm_ledger.json"

# Cooldown per tipo di errore. Un 429 "al minuto" si smaltisce in fretta; una
# quota giornaliera no, e insistere brucia solo tempo del run.
COOLDOWN_RATE = 120          # 429 generico / rate limit per minuto
COOLDOWN_TRANSPORT = 60      # 5xx, timeout, rete
COOLDOWN_AUTH = 86400        # chiave sbagliata/revocata: inutile ritentare oggi

# Provider senza tetto giornaliero dichiarato: non contano come "infiniti" nel
# calcolo di capacità. Meglio pianificare meno lavoro e trovarne ancora, che
# pianificarne troppo e scoprire il muro a metà run.
NO_RPD_ASSUMED = 500

# Stima token da caratteri. Grossolana ma sufficiente: serve a decidere se
# CHIEDERE, non a fatturare. Il consumo vero, quando il provider lo riporta
# nella risposta, sovrascrive la stima nel ledger.
CHARS_PER_TOKEN = 4


def est_tokens(text: str) -> int:
    return (len(text or "") // CHARS_PER_TOKEN) + 1


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return _utc_now().strftime("%Y-%m-%d")


def _seconds_to_utc_midnight() -> int:
    now = _utc_now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


# --------------------------------------------------------------------------
# Registro provider
# --------------------------------------------------------------------------

def load_providers(path: Path = CONFIG, env: dict = None, require_key: bool = True) -> list:
    """
    Provider attivi e utilizzabili, ordinati per priorità (numero basso = prima).
    Un provider senza la sua API key in ambiente viene escluso: il registro
    descrive il possibile, l'ambiente decide il disponibile.
    """
    env = os.environ if env is None else env
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for p in data.get("providers", []):
        if not p.get("active"):
            continue
        key = env.get(p.get("api_key_env") or "", "")
        if require_key and not key:
            continue
        p = dict(p)
        p["api_key"] = key
        p["limits"] = p.get("limits") or {}
        p["classes"] = p.get("classes") or ["fast"]
        out.append(p)
    out.sort(key=lambda x: x.get("priority", 100))
    return out


# --------------------------------------------------------------------------
# Ledger di quota (persistito su file, non in memoria di processo)
# --------------------------------------------------------------------------

class QuotaLedger:
    """
    Contabilità per provider e per giorno UTC: chiamate, token, cooldown.

    Sta su file JSON perché deve sopravvivere alla fine del run (i container
    Actions sono usa e getta) ed essere leggibile a occhio in un diff git.
    Scrittore singolo: con più shard in parallelo ogni shard tiene il suo file
    e il job reducer li somma (vedi FASE_C.md).
    """

    def __init__(self, path: Path = LEDGER):
        self.path = Path(path)
        self.data = {"date": _today(), "providers": {}}
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("providers") is not None:
                    self.data = loaded
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Ledger illeggibile ({e}): riparto pulito.")
        self._roll_day()

    def _roll_day(self):
        """A mezzanotte UTC i contatori giornalieri ripartono; i cooldown no."""
        if self.data.get("date") != _today():
            for st in self.data.get("providers", {}).values():
                st["calls"] = 0
                st["tokens_in"] = 0
                st["tokens_out"] = 0
                st["errors"] = 0
            self.data["date"] = _today()

    def state(self, pid: str) -> dict:
        return self.data.setdefault("providers", {}).setdefault(
            pid, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "errors": 0,
                  "cooldown_until": None, "last_error": None})

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
        except OSError as e:
            logger.warning(f"Ledger non salvato: {e}")

    # --- letture ---

    def in_cooldown(self, pid: str) -> bool:
        until = self.state(pid).get("cooldown_until")
        if not until:
            return False
        try:
            return _utc_now() < datetime.fromisoformat(until)
        except ValueError:
            return False

    def remaining(self, provider: dict) -> dict:
        """Quanto resta oggi a questo provider: chiamate e token (None = nessun tetto)."""
        st = self.state(provider["id"])
        lim = provider.get("limits") or {}
        rpd, tpd = lim.get("rpd"), lim.get("tpd")
        used_tokens = st["tokens_in"] + st["tokens_out"]
        return {
            "calls": None if rpd is None else max(0, rpd - st["calls"]),
            "tokens": None if tpd is None else max(0, tpd - used_tokens),
        }

    # --- scritture ---

    def record(self, pid: str, tokens_in: int, tokens_out: int):
        st = self.state(pid)
        st["calls"] += 1
        st["tokens_in"] += max(0, tokens_in)
        st["tokens_out"] += max(0, tokens_out)
        st["cooldown_until"] = None      # una chiamata riuscita chiude il cooldown

    def penalize(self, pid: str, seconds: int, reason: str = ""):
        st = self.state(pid)
        st["errors"] += 1
        st["last_error"] = reason[:200]
        st["cooldown_until"] = (_utc_now() + timedelta(seconds=seconds)).isoformat()


# --------------------------------------------------------------------------
# Classificazione errori
# --------------------------------------------------------------------------

def classify_error(status: int = None, message: str = "") -> tuple:
    """
    (cooldown_seconds, etichetta). Distinguere serve: un limite giornaliero e un
    limite al minuto si assomigliano nel testo ma vanno trattati all'opposto.
    """
    m = (message or "").lower()
    if status in (401, 403) or "invalid api key" in m or "unauthorized" in m:
        return COOLDOWN_AUTH, "auth"
    if status == 413 or "too large" in m or "context length" in m or "reduce the length" in m:
        return 0, "too_large"            # non è quota: si riprova più corto
    daily = any(w in m for w in ("per day", "daily", "rpd", "tpd", "quota exceeded",
                                 "resource_exhausted", "requests per day"))
    if status == 429 or "rate limit" in m or "quota" in m or daily:
        return (_seconds_to_utc_midnight() if daily else COOLDOWN_RATE,
                "daily" if daily else "rate")
    return COOLDOWN_TRANSPORT, "transport"


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

class LLMPool:
    """
    Un solo metodo conta: complete(). Gli dai la classe del compito e una
    funzione che costruisce il prompt data una lunghezza massima (perché ogni
    provider ha un tetto di input diverso), lui trova chi ha quota e risponde.
    """

    def __init__(self, providers: list = None, ledger: QuotaLedger = None,
                 pace: bool = True):
        self.providers = providers if providers is not None else load_providers()
        self.ledger = ledger or QuotaLedger()
        self.pace = pace
        self._last_call = {}     # pid -> timestamp, per rispettare gli RPM
        self._clients = {}       # pid -> client Gemini (riuso)
        self.calls = 0

    # --- selezione ---

    def candidates(self, task_class: str = "fast", need_tokens: int = 0) -> list:
        out = []
        for p in self.providers:
            if task_class not in p["classes"]:
                continue
            if self.ledger.in_cooldown(p["id"]):
                continue
            rem = self.ledger.remaining(p)
            if rem["calls"] is not None and rem["calls"] <= 0:
                continue
            if rem["tokens"] is not None and rem["tokens"] < max(1, need_tokens):
                continue
            out.append(p)
        return out

    def capacity(self, task_class: str = "fast", tokens_per_call: int = 900) -> int:
        """
        Quante chiamate di questa classe il pool può ancora servire OGGI.
        È così che il budget di un run smette di essere una costante scritta a
        mano e diventa una misura. None-limiti = provider senza tetto noto:
        contribuiscono un valore prudenziale, non infinito.
        """
        total = 0
        for p in self.providers:
            if task_class not in p["classes"] or self.ledger.in_cooldown(p["id"]):
                continue
            rem = self.ledger.remaining(p)
            by_calls = rem["calls"] if rem["calls"] is not None else NO_RPD_ASSUMED
            by_tokens = (rem["tokens"] // max(1, tokens_per_call)
                         if rem["tokens"] is not None else NO_RPD_ASSUMED)
            total += max(0, min(by_calls, by_tokens))
        return total

    def report(self) -> list:
        """Stato leggibile del pool (per log e per docs/data/llm_health.json)."""
        rows = []
        for p in self.providers:
            st = self.ledger.state(p["id"])
            rem = self.ledger.remaining(p)
            rows.append({
                "id": p["id"], "label": p.get("label", p["id"]),
                "classes": p["classes"], "model": p.get("model"),
                "calls_today": st["calls"], "tokens_today": st["tokens_in"] + st["tokens_out"],
                "calls_left": rem["calls"], "tokens_left": rem["tokens"],
                "cooldown": self.ledger.in_cooldown(p["id"]),
                "last_error": st.get("last_error"),
            })
        return rows

    # --- esecuzione ---

    def _respect_rpm(self, p: dict):
        rpm = (p.get("limits") or {}).get("rpm")
        if not self.pace or not rpm:
            return
        gap = 60.0 / float(rpm)
        last = self._last_call.get(p["id"])
        if last is not None:
            wait = gap - (time.time() - last)
            if wait > 0:
                time.sleep(wait)

    def complete(self, prompt_fn, system: str = "", task_class: str = "fast",
                 max_output_tokens: int = 8192, temperature: float = 0.0,
                 timeout: int = 120):
        """
        prompt_fn(max_chars) -> str : costruisce il prompt su misura del provider.
        Ritorna dict {text, provider, model, tokens_in, tokens_out} oppure None
        se nessun provider della classe ha quota (il chiamante NON deve marcare
        il lavoro come fatto: va rimesso in coda).
        """
        for p in self.candidates(task_class):
            max_chars = (p.get("limits") or {}).get("max_input_chars") or 8000
            for attempt in (1, 2):
                prompt = prompt_fn(max_chars)
                need = est_tokens(prompt) + est_tokens(system)
                self._respect_rpm(p)
                try:
                    text, tin, tout = self._dispatch(p, system, prompt, max_output_tokens,
                                                     temperature, timeout)
                    self._last_call[p["id"]] = time.time()
                    self.ledger.record(p["id"], tin or need, tout or est_tokens(text))
                    self.ledger.save()
                    self.calls += 1
                    return {"text": text, "provider": p["id"], "model": p.get("model"),
                            "tokens_in": tin or need, "tokens_out": tout or est_tokens(text)}
                except ProviderError as e:
                    self._last_call[p["id"]] = time.time()
                    cooldown, kind = classify_error(e.status, str(e))
                    if kind == "too_large" and attempt == 1:
                        max_chars = max(1200, max_chars // 2)
                        logger.warning(f"{p['id']}: input troppo lungo, riprovo a {max_chars} char")
                        continue
                    self.ledger.penalize(p["id"], cooldown, f"{kind}: {e}")
                    self.ledger.save()
                    logger.warning(f"{p['id']} fuori per {cooldown}s ({kind}): {e}")
                    break
        return None

    def _dispatch(self, p: dict, system: str, prompt: str, max_output_tokens: int,
                  temperature: float, timeout: int) -> tuple:
        if p.get("kind") == "gemini":
            return self._call_gemini(p, system, prompt, max_output_tokens, temperature)
        return self._call_openai(p, system, prompt, max_output_tokens, temperature, timeout)

    def _call_openai(self, p, system, prompt, max_output_tokens, temperature, timeout):
        import requests
        try:
            r = requests.post(
                p["base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {p['api_key']}",
                         "Content-Type": "application/json"},
                json={"model": p["model"], "temperature": temperature,
                      "max_tokens": max_output_tokens,
                      "messages": ([{"role": "system", "content": system}] if system else [])
                                  + [{"role": "user", "content": prompt}]},
                timeout=timeout)
        except Exception as e:                       # rete/timeout
            raise ProviderError(str(e), status=None)
        if r.status_code != 200:
            raise ProviderError(r.text[:300], status=r.status_code)
        body = r.json()
        usage = body.get("usage") or {}
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return text, usage.get("prompt_tokens"), usage.get("completion_tokens")

    def _call_gemini(self, p, system, prompt, max_output_tokens, temperature):
        try:
            from google import genai
            from google.genai import types
        except ImportError as e:
            raise ProviderError(f"google-genai non installato: {e}", status=None)
        client = self._clients.get(p["id"])
        if client is None:
            try:
                client = self._clients[p["id"]] = genai.Client(api_key=p["api_key"])
            except Exception as e:
                raise ProviderError(str(e), status=401)
        try:
            resp = client.models.generate_content(
                model=p["model"], contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system or None, temperature=temperature,
                    max_output_tokens=max_output_tokens))
        except Exception as e:
            status = 429 if any(w in str(e).lower() for w in
                                ("429", "resource_exhausted", "quota")) else None
            raise ProviderError(str(e), status=status)
        um = getattr(resp, "usage_metadata", None)
        return (resp.text or "",
                getattr(um, "prompt_token_count", None),
                getattr(um, "candidates_token_count", None))


class ProviderError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# Self-test offline (nessuna rete)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "ledger.json"
    fake = [
        {"id": "cerebras", "label": "Cerebras", "kind": "openai", "priority": 10,
         "classes": ["fast"], "model": "gpt-oss-120b", "api_key": "x",
         "limits": {"rpm": 30, "rpd": None, "tpd": 1000000, "max_input_chars": 24000}},
        {"id": "groq", "label": "Groq", "kind": "openai", "priority": 20,
         "classes": ["fast", "mid"], "model": "llama-3.3-70b", "api_key": "x",
         "limits": {"rpm": 30, "rpd": 1000, "tpd": 100000, "max_input_chars": 2800}},
        {"id": "gemini", "label": "Gemini", "kind": "gemini", "priority": 30,
         "classes": ["mid", "frontier"], "model": "gemini-2.5-flash", "api_key": "x",
         "limits": {"rpm": 10, "rpd": 250, "max_input_chars": 24000}},
    ]
    pool = LLMPool(providers=fake, ledger=QuotaLedger(tmp), pace=False)

    cap_fast = pool.capacity("fast", tokens_per_call=900)
    cap_frontier = pool.capacity("frontier", tokens_per_call=2000)
    print(f"capacità oggi — fast: {cap_fast} chiamate · frontier: {cap_frontier}")
    # Cerebras: nessun tetto di richieste dichiarato -> vale il prudenziale (500),
    # non 1M/900. Groq: 100k token / 900 = 111. Totale 611, non "infinito".
    assert cap_fast == NO_RPD_ASSUMED + 111, cap_fast
    assert cap_frontier == 250, cap_frontier  # solo Gemini, 250 RPD

    # Il ledger consuma davvero
    for _ in range(50):
        pool.ledger.record("groq", 1500, 300)
    assert pool.ledger.remaining(fake[1])["calls"] == 950
    assert pool.ledger.remaining(fake[1])["tokens"] == 100000 - 50 * 1800

    # Quota giornaliera finita -> il provider esce dai candidati, la classe no
    for _ in range(250):
        pool.ledger.record("gemini", 1000, 200)
    assert [p["id"] for p in pool.candidates("frontier")] == []
    assert "groq" in [p["id"] for p in pool.candidates("mid")]

    # Un 429 "per day" spegne fino a mezzanotte UTC, uno al minuto no
    assert classify_error(429, "You exceeded your requests per day")[1] == "daily"
    assert classify_error(429, "rate limit reached for 12000 TPM")[1] == "rate"
    assert classify_error(413, "request too large")[0] == 0
    assert classify_error(401, "invalid api key")[1] == "auth"

    # Un provider in cooldown esce; la classe continua a essere servita da altri
    pool.ledger.penalize("cerebras", 120, "429: rate")
    assert [p["id"] for p in pool.candidates("fast")] == ["groq"]
    # Finiti anche i token di Groq, la classe 'fast' è davvero a secco
    for _ in range(6):
        pool.ledger.record("groq", 1500, 300)
    assert pool.candidates("fast") == []
    assert pool.capacity("fast") == 0

    # Il ledger sopravvive al processo (è questo il punto)
    pool.ledger.save()
    again = QuotaLedger(tmp)
    assert again.state("groq")["calls"] == 56
    assert again.in_cooldown("cerebras")

    print("Ledger persistito:", tmp)
    real = load_providers()
    print(f"Provider utilizzabili con le chiavi presenti in ambiente: "
          f"{[p['id'] for p in real] or 'nessuno'}")
    print("OK — self-test pool superato.")
