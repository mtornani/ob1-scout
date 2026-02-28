import re
import logging
import unicodedata

logger = logging.getLogger(__name__)

OPPORTUNITY_TYPE_LABEL = {
    "svincolato": "SVINCOLATO",
    "rescissione": "IN USCITA (rescissione)",
    "prestito": "DISPONIBILE IN PRESTITO",
    "talento_serie_d": "TALENTO DA SERIE D",
    "infortunio": "MERCATO APERTO (infortunio squad)",
    "anomalia": "ANOMALIA RILEVATA",
}

# Label compatte per Twitter (280 char budget stretto)
OPPORTUNITY_TYPE_SHORT = {
    "svincolato": "SVINCOLATO",
    "rescissione": "IN USCITA",
    "prestito": "IN PRESTITO",
    "talento_serie_d": "TALENTO SERIE D",
    "infortunio": "MERCATO APERTO",
    "anomalia": "ANOMALIA",
}

class PostFormatter:
    def __init__(self, telegram_link="t.me/ob1scout"):
        self.telegram_link = telegram_link

    def format_all(self, anomaly, stats):
        """Genera tutte le versioni dei post."""
        return {
            "twitter": self.format_twitter(anomaly, stats),
            "bluesky": self.format_bluesky(anomaly, stats),
            "telegram": self.format_telegram(anomaly, stats),
            "linkedin": self.format_linkedin(anomaly, stats)
        }

    def _get_player_info(self, anomaly):
        """Estrae info strutturate dall'anomalia normalizzata."""
        name = anomaly.get('player_name', 'Giocatore Ignoto')
        age = str(anomaly.get('age', 'N/D')) if anomaly.get('age') else 'N/D'
        role = anomaly.get('role', 'N/D')
        club = anomaly.get('current_club', 'N/D')
        market_value = anomaly.get('market_value', 'N/D')
        opp_type = OPPORTUNITY_TYPE_LABEL.get(anomaly.get('opportunity_type', ''), anomaly.get('opportunity_type', 'N/D'))
        description = anomaly.get('description', anomaly.get('raw_content', ''))
        tactical = anomaly.get('tactical_reason', '')
        source = anomaly.get('source', 'OB1')
        clubs_involved = anomaly.get('clubs_involved', [])
        
        # Short description for tight formats
        detail = description[:120] + "..." if len(description) > 120 else description
        
        return {
            "name": name, "age": age, "role": role, "club": club,
            "market_value": market_value, "opp_type": opp_type,
            "description": description, "detail": detail,
            "tactical": tactical, "source": source,
            "clubs_involved": clubs_involved,
        }

    @staticmethod
    def _twitter_len(text):
        """Conta i caratteri come li conta X/Twitter.
        URL → sempre 23 chars. Emoji (So) → 2 chars. Resto → 1 char.
        """
        urls = re.findall(r'https?://\S+', text)
        text_clean = re.sub(r'https?://\S+', '', text)
        normalized = unicodedata.normalize('NFC', text_clean)
        count = sum(2 if unicodedata.category(ch).startswith('So') else 1 for ch in normalized)
        return count + len(urls) * 23

    def format_twitter(self, anomaly, stats):
        info = self._get_player_info(anomaly)
        opp_short = OPPORTUNITY_TYPE_SHORT.get(
            anomaly.get('opportunity_type', ''), info['opp_type'])
        clubs_str = f" → {', '.join(info['clubs_involved'])}" if info['clubs_involved'] else ""

        TWITTER_LIMIT = 280

        date = stats.get('date_short', stats['date'])
        header = f"Lega Pro {date}. {stats['total']} svincolati, {stats['under_28']} U28.\n\n"
        player_line = f"{info['name']}, {info['age']}. {opp_short}{clubs_str}.\n"
        footer = f"\nhttps://t.me/Ob1LegaPro_bot"

        fixed_len = self._twitter_len(header + player_line + footer)
        budget = TWITTER_LIMIT - fixed_len - 4  # -4 per "...\n"

        if budget > 0 and info['description']:
            desc = info['description']
            if self._twitter_len(desc) > budget:
                while desc and self._twitter_len(desc) > budget:
                    desc = desc[:-1]
                desc = desc.rstrip() + "..."
            detail_line = f"{desc}\n"
        else:
            detail_line = ""

        return header + player_line + detail_line + footer

    @staticmethod
    def _grapheme_len(text):
        """Conta grapheme clusters (approssimato via NFC normalization)."""
        return len(unicodedata.normalize('NFC', text))

    def format_bluesky(self, anomaly, stats):
        """Genera post Bluesky entro 300 graphemes. Tronca la descrizione, mai il footer."""
        info = self._get_player_info(anomaly)
        BSKY_LIMIT = 300

        header = (
            f"Lega Pro, {stats['date']}.\n\n"
            f"{stats['total']} svincolati. {stats['under_28']} under 28.\n"
            f"{info['name']}, {info['age']} anni ({info['role']}). {info['opp_type']}.\n"
        )
        footer = (
            f"\n\nUn algoritmo da 5$/mese. Nessun agente. Nessun pranzo.\n\n"
            f"Segui su Telegram: {self.telegram_link}"
        )

        fixed_len = self._grapheme_len(header) + self._grapheme_len(footer)
        budget = BSKY_LIMIT - fixed_len

        desc = info['description']
        if budget <= 10:
            desc_line = ""
        elif self._grapheme_len(desc) > budget:
            while desc and self._grapheme_len(desc) > budget - 3:
                desc = desc[:-1]
            desc_line = desc.rstrip() + "..."
        else:
            desc_line = desc

        template = header + desc_line + footer
        return template

    def format_telegram(self, anomaly, stats):
        info = self._get_player_info(anomaly)
        clubs_line = f"🏟 Squadre coinvolte: {', '.join(info['clubs_involved'])}\n" if info['clubs_involved'] else ""
        tactical_line = f"📐 Analisi: {info['tactical']}\n" if info['tactical'] else ""
        template = (
            f"📊 OB1 Scout — Report {stats['date']}\n\n"
            f"Svincolati monitorati: {stats['total']}\n"
            f"Under 28: {stats['under_28']}\n"
            f"Anomalie rilevate: {stats['total']}\n\n"
            f"🔴 Top segnalazione:\n"
            f"👤 {info['name']}, {info['age']} anni — {info['role']}\n"
            f"📍 Club: {info['club']}\n"
            f"💶 Valore: {info['market_value']}\n"
            f"📌 Tipo: {info['opp_type']}\n"
            f"{clubs_line}"
            f"📝 {info['description']}\n"
            f"{tactical_line}\n"
            f"⚙️ Sistema automatico — scraping ogni 6h\n"
            f"💰 Costo operativo: <5$/mese"
        )
        return template

    def format_linkedin(self, anomaly, stats):
        info = self._get_player_info(anomaly)
        template = (
            f"Intelligence Report: Lega Pro {stats['date']}\n\n"
            f"Il sistema OB1 Scout ha processato l'ultimo batch di dati. "
            f"Risultato: {stats['total']} opportunità rilevate, di cui {stats['under_28']} profili under 28.\n\n"
            f"Focus del giorno: {info['name']} ({info['age']} anni) — {info['role']} | {info['opp_type']}.\n"
            f"Nota tecnica: {info['description']}\n\n"
            f"Nessun bias umano. Solo dati e asimmetria informativa.\n\n"
            f"#Scouting #LegaPro #DataAnalysis #FootballIntelligence"
        )
        return template
