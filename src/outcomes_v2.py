#!/usr/bin/env python3
"""
OB1 v2 — Outcome & lead time onesto (Fase B3)

Ripara il KPI centrale (Finding 1 dell'audit): in Fase A il lead time era rotto
— match su nomi deboli ("Felipe" → qualsiasi articolo), fonti non calcistiche
(l'articolo UFC di André Maia), pagine-profilo scambiate per "hype mainstream",
anticipi 0 fasulli. Qui si misura solo ciò che è difendibile.

Regole:
  1. Solo IDENTITÀ FORTI sono eleggibili (nome completo, non soprannome, con club).
  2. La fonte deve essere calcistica (via out l'UFC/MMA/boxe).
  3. Si distingue "esiste una pagina-profilo" (aggregatore) da "copertura
     editoriale mainstream" (hype vero). Solo il secondo conta come lead time.
  4. Il lead time è positivo e verificabile con link, o non è un lead.

Codice puro e testabile senza rete.
"""

from datetime import datetime
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


def classify_source(url: str, title: str = "") -> str:
    """
    'noise'   → non calcistico (non conta),
    'profile' → pagina-profilo aggregatore (prova esistenza, NON hype),
    'hype'    → copertura editoriale mainstream (conta come lead time).
    """
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


def evaluate_mainstream(name, club, first_detected, hype_url, hype_date, hype_title=""):
    """
    Verdetto completo e onesto su un potenziale lead time.
    Ritorna dict: {valid, lead_time_days, reason} — valid=True solo se è una
    prova difendibile (identità forte + fonte calcistica editoriale + anticipo>0).
    """
    if not is_strong_identity(name, club):
        return {"valid": False, "lead_time_days": None, "reason": "identita_debole"}
    kind = classify_source(hype_url, hype_title)
    if kind == "noise":
        return {"valid": False, "lead_time_days": None, "reason": "fonte_non_calcistica"}
    if kind == "profile":
        return {"valid": False, "lead_time_days": None, "reason": "solo_pagina_profilo"}
    lt = compute_lead_time(first_detected, hype_date)
    if lt is None:
        return {"valid": False, "lead_time_days": None, "reason": "anticipo_nullo_o_negativo"}
    return {"valid": True, "lead_time_days": lt, "reason": "ok"}


if __name__ == "__main__":
    cases = [
        ("André Maia", "Palmeiras", "2026-03-01", "https://espn.com/mma/story/ufc-fight-night", "2026-03-10"),
        ("Felipe", "", "2026-03-01", "https://goal.com/news/felipe", "2026-03-10"),
        ("Diego Londoño", "Once Caldas", "2026-01-01", "https://transfermarkt.com/diego-londono/profil/999", "2026-03-01"),
        ("Bruno Baldini", "Londrina", "2026-05-01", "https://goal.com/br/noticias/baldini-destaque", "2026-05-18"),
    ]
    for name, club, det, url, hype in cases:
        v = evaluate_mainstream(name, club, det, url, hype)
        print(f"{name:16s} valid={str(v['valid']):5s} lead={v['lead_time_days']} ({v['reason']})")
