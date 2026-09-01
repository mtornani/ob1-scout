#!/usr/bin/env python3
"""
OB1 v2 — Monitor fonti (Fase B2)

Discovery SOURCE-FIRST: invece di cercare giocatori a caso, si monitorano le
fonti curate del registro (config/sources.json), si scoprono gli articoli NUOVI
(delta), e solo quelli finiscono all'estrattore. La ricerca generica resta
declassata a scoperta di fonti nuove, non lavoro quotidiano.

discover_item_urls() è codice puro e testabile senza rete.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

CONFIG = Path(__file__).parent.parent / "config" / "sources.json"

# Link plausibilmente "articolo/profilo" (hanno un path con slug lungo o data),
# non home/tag/social.
_ARTICLE_HINT = re.compile(r"/(\d{4}|noticias?|news|jugador|player|spieler|"
                           r"giocatore|profil|notizie|artic|story|match)", re.I)
_SKIP_HINT = re.compile(r"(facebook|twitter|instagram|youtube|tiktok|whatsapp|"
                        r"linkedin|/tag/|/category/|mailto:|"
                        r"//api\.|/images?/|/img/|/assets/|/static/|/uploads/|"
                        r"/portaldeclubes|/socios|/tienda|/mayores|/senior|"
                        # /wp-json/ è l'indice stesso (letto da parse_index),
                        # non un articolo: le risposte REST di WordPress
                        # contengono i propri link _self/_links e i link di
                        # paginazione, che altrimenti passerebbero il
                        # controllo "ha uno slug" ed entrerebbero come se
                        # fossero contenuto (osservato su fcf.com.co e
                        # the-aiff.com, 26 ago 2026).
                        r"/wp-json/|/feed/?(\?|$|#)|sitemap[-.]|"
                        # Misurando l'impronta delle 83 fonti (26 ago 2026,
                        # scripts/impronta_fonti.py) sono usciti altri due
                        # generi di falso positivo: pagine di login
                        # (tap.info.tn, kickoff.com — un indice a volte
                        # elenca anche "accedi/registrati" fra i suoi link)
                        # e la home page nuda sotto un percorso non-radice
                        # (the-afc.com/en/home.html: _has_article_path la
                        # lasciava passare perché lunga 12 caratteri).
                        r"/(login|sign_in|signin|register)(/|\?|$)|/home\.html?(\?|$)|"
                        r"\.(jpg|jpeg|png|gif|webp|svg|pdf|css|js|ico)(\?|$))", re.I)


def load_registry(path: Path = CONFIG, only_active: bool = True) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    src = data.get("sources", [])
    return [s for s in src if s.get("active")] if only_active else src


def _domain(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _same_domain(url: str, base_dom: str) -> bool:
    """True se url è sul dominio base o un suo sottodominio (no falsi positivi
    tipo fif.ci.attacker.com per base fif.ci)."""
    d = _domain(url)
    return bool(base_dom) and (d == base_dom or d.endswith("." + base_dom))


def _has_article_path(url: str) -> bool:
    """Un articolo/profilo ha uno slug, non è la root. Path corti ammessi solo
    se hanno cifre (ID/data) o sotto-segmenti — così non si scartano URL validi
    tipo /news/12345 pur bocciando homepage e sezioni-radice."""
    path = urlparse(url).path.strip("/")
    if not path:
        return False
    if len(path) >= 8:
        return True
    return any(ch.isdigit() for ch in path) or "/" in path


def discover_item_urls(markdown: str, source_url: str = "", max_items: int = 25) -> list:
    """
    Estrae dai contenuti di una pagina-fonte (markdown di Jina Reader) i link
    plausibili ad articoli/profili. Preferisce lo stesso dominio della fonte.
    Codice puro: testabile passando testo, senza rete.
    """
    if not markdown:
        return []
    base_dom = _domain(source_url)
    urls = re.findall(r"\((https?://[^)\s]+)\)", markdown)      # link markdown
    # Link nudi: ferma anche su virgolette/parentesi graffe/angolari, non
    # solo su spazio e ')'. Senza, un URL dentro JSON minificato senza
    # spazi ("link":"https://x/y","id":2,...) si mangia tutto il resto
    # della stringa fino al primo spazio vero — che in un JSON compatto
    # può non esserci affatto. Vedi parse_index(), che alimenta questa
    # funzione con l'indice REST di un sito, non solo markdown.
    urls += re.findall(r'(?<![(\w])(https?://[^\s)"\'<>{}\\]+)', markdown)

    seen, out = set(), []
    for u in urls:
        u = u.rstrip(".,);]")
        if u in seen or _SKIP_HINT.search(u):
            continue
        seen.add(u)
        if not _has_article_path(u):   # scarta homepage / root di sezione
            continue
        if _same_domain(u, base_dom) or _ARTICLE_HINT.search(u):
            out.append(u)
        if len(out) >= max_items:
            break
    return out


# Termini "giovanili" per lingua, per la ricerca ristretta al dominio.
# ar/id/vi/th/uz aggiunti per l'algoritmo copertura bassa (2026-08-19c):
# senza, i domini registrati in quelle lingue (filgoal.com, pssi.org,
# vff.org.vn, fathailand.org, ufa.uz...) usavano di default i termini
# inglesi (YOUTH_TERMS["en"]) nella query "site:{dominio} {termini}" — su un
# sito in lingua locale la discovery restava vuota o quasi per costruzione,
# non per assenza di contenuto. th verificato: "เยาวชน U17 U20 ฟุตบอล
# อะคาเดมี ดาวรุ่ง" trova davvero notizie di convocazioni giovanili reali su
# fathailand.org (fonte già registrata). ar verificato in modo simile. id/vi
# verificati meno a fondo (risultati plausibili ma non su un dominio
# registrato specifico); uz non verificato per mancanza di risultati utili
# in sessione — sono il prossimo da ricontrollare se la discovery in
# Uzbekistan resta scarsa.
YOUTH_TERMS = {
    "es": "juvenil sub-20 sub-17 cantera promesa",
    "pt": "juvenil sub-20 sub-17 base revelação",
    "en": "youth U20 U17 academy prospect",
    "fr": "jeune U20 U17 espoir formation",
    "sr": "omladinac U19 U17 talenat",
    "hr": "mladi U19 U17 talent",
    "ar": "شباب تحت 20 تحت 17 أكاديمية موهبة",
    "id": "muda U20 U17 akademi talenta",
    "vi": "trẻ U20 U17 học viện tài năng",
    "th": "เยาวชน U20 U17 อะคาเดมี ดาวรุ่ง",
    "uz": "yoshlar U20 U17 akademiya iste'dod",
}


# Percorsi che un sito pubblica DA SOLO, senza che nessuno glielo chieda —
# l'elenco dei propri contenuti recenti, in una forma leggibile a macchina.
# Ordine di tentativo: il più strutturato/fresco prima. Misurato il 26 ago
# 2026 su fcf.com.co (la fonte più produttiva del registro, 180 evidenze,
# ma FERMA dal 5 agosto): /wp-json risponde con i 10 articoli di oggi e
# ieri, comunicati e convocazioni compresi — lo stesso identico contenuto
# che la ricerca (site:fcf.com.co ... 2026) restituiva 0 risultati per
# TUTTE le fonti provate, non solo per quella spenta. Vedi _da_ricerca.
INDEX_PATHS = (
    "/wp-json/wp/v2/posts?per_page=20",  # WordPress REST — il più fresco
    "/feed/",                            # RSS/Atom, quasi universale
    "/sitemap.xml",
    "/wp-sitemap.xml",
)

# Il JSON di WordPress scrive gli slash con l'escape \/ dentro le stringhe
# ("https:\/\/fcf.com.co\/2026\/08\/26\/..."): senza normalizzarlo, la
# ricerca di URL nel testo si fermerebbe al primo backslash.
_ESCAPED_SLASH = re.compile(r"\\/")

# Jina Reader risponde SEMPRE 200 dalla sua infrastruttura, anche quando il
# sito di destinazione ha risposto 404/403/500 — l'errore vero finisce scritto
# dentro il testo ("Warning: Target URL returned error 404: Not Found"), non
# nello status HTTP che read_raw() controlla. Trovato su the-aiff.com: il
# percorso /wp-json non esiste (404), ma la pagina di errore del sito porta
# comunque il menu di navigazione completo — che senza questo controllo
# veniva letto come se fossero 68 articoli nuovi (register, executive-
# committees, disciplinary-committee...). Un menu non cambia da un run
# all'altro: non è "contenuto nuovo", è la cornice della pagina.
_ERRORE_BERSAGLIO = re.compile(r"Warning: Target URL returned error \d")


def parse_index(text: str, base_dom: str, max_items: int = 25) -> list:
    """
    URL di articoli dentro un indice di sito — WordPress REST JSON,
    RSS/Atom, sitemap XML: qualunque formato, perché tutti scrivono URL
    letterali nel testo e discover_item_urls() li sa già riconoscere e
    filtrare (niente tag/categoria/asset, solo path con uno slug vero).

    Puro: nessuna rete, testabile su un campione catturato.
    """
    if not text or _ERRORE_BERSAGLIO.search(text[:400]):
        return []
    pulito = _ESCAPED_SLASH.sub("/", text)
    return discover_item_urls(pulito, source_url=f"https://{base_dom}/",
                              max_items=max_items)


class SourceMonitor:
    """Scoperta articoli nuovi per una fonte (delta) + fetch via Jina."""

    JINA = "https://r.jina.ai/"

    def __init__(self, db, scraper=None):
        self.db = db            # OB1DatabaseV2 (per il delta seen_items)
        self.scraper = scraper  # AsyncGlobalScraper (search + deep_read), opzionale
        # Osservabilità (26 ago 2026): quante fonti sono state lette
        # dall'indice del sito e quante sono dovute passare dalla ricerca —
        # letti da ingest_v2.py a fine run, stesso posto di jina_failures.
        self.via_indice = 0
        self.via_ricerca = 0

    async def new_items(self, source: dict) -> list:
        """
        URL articolo NUOVI per questa fonte. Due strade, in ordine:

        1. L'indice che il sito pubblica da solo (_da_indice_sito) — un
           fetch diretto via Jina Reader, nessun motore di ricerca in
           mezzo. Funziona anche quando la ricerca è degradata o ferma,
           perché non dipende da lei.
        2. La ricerca ristretta al dominio (_da_ricerca), SOLO se la prima
           non ha trovato niente — siti senza un indice leggibile restano
           coperti come prima.

        Gli aggregatori (tier secondary) non si spazzano in discovery, in
        nessuna delle due strade.
        """
        if self.scraper is None or source.get("tier") == "secondary":
            return []
        dom = _domain(source["url"])

        found = await self._da_indice_sito(source, dom)
        if found:
            self.via_indice += 1
        else:
            found = await self._da_ricerca(source, dom)
            if found:
                self.via_ricerca += 1

        return self.db.filter_new_items(source["id"], found)

    async def _da_indice_sito(self, source: dict, dom: str) -> list:
        """
        Prova ogni INDEX_PATHS finché uno risponde con articoli veri.

        `"indice": "self"` nel registro dice che l'indice È l'URL registrato,
        e che gli INDEX_PATHS non vanno nemmeno provati. Serve dove il sito
        pubblica un elenco leggibile ma non un feed: miseleccion.mx/noticia
        elenca 20 convocatorias vere, mentre /wp-json e /feed sullo stesso
        dominio restituiscono due link spazzatura (una pagina e una pubblicità
        Amazon) — abbastanza da sembrare un successo e fermare la ricerca lì.
        È dichiarato per fonte e non dedotto: provare l'URL registrato come
        indice per TUTTI farebbe regredire proprio la fonte che oggi produce
        la maggior parte delle nostre prove, perché la home della FCF elenca
        le voci del menu (/seleccion-mayores/, /mundial-2026/) mentre il suo
        /wp-json elenca le convocatorias. Un'euristica buona in media qui
        peggiorerebbe il caso che conta.
        """
        base = source["url"].rstrip("/")
        if source.get("indice") == "self":
            return parse_index(await self.scraper.read_raw(source["url"]), dom)
        for path in INDEX_PATHS:
            text = await self.scraper.read_raw(base + path)
            found = parse_index(text, dom)
            if found:
                return found
        return []

    async def _da_ricerca(self, source: dict, dom: str) -> list:
        """
        Ricerca ristretta al dominio (site:) con termini giovanili nella
        lingua della fonte — l'unica strada finché non c'era _da_indice_sito,
        ora il ripiego per i siti senza un indice leggibile (niente
        WordPress, niente feed, niente sitemap: verificato su afa.com.ar).
        """
        terms = YOUTH_TERMS.get(source.get("lang"), YOUTH_TERMS["en"])
        query = f"site:{dom} {terms} 2026"

        results = await self.scraper.search_query(query)
        found = []
        for r in results:
            u = (r.get("url") or "").rstrip(".,);]")
            if not u or _SKIP_HINT.search(u):
                continue
            if not _same_domain(u, dom):        # dominio esatto o sottodominio
                continue
            if not _has_article_path(u):        # scarta root/homepage
                continue
            found.append(u)
        return list(dict.fromkeys(found))


# --------------------------------------------------------------- test

def _test() -> None:
    # 1. WordPress REST — la forma reale di fcf.com.co (catturata il 26 ago
    #    2026 via /wp-json/wp/v2/posts?per_page=20). Gli slash sono escaped:
    #    la prova che _ESCAPED_SLASH funziona è che l'URL torni intero.
    wp_json = (
        '[{"id":1,"date":"2026-08-26T10:00:00",'
        '"link":"https:\\/\\/fcf.com.co\\/2026\\/08\\/26\\/'
        'convocatoria-de-la-seleccion-colombia-masculina-sub-17\\/"},'
        '{"id":2,"date":"2026-08-25T09:00:00",'
        '"link":"https:\\/\\/fcf.com.co\\/2026\\/08\\/25\\/'
        'microciclo-bogota-seleccion-colombia-femenina-sub20\\/"},'
        '{"id":3,"date":"2026-08-24T08:00:00",'
        '"link":"https:\\/\\/fcf.com.co\\/wp-content\\/uploads\\/2026\\/08\\/'
        'foto.jpg"}]'
    )
    trovati = parse_index(wp_json, "fcf.com.co")
    assert any("convocatoria-de-la-seleccion" in u for u in trovati)
    assert any("microciclo-bogota" in u for u in trovati)
    assert not any(u.endswith(".jpg") for u in trovati), \
        "un asset (/wp-content/uploads/.../foto.jpg) non e' un articolo"
    assert all("\\" not in u for u in trovati), "slash non normalizzati"

    # 2. RSS/Atom — formato quasi universale, nessun escape da normalizzare.
    rss = """<rss><channel>
      <item><link>https://example.org/2026/08/26/giovane-talento-sub17</link></item>
      <item><link>https://example.org/tag/calcio</link></item>
    </channel></rss>"""
    trovati = parse_index(rss, "example.org")
    assert any("giovane-talento-sub17" in u for u in trovati)
    assert not any("/tag/" in u for u in trovati)

    # 3. Sitemap XML.
    sitemap = """<urlset>
      <url><loc>https://example.org/notizie/promessa-2026-sub19</loc></url>
      <url><loc>https://example.org/</loc></url>
    </urlset>"""
    trovati = parse_index(sitemap, "example.org")
    assert any("promessa-2026-sub19" in u for u in trovati)
    assert "https://example.org/" not in trovati, "la home non e' un articolo"

    # 4. Pagina vuota o irraggiungibile: nessun candidato inventato.
    assert parse_index("", "example.org") == []
    assert parse_index("<html></html>", "example.org") == []

    # 5. Caso reale, the-aiff.com 26 ago 2026: /wp-json non esiste (404), ma
    #    Jina Reader risponde comunque 200 e porta la pagina di errore del
    #    sito — che ha un menu completo. Senza il controllo del testo, i
    #    link del menu (mai nuovi, sempre gli stessi) passavano come articoli.
    pagina_errore_con_menu = (
        "Title: \n\nURL Source: https://www.the-aiff.com/wp-json/wp/v2/posts\n\n"
        "Warning: Target URL returned error 404: Not Found\n\n"
        "Markdown Content:\n"
        "*   [General Body](https://www.the-aiff.com/general-body)\n"
        "*   [Executive Committee](https://www.the-aiff.com/executive-committees)\n"
    )
    assert parse_index(pagina_errore_con_menu, "the-aiff.com") == []

    # 6. Ma un vero 200 con un URL che CONTIENE per caso la parola "error"
    #    nello slug non deve essere scartato: il controllo guarda la firma
    #    esatta di Jina, non una parola sciolta nel testo.
    pagina_vera = (
        "Title: Un articolo\n\nURL Source: https://example.org/\n\n"
        "Markdown Content:\n"
        "[Come evitare gli error di formazione U17](https://example.org/2026/08/26/error-formazione-u17)\n"
    )
    assert parse_index(pagina_vera, "example.org") != []

    # 7. Misurando le 83 fonti (scripts/impronta_fonti.py, 26 ago 2026) sono
    #    usciti altri due generi di rumore: pagine di login/registrazione
    #    (tap.info.tn, kickoff.com) e un altro indice citato DENTRO
    #    l'indice — sitemap che rimanda a un altro file sitemap
    #    (itatiaia.com.br, righttodream.com) invece che a un articolo.
    rumore = """<rss><channel>
      <item><link>https://example.org/user/sign_in</link></item>
      <item><link>https://example.org/login/ar</link></item>
      <item><link>https://example.org/sitemap-news.xml</link></item>
      <item><link>https://example.org/en/home.html</link></item>
      <item><link>https://example.org/2026/08/26/vero-articolo-sub17</link></item>
    </channel></rss>"""
    trovati = parse_index(rumore, "example.org")
    assert trovati == ["https://example.org/2026/08/26/vero-articolo-sub17"], trovati

    # 8. `"indice": "self"` legge l'URL registrato e NON tocca gli
    #    INDEX_PATHS. Caso reale, miseleccion.mx il 31 ago 2026: /noticia
    #    elenca venti convocatorias vere, mentre /wp-json e /feed sullo stesso
    #    dominio rispondono con due link spazzatura — abbastanza da sembrare
    #    un indice riuscito e fermare la ricerca sul risultato peggiore.
    import asyncio

    class _ScraperFinto:
        def __init__(self):
            self.letti = []

        async def read_raw(self, url):
            self.letti.append(url)
            if url == "https://miseleccion.mx/noticia":
                return ("Markdown Content:\n"
                        "[CONVOCATORIA Sub-17](https://miseleccion.mx/noticia/"
                        "6597-CONVOCATORIA-Seleccion-Sub-17)\n")
            return ("Markdown Content:\n"
                    "[Somos](https://miseleccion.mx/somos-mexico)\n")

    class _DbFinto:
        def filter_new_items(self, source_id, keys):
            return keys

    finto = _ScraperFinto()
    mon = SourceMonitor(_DbFinto(), finto)
    fonte = {"id": "mx_fmf", "url": "https://miseleccion.mx/noticia",
             "indice": "self", "tier": "primary"}
    trovati = asyncio.run(mon.new_items(fonte))
    assert trovati == ["https://miseleccion.mx/noticia/"
                       "6597-CONVOCATORIA-Seleccion-Sub-17"], trovati
    assert finto.letti == ["https://miseleccion.mx/noticia"], finto.letti

    # 8b. Senza il campo, il comportamento di prima non cambia di una virgola:
    #     si provano gli INDEX_PATHS appesi all'URL, non l'URL stesso.
    finto2 = _ScraperFinto()
    mon2 = SourceMonitor(_DbFinto(), finto2)
    asyncio.run(mon2.new_items({"id": "x", "url": "https://miseleccion.mx/noticia",
                                "tier": "primary"}))
    assert "https://miseleccion.mx/noticia" not in finto2.letti, finto2.letti
    assert all(u.startswith("https://miseleccion.mx/noticia/")
               for u in finto2.letti), finto2.letti

    print("sources_v2: ok")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        _test()
    else:
        reg = load_registry()
        from collections import Counter
        by_region = Counter(s["region"] for s in reg)
        print(f"Fonti attive: {len(reg)}")
        for region, n in by_region.most_common():
            print(f"  {region:14s} {n}")
