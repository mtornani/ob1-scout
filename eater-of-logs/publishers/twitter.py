import requests
from requests_oauthlib import OAuth1
import logging

logger = logging.getLogger(__name__)

class TwitterPublisher:
    def __init__(self, api_key, api_secret, access_token, access_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.access_secret = access_secret
        self.api_url = "https://api.twitter.com/2/tweets"

    def publish(self, text):
        """Pubblica un tweet su X usando OAuth 1.0a e requests (per compatibilità Free Tier)."""
        auth = OAuth1(
            self.api_key, 
            self.api_secret, 
            self.access_token, 
            self.access_secret
        )
        
        payload = {"text": text}
        
        try:
            response = requests.post(self.api_url, auth=auth, json=payload)
            response.raise_for_status()
            res_data = response.json()
            tweet_id = res_data.get('data', {}).get('id')
            logger.info(f"Tweet pubblicato su X con successo. ID: {tweet_id}")
            return True, tweet_id
        except Exception as e:
            logger.error(f"Errore nella pubblicazione su X: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Dettagli errore X: {e.response.text}")
            return False, None
