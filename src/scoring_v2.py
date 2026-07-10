#!/usr/bin/env python3
"""
OB1 v2 — Scoring trasparente (Fase B1)

Il punteggio è CODICE, non LLM. Ogni componente è esplicito e ispezionabile:
score_player() ritorna il punteggio E il breakdown, così si vede sempre *perché*
un giocatore ha quel numero. Questo risolve alla radice il difetto della v1
(punteggio opaco da LLM, evidenza fragile in cima alla dashboard).

Principio: MERITO (età + produzione + asimmetria − visibilità) moltiplicato per
la CONFIDENZA (quante fonti distinte + persistenza nel tempo). Un nome visto una
volta sola, per quanto brillante, non può stare in cima: la confidenza lo sconta.

NB: modello proposto per la v2, DA VALIDARE con gli outcome reali prima di
diventare default (vedi CLAUDE.md §2). Non è ancora la verità rivelata.
"""

import re

# Marcatori di alta visibilità mediatica (= bassa asimmetria → penalità).
TOP_MARKERS = (
    "real madrid", "barcelona", "barcellona", "man city", "manchester city",
    "psg", "bayern", "juventus", "chelsea", "liverpool", "arsenal",
    "premier league", "serie a", "la liga", "bundesliga", "ligue 1",
    "champions league",
)


def parse_stats(text) -> dict:
    """Estrae numeri (gol/assist/minuti/presenze) da un testo stat libero."""
    out = {}
    if not text:
        return out
    for key, pat in (
        ("goals", r'(?:gol|goals?|gls)[.\s:]*([0-9]+)'),
        ("assists", r'(?:assist|ast)[.\s:]*([0-9]+)'),
        ("apps", r'(?:presenze|appearances|mp|matches)[.\s:]*([0-9]+)'),
        ("minutes", r"(?:min|minuti|minutes)[.\s:]*([0-9][0-9.,']*)"),
    ):
        m = re.search(pat, str(text), re.IGNORECASE)
        if m:
            try:
                out[key] = int(re.sub(r"[.,']", "", m.group(1)))
            except ValueError:
                pass
    return out


def _youth_points(age) -> float:
    """Più giovane = più runway = più valore potenziale. Max 20."""
    if age is None:
        return 6.0  # neutro-basso: età ignota è essa stessa un limite
    if age <= 15:
        return 20.0
    if age <= 16:
        return 18.0
    if age <= 17:
        return 15.0
    if age <= 18:
        return 11.0
    if age <= 19:
        return 7.0
    if age <= 20:
        return 4.0
    return 1.0


def _production_points(stats: dict) -> float:
    """Produzione concreta documentata. Max 40. Senza dati → 0."""
    if not stats:
        return 0.0
    g = stats.get("goals", 0)
    a = stats.get("assists", 0)
    apps = stats.get("apps", 0)
    pts = min(g * 4 + a * 2.5, 30)          # contributo offensivo
    if apps:
        pts += min(apps * 0.5, 10)          # continuità (ha giocato davvero)
    return min(pts, 40.0)


def _asymmetry_points(is_ghost: bool, club, league) -> float:
    """Asimmetria informativa: invisibile al mainstream = più valore. Max 25."""
    pts = 0.0
    if is_ghost:
        pts += 15.0                         # nessuna presenza Transfermarkt
    ctx = f"{club or ''} {league or ''}".lower()
    if any(m in ctx for m in TOP_MARKERS):
        pts -= 20.0                         # top club/lega = già visibile
    elif club or league:
        pts += 8.0                          # contesto minore/identificato
    return max(-20.0, min(pts, 25.0))


def _confidence(n_sources: int, detection_count: int) -> float:
    """
    Confidenza in [0.45, 1.0]: quante fonti DISTINTE (segnale primario v2) +
    persistenza nel tempo (quante volte ri-rilevato). Il moltiplicatore impedisce
    che un'evidenza singola domini la classifica.
    """
    n_sources = n_sources or 1
    detection_count = detection_count or 1
    conf = 0.45
    conf += 0.18 * min(max(n_sources - 1, 0), 3)      # fino a +0.54 per fonti extra
    if detection_count >= 30:
        conf += 0.30
    elif detection_count >= 10:
        conf += 0.20
    elif detection_count >= 3:
        conf += 0.10
    return round(min(conf, 1.0), 3)


def score_player(age=None, is_ghost=False, club=None, league=None,
                 stats=None, n_sources=1, detection_count=1,
                 position=None) -> dict:
    """
    Ritorna {'score': int 0-100, 'confidence': float, 'merit': float,
             'breakdown': {...}} — tutto ispezionabile.
    `stats` può essere un dict {goals,assists,...} o un testo libero.
    """
    if isinstance(stats, str) or stats is None:
        stats = parse_stats(stats)

    youth = _youth_points(age)
    production = _production_points(stats)
    asymmetry = _asymmetry_points(is_ghost, club, league)
    merit = max(0.0, youth + production + asymmetry)

    confidence = _confidence(n_sources, detection_count)
    score = int(round(min(merit, 100.0) * confidence))

    return {
        "score": score,
        "confidence": confidence,
        "merit": round(merit, 1),
        "breakdown": {
            "youth": round(youth, 1),
            "production": round(production, 1),
            "asymmetry": round(asymmetry, 1),
            "n_sources": n_sources or 1,
            "detection_count": detection_count or 1,
        },
    }


if __name__ == "__main__":
    # Confronto illustrativo: ghost brillante visto 1 volta vs corroborato 79 volte
    ghost_1x = score_player(age=16, is_ghost=True, club="Ibrachina",
                            stats={}, n_sources=1, detection_count=1)
    solid = score_player(age=19, is_ghost=False, club="Londrina",
                         stats={"goals": 8, "assists": 4, "apps": 20},
                         n_sources=1, detection_count=79)
    print("Ghost 1x:", ghost_1x["score"], ghost_1x["breakdown"])
    print("Solid 79x:", solid["score"], solid["breakdown"])
