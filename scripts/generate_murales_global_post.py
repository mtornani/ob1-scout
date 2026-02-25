#!/usr/bin/env python3
"""
OB1 Global Scout - Generate Murales Global Post
Reads the SQLite memory to find the highest asymmetry score anomaly and exposes the delay of the global market.
"""

import sqlite3
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "ob1_global.db"
SOCIAL_DIR = BASE_DIR / "data" / "social"
SOCIAL_DIR.mkdir(parents=True, exist_ok=True)

def fetch_top_anomaly():
    """Fetch the most recent anomaly with the highest score."""
    if not DB_PATH.exists():
        return None
        
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get the anomaly with the highest score
    # Joining with lead_times to get lead_time_days
    try:
        c.execute('''
            SELECT a.*, l.lead_time_days, l.ob1_detection_date 
            FROM anomalies a
            JOIN lead_times l ON a.player_name = l.player_name
            WHERE a.score >= 15 
            ORDER BY a.score DESC, a.detection_date DESC LIMIT 1
        ''')
        row = c.fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"Error fetching top anomaly: {e}")
        return None
    finally:
        conn.close()

def generate_x_post(anomaly):
    """Generate the X post based on Information Asymmetry."""
    if not anomaly:
        return "Nessuna anomalia rilevata questa settimana."
        
    player = anomaly.get('player_name', 'Unknown')
    region = anomaly.get('region', 'Global')
    score = anomaly.get('score', 0)
    lead_time = anomaly.get('lead_time_days', 0)
    
    post = f"""🌍 Radar Globale — Asimmetria Informativa
    
Caso: {player}, {region}.
Informazione isolata dal rumore globale.
Asymmetry Score: {score}/100

Vantaggio accumulato: +{lead_time if lead_time is not None else "?"} giorni sui media mainstream.

Quando i giornali ne parleranno, il prezzo sarà già inavvicinabile.
Il mercato globale è lento. L'informazione no.

🔗 t.me/ob1scout_global"""
    return post

def generate_tg_post(anomaly):
    """Generate the Telegram post."""
    if not anomaly:
        return "Nessuna anomalia rilevata questa settimana."
        
    player = anomaly.get('player_name', 'Unknown')
    region = anomaly.get('region', 'Global')
    score = anomaly.get('score', 0)
    lead_time = anomaly.get('lead_time_days', 0)
    context = anomaly.get('raw_content', 'Anomalia emergente.')[:200]
    
    post = f"""🌍 OB1 GLOBAL RADAR — Asimmetria Informativa
    
━━━ SEGNALE RILEVATO ━━━

🔴 Caso: {player}
🔴 Regione: {region}
🔴 Asymmetry Score: {score}/100
🔴 Lead Time: +{lead_time if lead_time is not None else "?"} giorni sui radar tradizionali

━━━ INTELLIGENCE ━━━
{context}...

━━━━━━━━━━━━━━━━━━
Il sistema estrae il talento a migliaia di chilometri di distanza.
I radar da milioni di euro non l'hanno ancora visto.

🔗 Dashboard: https://mtornani.github.io/ob1-scout/"""
    return post

def main():
    print("🎨 OB1 Social Post Generator (Global Asymmetry)")
    anomaly = fetch_top_anomaly()
    
    if not anomaly:
        print("⚠️ Nessuna anomalia trovata nel DB. Post saltato.")
        return
        
    x_post = generate_x_post(anomaly)
    tg_post = generate_tg_post(anomaly)
    
    x_file = SOCIAL_DIR / "weekly_x_post.txt"
    tg_file = SOCIAL_DIR / "weekly_telegram_post.txt"
    
    x_file.write_text(x_post, encoding="utf-8")
    tg_file.write_text(tg_post, encoding="utf-8")
    
    print(f"✅ X post: {x_file} ({len(x_post)} char)")
    print(f"✅ Telegram post: {tg_file} ({len(tg_post)} char)")

if __name__ == "__main__":
    main()
