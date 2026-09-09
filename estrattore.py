#!/usr/bin/env python3
"""
Lettura di pagine web e PDF - Fase 4.

Prende un indirizzo e restituisce il testo pulito, il titolo e i collegamenti trovati.
Serve per i siti che non hanno un feed e per i bandi pubblicati come PDF.

Unica libreria esterna di tutto il progetto: pypdf (solo per i PDF).
"""
import io
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

UA = "MonitorBandi/0.1 (monitoraggio bandi pubblici, uso personale)"
TIMEOUT = 40
MAX_TESTO = 60000        # oltre non serve: i bandi veri stanno abbondantemente dentro
MAX_SCARICO = 12_000_000  # 12 MB: oltre e' quasi sempre un allegato che non ci interessa

# Parti della pagina che non contengono mai il bando: menu, piè di pagina, banner.
IGNORA = {"script", "style", "noscript", "nav", "header", "footer", "form",
          "aside", "svg", "iframe", "button"}

SPAZI = re.compile(r"[ \t\r\f\v]+")
RIGHE = re.compile(r"\n{3,}")


# Le immagini di anteprima: quelle che i siti dichiarano per la condivisione.
META_IMMAGINE = ("og:image", "twitter:image", "og:image:secure_url")


class LettoreHTML(HTMLParser):
    """Tiene il testo che conta e i collegamenti, buttando via menu e script."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.pezzi = []
        self.link = []
        self.titolo = ""
        self._salta = 0
        self._in_titolo = False
        self._href = None
        self._testo_link = []
        self.immagine = ""

    def handle_starttag(self, tag, attrs):
        if tag == "meta" and not self.immagine:
            a = dict(attrs)
            chiave = (a.get("property") or a.get("name") or "").lower()
            if chiave in META_IMMAGINE and a.get("content"):
                self.immagine = urljoin(self.base, a["content"])
        if tag in IGNORA:
            self._salta += 1
            return
        if tag == "title":
            self._in_titolo = True
        elif tag == "a" and not self._salta:
            self._href = dict(attrs).get("href")
            self._testo_link = []
        elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "br"):
            self.pezzi.append("\n")

    def handle_endtag(self, tag):
        if tag in IGNORA:
            self._salta = max(0, self._salta - 1)
        elif tag == "title":
            self._in_titolo = False
        elif tag == "a" and self._href is not None:
            testo = SPAZI.sub(" ", "".join(self._testo_link)).strip()
            if testo:
                self.link.append((urljoin(self.base, self._href), testo))
            self._href = None

    def handle_data(self, dati):
        if self._in_titolo:
            self.titolo += dati
        if self._salta:
            return
        self.pezzi.append(dati)
        if self._href is not None:
            self._testo_link.append(dati)

    def testo(self):
        t = SPAZI.sub(" ", "".join(self.pezzi))
        t = "\n".join(r.strip() for r in t.split("\n"))
        return RIGHE.sub("\n\n", t).strip()[:MAX_TESTO]


def _codifica(intestazioni, corpo):
    """La codifica dichiarata dal sito, non quella che ci fa comodo.

    Molte pagine della PA sono ancora in ISO-8859-1: dandole per UTF-8
    le lettere accentate diventano caratteri strani dentro i titoli.
    """
    tipo = intestazioni.get("Content-Type", "")
    m = re.search(r"charset=([\w\-]+)", tipo, re.I)
    if m:
        return m.group(1)
    m = re.search(rb'charset=["\']?([\w\-]+)', corpo[:4000], re.I)
    return m.group(1).decode("ascii", "ignore") if m else "utf-8"


def scarica(url):
    req = Request(url, headers={"User-Agent": UA,
                                "Accept": "text/html,application/pdf,*/*",
                                "Accept-Language": "it-IT,it;q=0.9"})
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read(MAX_SCARICO), dict(r.headers), r.geturl()


def testo_da_pdf(dati):
    """Legge il testo di un PDF. Se e' una scansione (immagini) esce vuoto: e' normale."""
    try:
        import logging
        from pypdf import PdfReader
        # Quasi tutti i PDF della PA sono malfatti e pypdf lo dice a ogni riga:
        # sono avvisi innocui che riempirebbero il registro del giro notturno.
        logging.getLogger("pypdf").setLevel(logging.ERROR)
    except ImportError:
        return "", "manca la libreria pypdf"
    try:
        lettore = PdfReader(io.BytesIO(dati))
        pagine = [(p.extract_text() or "") for p in lettore.pages[:40]]
        testo = RIGHE.sub("\n\n", "\n".join(pagine)).strip()[:MAX_TESTO]
        if len(testo) < 200:
            return testo, "PDF quasi vuoto: probabilmente e' una scansione da leggere a occhio"
        return testo, None
    except Exception as e:
        return "", "PDF illeggibile (%s)" % type(e).__name__


def leggi(url):
    """Restituisce {tipo, titolo, testo, link, nota}. Non solleva eccezioni di rete: le riporta."""
    try:
        corpo, intestazioni, url_finale = scarica(url)
    except Exception as e:
        codice = getattr(e, "code", None)
        if codice == 403:
            nota = "il sito blocca i programmi automatici (403)"
        elif codice:
            nota = "errore HTTP %s" % codice
        else:
            nota = "irraggiungibile (%s)" % type(e).__name__
        return {"tipo": None, "titolo": "", "testo": "", "link": [],
                "immagine": "", "nota": nota}

    tipo = intestazioni.get("Content-Type", "").lower()
    if "pdf" in tipo or url_finale.lower().endswith(".pdf"):
        testo, nota = testo_da_pdf(corpo)
        titolo = ""
        for riga in testo.split("\n"):
            if len(riga.strip()) > 15:
                titolo = riga.strip()[:200]
                break
        return {"tipo": "pdf", "titolo": titolo, "testo": testo, "link": [],
                "immagine": "", "nota": nota}

    testo_html = corpo.decode(_codifica(intestazioni, corpo), "replace")
    lettore = LettoreHTML(url_finale)
    try:
        lettore.feed(testo_html)
    except Exception:
        pass  # HTML malfatto: teniamo quello che siamo riusciti a leggere
    return {"tipo": "html", "titolo": unescape(lettore.titolo).strip()[:300],
            "testo": lettore.testo(), "link": lettore.link,
            "immagine": lettore.immagine, "nota": None}


# ---------------------------------------------------------------- scelta dei link

PAROLE_BANDO = ["bando", "avviso", "contribut", "concors", "finanziam", "call",
                "premio", "candidatur", "selezione", "sovvenzion", "voucher",
                "manifestazione-di-interesse", "manifestazione di interesse"]

SCARTA = ["facebook.", "twitter.", "x.com", "instagram.", "linkedin.", "youtube.",
          "whatsapp.", "mailto:", "javascript:", "/privacy", "/cookie", "/accessibilit",
          "/login", "/area-riservata", "?share=", "/feed", "/tag/", "/category/"]


# Nomi che tradiscono il PDF con il testo integrale del bando, non un allegato qualsiasi.
PDF_DEL_BANDO = ["bando", "avviso", "regolamento", "testo", "guidelines", "call",
                 "disciplinare", "invito"]


def pdf_del_bando(link, massimo=2):
    """Sceglie i PDF che contengono il bando vero.

    Serve perche' la pagina web riassume, ma il contributo massimo per singolo
    richiedente e i requisiti dettagliati stanno quasi sempre solo nel PDF.
    """
    trovati, visti = [], set()
    for href, testo in link:
        if not href.lower().split("?")[0].endswith(".pdf") or href in visti:
            continue
        visti.add(href)
        spia = (href + " " + testo).lower()
        if any(p in spia for p in PDF_DEL_BANDO):
            trovati.append(href)
        if len(trovati) >= massimo:
            break
    return trovati


def leggi_con_allegati(url):
    """Legge la pagina e, se rimanda al PDF del bando, ci attacca anche quello."""
    doc = leggi(url)
    if doc["tipo"] != "html" or not doc["testo"]:
        return doc
    allegati = []
    for pdf in pdf_del_bando(doc["link"]):
        letto = leggi(pdf)
        if letto["testo"]:
            allegati.append(letto["testo"])

    if allegati:
        # Il PDF va PRIMA e la pagina si taglia corta: molte pagine «Bandi» sono
        # archivi che elencano anche le edizioni vecchie, e il modello finiva per
        # prendere da li' la scadenza sbagliata. Il documento allegato fa fede.
        doc["nota"] = doc["nota"] or "letto anche il PDF del bando"
        doc["testo"] = ("\n\n".join(allegati)
                        + "\n\n--- dalla pagina web ---\n\n" + doc["testo"][:1500])[:MAX_TESTO]
    return doc


def link_interessanti(link, url_base, massimo=25):
    """Sceglie i collegamenti che sembrano portare a un bando, non al menu del sito."""
    dominio = urlparse(url_base).netloc
    visti, buoni, ripiego = set(), [], []

    for href, testo in link:
        pulito = href.split("#")[0].rstrip("/")
        if not pulito.startswith("http") or pulito in visti:
            continue
        if any(s in pulito.lower() for s in SCARTA):
            continue
        stesso_sito = urlparse(pulito).netloc == dominio
        pdf = pulito.lower().endswith(".pdf")
        if not stesso_sito and not pdf:
            continue
        visti.add(pulito)

        spia = (pulito + " " + testo).lower()
        if any(p in spia for p in PAROLE_BANDO):
            buoni.append((pulito, testo))
        elif len(testo) >= 30:
            ripiego.append((pulito, testo))

    # Se il sito non usa mai la parola «bando», ripieghiamo sui titoli lunghi.
    scelti = buoni if buoni else ripiego
    return scelti[:massimo]
