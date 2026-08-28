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

# "Il provider è pieno adesso" ≠ "abbiamo finito la quota". La prima è una coda
# momentanea (NVIDIA risponde "Worker local total request limit reached (33/32)"),
# e va aspettata qualche secondo, non pagata escludendo il provider dal run.
TRANSIENT_CAPACITY = re.compile(
    r"resource\s*exhausted|request limit reached|worker local|overloaded|"
    r"capacity|no healthy upstream|temporarily unavailable|try again later", re.I)
TRANSIENT_WAIT = float(os.getenv("FREE_TRANSIENT_WAIT", "5"))

# L'unità non può essere seguita da una lettera (così "5m45.6s" si legge
# tutto, e "12000 TPM" non diventa una durata).
_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)(?![A-Za-z])", re.I)
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

# Tetto di input per i provider gratuiti: i free tier limitano i token al
# minuto (Groq 8k TPM dal 19 ago 2026, non più 12k) e una richiesta troppo
# grande torna 413 o 429. ~2800 char ≈ 2.2k token. Gemini, quando lo si usa,
# riceve il testo pieno.
FREE_MAX_CHARS = int(os.getenv("FREE_MAX_CHARS", "2800"))

# Tetto di OUTPUT per i provider gratuiti. Trovato il 21 ago 2026 leggendo i
# log di 4 run consecutivi (07:24/13:28/19:11/21-02:09 UTC): ogni run fa UNA
# chiamata Groq riuscita, poi "rate limit... riprovo tra 9.0s" e infine
# "quota/rate limit esaurita → escluso dal run" — la catena gratuita muore
# a metà del PRIMO ciclo, con budget 15 e 1 sola chiamata usata, per 4 cicli
# di fila (~18h, 247/56 giocatori invariati). Causa: call_openai_chat
# chiedeva max_tokens=8192 di default per OGNI chiamata, mentre il tier
# free di Groq è 8k TPM totali — una singola richiesta che PRENOTA 8192
# token di output è già oltre l'intera finestra al minuto, prompt escluso.
# L'estrazione tipizzata (JSON array di calciatori) non si avvicina a
# quella taglia: 2048 lascia ampio margine (poche decine di osservazioni)
# restando ben sotto la finestra insieme ai ~2.2k token di FREE_MAX_CHARS.
FREE_MAX_OUTPUT_TOKENS = int(os.getenv("FREE_MAX_OUTPUT_TOKENS", "2048"))

# Ordine della catena: prima chi ha il free tier più capiente e affidabile.
# Ogni voce è (env_chiave, base_url, env_modello, modello_default, label).
FREE_PROVIDER_SPECS = [
    # llama-3.3-70b-versatile deprecato da Groq il 17 giugno 2026 (free e
    # developer tier): ogni chiamata tornava 404 "model does not exist".
    # Scoperto il 19 agosto 2026 controllando i log del cron dopo un run —
    # era rotto da almeno 3 cicli consecutivi (16:06, 19:03, 20:08 UTC),
    # ingest completamente fermo (extract_failed: 15/15) senza che il
    # workflow lo segnalasse come errore (extract_from_source ritorna None
    # e il ciclo continua, non solleva). openai/gpt-oss-120b è il
    # rimpiazzo raccomandato da Groq stesso, verificato sul free tier reale
    # (console.groq.com/docs/rate-limits, 19 ago 2026: 30 RPM/8K TPM, "Free
    # Plan", non "Developer Plan") — non dato per buono dalla sola nota di
    # deprecazione, che altrove elencava lo stesso modello come "richiede
    # piano a pagamento" (falso, verificato sulla pagina dei limiti vera).
    ("GROQ_API_KEY", "https://api.groq.com/openai/v1",
     "GROQ_MODEL", "openai/gpt-oss-120b", "groq"),
    # Cerebras TOLTO dalla catena il 27 ago 2026, per decisione dell'utente
    # ("se cerebras dà 402, lo salutiamo"). La chiave c'è ed è valida — nel
    # run #186 compariva regolarmente nella catena risolta — ma sull'account
    # OB1 ogni modello risponde 402 payment_required: il free tier non è
    # attivo e si sblocca solo dalla billing tab, strada che questo progetto
    # evita per principio dopo il caso Gemini. Finché restava in lista era
    # peggio che inutile: veniva interrogato, falliva, e costava un round-trip
    # per ogni chiamata prima di passare all'anello dopo.
    # Resta registrato in config/llm_providers.json (active:false) con la
    # ragione: se un giorno il free tier viene attivato, va rimesso qui —
    # 60k token/minuto e 1M al giorno sono ancora il tetto più alto del
    # listino, Mistral escluso.
    # SambaNova Cloud (aggiunto 27 ago 2026, cercando un'alternativa a
    # Cerebras: sull'account OB1 Cerebras risponde 402 payment_required, cioè
    # il free tier non è attivo, e sbloccarlo passa dalla billing tab — cosa
    # che questo progetto evita per principio dopo il caso Gemini). Free tier
    # dichiarato: 200.000 token al giorno PER MODELLO, quindi la quota non è
    # condivisa fra due modelli diversi. Endpoint OpenAI-compatible verificato
    # sulla doc SambaNova (docs.sambanova.ai, "OpenAI Client Libraries
    # Compatibility"): https://api.sambanova.ai/v1, stesso schema degli altri
    # anelli, nessun codice nuovo.
    # NON verificato dal vivo su una chiave reale: il tier free e il nome
    # esatto del modello vanno confermati al primo run con SAMBANOVA_API_KEY
    # impostata — se il modello non esiste torna 404 e brucia un anello, come
    # già successo con i Llama ritirati da Cerebras il 3 ago.
    ("SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1",
     "SAMBANOVA_MODEL", "Meta-Llama-3.3-70B-Instruct", "sambanova"),
    # meta-llama/llama-3.3-70b-instruct:free è uscito dal catalogo free di
    # OpenRouter a inizio agosto 2026 — verificato in due modi indipendenti
    # il 27 ago: il messaggio d'errore reale nel run #188 ("This model is
    # unavailable for free... use this slug instead: meta-llama/llama-
    # 3.3-70b-instruct", che è la versione A PAGAMENTO, quindi NON va usata
    # qui) e la lista modelli pubblica di OpenRouter (openrouter.ai/api/v1/
    # models), che oggi non contiene nessun modello ~70B sul tier free.
    # Lasciato com'è, non spento: con la classificazione del 404 come
    # permanente (vedi call_free_chain) il costo di un anello morto è un
    # solo round-trip a inizio run, non uno per chiamata. OPENROUTER_MODEL
    # resta per puntarlo a un modello free verificato quando ce ne sarà uno
    # buono per l'estrazione — nessuno dei free attuali (tutti <10B o di
    # nicchia) è stato validato con compare_llm.py.
    ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
     "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free", "openrouter"),
    # NVIDIA NIM: verificato 3 ago 2026. nemotron-3-ultra risponde in ~7s;
    # l'endpoint meta/llama-3.3-70b è servito molto più lentamente (fino al
    # timeout), quindi non è il default. Il free tier NVIDIA è a CREDITI, non a
    # quota ricorrente: sta in fondo alla catena, si consuma e non torna.
    ("NVIDIA_API_KEY", "https://integrate.api.nvidia.com/v1",
     "NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b", "nvidia"),
]

# Parametri extra per provider, quando l'endpoint OpenAI-compatible non basta.
# I modelli "reasoning" vanno messi in modalità non pensante: sull'estrazione
# tipizzata producono lo STESSO JSON spendendo ~3,5x i token di output
# (misurato su nemotron-3-ultra il 3 ago 2026: 1176 token con thinking, 339
# senza). Il ragionamento serve dove la decisione è difficile, non qui.
PROVIDER_EXTRA_BODY = {
    "nvidia": {"chat_template_kwargs": {"enable_thinking": False}},
}


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
                  "max_tokens": max_tokens, "messages": messages,
                  **PROVIDER_EXTRA_BODY.get(provider["label"], {}),
                  **(provider.get("extra_body") or {})},
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
                    temperature: float = 0.0, timeout: int = 120,
                    start: int = 0) -> tuple:
    """
    Prova i provider finché uno risponde, partendo dal `start`-esimo.

    Ritorna (testo, label) al primo successo, (None, None) se hanno fallito
    tutti. `dead` è un set di label da saltare e che questa funzione aggiorna:
    un provider che ha esaurito la quota non va ritentato a ogni fonte —
    ogni 429 costa tempo di run e non produce niente.

    Perché `start` (27 ago 2026). L'ordine era fisso, quindi il primo anello
    prendeva TUTTO: nel run di produzione #186, via_groq 15 su 15 chiamate,
    con OpenRouter e NVIDIA configurati e con chiave valida che non venivano
    mai raggiunti. Il tetto di Groq (8-12k token al minuto) è così diventato
    il tetto dell'intera pipeline — ed è il motivo per cui fra una chiamata e
    l'altra si aspettano 25 secondi, cioè 6 degli 11 minuti di un run.

    Ruotando il punto di partenza, N provider vivi si dividono le chiamate:
    ognuno viene interrogato per primo una volta su N, e la sua finestra al
    minuto viene toccata N volte meno spesso. Il ripiego resta identico —
    dopo il primo si prosegue lungo tutta la lista — quindi un provider giù
    non fa perdere la chiamata, esattamente come prima.
    """
    dead = dead if dead is not None else set()
    if providers and start:
        i = start % len(providers)
        providers = providers[i:] + providers[:i]
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
                # 401/403 (chiave) e 404 (modello) sono la STESSA categoria:
                # un problema di CONFIGURAZIONE, non di traffico. Non si
                # aggiusta aspettando, quindi ritentare ad ogni chiamata
                # successiva non fa che ripagare lo stesso errore.
                #
                # Il 404 mancava da questo gruppo fino al 27 ago 2026, ed è
                # esattamente il bug già descritto sopra per Cerebras ("un
                # modello che non esiste torna 404 e brucia un anello"), qui
                # semplicemente non applicato: misurato sul run #188,
                # OpenRouter ha risposto "This model is unavailable for
                # free... meta-llama/llama-3.3-70b-instruct" (il modello free
                # è stato tolto dal catalogo a inizio agosto — verificato
                # sulla lista modelli reale di OpenRouter, non sul messaggio
                # da solo) SEI volte nello stesso run, una per ogni chiamata
                # che lo raggiungeva nella rotazione: sei round-trip falliti
                # pagati per intero invece di uno.
                if e.status in (401, 403, 404):
                    motivo = {401: "chiave rifiutata", 403: "chiave rifiutata",
                             404: "modello non trovato"}[e.status]
                    logger.error(f"{p['label']}: {motivo} → escluso dal run. {e}")
                    dead.add(p["label"])
                    break
                if TRANSIENT_CAPACITY.search(str(e)) and attempt == 1:
                    # Non è quota nostra: è il provider momentaneamente pieno
                    # (NVIDIA: "Worker local total request limit reached 33/32").
                    # Escluderlo per tutto il run sarebbe sprecare capacità viva.
                    logger.warning(f"{p['label']}: provider pieno, riprovo tra {TRANSIENT_WAIT}s.")
                    time.sleep(TRANSIENT_WAIT)
                    continue
                if is_quota_error(e, e.status):
                    wait = e.retry_after
                    if attempt == 1 and wait is not None and wait <= FREE_RETRY_WAIT_MAX:
                        logger.warning(f"{p['label']}: rate limit, riprovo tra {wait:.1f}s "
                                       f"(finestra scorrevole, non quota esaurita).")
                        time.sleep(wait + 0.5)
                        continue
                    # Prima si stampava solo la frase generica: lo stesso
                    # bug di osservabilità già trovato e corretto per Jina
                    # Search e ddgs (21 ago 2026) — l'errore vero (quale
                    # status, quale body) restava chiuso dentro l'eccezione e
                    # non arrivava mai ai log di produzione. Qui era lo
                    # stesso: run dopo run "quota/rate limit esaurita" senza
                    # sapere se fosse SambaNova, Groq o chiunque altro a dare
                    # cosa. Ora c'è.
                    logger.warning(f"{p['label']}: quota/rate limit esaurita → "
                                   f"escluso dal run. {e}")
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
    # Cerebras non c'è più anche se la sua chiave è in ambiente: è stato
    # tolto dal listino il 27 ago 2026 (402 payment_required su ogni modello).
    assert [p["label"] for p in chain] == ["groq", "openrouter"], chain
    assert chain[0]["model"] == "openai/gpt-oss-120b"
    assert chain[1]["model"].endswith(":free")

    # Un provider senza chiave non entra in catena: è la proprietà che rende
    # sicuro aggiungere anelli nuovi al listino (il codice descrive il
    # possibile, i secrets decidono il reale). SambaNova è stato aggiunto il
    # 27 ago 2026 senza chiave in produzione: questo assert è la prova che
    # aggiungerlo non ha cambiato nulla per chi non lo configura.
    assert "sambanova" not in [p["label"] for p in chain], \
        "un provider senza chiave non deve entrare in catena"

    # ...e quando la chiave c'è, entra al posto giusto: prima di OpenRouter,
    # che ha solo 50 richieste al giorno.
    env_sn = dict(env, SAMBANOVA_API_KEY="s")
    chain_sn = [p["label"] for p in resolve_free_providers(env_sn)]
    assert chain_sn == ["groq", "sambanova", "openrouter"], chain_sn

    # --- rotazione: i provider si dividono le chiamate (27 ago 2026) ---
    # Il bug che chiude: ordine fisso = il primo anello prende tutto. Nel run
    # #186 via_groq era 15 su 15, con altri provider vivi mai raggiunti, e il
    # tetto al minuto di Groq diventava il tetto della pipeline.
    tre = [{"label": l} for l in ("a", "b", "c")]
    primi = []

    def _finto(provider, system, prompt, *a, **k):
        primi.append(provider["label"])
        return '[{"ok": 1}]'

    _vero = globals()["call_openai_chat"]
    globals()["call_openai_chat"] = _finto
    try:
        for giro in range(6):
            call_free_chain(tre, "s", "p", dead=set(), start=giro)
    finally:
        globals()["call_openai_chat"] = _vero
    assert primi == ["a", "b", "c", "a", "b", "c"], primi
    from collections import Counter as _C
    assert set(_C(primi).values()) == {2}, \
        f"su 6 chiamate e 3 provider ognuno deve essere primo 2 volte: {_C(primi)}"

    # start=0 su una lista sola non deve rompere nulla (il caso reale di oggi:
    # tolti Cerebras e con un solo secret impostato, la catena ha un anello).
    globals()["call_openai_chat"] = _finto
    primi.clear()
    try:
        call_free_chain([{"label": "solo"}], "s", "p", dead=set(), start=7)
    finally:
        globals()["call_openai_chat"] = _vero
    assert primi == ["solo"], primi

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

    # 3) Durate e "quando riprovare" — messaggi veri di Groq (3 ago 2026,
    #    formato invariato dopo il cambio modello del 19 ago)
    assert parse_duration("3.456s") == 3.456 and parse_duration("280ms") == 0.28
    assert parse_duration("5m45.6s") == 345.6 and parse_duration("2h") == 7200
    assert parse_duration("Limit 12000 TPM") is None     # non è una durata
    groq_429 = ("Rate limit reached for model `openai/gpt-oss-120b` on tokens per "
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
        if provider["label"] == "openrouter":
            return '[{"name": "Kauan Ribeiro"}]'
        return "non dovrebbe arrivarci"

    _real = call_openai_chat
    globals()["call_openai_chat"] = fake_call
    dead = set()
    text, label = call_free_chain(chain, "sys", "prompt", dead=dead)
    assert (label, text) == ("openrouter", '[{"name": "Kauan Ribeiro"}]'), (label, text)
    assert dead == {"groq"}, dead
    calls.clear()
    text, label = call_free_chain(chain, "sys", "prompt", dead=dead)
    assert calls == ["openrouter"], calls        # groq non viene più ritentato

    # Tutti morti: (None, None), così il chiamante NON marca il lavoro come fatto
    dead = {"groq", "cerebras", "openrouter"}
    assert call_free_chain(chain, "s", "p", dead=dead) == (None, None)

    # Un 404 (modello non trovato) va escluso come 401/403, non ritentato ad
    # ogni chiamata. Bug reale del run #188: OpenRouter dava 404 su OGNI
    # chiamata (6 volte in un run solo) perché il 404 non finiva nel gruppo
    # dei permanenti — qui la seconda chiamata a call_free_chain non deve
    # nemmeno provare openrouter.
    tre_404 = [{"label": "groq"}, {"label": "openrouter"}, {"label": "nvidia"}]
    tentativi_404 = []

    def fake_404(provider, system, prompt, *a, **k):
        tentativi_404.append(provider["label"])
        if provider["label"] == "groq":
            raise ProviderCallError("groq: HTTP 429: rate limit", status=429)
        if provider["label"] == "openrouter":
            raise ProviderCallError(
                'openrouter: HTTP 404: {"error":{"message":"This model is '
                'unavailable for free."}}', status=404)
        return '[{"name": "ok"}]'

    globals()["call_openai_chat"] = fake_404
    dead404 = set()
    text, label = call_free_chain(tre_404, "s", "p", dead=dead404)
    assert label == "nvidia", (label, text)         # groq 429, openrouter 404, arriva a nvidia
    assert dead404 == {"groq", "openrouter"}, dead404
    tentativi_404.clear()
    call_free_chain(tre_404, "s", "p", dead=dead404)
    assert tentativi_404 == ["nvidia"], \
        (f"groq e openrouter vanno esclusi per il resto del run, non "
         f"ritentati ad ogni chiamata: {tentativi_404}")

    globals()["call_openai_chat"] = _real

    live = resolve_free_providers()
    print(f"modalità effettiva: {resolve_llm_mode()}")
    print(f"catena free con le chiavi in ambiente: "
          f"{[p['label'] for p in live] or 'nessuna (imposta GROQ_API_KEY)'}")
    print("OK — self-test catena free superato.")
