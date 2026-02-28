import os
import sys
import logging
from dotenv import load_dotenv

# Aggiungi la directory corrente al path per importazioni locali
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Assicura che l'output su console gestisca UTF-8 su Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from selector import AnomalySelector
from formatter import PostFormatter
from publishers.telegram import TelegramPublisher

# Configurazione logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("eater.log")
    ]
)
logger = logging.getLogger("EaterOfLogs")

def main():
    logger.info("--- Starting Eater of Logs ---")
    
    # Carica variabili d'ambiente (per testing locale)
    load_dotenv()
    
    # Configurazione percorsi
    # Se eseguito da GitHub Actions, il path relativo sarà dalla root del repo
    # In locale potrebbe variare.
    anomalies_path = os.getenv("ANOMALIES_FILE", "eater-of-logs/data.json")
    
    # 1. Inizializza Selector
    selector = AnomalySelector(anomalies_path=anomalies_path)
    
    # 2. Seleziona top anomalia e statistiche
    top_anomaly = selector.get_top_anomaly()
    if not top_anomaly:
        logger.warning("Nessuna anomalia trovata. Uscita.")
        return
    
    stats = selector.get_stats()
    
    # 3. Formatta i post
    # Il link telegram pubblico può essere passato via env
    public_link = os.getenv("TELEGRAM_PUBLIC_LINK", "https://t.me/ob1scoutlegapro")
    formatter = PostFormatter(telegram_link=public_link)
    posts = formatter.format_all(top_anomaly, stats)
    
    # 4. Invia Proposta all'Ufficio Telegram
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    office_id = os.getenv("TELEGRAM_OFFICE_CHAT_ID")
    public_id = os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID")
    
    if not all([bot_token, office_id]):
        logger.error("Credenziali Telegram mancanti (TOKEN o OFFICE_ID).")
        # In modalità dry-run stampiamo comunque i post
        logger.info("DRY RUN - Post generati:")
        for platform, content in posts.items():
            print(f"\n--- {platform.upper()} ---\n{content}")
        return

    publisher = TelegramPublisher(bot_token, office_id, public_id)
    success = publisher.send_proposal(posts, top_anomaly.get('id', 'unknown'))
    
    if success:
        logger.info("Workflow completato con successo. In attesa di approvazione umana.")
    else:
        logger.error("Fallimento nell'invio della proposta.")

if __name__ == "__main__":
    main()
