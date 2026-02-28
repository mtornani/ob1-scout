import requests
import datetime
import logging

logger = logging.getLogger(__name__)

class BlueskyPublisher:
    def __init__(self, handle, app_password):
        self.handle = handle
        self.app_password = app_password
        self.base_url = "https://bsky.social/xrpc"
        self.session = None

    def _create_session(self):
        """Crea una sessione con Bluesky."""
        url = f"{self.base_url}/com.atproto.server.createSession"
        payload = {
            "identifier": self.handle,
            "password": self.app_password
        }
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            self.session = response.json()
            logger.info(f"Sessione Bluesky creata per {self.handle}")
            return True
        except Exception as e:
            logger.error(f"Errore nella creazione della sessione Bluesky: {e}")
            return False

    def publish(self, text):
        """Pubblica un post su Bluesky con retry automatico se il token e' scaduto."""
        if not self.session:
            if not self._create_session():
                return False

        url = f"{self.base_url}/com.atproto.repo.createRecord"

        # ISO format con Z finale (richiesto da AT Protocol)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")

        payload = {
            "repo": self.session["did"],
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": text,
                "createdAt": now,
                "langs": ["it"],
            }
        }

        headers = {
            "Authorization": f"Bearer {self.session['accessJwt']}"
        }

        try:
            response = requests.post(url, json=payload, headers=headers)

            # Token scaduto → rinnova e riprova
            if response.status_code == 401:
                logger.warning("Token Bluesky scaduto, rinnovo sessione...")
                self.session = None
                if not self._create_session():
                    return False
                headers["Authorization"] = f"Bearer {self.session['accessJwt']}"
                payload["repo"] = self.session["did"]
                response = requests.post(url, json=payload, headers=headers)

            if not response.ok:
                logger.error(f"Bluesky API error: {response.status_code} - {response.text}")
                return False

            logger.info("Post pubblicato su Bluesky con successo.")
            return True
        except Exception as e:
            logger.error(f"Errore nella pubblicazione su Bluesky: {e}")
            return False
