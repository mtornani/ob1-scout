#!/usr/bin/env python3
"""
OB1 v2 — Prefiltro e condensazione (Fase C1)

La chiamata LLM più economica è quella che non fai. Oggi ogni articolo trovato
dal monitor va all'estrattore con 6000 caratteri di testo, anche quando è la
cronaca di una partita di Serie A senza un solo giovane dentro, o una pagina di
navigazione piena di link. È lì che se ne va la quota gratuita.

Due leve, entrambe in codice puro (niente rete, niente modello):

  1. should_extract() — un articolo merita una chiamata solo se contiene INSIEME
     un segnale di età/categoria giovanile e qualcosa che somigli a un nome
     proprio. Filtro volutamente generoso: sbagliare scartando costa un
     giocatore perso, sbagliare tenendo costa qualche token. Nel dubbio, tiene.

  2. condense() — al modello si manda solo il testo che porta segnale
     (paragrafi con età/nomi/statistiche + il contesto attorno), non l'articolo
     intero con menu, cookie banner e articoli correlati. Tipicamente 2-4× di
     token in meno a parità di estrazione.

Più content_fingerprint(), che rende possibile la cache: lo stesso contenuto
ripubblicato da tre aggregatori si paga una volta sola.

Test: python src/prefilter_v2.py
"""

import hashlib
import re
import unicodedata

# Anno di nascita plausibile per un giovane osservabile oggi (16-21 anni).
RE_BIRTH_YEAR = re.compile(r"\b(200[4-9]|201[0-2])\b")

# "17 anni", "17 años", "17 anos", "17 ans", "17-year-old", "17 Jahre",
# "17 godina", "17 lat", "17 yaşında" — l'età scritta in lettere no: il
# modello la troverà comunque se il paragrafo passa per altri segnali.
RE_AGE = re.compile(
    r"\b(1[4-9]|2[01])\s*[-–\s]?\s*"
    r"(anni|a[nñ]os|anos|ans|year[-\s]?old|years?\s+old|jahre|godina|godine|"
    r"lat|ya[sş][ıi]nda|jaar|år|let)\b", re.I)

# Categorie giovanili nelle lingue del registro fonti.
RE_YOUTH = re.compile(
    r"\b(sub[-\s]?1[4-9]|sub[-\s]?2[01]|u[-\s]?1[4-9]\b|u[-\s]?2[01]\b|"
    r"under[-\s]?1[4-9]|under[-\s]?2[01]|primavera|cantera|juvenil|juniores|"
    r"juniori|omladin\w*|kadet\w*|categor[íi]as?\s+inferiores|"
    r"academy|youth\s+team|academia|centro\s+de\s+forma[çc][ãa]o|"
    r"formaci[óo]n|base\s+do\s+|espoirs?|nachwuchs|primeiro\s+contrato)\b", re.I)

# Statistiche: rafforzano un paragrafo ma da sole non bastano.
RE_STATS = re.compile(
    r"\b(\d{1,3}\s*(gol|goals?|goles|assist|asistencias?|presenze|"
    r"partidas?|partidos?|appearances?|minutos?|minuti|minutes))\b", re.I)

# Nome proprio: due parole capitalizzate di fila (tollera accenti e apostrofi).
RE_NAME_BIGRAM = re.compile(
    r"\b[A-ZÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÑÇŠĐŽĆČ][\w'’\-]{1,}\s+"
    r"[A-ZÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÑÇŠĐŽĆČ][\w'’\-]{1,}\b")

# Righe di navigazione/boilerplate tipiche dell'output Jina Reader.
RE_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
RE_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# Sotto questa soglia (dopo pulizia) non c'è un articolo: è un menu, un teaser
# o una pagina vuota. Un pezzo vero, letto via Jina, sta sulle migliaia di char.
MIN_CLEAN_CHARS = 120

BOILERPLATE_HINT = re.compile(
    r"^(share|condividi|compartir|partager|cookie|privacy|newsletter|"
    r"leggi anche|leia mais|ver tambi[ée]n|read more|advertisement|"
    r"pubblicit[àa]|menu|home|login|iscriviti|subscribe)\b", re.I)


def _clean_line(line: str) -> str:
    """Toglie immagini e riduce i link markdown al loro testo."""
    line = RE_IMAGE.sub(" ", line)
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line).strip()


def _is_navigation(line: str) -> bool:
    """Riga fatta soprattutto di link o di parole-menu: non è cronaca."""
    if not line.strip():
        return True
    if BOILERPLATE_HINT.match(line.strip()):
        return True
    link_chars = sum(len(m.group(0)) for m in RE_MD_LINK.finditer(line))
    if link_chars > 0.5 * len(line):
        return True
    text = _clean_line(line)
    if len(text) < 25 and not any(ch.isdigit() for ch in text):
        return True
    return False


def clean_text(text: str) -> str:
    """Testo senza navigazione/boilerplate, paragrafi preservati."""
    if not text:
        return ""
    kept = []
    for raw in text.splitlines():
        if _is_navigation(raw):
            continue
        line = _clean_line(raw)
        if line:
            kept.append(line)
    return "\n".join(kept)


def signals(text: str) -> dict:
    """Conteggio dei segnali utili in un pezzo di testo. Codice puro."""
    t = text or ""
    return {
        "birth_year": len(RE_BIRTH_YEAR.findall(t)),
        "age": len(RE_AGE.findall(t)),
        "youth": len(RE_YOUTH.findall(t)),
        "stats": len(RE_STATS.findall(t)),
        "names": len(RE_NAME_BIGRAM.findall(t)),
    }


def relevance(text: str) -> dict:
    """
    Verdetto sul singolo articolo: vale una chiamata LLM?

    Regola: serve almeno un segnale di GIOVANE (età esplicita, anno di nascita
    o categoria giovanile) E almeno un nome proprio plausibile. Le statistiche
    da sole non bastano (le ha ogni cronaca di prima squadra), ma alzano il
    punteggio usato per l'ordine di lavorazione in coda.
    """
    cleaned = clean_text(text)
    s = signals(cleaned)
    youth_signal = s["age"] + s["birth_year"] + s["youth"]
    has_name = s["names"] > 0
    score = min(10, 2 * s["age"] + 2 * s["birth_year"] + s["youth"] +
                min(2, s["stats"]) + min(2, s["names"]))
    keep = youth_signal > 0 and has_name and len(cleaned) >= MIN_CLEAN_CHARS
    if not keep:
        if len(cleaned) < MIN_CLEAN_CHARS:
            reason = "testo troppo corto o solo navigazione"
        elif not youth_signal:
            reason = "nessun segnale di età/categoria giovanile"
        else:
            reason = "nessun nome proprio riconoscibile"
    else:
        reason = "ok"
    return {"keep": keep, "score": score, "reason": reason,
            "signals": s, "clean_chars": len(cleaned)}


def should_extract(text: str) -> bool:
    return relevance(text)["keep"]


def condense(text: str, max_chars: int = 2500, context: int = 1) -> str:
    """
    Tiene i paragrafi che portano segnale più `context` paragrafi adiacenti
    (dove spesso sta il nome, se il segnale è l'età). Ordine originale
    preservato, duplicati rimossi, taglio finale a max_chars su confine di
    paragrafo quando possibile.
    """
    cleaned = clean_text(text)
    if not cleaned:
        return ""
    paras = [p.strip() for p in re.split(r"\n{2,}|\n(?=[A-ZÁÀÂÃÉÊÍÓÔÕÚÑÇ])", cleaned)
             if p.strip()]
    if not paras:
        return ""

    scored = []
    for i, p in enumerate(paras):
        s = signals(p)
        has = s["age"] + s["birth_year"] + s["youth"] + s["stats"] > 0 and s["names"] > 0
        scored.append(bool(has) or (s["age"] + s["birth_year"] + s["youth"]) > 0)

    keep_idx = set()
    for i, hit in enumerate(scored):
        if hit:
            for j in range(max(0, i - context), min(len(paras), i + context + 1)):
                keep_idx.add(j)
    if not keep_idx:                      # nessun paragrafo "caldo": prendi l'inizio
        keep_idx = set(range(min(3, len(paras))))
    keep_idx.add(0)                       # il lede quasi sempre contiene il nome

    out, seen, used = [], set(), 0
    for i in sorted(keep_idx):
        p = paras[i]
        if p in seen:
            continue
        seen.add(p)
        if used + len(p) + 2 > max_chars:
            room = max_chars - used
            if room > 200:
                out.append(p[:room].rsplit(" ", 1)[0])
            break
        out.append(p)
        used += len(p) + 2
    return "\n\n".join(out)


def content_fingerprint(text: str) -> str:
    """
    Impronta del CONTENUTO (non dell'URL): normalizza spazi, accenti e
    maiuscole. Serve a non pagare due volte lo stesso pezzo ripubblicato da
    aggregatori diversi, e a riconoscere che una pagina non è cambiata.
    """
    t = unicodedata.normalize("NFKD", clean_text(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Self-test offline
# --------------------------------------------------------------------------

if __name__ == "__main__":
    ARTICOLO_PT = """[Home](https://ex.com) | [Futebol](https://ex.com/f) | [Contato](https://ex.com/c)

![foto](https://ex.com/i.jpg)

Revelação da base do Athletico, Kauan Ribeiro assinou seu primeiro contrato profissional
nesta terça-feira. O meia de 17 anos, nascido em 2009, foi promovido ao elenco principal
depois de 14 gols em 22 partidas pelo sub-20.

Compartilhe

O técnico elogiou a evolução do jovem, que treina com os profissionais desde janeiro.

Leia mais
[Athletico anuncia patrocínio](https://ex.com/p)
"""

    ARTICOLO_ES = """El delantero Mateo Fernández, de 18 años, debutó con el primer equipo
en la Primera B. El juvenil, formado en la cantera, disputó 12 partidos con la sub-20 la
temporada pasada, marcando 6 goles."""

    CRONACA_SENIOR = """La partita è finita 2-1. Il capitano Giorgio Chiellini ha segnato
al 78esimo minuto davanti a 40.000 spettatori. L'allenatore ha commentato la prestazione
della squadra dicendo che il campionato resta lungo e che serviranno altri punti nelle
prossime nove giornate contro avversarie dirette."""

    NAVIGAZIONE = """[Home](https://x.com) [News](https://x.com/n) [Video](https://x.com/v)
[Login](https://x.com/l) [Privacy](https://x.com/p)"""

    r_pt = relevance(ARTICOLO_PT)
    r_es = relevance(ARTICOLO_ES)
    r_sr = relevance(CRONACA_SENIOR)
    r_nav = relevance(NAVIGAZIONE)

    for label, r in (("PT giovanile", r_pt), ("ES giovanile", r_es),
                     ("IT cronaca senior", r_sr), ("nav", r_nav)):
        print(f"{label:20} keep={str(r['keep']):5} score={r['score']:2}  {r['reason']}")

    assert r_pt["keep"] and r_es["keep"]
    assert not r_sr["keep"], r_sr          # nessun segnale giovanile -> niente chiamata
    assert not r_nav["keep"], r_nav        # pagina di soli link -> niente chiamata

    cond = condense(ARTICOLO_PT, max_chars=2500)
    print(f"\ncondensazione: {len(ARTICOLO_PT)} -> {len(cond)} char "
          f"({100 - int(100 * len(cond) / len(ARTICOLO_PT))}% in meno)")
    assert "Kauan Ribeiro" in cond and "17 anos" in cond      # il segnale resta
    assert "Compartilhe" not in cond and "patrocínio" not in cond   # il rumore no
    assert len(cond) < len(ARTICOLO_PT)

    # Il taglio rispetta il tetto del provider più stretto (Groq: 2800 char)
    lungo = (ARTICOLO_ES + "\n\n") * 40
    assert len(condense(lungo, max_chars=2800)) <= 2800

    # Stesso contenuto ripubblicato = stessa impronta (cache), testo diverso = no
    a = content_fingerprint(ARTICOLO_ES)
    b = content_fingerprint("  " + ARTICOLO_ES.replace("\n", "  ").upper() + " ")
    assert a == b, (a, b)
    assert content_fingerprint(CRONACA_SENIOR) != a

    print("OK — self-test prefiltro superato.")
