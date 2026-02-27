import os
import sys
import logging
import argparse
from dotenv import load_dotenv

# Aggiungi la directory corrente al path per importazioni locali
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from selector import AnomalySelector
from formatter import PostFormatter
from publishers.telegram import TelegramPublisher
from publishers.bluesky import BlueskyPublisher
from publishers.twitter import TwitterPublisher

# Configurazione logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("EaterPusher")

def main():
    parser = argparse.ArgumentParser(description="Pubblica i post approvati su vari canali.")
    parser.add_argument("--anomaly_id", type=str, required=True, help="ID dell'anomalia da pubblicare")
    parser.add_argument("--platforms", type=str, default="all", help="Piattaforme separate da virgola (twitter,bluesky,telegram) o 'all'")
    
    args = parser.parse_args()
    # Cerca .env sia nella dir corrente che in quella dello script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(script_dir, ".env"))
    load_dotenv()  # fallback per GitHub Actions che usa variabili d'ambiente dirette
    
    logger.info(f"Avvio pubblicazione per anomalia ID: {args.anomaly_id} su piattaforme: {args.platforms}")
    
    # 1. Carica l'anomalia specifica
    anomalies_path = os.getenv("ANOMALIES_FILE", "ob1_serie_c/ob1-serie-c/src/data.json")
    selector = AnomalySelector(anomalies_path=anomalies_path)
    anomalies = selector.load_anomalies()
    
    target_anomaly = next((a for a in anomalies if str(a.get('id')) == args.anomaly_id), None)
    
    if not target_anomaly:
        logger.error(f"Anomalia con ID {args.anomaly_id} non trovata.")
        sys.exit(1)
        
    # 2. Formatta i post
    stats = selector.get_stats()
    public_link = os.getenv("TELEGRAM_PUBLIC_LINK", "https://t.me/ob1scoutlegapro")
    formatter = PostFormatter(telegram_link=public_link)
    posts = formatter.format_all(target_anomaly, stats)
    
    platforms_to_publish = args.platforms.split(",") if args.platforms != "all" else ["twitter", "bluesky", "telegram"]
    
    # 3. Pubblica su piattaforme selezionate
    
    # --- Telegram Public ---
    if "telegram" in platforms_to_publish:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        public_id = os.getenv("TELEGRAM_PUBLIC_CHANNEL_ID")
        if bot_token and public_id:
            tp = TelegramPublisher(bot_token, "", public_id)
            tp.publish_to_public_channel(posts["telegram"])
        else:
            logger.warning("Credenziali Telegram mancanti per pubblicazione pubblica.")

    # --- Bluesky ---
    if "bluesky" in platforms_to_publish:
        handle = os.getenv("BSKY_HANDLE")
        password = os.getenv("BSKY_APP_PASSWORD")
        if handle and password:
            bp = BlueskyPublisher(handle, password)
            bp.publish(posts["bluesky"])
        else:
            logger.warning("Credenziali Bluesky mancanti.")

    # --- X/Twitter ---
    if "twitter" in platforms_to_publish:
        api_key = os.getenv("X_API_KEY")
        api_secret = os.getenv("X_API_SECRET")
        access_token = os.getenv("X_ACCESS_TOKEN")
        access_secret = os.getenv("X_ACCESS_SECRET")
        if all([api_key, api_secret, access_token, access_secret]):
            xp = TwitterPublisher(api_key, api_secret, access_token, access_secret)
            xp.publish(posts["twitter"])
        else:
            logger.warning("Credenziali X/Twitter mancanti.")

    logger.info("Operazione di pubblicazione terminata.")

if __name__ == "__main__":
    main()
