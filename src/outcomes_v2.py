#!/usr/bin/env python3
"""
OB1 v2 — Outcome & lead time onesto (Fase B3)

Ripara il KPI centrale (Finding 1 dell'audit): in Fase A il lead time era rotto
— match su nomi deboli ("Felipe" → qualsiasi articolo), fonti non calcistiche
(l'articolo UFC di André Maia), pagine-profilo scambiate per "hype mainstream",
anticipi 0 fasulli. Qui si misura solo ciò che è difendibile.

Regole:
  0. La PREMESSA regge secondo il gate stesso (src/challenge_v2.contesta):
     un club di cui scrive tutto il mondo, o una fonte che dichiara la
     persona già affermata, non diventa un anticipo solo perché una fonte
     successiva ne parla — è un'identità che il prodotto rifiuta comunque.
  1. Solo IDENTITÀ FORTI sono eleggibili (nome completo, non soprannome, con club).
  2. La fonte deve essere calcistica (via out l'UFC/MMA/boxe).
  3. Si distingue "esiste una pagina-profilo" (aggregatore), "fonte non
     editoriale" (federazione, academy, aggregatore — riappare, non viene
     "ripresa") e "copertura editoriale mainstream" (hype vero). Solo
     l'ultimo conta come lead time.
  4. Il lead time è positivo e verificabile con link, o non è un lead.

Regola 0 e il "fonte non editoriale" della regola 3 sono arrivati il 1 set
2026, misurando i 4 outcome vivi allora in produzione: 3 erano la stessa
federazione che riconvocava lo stesso ragazzo (non stampa), il quarto era
"Enzo Fernández" al Benfica — bloccato altrove come gia_coperto_da_tutti,
eppure contato come "anticipo confermato". Zero dei quattro reggeva.

Codice puro e testabile senza rete (importa src.challenge_v2, che è
altrettanto puro — nessuna rete, nessun DB).
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from src.challenge_v2 import contesta as contesta_premessa
    from src.challenge_v2 import bloccanti as bloccanti_premessa
except ImportError:  # layout PYTHONPATH=src
    from challenge_v2 import contesta as contesta_premessa
    from challenge_v2 import bloccanti as bloccanti_premessa
from urllib.parse import urlparse

NOISE_HINTS = ("ufc", "mma", "boxing", "/fight", "wrestling", "nba", "nfl")

# Aggregatori: la loro pagina prova l'ESISTENZA del giocatore, non l'hype.
PROFILE_DOMAINS = ("transfermarkt", "soccerway", "besoccer", "sofascore",
                   "fbref", "worldfootball", "footballdatabase")
PROFILE_PATHS = ("/spieler/", "/player/", "/profil/", "/giocatore/", "/jugador/",
                 "/players/", "/fiche/")


def _domain(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def is_strong_identity(name: str, club=None) -> bool:
    """Nome completo (≥2 token), non handle/soprannome, con club noto."""
    name = (name or "").strip()
    tokens = [t for t in name.split() if t]
    if len(tokens) < 2:
        return False
    if any(ch.isdigit() for ch in name) or "_" in name:
        return False
    return bool(club and str(club).strip())


# Tipi del registro fonti (config/sources.json, letto da src/claims_v2.registro)
# che NON sono copertura editoriale — sono il canale che il prodotto usa per
# SCOPRIRE, non prove che qualcun altro l'ha scoperto dopo di noi. Una
# federazione che riconvoca lo stesso ragazzo mesi dopo non è "la stampa
# mainstream l'ha ripreso": è la stessa bocca che parla due volte.
#
# Trovato misurando i 4 outcome vivi del 1 set 2026: 3 su 4 erano fcf.com.co
# (type=federation) che riconvocava lo stesso ragazzo in un microciclo
# successivo — nessuna fonte di stampa terza c'entrava. classify_source() non
# consultava il registro, quindi qualunque dominio non fosse nella lista corta
# PROFILE_DOMAINS finiva "hype" per esclusione, federazioni comprese.
TIPI_NON_STAMPA = frozenset({
    "federation", "confederation", "academy", "aggregator",
    "results_stats", "encyclopedic", "fan_site", "niche_scouting",
})


def classify_source(url: str, title: str = "", tipo_fonte: Optional[str] = None) -> str:
    """
    'noise'   → non calcistico (non conta),
    'profile' → pagina-profilo aggregatore (prova esistenza, NON hype),
    'non_press' → dominio registrato ma di tipo non editoriale (federazione,
                  academy, aggregatore...): riappare, non viene "ripreso",
    'hype'    → copertura editoriale mainstream (conta come lead time).

    `tipo_fonte`: il `type` dal registro (src/claims_v2.registro()), se il
    dominio è fra le fonti curate di Global. None per un dominio trovato
    dalla corroborazione libera (goal.com, espn.com, l'equipe.fr...): quelli
    restano "hype" di default, perché sono esattamente il caso che il
    tabellone vuole misurare — una fonte indipendente che ci arriva dopo.
    """
    if tipo_fonte in TIPI_NON_STAMPA:
        return "non_press"
    u = (url or "").lower()
    if any(h in u for h in NOISE_HINTS):
        return "noise"
    dom = _domain(url)
    if any(d in dom for d in PROFILE_DOMAINS) and any(p in u for p in PROFILE_PATHS):
        return "profile"
    return "hype"


def compute_lead_time(first_detected: str, hype_date: str):
    """Giorni tra il rilevamento OB1 e l'hype. None se non calcolabile/negativo."""
    try:
        d0 = datetime.fromisoformat(first_detected)
        d1 = datetime.fromisoformat(hype_date)
    except (ValueError, TypeError):
        return None
    days = (d1 - d0).days
    return days if days > 0 else None


def evaluate_mainstream(name, club, first_detected, hype_url, hype_date, hype_title="",
                        hype_source_type: Optional[str] = None):
    """
    Verdetto completo e onesto su un potenziale lead time.
    Ritorna dict: {valid, lead_time_days, reason} — valid=True solo se è una
    prova difendibile (premessa che regge + identità forte + fonte
    calcistica editoriale + anticipo>0).

    `hype_source_type`: il `type` dal registro fonti (src/claims_v2.registro()),
    se il dominio della fonte è fra quelle curate — None per un dominio
    trovato dalla corroborazione libera. Vedi classify_source().
    """
    # La PREMESSA prima della fonte: se il gate stesso dice che questo club
    # smentisce "poco coperto" o che il nome è una descrizione, non è un
    # anticipo — è un'identità che non regge, a prescindere da chi ne scrive
    # dopo. Trovato il 1 set 2026: "Enzo Fernández" al Benfica risultava
    # "anticipo confermato" (2 giorni) mentre lo stesso database lo marcava
    # gia_coperto_da_tutti e non pubblicabile — il tabellone premiava una
    # scheda che il prodotto stesso rifiuta. evidenze=[] apposta: qui basta
    # il nome del club, e non serve duplicare le prove già lette altrove.
    premessa = bloccanti_premessa(contesta_premessa(
        {"canonical_name": name, "age": None, "club": club}, []))
    if premessa:
        return {"valid": False, "lead_time_days": None,
                "reason": "premessa_non_regge:" + ",".join(x["codice"] for x in premessa)}
    if not is_strong_identity(name, club):
        return {"valid": False, "lead_time_days": None, "reason": "identita_debole"}
    kind = classify_source(hype_url, hype_title, tipo_fonte=hype_source_type)
    if kind == "noise":
        return {"valid": False, "lead_time_days": None, "reason": "fonte_non_calcistica"}
    if kind == "profile":
        return {"valid": False, "lead_time_days": None, "reason": "solo_pagina_profilo"}
    if kind == "non_press":
        # Trovato lo stesso giorno: 3 outcome su 4 erano fcf.com.co
        # (federazione) che riconvocava lo stesso ragazzo in un microciclo
        # successivo. Non è la stampa che ci ha ripresi — è la stessa bocca
        # che ha parlato due volte, ed è un segnale già misurato altrove
        # (selezione_v2 / detection_count), non un anticipo sulla stampa.
        return {"valid": False, "lead_time_days": None, "reason": "fonte_non_editoriale"}
    lt = compute_lead_time(first_detected, hype_date)
    if lt is None:
        return {"valid": False, "lead_time_days": None, "reason": "anticipo_nullo_o_negativo"}
    return {"valid": True, "lead_time_days": lt, "reason": "ok"}


if __name__ == "__main__":
    cases = [
        ("André Maia", "Palmeiras", "2026-03-01", "https://espn.com/mma/story/ufc-fight-night", "2026-03-10", None),
        ("Felipe", "", "2026-03-01", "https://goal.com/news/felipe", "2026-03-10", None),
        ("Diego Londoño", "Once Caldas", "2026-01-01", "https://transfermarkt.com/diego-londono/profil/999", "2026-03-01", None),
        ("Bruno Baldini", "Londrina", "2026-05-01", "https://goal.com/br/noticias/baldini-destaque", "2026-05-18", None),
    ]
    for name, club, det, url, hype, tipo in cases:
        v = evaluate_mainstream(name, club, det, url, hype, hype_source_type=tipo)
        print(f"{name:16s} valid={str(v['valid']):5s} lead={v['lead_time_days']} ({v['reason']})")

    print()
    # I due casi reali che hanno rivelato il bug il 1 set 2026: entrambi
    # devono ora risultare invalidi, con un motivo che spiega perché.

    # 1) fcf.com.co (federazione) riconvoca lo stesso ragazzo — non è la
    #    stampa che l'ha ripreso, è la stessa bocca.
    v = evaluate_mainstream(
        "Luis Eduardo Mena Padilla", "Independiente Valle del Cauca",
        "2026-07-18", "http://www.fcf.com.co/2026/07/27/convocatoria-x/",
        "2026-07-31", hype_source_type="federation")
    assert not v["valid"], v
    assert v["reason"] == "fonte_non_editoriale", v

    # 2) Enzo Fernández al Benfica: il gate lo blocca altrove come
    #    gia_coperto_da_tutti (Benfica è in CLUB_GIA_COPERTI). Non deve
    #    contare come anticipo, a prescindere da quanto sia buona la fonte.
    v = evaluate_mainstream(
        "Enzo Fernández", "Benfica", "2026-08-25",
        "https://placar.com.br/ultimas-noticias/", "2026-08-27",
        hype_source_type="national_press")
    assert not v["valid"], v
    assert v["reason"].startswith("premessa_non_regge"), v
    assert "gia_coperto_da_tutti" in v["reason"], v

    # 3) La stessa fonte (placar.com.br, national_press), su un club normale:
    #    resta "hype" vero, la regola 3 non deve penalizzare la stampa vera.
    v = evaluate_mainstream(
        "Bruno Baldini", "Londrina", "2026-05-01",
        "https://placar.com.br/futebol/baldini-destaque", "2026-05-18",
        hype_source_type="national_press")
    assert v["valid"] and v["reason"] == "ok", v

    print("OK evaluate_mainstream: la premessa del gate e il tipo di fonte "
          "contano prima della data — una federazione che si ripete non e' "
          "la stampa, e un club gia' coperto non diventa un anticipo "
          "perche' qualcuno ne scrive dopo.")
