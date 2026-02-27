import requests
import json
import logging
import html as html_module

logger = logging.getLogger(__name__)

def esc(text):
    """Escape special HTML chars in Telegram HTML mode."""
    return html_module.escape(str(text))

class TelegramPublisher:
    def __init__(self, bot_token, office_chat_id, public_channel_id):
        self.bot_token = bot_token
        self.office_chat_id = office_chat_id
        self.public_channel_id = public_channel_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}"

    def send_proposal(self, posts, anomaly_id):
        """Invia la proposta all'Ufficio Telegram con bottoni inline."""
        text = "🦎 <b>EATER OF LOGS — Nuovo post pronto</b>\n\n"
        
        text += "📝 <b>Versione X/Twitter:</b>\n"
        text += f"<i>{esc(posts['twitter'])}</i>\n\n"
        
        text += "📝 <b>Versione Bluesky:</b>\n"
        text += f"<i>{esc(posts['bluesky'])}</i>\n\n"
        
        text += "📝 <b>Versione Telegram:</b>\n"
        text += f"<i>{esc(posts['telegram'])}</i>\n\n"
        
        text += "📝 <b>Versione LinkedIn:</b>\n"
        text += f"<i>{esc(posts['linkedin'])}</i>\n\n"
        
        text += "Azione:"
        
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Pubblica Tutto", "callback_data": f"publish_all:{anomaly_id}"},
                    {"text": "❌ Scarta", "callback_data": f"reject:{anomaly_id}"}
                ],
                [
                    {"text": "🐦 Solo X", "callback_data": f"publish_twitter:{anomaly_id}"},
                    {"text": "🦋 Solo Bluesky", "callback_data": f"publish_bluesky:{anomaly_id}"}
                ],
                [
                    {"text": "📢 Solo TG", "callback_data": f"publish_telegram:{anomaly_id}"},
                    {"text": "✏️ Modifica", "callback_data": f"edit:{anomaly_id}"}
                ]
            ]
        }

        payload = {
            "chat_id": self.office_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup
        }

        try:
            response = requests.post(f"{self.api_url}/sendMessage", json=payload)
            if not response.ok:
                error_msg = f"Telegram API error: {response.status_code} - {response.text}"
                logger.error(error_msg)
                # Scrivi su file per debug
                with open("tg_error.txt", "w", encoding="utf-8") as f:
                    f.write(f"Status: {response.status_code}\n{response.text}\n\nMessage length: {len(text)}\n")
                return False
            logger.info("Proposta inviata all'Ufficio Telegram.")
            return True
        except Exception as e:
            logger.error(f"Errore nell'invio della proposta a Telegram: {e}")
            return False

    def publish_to_public_channel(self, text):
        """Invia il post finale al canale pubblico."""
        payload = {
            "chat_id": self.public_channel_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        try:
            response = requests.post(f"{self.api_url}/sendMessage", json=payload)
            response.raise_for_status()
            logger.info("Pubblicato su canale pubblico Telegram.")
            return True
        except Exception as e:
            logger.error(f"Errore nella pubblicazione su canale pubblico Telegram: {e}")
            return False
