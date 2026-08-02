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

import requests

logger = logging.getLogger(__name__)

VALID_MODES = ("free_first", "free_only", "gemini_first")

# Tetto di input per i provider gratuiti: i free tier limitano i token al
# minuto (Groq ~12k TPM) e una richiesta troppo grande torna 413 o 429.
# ~2800 char ≈ 2.2k token. Gemini, quando lo si usa, riceve il testo pieno.
FREE_MAX_CHARS = int(os.getenv("FREE_MAX_CHARS", "2800"))

# Ordine della catena: prima chi ha il free tier più capiente e affidabile.
# Ogni voce è (env_chiave, base_url, env_modello, modello_default, label).
FREE_PROVIDER_SPECS = [
    ("GROQ_API_KEY", "https://api.groq.com/openai/v1",
     "GROQ_MODEL", "llama-3.3-70b-versatile", "groq"),
    ("CEREBRAS_API_KEY", "https://api.cerebras.ai/v1",
     "CEREBRAS_MODEL", "llama-3.3-70b", "cerebras"),
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
                                f"{resp.text[:200]}", status=resp.status_code)
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
        try:
            text = call_openai_chat(p, system, prompt, max_tokens, temperature, timeout)
            if text.strip():
                return text, p["label"]
            logger.warning(f"{p['label']}: risposta vuota, provo il prossimo.")
        except ProviderCallError as e:
            if is_quota_error(e, e.status):
                logger.warning(f"{p['label']}: quota/rate limit esaurita → escluso dal run.")
                dead.add(p["label"])
            elif e.status in (401, 403):
                logger.error(f"{p['label']}: chiave rifiutata → escluso dal run.")
                dead.add(p["label"])
            else:
                logger.error(str(e))
    return None, None


class ProviderCallError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# Self-test offline (nessuna rete)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    # 1) Ordine della catena e default dei modelli
    env = {"GROQ_API_KEY": "g", "CEREBRAS_API_KEY": "c", "OPENROUTER_API_KEY": "o"}
    chain = resolve_free_providers(env)
    assert [p["label"] for p in chain] == ["groq", "cerebras", "openrouter"], chain
    assert chain[0]["model"] == "llama-3.3-70b-versatile"
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

    # 3) Riconoscimento errori di quota (i messaggi veri dei provider)
    assert is_quota_error("HTTP 429: Rate limit reached for llama-3.3-70b")
    assert is_quota_error("RESOURCE_EXHAUSTED: quota exceeded")
    assert is_quota_error("qualsiasi cosa", status=429)
    assert not is_quota_error("HTTP 500: internal server error")
    assert not is_quota_error("connessione rifiutata")

    # 4) La catena salta i provider morti e si ferma al primo che risponde
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
