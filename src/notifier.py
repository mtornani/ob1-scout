#!/usr/bin/env python3
"""
OB1 Global Scout - Admin Notifier
Best-effort admin alert to Mirko's private Telegram channel.
Never raises — pipeline safety is the priority.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)


def admin_alert(severity: str, source: str, message: str) -> None:
    """
    Send a best-effort admin alert to the internal Telegram channel (TELEGRAM_OFFICE_CHAT_ID).

    - Never raises. On Telegram failure: logs to file and returns.
    - Does NOT call send_telegram_notification (anti-recursion: uses requests.post directly).
    - Env vars read at call time (not import time) so load_dotenv() order doesn't matter.

    Args:
        severity: "WARN" | "ERROR" | "CRITICAL"
        source:   module/component name, e.g. "scraper", "intelligence/ingest"
        message:  human-readable problem description
    """
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_OFFICE_CHAT_ID', '')

    text = f"[ADMIN-OB1-GLOBAL] {severity} | {source}\n{message}"
    logger.warning(f"[admin_alert] {severity} | {source}: {message}")

    if not token or not chat_id:
        logger.warning("[admin_alert] TELEGRAM_BOT_TOKEN or TELEGRAM_OFFICE_CHAT_ID not set — alert not delivered.")
        return

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=8,
        )
        if resp.status_code != 200:
            logger.error(f"[admin_alert] Telegram HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"[admin_alert] Send failed (best-effort, pipeline continues): {e}")
