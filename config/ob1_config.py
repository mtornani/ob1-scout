#!/usr/bin/env python3
"""
OB1 Configuration - Gestione centralizzata di tutte le configurazioni
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# API Keys
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
SERPER_API_KEY = os.getenv('SERPER_API_KEY', '')

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')
TELEGRAM_OFFICE_CHAT_ID = os.getenv('TELEGRAM_OFFICE_CHAT_ID', '')

# Search
TIMEOUT_SECONDS = 30

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

# Directories — created at import time
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = PROJECT_ROOT / "logs"

for _dir in [DATA_DIR, OUTPUT_DIR, CACHE_DIR, LOG_DIR]:
    _dir.mkdir(exist_ok=True, parents=True)

# Debug
DEBUG_MODE = os.getenv('OB1_DEBUG', 'false').lower() == 'true'
TEST_MODE = os.getenv('OB1_TEST', 'false').lower() == 'true'
TEST_MAX_QUERIES = 5
