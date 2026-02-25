#!/usr/bin/env python3
"""
OB1 Configuration - Gestione centralizzata di tutte le configurazioni
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# API KEYS - Configura qui le tue chiavi
# ============================================================================

# AnyCrawl (primario)
ANYCRAWL_API_KEY = os.getenv('ANYCRAWL_API_KEY', '')

# Serper.dev (fallback 1 - 2500 ricerche gratis)
SERPER_API_KEY = os.getenv('SERPER_API_KEY', '')

# Tavily API (fallback 2 - Ottimizzato per AI)
TAVILY_API_KEY = os.getenv('TAVILY_API_KEY', '')

# Gemini API (Cervello RAG)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

# Telegram Notifications
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

# Brave Search (fallback 3)
BRAVE_API_KEY = os.getenv('BRAVE_API_KEY', '')

# SearXNG public instances (fallback GRATIS)
SEARXNG_INSTANCES = [
    'https://search.sapti.me',
    'https://searx.be',
    'https://searx.work',
    'https://search.privacyguides.net',
    'https://searx.tiekoetter.com',
    'https://searx.ninja',
    'https://search.ononoki.org',
    'https://northboot.xyz'
]

# ============================================================================
# SEARCH PARAMETERS - Ottimizzati per OB1
# ============================================================================

# Parametri ricerca OTTIMIZZATI (erano troppo restrittivi!)
MAX_SERP = 30  # Era 14 - TROPPO BASSO! Aumentato a 30
MIN_TEXT_LEN = 300  # Era 600 - TROPPO ALTO! Ridotto a 300
MIN_KEYWORD_MATCHES = 1  # Era 2 - Troppo restrittivo
TIMEOUT_SECONDS = 30

# Rate limiting (per non bruciare crediti)
DELAY_BETWEEN_SEARCHES = 1.0  # secondi tra una ricerca e l'altra
MAX_RETRIES_PER_QUERY = 2

# ============================================================================
# REGIONS & QUERIES - Bilanciamento globale
# ============================================================================

# PROBLEMA RISOLTO: Ora cerca in TUTTE le regioni, non solo Asia!
ENABLED_REGIONS = [
    "africa",
    "asia", 
    "south_america",
    "europe",
    "north_america",
    "international"
]

# Peso per regione (Europa ha più peso perché mercato principale)
REGION_WEIGHTS = {
    "africa": 0.10,
    "asia": 0.20,
    "south_america": 0.20,
    "europe": 0.35,  # Mercato principale
    "north_america": 0.10,
    "international": 0.05
}

# Query ottimizzate per trovare anomalie (giovani talenti)
QUERY_TEMPLATES = {
    "breakout": [
        "{player} breakthrough performance",
        "{player} wonderkid talent",
        "{player} rising star football"
    ],
    "transfer": [
        "{player} transfer news",
        "{player} scouting report",
        "{player} market value"
    ],
    "performance": [
        "{player} goals assists stats",
        "{player} U20 performance",
        "{player} youth league highlights"
    ]
}

# Keywords che indicano una possibile anomalia
ANOMALY_KEYWORDS = [
    # Performance eccezionali
    "breakthrough", "wonderkid", "prodigy", "talent", "rising star",
    "exceptional", "outstanding", "impressive", "sensational",
    
    # Interesse di mercato
    "transfer", "scouting", "linked", "target", "interest",
    "bid", "offer", "contract", "signing",
    
    # Record e achievement
    "record", "youngest", "debut", "hattrick", "goal",
    "assist", "man of the match", "player of the month",
    
    # Hype mediatico
    "next", "new", "future", "star", "phenomenon"
]

# ============================================================================
# OUTPUT & LOGGING
# ============================================================================

# Directory
PROJECT_ROOT = Path(__file__).parent.parent  # ob1-scout/ root, not config/
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = PROJECT_ROOT / "logs"

# Crea directory se non esistono
for dir_path in [DATA_DIR, OUTPUT_DIR, CACHE_DIR, LOG_DIR]:
    dir_path.mkdir(exist_ok=True, parents=True)

# File paths
CACHE_FILE = CACHE_DIR / "seen_urls.json"
DAILY_OUTPUT = OUTPUT_DIR / "daily.json"
SEARCH_LOG = LOG_DIR / "ob1_search.log"
MAIN_LOG = LOG_DIR / "ob1_radar.log"

# ============================================================================
# SCORING & THRESHOLDS
# ============================================================================

# Score minimi per considerare un'anomalia valida
MIN_ANOMALY_SCORE = 15  # Era forse troppo alto? Abbassiamo un po'
MIN_SOURCES = 2  # Minimo 2 fonti diverse devono parlarne

# Boost score per...
SCORE_BOOST_FACTORS = {
    "multiple_sources": 1.5,  # Se più fonti parlano dello stesso giocatore
    "recent_content": 1.3,    # Se contenuto è recente (ultimi 7 giorni)
    "premium_source": 1.4,    # Se fonte è autorevole (transfermarkt, etc)
    "high_engagement": 1.2    # Se articolo ha molte views/comments
}

# Fonti premium (più affidabili)
PREMIUM_SOURCES = [
    "transfermarkt",
    "whoscored",
    "sofascore",
    "espn",
    "goal.com",
    "football-italia",
    "fourfourtwo",
    "the-athletic"
]

# ============================================================================
# FALLBACK MODE PREVENTION
# ============================================================================

# CRITICO: Questi parametri prevengono il fallback mode
MIN_CANDIDATES_TO_EXIT_FALLBACK = 5  # Almeno 5 candidati per uscire da fallback
MIN_SEARCH_SUCCESS_RATE = 0.3  # Almeno 30% delle ricerche deve avere successo

# Se dopo questo numero di tentativi non troviamo nulla, proviamo query più generiche
FALLBACK_QUERY_THRESHOLD = 10

# ============================================================================
# DEBUG & DEVELOPMENT
# ============================================================================

# Modalità debug (logga più info)
DEBUG_MODE = os.getenv('OB1_DEBUG', 'false').lower() == 'true'

# Test mode (usa query limitate per test veloci)
TEST_MODE = os.getenv('OB1_TEST', 'false').lower() == 'true'
TEST_MAX_QUERIES = 5  # Numero query in test mode

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_search_config():
    """Ritorna configurazione per il RobustSearchEngine"""
    return {
        'anycrawl_key': ANYCRAWL_API_KEY,
        'serper_key': SERPER_API_KEY,
        'tavily_key': TAVILY_API_KEY,
        'gemini_key': GEMINI_API_KEY,
        'brave_key': BRAVE_API_KEY,
        'searxng_instances': SEARXNG_INSTANCES,
        'timeout': TIMEOUT_SECONDS,
        'max_retries': MAX_RETRIES_PER_QUERY
    }

def validate_config():
    """Valida che ci sia almeno una API configurata"""
    has_api = any([
        ANYCRAWL_API_KEY,
        SERPER_API_KEY,
        BRAVE_API_KEY,
        SEARXNG_INSTANCES
    ])
    
    if not has_api:
        raise ValueError(
            "❌ ERRORE: Nessuna API configurata!\n"
            "Configura almeno una delle seguenti:\n"
            "- ANYCRAWL_API_KEY\n"
            "- SERPER_API_KEY\n"
            "- BRAVE_API_KEY\n"
            "- SEARXNG_INSTANCES (sempre disponibile)\n"
        )
    
    return True

def print_config_summary():
    """Stampa riassunto configurazione"""
    print("=" * 60)
    print("🔧 OB1 CONFIGURATION SUMMARY")
    print("=" * 60)
    
    print("\n📡 API Status:")
    print(f"  AnyCrawl: {'✅' if ANYCRAWL_API_KEY else '❌ non configurata'}")
    print(f"  Serper:   {'✅' if SERPER_API_KEY else '⚠️  non configurata (opzionale)'}")
    print(f"  Brave:    {'✅' if BRAVE_API_KEY else '⚠️  non configurata (opzionale)'}")
    print(f"  SearXNG:  ✅ {len(SEARXNG_INSTANCES)} istanze pubbliche")
    
    print("\n🔍 Search Parameters:")
    print(f"  MAX_SERP: {MAX_SERP}")
    print(f"  MIN_TEXT_LEN: {MIN_TEXT_LEN}")
    print(f"  MIN_KEYWORD_MATCHES: {MIN_KEYWORD_MATCHES}")
    print(f"  TIMEOUT: {TIMEOUT_SECONDS}s")
    
    print("\n🌍 Regions:")
    for region, weight in REGION_WEIGHTS.items():
        print(f"  {region}: {weight*100:.0f}%")
    
    print("\n📊 Thresholds:")
    print(f"  MIN_ANOMALY_SCORE: {MIN_ANOMALY_SCORE}")
    print(f"  MIN_CANDIDATES (fallback): {MIN_CANDIDATES_TO_EXIT_FALLBACK}")
    print(f"  MIN_SUCCESS_RATE: {MIN_SEARCH_SUCCESS_RATE*100:.0f}%")
    
    print("\n📁 Paths:")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"  Cache: {CACHE_DIR}")
    print(f"  Logs: {LOG_DIR}")
    
    print("=" * 60)

# ============================================================================
# QUICK SETUP GUIDE
# ============================================================================

SETUP_GUIDE = """
🚀 OB1 QUICK SETUP GUIDE
========================

1. CONFIGURAZIONE BASE (già fatto! ✅)
   - ob1_config.py è pronto
   - ob1_robust_search.py fornisce fallback automatico

2. AGGIUNGI API KEYS OPZIONALI (raccomandato per massima affidabilità)

   a) Serper.dev (2500 ricerche gratis):
      - Vai su https://serper.dev
      - Registrati (1 minuto)
      - Copia API key
      - Aggiungi a ob1_config.py: SERPER_API_KEY = 'la-tua-key'

   b) Brave Search (2000 ricerche/mese gratis):
      - Vai su https://brave.com/search/api/
      - Registrati
      - Copia API key
      - Aggiungi a ob1_config.py: BRAVE_API_KEY = 'la-tua-key'

3. TESTA IL SISTEMA
   ```bash
   python ob1_robust_search.py
   ```

4. INTEGRA CON IL TUO RUN.PY
   - Sostituisci le chiamate ad anycrawl con RobustSearchEngine
   - Esempio nel file ob1_integration_example.py

5. MONITORA I LOG
   ```bash
   tail -f logs/ob1_radar.log
   tail -f logs/ob1_search.log
   ```

IMPORTANTE: Anche senza configurare Serper/Brave, il sistema funziona 
usando SearXNG pubblico come fallback GRATIS illimitato! 🎉
"""

if __name__ == "__main__":
    # Valida configurazione
    try:
        validate_config()
        print_config_summary()
        print(SETUP_GUIDE)
    except ValueError as e:
        print(str(e))
