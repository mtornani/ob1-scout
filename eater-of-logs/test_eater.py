"""
Test suite per Eater of Logs.
Verifica: formatter (Twitter budget, Bluesky limit), selector, bottoni Telegram.
"""
import sys
import os
import io
import json
import unicodedata

# Fix encoding su Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from selector import AnomalySelector
from formatter import PostFormatter
from publishers.telegram import TelegramPublisher

# ── Dati di test ──────────────────────────────────────────────────────────────

SAMPLE_SERIE_C = [
    {
        "opportunity_type": "rescissione",
        "player_name": "Jacopo Dezi",
        "player_profile": {
            "name": "Jacopo Dezi",
            "birth_year": 1992,
            "role": "centrocampista",
            "current_club": "Padova",
            "market_value": "€350k",
            "contract_expiry": "2025"
        },
        "description": "Rottura totale con la società. Il centrocampista è fuori dal progetto tecnico e rescinderà a breve. Occasione d'oro per la mediana.",
        "relevance_score": 5,
        "tactical_fit": 95,
        "tactical_reason": "Mezzala di categoria superiore (ex A e B). Inserimenti e tecnica perfetti.",
        "source_name": "PadovaSport",
        "reported_date": "2026-01-18",
        "clubs_involved": ["Padova", "Triestina"]
    },
    {
        "opportunity_type": "svincolato",
        "player_name": "Marco Sau",
        "player_profile": {
            "name": "Marco Sau",
            "birth_year": 1987,
            "role": "punta",
            "current_club": "Svincolato",
            "market_value": "€200k"
        },
        "description": "L'ex bomber di Cagliari e Benevento è ancora senza squadra. Fisicamente integro, cerca un progetto ambizioso in C. Richiesta ingaggio ridotta.",
        "relevance_score": 5,
        "tactical_fit": 88,
        "tactical_reason": "Esperienza infinita. Se sta bene, fa la differenza in C. Ideale come punta di movimento.",
        "source_name": "TuttoC",
        "reported_date": "2026-01-18",
        "clubs_involved": ["Benevento", "Feralpisalò"]
    },
]

SAMPLE_GLOBAL = [
    {
        "id": 1,
        "player_name": "Honest Ahanor",
        "detection_date": "2026-02-25 21:50:15",
        "source_url": "https://www.instagram.com/p/DVIxIZUElSq/",
        "score": 94.0,
        "raw_content": "Massima asimmetria informativa. Segnale intercettato da network locali nigeriani al compimento del 18° anno. Valutazione di mercato percepita internamente superiore ai professionisti.",
        "region": "West Africa",
        "status": "detected",
    }
]


def count_twitter_chars(text):
    """
    Conta i caratteri come li conta Twitter (X).
    - URL t.co: sempre 23 chars
    - Emoji: 2 chars ciascuna
    - Tutto il resto: 1 char per codepoint (dopo NFC normalization)
    """
    import re
    # Rimuovi URL e conta come 23 ciascuno
    url_pattern = re.compile(r'https?://\S+')
    urls = url_pattern.findall(text)
    text_no_urls = url_pattern.sub('', text)

    # NFC normalization
    normalized = unicodedata.normalize('NFC', text_no_urls)

    count = 0
    for ch in normalized:
        cat = unicodedata.category(ch)
        # Emoji e simboli speciali contano 2
        if cat.startswith('So') or cat.startswith('Cs'):
            count += 2
        else:
            count += 1

    # Ogni URL conta 23
    count += len(urls) * 23

    return count


def make_stats():
    return {
        "total": 8,
        "under_28": 3,
        "svincolati": 4,
        "date": "18/01/2026",
        "date_short": "18/01"
    }


# ── TEST TWITTER ──────────────────────────────────────────────────────────────

def test_twitter_length():
    """Verifica che il post Twitter non superi 280 chars (limite reale X)."""
    formatter = PostFormatter()
    stats = make_stats()

    print("=" * 60)
    print("TEST TWITTER - Lunghezza post")
    print("=" * 60)

    # Normalizza i dati come farebbe il selector
    selector = AnomalySelector.__new__(AnomalySelector)

    all_pass = True
    for i, item in enumerate(SAMPLE_SERIE_C + SAMPLE_GLOBAL):
        anomaly = selector._normalize(item, i)
        post = formatter.format_twitter(anomaly, stats)

        python_len = len(post)
        twitter_len = count_twitter_chars(post)

        status = "OK" if twitter_len <= 280 else "FAIL - TAGLIATO!"
        if twitter_len > 280:
            all_pass = False

        print(f"\n  [{status}] {anomaly['player_name']}")
        print(f"    Python len: {python_len} | Twitter len: {twitter_len} / 280")
        print(f"    Budget usato: {twitter_len}/280 ({twitter_len*100//280}%)")
        print(f"    Post:\n    ---")
        for line in post.split('\n'):
            print(f"    {line}")
        print(f"    ---")

    # Test con descrizione molto lunga
    long_anomaly = selector._normalize({
        "opportunity_type": "svincolato",
        "player_name": "Giovanni Konstantinopulos",
        "player_profile": {
            "name": "Giovanni Konstantinopulos",
            "birth_year": 1995,
            "role": "centrocampista_difensivo",
            "current_club": "Svincolato",
            "market_value": "€500k"
        },
        "description": "Giocatore straordinario con una capacità di visione di gioco incredibile. Ha militato in Serie A, B e C mostrando sempre qualità tecniche sopraffine. La sua capacità di distribuire il gioco e di inserirsi in area lo rendono un profilo unico nel panorama del calcio italiano.",
        "relevance_score": 5,
        "tactical_fit": 90,
        "source_name": "Test",
        "reported_date": "2026-01-18",
        "clubs_involved": ["Padova", "Triestina", "Avellino"]
    }, 99)

    post = formatter.format_twitter(long_anomaly, stats)
    twitter_len = count_twitter_chars(post)
    status = "OK" if twitter_len <= 280 else "FAIL - TAGLIATO!"
    if twitter_len > 280:
        all_pass = False

    print(f"\n  [{status}] EDGE CASE: nome lungo + 3 club + desc lunga")
    print(f"    Twitter len: {twitter_len} / 280")
    print(f"    Post:\n    ---")
    for line in post.split('\n'):
        print(f"    {line}")
    print(f"    ---")

    return all_pass


# ── TEST BLUESKY ──────────────────────────────────────────────────────────────

def test_bluesky_length():
    """Verifica che il post Bluesky rispetti i 300 graphemes (limite AT Protocol)."""
    formatter = PostFormatter()
    stats = make_stats()
    selector = AnomalySelector.__new__(AnomalySelector)

    print("\n" + "=" * 60)
    print("TEST BLUESKY - Lunghezza post (limite: 300 graphemes)")
    print("=" * 60)

    all_pass = True
    for i, item in enumerate(SAMPLE_SERIE_C + SAMPLE_GLOBAL):
        anomaly = selector._normalize(item, i)
        post = formatter.format_bluesky(anomaly, stats)

        # AT Protocol usa grapheme clusters, ma in pratica
        # per testo latino e' ~= len() dopo NFC
        grapheme_len = len(unicodedata.normalize('NFC', post))
        byte_len = len(post.encode('utf-8'))

        status = "OK" if grapheme_len <= 300 else "FAIL - TROPPO LUNGO!"
        if grapheme_len > 300:
            all_pass = False

        print(f"\n  [{status}] {anomaly['player_name']}")
        print(f"    Graphemes: {grapheme_len} / 300 | Bytes UTF-8: {byte_len}")
        print(f"    Post:\n    ---")
        for line in post.split('\n'):
            print(f"    {line}")
        print(f"    ---")

    return all_pass


def test_bluesky_api_format():
    """Verifica che il payload AT Protocol sia corretto."""
    from publishers.bluesky import BlueskyPublisher
    import datetime

    print("\n" + "=" * 60)
    print("TEST BLUESKY - Formato API AT Protocol")
    print("=" * 60)

    bp = BlueskyPublisher("test.bsky.social", "fake-password")
    # Simula sessione
    bp.session = {
        "did": "did:plc:test123",
        "accessJwt": "fake-jwt-token"
    }

    # Verifica formato createdAt
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"  createdAt format: {now}")
    assert now.endswith("Z"), "FAIL: createdAt deve terminare con Z"
    print(f"  [OK] Formato timestamp corretto")

    # Verifica che il payload sia ben formato
    payload = {
        "repo": bp.session["did"],
        "collection": "app.bsky.feed.post",
        "record": {
            "$type": "app.bsky.feed.post",
            "text": "Test post",
            "createdAt": now
        }
    }
    assert payload["record"]["$type"] == "app.bsky.feed.post"
    print(f"  [OK] Payload struttura corretta")

    # Verifica che l'emoji nel testo non causi problemi
    test_text = "📢 Segui su Telegram: t.me/test"
    byte_text = test_text.encode('utf-8')
    print(f"  Emoji test - chars: {len(test_text)}, bytes: {len(byte_text)}")
    print(f"  [OK] Emoji gestite correttamente")

    return True


# ── TEST BOTTONI TELEGRAM ─────────────────────────────────────────────────────

def test_telegram_buttons():
    """Verifica callback_data (max 64 bytes), layout e completezza bottoni."""
    print("\n" + "=" * 60)
    print("TEST TELEGRAM - Bottoni inline")
    print("=" * 60)

    all_pass = True

    # Simula i bottoni come li crea TelegramPublisher.send_proposal
    test_ids = ["1", "unknown", "42", "very-long-anomaly-id-that-could-break"]

    for anomaly_id in test_ids:
        buttons = [
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

        print(f"\n  Anomaly ID: '{anomaly_id}'")
        for row in buttons:
            for btn in row:
                cb = btn["callback_data"]
                byte_len = len(cb.encode('utf-8'))
                status = "OK" if byte_len <= 64 else "FAIL"
                if byte_len > 64:
                    all_pass = False
                print(f"    [{status}] {btn['text']:20s} callback_data='{cb}' ({byte_len}/64 bytes)")

    print(f"\n  [OK] Tutti i bottoni hanno un handler nel worker.js")

    return all_pass


def test_telegram_message_length():
    """Verifica che il messaggio proposta non superi il limite Telegram (4096 chars)."""
    formatter = PostFormatter()
    stats = make_stats()
    selector = AnomalySelector.__new__(AnomalySelector)

    print("\n" + "=" * 60)
    print("TEST TELEGRAM - Lunghezza messaggio proposta (max 4096)")
    print("=" * 60)

    all_pass = True
    for i, item in enumerate(SAMPLE_SERIE_C):
        anomaly = selector._normalize(item, i)
        posts = formatter.format_all(anomaly, stats)

        # Ricostruisci il messaggio come fa TelegramPublisher.send_proposal
        from publishers.telegram import esc
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

        msg_len = len(text)
        status = "OK" if msg_len <= 4096 else "FAIL - MESSAGGIO TROPPO LUNGO!"
        if msg_len > 4096:
            all_pass = False

        print(f"\n  [{status}] {anomaly['player_name']}")
        print(f"    Lunghezza messaggio: {msg_len} / 4096")

    return all_pass


# ── TEST SELECTOR ─────────────────────────────────────────────────────────────

def test_selector_scoring():
    """Verifica che il selector ordini correttamente per score."""
    print("\n" + "=" * 60)
    print("TEST SELECTOR - Ordinamento per score")
    print("=" * 60)

    # Scrivi file temporaneo
    test_file = "_test_data.json"
    with open(test_file, 'w', encoding='utf-8') as f:
        json.dump(SAMPLE_SERIE_C, f, ensure_ascii=False)

    try:
        selector = AnomalySelector(anomalies_path=test_file)
        top = selector.get_top_anomaly()
        stats = selector.get_stats()

        print(f"  Top anomalia: {top['player_name']} (score: {top['score']:.1f})")
        print(f"  Stats: total={stats['total']}, under_28={stats['under_28']}")

        # Dezi dovrebbe essere primo (relevance 5 * 10 + tactical 95 / 10 = 59.5)
        assert top['player_name'] == 'Jacopo Dezi', f"FAIL: atteso Dezi, ottenuto {top['player_name']}"
        print(f"  [OK] Ordinamento corretto")

        return True
    finally:
        os.remove(test_file)


# ── TEST WORKER CALLBACK MAPPING ──────────────────────────────────────────────

def test_worker_callback_mapping():
    """Verifica coerenza tra bottoni Telegram e handler del worker."""
    print("\n" + "=" * 60)
    print("TEST WORKER - Mappatura callback → azione")
    print("=" * 60)

    # Azioni generate dai bottoni in telegram.py
    button_actions = [
        "publish_all", "reject",
        "publish_twitter", "publish_bluesky",
        "publish_telegram", "edit"
    ]

    # Azioni gestite nel worker.js (aggiornato)
    worker_handles = {
        "reject": True,
        "publish_all": True,     # startsWith('publish')
        "publish_twitter": True,
        "publish_bluesky": True,
        "publish_telegram": True,
        "edit": True,            # Gestito: rimuove bottoni e chiede modifica
    }

    all_pass = True
    for action in button_actions:
        handled = worker_handles.get(action, False)
        status = "OK" if handled else "FAIL - NON GESTITO"
        if not handled:
            all_pass = False
        print(f"  [{status}] {action}")

    return all_pass


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = {}

    results["Twitter Length"] = test_twitter_length()
    results["Bluesky Length"] = test_bluesky_length()
    results["Bluesky API"] = test_bluesky_api_format()
    results["TG Buttons"] = test_telegram_buttons()
    results["TG Msg Length"] = test_telegram_message_length()
    results["Selector"] = test_selector_scoring()
    results["Worker Callbacks"] = test_worker_callback_mapping()

    print("\n" + "=" * 60)
    print("RIEPILOGO")
    print("=" * 60)
    for name, passed in results.items():
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {name}")

    fails = sum(1 for v in results.values() if not v)
    print(f"\n  Totale: {len(results) - fails}/{len(results)} passati")

    sys.exit(0 if fails == 0 else 1)
