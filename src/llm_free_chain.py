#!/usr/bin/env python3
"""
OB1 v2 — Catena LLM a costo zero

Perché esiste: con il billing acceso sul progetto Google, Gemini non si ferma
al free tier — supera la quota e FATTURA. La difesa non è "usare meno Gemini":
è non chiamarlo per primo. Qui c'è la catena di provider gratuiti (Groq,
Cerebras, OpenRouter, NVIDIA, o un qualsiasi endpoint OpenAI-compatible) che
l'estrattore prova PRIMA, tenendo Gemini come rete di sicurezza — o escludendolo
del tutto.

Tre modalità (env OB1_LLM_MODE):
  free_first   provider gratuiti, poi Gemini se tutti falliscono  [default se
               esiste almeno una chiave free]
  free_only    mai Gemini: costo garantito zero, anche a costo di saltare fonti
  gemini_first comportamento storico (Gemini primario, free come fallback)

Tutti i provider parlano l'API /v1/chat/completions di OpenAI, quindi cambiarne
uno è una riga di configurazione, non un ramo di codice.

Test offline: python src/llm_free_chain.py
"""

import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

VALID_MODES = ("free_first", "free_only", "gemini_first")

# I rate limit dei free tier sono a finestra SCORREVOLE, non giornaliera: Groq
# risponde "Rate limit reached ... tokens per day (TPD)" ma nello stesso
# messaggio dice "Please try again in 3.456s". Trattare quel 429 come "provider
# finito per oggi" costa un run intero — e in free_first spinge il lavoro su
# Gemini, cioè esattamente sulla fattura che stiamo evitando. Quindi: se il
# provider dice quando riprovare e l'attesa è breve, si aspetta e si riprova.
FREE_RETRY_WAIT_MAX = float(os.getenv("FREE_RETRY_WAIT_MAX", "20"))

# L'unità non può essere seguita da una lettera (così "5m45.6s" si legge
# tutto, e "12000 TPM" non diventa una durata).
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)(?![A-Za-z])", re.I)
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

# Tetto di input per i provider gratuiti: i free tier limitano i token al
# minuto (Groq ~12k TPM) e una richiesta troppo grande torna 413 o 429.
# ~2800 char ≈ 2.2k token. Gemini, quando lo si usa, riceve il testo pieno.
FREE_MAX_CHARS = int(os.getenv("FREE_MAX_CHARS", "2800"))

# Ordine della catena: prima chi ha il free tier più capiente e affidabile.
# Ogni voce è (env_chiave, base_url, env_modello, modello_default, label).
FREE_PROVIDER_SPECS = [
    ("GROQ_API_KEY", "https://api.groq.com/openai/v1",
     "GROQ_MODEL", "llama-3.3-70b-versatile", "groq"),
    # Cerebras ha ritirato i Llama dal catalogo free (verificato 3 ago 2026:
    # /v1/models espone gemma-4-31b, zai-glm-4.7, gpt-oss-120b). Un modello che
    # non esiste torna 404 e brucia un anello della catena: il default segue il
    # catalogo, e CEREBRAS_MODEL resta per cambiarlo senza toccare il codice.
    ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1",
     "CEREBRAS_MODEL", "gpt-oss-120b", "cerebras"),
    ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
     "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free", "openrouter"),
    ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1",
     "NVIDIA_MODEL", "meta/llama-3.3-70b-instruct", "nvidia"),
]


def resolve_free_providers(env: dict = None) -> list:
    """
    Provider gratuiti configurati, in ordine di catena. Un provider entra solo
    se la sua chiave è in ambiente: il codice descrive il possibile, i secrets
    decidono il reale.

    In coda, se presente, l'endpoint generico COMPARE_BASE_URL+COMPARE_API_KEY
    (lo stesso già usato da compare_llm.py, così una chiave sperimentata lì si
    può promuovere in produzione senza toccare codice).
    """
    env = os.environ if env is None else env
    out = []
    for key_env, base_url, model_env, default_model, label in FREE_PROVIDER_SPECS:
        api_key = (env.get(key_env) or "").strip()
        if not api_key:
            continue
        out.append({"label": label, "base_url": base_url, "api_key": api_key,
                    "model": (env.get(model_env) or default_model).strip()})
    base, key = (env.get("COMPARE_BASE_URL") or "").strip(), (env.get("COMPARE_API_KEY") or "").strip()
    if base and key:
        out.append({"label": "custom", "base_url": base, "api_key": key,
                    "model": (env.get("COMPARE_MODEL") or "gpt-4o-mini").strip()})
    return out


def resolve_llm_mode(explicit: str = None, env: dict = None) -> str:
    """
    Modalità effettiva. Se nessuno ha deciso e c'è almeno una chiave gratuita,
    il default è free_first: la scelta sicura è quella che non può generare una
    fattura per distrazione.
    """
    env = os.environ if env is None else env
    mode = (explicit or env.get("OB1_LLM_MODE") or "").strip().lower()
    if mode in VALID_MODES:
        return mode
    if mode:
        logger.warning(f"OB1_LLM_MODE='{mode}' non riconosciuto: uso il default.")
    return "free_first" if resolve_free_providers(env) else "gemini_first"


def is_quota_error(exc_or_msg, status: int = None) -> bool:
    """429, quota, TPM/RPD esauriti: il provider è finito per ora, si passa oltre."""
    if status in (429, 402):
        return True
    s = str(exc_or_msg).lower()
    return any(w in s for w in ("429", "quota", "resource_exhausted", "rate limit",
                                "tokens per minute", "tpm", "rpd", "insufficient_quota",
                                "too many requests"))


def parse_duration(text: str):
    """
    '3.456s' → 3.456 · '280ms' → 0.28 · '5m45.6s' → 345.6 · '2h' → 7200.
    None se non c'è una durata riconoscibile.
    """
    parts = _DURATION.findall(text or "")
    if not parts:
        return None
    return sum(float(v) * _UNIT_SECONDS[u.lower()] for v, u in parts)


def parse_retry_seconds(message: str = "", headers: dict = None):
    """
    Quanto aspettare prima di ritentare, secondo il provider stesso. Guarda
    (in ordine) il messaggio d'errore, l'header standard Retry-After e gli
    header x-ratelimit-reset-*. None se il provider non lo dice.
    """
    m = re.search(r"try again in\s+(.{0,24})", message or "", re.I)
    if m:
        secs = parse_duration(m.group(1))   # "3.456s" · "2m30s" · "280ms"
        if secs is not None:
            return secs
    for key in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        raw = (headers or {}).get(key) or (headers or {}).get(key.title())
        if not raw:
            continue
        try:
            return float(raw)          # Retry-After è in secondi interi
        except (TypeError, ValueError):
            secs = parse_duration(str(raw))
            if secs is not None:
                return secs
    return None


def call_openai_chat(provider: dict, system: str, prompt: str,
                     max_tokens: int = 8192, temperature: float = 0.0,
                     timeout: int = 120) -> str:
    """
    Una chiamata a un endpoint OpenAI-compatible. Solleva ProviderCallError con
    lo status HTTP, così il chiamante distingue "quota finita" da "chiave
    sbagliata" da "il sito è giù".
    """
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    try:
        resp = requests.post(
            provider["base_url"].rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {provider['api_key']}",
                     "Content-Type": "application/json"},
            json={"model": provider["model"], "temperature": temperature,
                  "max_tokens": max_tokens, "messages": messages},
            timeout=timeout)
    except requests.RequestException as e:
        raise ProviderCallError(f"{provider['label']}: rete/timeout: {e}") from e
    if resp.status_code != 200:
        raise ProviderCallError(f"{provider['label']}: HTTP {resp.status_code}: "
                                f"{resp.text[:200]}", status=resp.status_code,
                                headers=dict(resp.headers))
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, ValueError) as e:
        raise ProviderCallError(f"{provider['label']}: risposta illeggibile: {e}") from e


def call_free_chain(providers: list, system: str, prompt: str,
                    dead: set = None, max_tokens: int = 8192,
                    temperature: float = 0.0, timeout: int = 120) -> tuple:
    """
    Prova i provider in ordine finché uno risponde.

    Ritorna (testo, label) al primo successo, (None, None) se hanno fallito
    tutti. `dead` è un set di label da saltare e che questa funzione aggiorna:
    un provider che ha esaurito la quota non va ritentato a ogni fonte —
    ogni 429 costa tempo di run e non produce niente.
    """
    dead = dead if dead is not None else set()
    for p in providers:
        if p["label"] in dead:
            continue
        for attempt in (1, 2):
            try:
                text = call_openai_chat(p, system, prompt, max_tokens, temperature, timeout)
                if text.strip():
                    return text, p["label"]
                logger.warning(f"{p['label']}: risposta vuota, provo il prossimo.")
                break
            except ProviderCallError as e:
                if e.status in (401, 403):
                    logger.error(f"{p['label']}: chiave rifiutata → escluso dal run.")
                    dead.add(p["label"])
                    break
                if is_quota_error(e, e.status):
                    wait = e.retry_after
                    if attempt == 1 and wait is not None and wait <= FREE_RETRY_WAIT_MAX:
                        logger.warning(f"{p['label']}: rate limit, riprovo tra {wait:.1f}s "
                                       f"(finestra scorrevole, non quota esaurita).")
                        time.sleep(wait + 0.5)
                        continue
                    logger.warning(f"{p['label']}: quota/rate limit esaurita → escluso dal run.")
                    dead.add(p["label"])
                    break
                logger.error(str(e))
                break
    return None, None


class ProviderCallError(RuntimeError):
    def __init__(self, message, status=None, headers=None):
        super().__init__(message)
        self.status = status
        self.headers = headers or {}

    @property
    def retry_after(self):
        """Secondi di attesa suggeriti dal provider, o None se non lo dice."""
        return parse_retry_seconds(str(self), self.headers)


# --------------------------------------------------------------------------
# Self-test offline (nessuna rete)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    # 1) Ordine della catena e default dei modelli
    env = {"GROQ_API_KEY": "g", "CEREBRAS_API_KEY": "c", "OPENROUTER_API_KEY": "o"}
    chain = resolve_free_providers(env)
    assert [p["label"] for p in chain] == ["groq", "cerebras", "openrouter"], chain
    assert chain[0]["model"] == "llama-3.3-70b-versatile"
    assert chain[1]["model"] == "gpt-oss-120b"     # catalogo Cerebras, non Llama
    assert chain[2]["model"].endswith(":free")

    # Override del modello via env, e endpoint generico in coda
    env2 = dict(env, GROQ_MODEL="llama-3.1-8b-instant",
                COMPARE_BASE_URL="https://x/v1", COMPARE_API_KEY="k")
    chain2 = resolve_free_providers(env2)
    assert chain2[0]["model"] == "llama-3.1-8b-instant"
    assert chain2[-1]["label"] == "custom"

    # 2) Modalità: senza scelta esplicita, con chiavi free il default è free_first
    assert resolve_llm_mode(env={"GROQ_API_KEY": "g"}) == "free_first"
    assert resolve_llm_mode(env={}) == "gemini_first"          # nessuna free: storico
    assert resolve_llm_mode(env={"OB1_LLM_MODE": "free_only", "GROQ_API_KEY": "g"}) == "free_only"
    assert resolve_llm_mode("gemini_first", env={"GROQ_API_KEY": "g"}) == "gemini_first"
    assert resolve_llm_mode(env={"OB1_LLM_MODE": "boh", "GROQ_API_KEY": "g"}) == "free_first"

    # 3) Durate e "quando riprovare" — messaggi veri di Groq (3 ago 2026)
    assert parse_duration("3.456s") == 3.456 and parse_duration("280ms") == 0.28
    assert parse_duration("5m45.6s") == 345.6 and parse_duration("2h") == 7200
    assert parse_duration("Limit 12000 TPM") is None     # non è una durata
    groq_429 = ("Rate limit reached for model `llama-3.3-70b-versatile` on tokens per "
                "day (TPD): Limit 100000, Used 99870, Requested 134. Please try again "
                "in 3.456s. Need more tokens? Upgrade to Dev Tier")
    assert abs(parse_retry_seconds(groq_429) - 3.456) < 0.01
    assert parse_retry_seconds("429", {"retry-after": "12"}) == 12.0
    assert parse_retry_seconds("429", {"x-ratelimit-reset-tokens": "280ms"}) == 0.28
    assert parse_retry_seconds("429 nessun suggerimento") is None

    # 4) Riconoscimento errori di quota (i messaggi veri dei provider)
    assert is_quota_error("HTTP 429: Rate limit reached for llama-3.3-70b")
    assert is_quota_error("RESOURCE_EXHAUSTED: quota exceeded")
    assert is_quota_error("qualsiasi cosa", status=429)
    assert not is_quota_error("HTTP 500: internal server error")
    assert not is_quota_error("connessione rifiutata")

    # 5) La catena salta i provider morti e si ferma al primo che risponde
    calls = []

    def fake_call(provider, system, prompt, *a, **k):
        calls.append(provider["label"])
        if provider["label"] == "groq":
            raise ProviderCallError("groq: HTTP 429: rate limit", status=429)
        if provider["label"] == "cerebras":
            return '[{"name": "Kauan Ribeiro"}]'
        return "non dovrebbe arrivarci"

    _real = call_openai_chat
    globals()["call_openai_chat"] = fake_call
    dead = set()
    text, label = call_free_chain(chain, "sys", "prompt", dead=dead)
    assert (label, text) == ("cerebras", '[{"name": "Kauan Ribeiro"}]'), (label, text)
    assert dead == {"groq"}, dead
    calls.clear()
    text, label = call_free_chain(chain, "sys", "prompt", dead=dead)
    assert calls == ["cerebras"], calls          # groq non viene più ritentato

    # Tutti morti: (None, None), così il chiamante NON marca il lavoro come fatto
    dead = {"groq", "cerebras", "openrouter"}
    assert call_free_chain(chain, "s", "p", dead=dead) == (None, None)
    globals()["call_openai_chat"] = _real

    live = resolve_free_providers()
    print(f"modalità effettiva: {resolve_llm_mode()}")
    print(f"catena free con le chiavi in ambiente: "
          f"{[p['label'] for p in live] or 'nessuna (imposta GROQ_API_KEY)'}")
    print("OK — self-test catena free superato.")
