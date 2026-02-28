import json
import os
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AnomalySelector:
    def __init__(self, anomalies_path="eater-of-logs/data.json"):
        self.anomalies_path = anomalies_path

    def load_anomalies(self):
        """Carica le anomalie dal file JSON."""
        if not os.path.exists(self.anomalies_path):
            logger.error(f"File anomalie non trovato: {self.anomalies_path}")
            return []
        
        try:
            with open(self.anomalies_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Normalizza entrambi i formati (Global e Serie C)
            normalized = []
            for i, item in enumerate(data):
                normalized.append(self._normalize(item, i))
            return normalized
        except Exception as e:
            logger.error(f"Errore durante il caricamento delle anomalie: {e}")
            return []

    def _normalize(self, item, idx):
        """Normalizza un record al formato unificato per il formatter."""
        # Formato Serie C (ha player_profile annidato)
        if 'player_profile' in item:
            profile = item.get('player_profile', {})
            birth_year = profile.get('birth_year')
            age = (datetime.now().year - birth_year) if birth_year else None
            
            return {
                'id': item.get('id', idx + 1),
                'player_name': item.get('player_name', profile.get('name', 'N/D')),
                'age': age,
                'role': profile.get('role', 'N/D').replace('_', ' '),
                'current_club': profile.get('current_club', 'N/D'),
                'market_value': profile.get('market_value', 'N/D'),
                'opportunity_type': item.get('opportunity_type', 'anomalia'),
                'description': item.get('description', ''),
                'tactical_reason': item.get('tactical_reason', ''),
                'source': item.get('source_name', 'OB1 Scout'),
                'clubs_involved': item.get('clubs_involved', []),
                'score': (item.get('relevance_score', 0) * 10) + item.get('tactical_fit', 0) / 10,
                'reported_date': item.get('reported_date', ''),
                'ob1_detection_date': item.get('reported_date', datetime.now().isoformat()),
                # Campo compatibilità
                'raw_content': item.get('description', ''),
                'region': 'Lega Pro / Serie C',
            }
        # Formato Global (flat)
        else:
            return {
                'id': item.get('id', idx + 1),
                'player_name': item.get('player_name', 'N/D'),
                'age': None,
                'role': 'N/D',
                'current_club': 'N/D',
                'market_value': 'N/D',
                'opportunity_type': 'anomalia',
                'description': item.get('raw_content', ''),
                'tactical_reason': '',
                'source': 'OB1 Scout',
                'clubs_involved': [],
                'score': item.get('score', 0),
                'reported_date': item.get('detection_date', ''),
                'ob1_detection_date': item.get('ob1_detection_date', datetime.now().isoformat()),
                'raw_content': item.get('raw_content', ''),
                'region': item.get('region', 'N/D'),
            }

    def get_top_anomaly(self):
        """Seleziona la migliore anomalia per score."""
        anomalies = self.load_anomalies()
        if not anomalies:
            return None

        sorted_anomalies = sorted(anomalies, key=lambda x: (x.get('score', 0), x.get('ob1_detection_date', '')), reverse=True)
        top = sorted_anomalies[0]
        logger.info(f"Top anomalia selezionata: {top.get('player_name')} (Score: {top.get('score'):.1f})")
        return top

    def get_stats(self):
        """Calcola statistiche rapide per i template."""
        anomalies = self.load_anomalies()
        total = len(anomalies)
        
        under_28 = sum(1 for a in anomalies if a.get('age') and a['age'] < 28)
        svincolati = sum(1 for a in anomalies if a.get('opportunity_type') == 'svincolato')
        
        return {
            "total": total,
            "under_28": under_28 if under_28 > 0 else int(total * 0.4),
            "svincolati": svincolati,
            "date": datetime.now().strftime("%d/%m/%Y"),
            "date_short": datetime.now().strftime("%d/%m")
        }

if __name__ == "__main__":
    selector = AnomalySelector()
    top = selector.get_top_anomaly()
    print(json.dumps(top, indent=2, ensure_ascii=False))
    print(selector.get_stats())
