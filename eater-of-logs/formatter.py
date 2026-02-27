import logging

logger = logging.getLogger(__name__)

OPPORTUNITY_TYPE_LABEL = {
    "svincolato": "SVINCOLATO",
    "rescissione": "IN USCITA (rescissione)",
    "prestito": "DISPONIBILE IN PRESTITO",
    "talento_serie_d": "TALENTO DA SERIE D",
    "infortunio": "MERCATO APERTO (infortuno squad)",
    "anomalia": "ANOMALIA RILEVATA",
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

    def format_twitter(self, anomaly, stats):
        info = self._get_player_info(anomaly)
        clubs_str = f" → {', '.join(info['clubs_involved'])}" if info['clubs_involved'] else ""
        
        # Twitter conta caratteri speciali (→, è, à...) come 2 chars.
        # Usiamo un budget conservativo di 255 per evitare troncature lato X.
        TWITTER_SAFE_LIMIT = 255
        
        header = (
            f"Lega Pro, {stats['date']}. "
            f"{stats['total']} svincolati nel sistema, {stats['under_28']} under 28.\n\n"
        )
        player_line = f"{info['name']}, {info['age']}a. {info['opp_type']}{clubs_str}.\n"
        footer = f"\nL'algoritmo lo vede. Il tuo DS no.\nhttps://t.me/Ob1LegaPro_bot"
        
        fixed_len = len(header) + len(player_line) + len(footer)
        budget = TWITTER_SAFE_LIMIT - fixed_len - 4  # -4 per "...\n"
        
        if budget > 0 and info['description']:
            desc = info['description'][:budget] + ("..." if len(info['description']) > budget else "")
            detail_line = f"{desc}\n"
        else:
            detail_line = ""
        
        return header + player_line + detail_line + footer

    def format_bluesky(self, anomaly, stats):
        info = self._get_player_info(anomaly)
        template = (
            f"Lega Pro, {stats['date']}.\n\n"
            f"{stats['total']} svincolati. {stats['under_28']} under 28.\n"
            f"{info['name']}, {info['age']} anni ({info['role']}). {info['opp_type']}.\n"
            f"{info['detail']}\n\n"
            f"Un algoritmo da 5$/mese. Nessun agente. Nessun pranzo.\n\n"
            f"📢 Segui su Telegram: {self.telegram_link}"
        )
        if len(template) > 300:
            template = template[:297] + "..."
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
