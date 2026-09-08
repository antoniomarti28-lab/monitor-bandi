#!/usr/bin/env python3
"""
Raccoglitore bandi - Fase 1.
Legge le fonti elencate in fonti.json e salva i bandi nuovi in dati.db.
Usa SOLO la libreria standard di Python: non c'e' niente da installare.
"""
import hashlib
import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

BASE = Path(__file__).parent
DB = BASE / "dati.db"
FONTI = BASE / "fonti.json"

# Ci presentiamo con un nome onesto e andiamo piano: una richiesta ogni 2 secondi.
UA = "MonitorBandi/0.1 (monitoraggio bandi pubblici, uso personale)"
PAUSA = 2.0
TIMEOUT = 30

# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS bandi (
  id            TEXT PRIMARY KEY,
  titolo        TEXT NOT NULL,
  link          TEXT NOT NULL,
  ente          TEXT,
  fonte         TEXT,
  pubblicato    TEXT,
  scadenza      TEXT,
  importo       TEXT,
  importo_num   REAL,
  sommario      TEXT,
  trovato_il    TEXT,
  archiviato    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_trovato  ON bandi(trovato_il);
CREATE INDEX IF NOT EXISTS idx_scadenza ON bandi(scadenza);

CREATE TABLE IF NOT EXISTS fonti_stato (
  nome        TEXT PRIMARY KEY,
  ultimo_giro TEXT,
  esito       TEXT,
  voci        INTEGER DEFAULT 0,
  nuovi       INTEGER DEFAULT 0
);
"""


def apri_db():
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    return c


# ---------------------------------------------------------------- rete

def robots_permette(url):
    """Chiede al sito se gradisce essere letto da un programma. Se non risponde, assumiamo di si."""
    try:
        p = urlparse(url)
        rp = RobotFileParser()
        rp.set_url(p.scheme + "://" + p.netloc + "/robots.txt")
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def scarica(url):
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


# ---------------------------------------------------------------- lettura feed

NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/"}


def _testo(el):
    return "".join(el.itertext()).strip() if el is not None else ""


def leggi_feed(xml_bytes):
    """Legge sia RSS che Atom e restituisce voci uniformi."""
    # Alcuni siti (es. Regione Calabria) mandano uno spazio o un BOM prima dell'XML:
    # basta a mandare in errore il lettore, quindi lo togliamo.
    radice = ET.fromstring(xml_bytes.lstrip(b"\xef\xbb\xbf \t\r\n"))
    voci = []

    for item in radice.iter("item"):          # RSS 2.0
        voci.append({
            "titolo": _testo(item.find("title")),
            "link": _testo(item.find("link")),
            "sommario": _testo(item.find("description")),
            "pubblicato": _testo(item.find("pubDate")) or _testo(item.find("dc:date", NS)),
        })

    if not voci:                              # Atom
        for entry in radice.iter("{http://www.w3.org/2005/Atom}entry"):
            link_el = entry.find("atom:link", NS)
            voci.append({
                "titolo": _testo(entry.find("atom:title", NS)),
                "link": link_el.get("href") if link_el is not None else "",
                "sommario": (_testo(entry.find("atom:summary", NS))
                             or _testo(entry.find("atom:content", NS))),
                "pubblicato": (_testo(entry.find("atom:updated", NS))
                               or _testo(entry.find("atom:published", NS))),
            })
    return voci


TAG = re.compile(r"<[^>]+>")
SPAZI = re.compile(r"\s+")


def pulisci(testo):
    """Toglie i tag e riporta i codici HTML alle lettere vere.

    Serve html.unescape e non una lista scritta a mano: i feed usano decine di
    codici diversi (&#x27; &#8217; &#039;...) e uno solo dimenticato finisce
    dritto nel titolo del messaggio Telegram.
    """
    t = TAG.sub(" ", testo or "")
    t = unescape(unescape(t))  # due volte: alcuni feed li codificano due volte
    return SPAZI.sub(" ", t).strip()


# ---------------------------------------------------------------- estrazione dati

MESI = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
        "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}

RE_SCADENZA = re.compile(
    r"(?:scadenz\w*|entro(?:\s+e\s+non\s+oltre)?(?:\s+il)?|termine\w*|chiusur\w*|"
    r"presentazione.{0,25}?domand\w*).{0,60}?"
    r"(\d{1,2})[\s/\-.]+(\d{1,2}|" + "|".join(MESI) + r")[\s/\-.]+(\d{4})",
    re.IGNORECASE | re.DOTALL)

RE_IMPORTO = re.compile(
    r"(?:€|euro|eur)\s*"
    r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?|\d+(?:[.,]\d+)?\s*(?:mila|milion\w+|mln|mio))"
    r"|(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?|\d+(?:[.,]\d+)?\s*(?:mila|milion\w+|mln|mio))\s*(?:€|euro\b)",
    re.IGNORECASE)


def estrai_scadenza(testo):
    m = RE_SCADENZA.search(testo or "")
    if not m:
        return None
    giorno, mese, anno = m.group(1), m.group(2).lower(), m.group(3)
    n_mese = MESI.get(mese)
    if n_mese is None:
        try:
            n_mese = int(mese)
        except ValueError:
            return None
    try:
        return datetime(int(anno), n_mese, int(giorno)).date().isoformat()
    except ValueError:
        return None


def estrai_importo(testo):
    m = RE_IMPORTO.search(testo or "")
    if not m:
        return None, None
    grezzo = (m.group(1) or m.group(2) or "").strip().lower()
    molt = 1
    if "milion" in grezzo or "mln" in grezzo or "mio" in grezzo:
        molt = 1_000_000
    elif "mila" in grezzo:
        molt = 1_000
    numero = re.sub(r"[^\d,.]", "", grezzo)
    if molt == 1:
        numero = numero.replace(".", "").replace(",", ".")
    else:
        numero = numero.replace(",", ".")
    try:
        valore = float(numero) * molt
    except ValueError:
        valore = None
    return m.group(0).strip(), valore


def normalizza_data(grezza):
    """Le date dei feed arrivano in molti formati: proviamo i piu' comuni."""
    if not grezza:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(grezza.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- giro principale

def giro():
    cfg = json.loads(FONTI.read_text(encoding="utf-8"))
    db = apri_db()
    oggi = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = []

    for f in cfg["fonti"]:
        if not f.get("attiva", True):
            continue
        nome, url = f["nome"], f["url"]
        esito, voci, nuovi = "ok", 0, 0
        try:
            if not robots_permette(url):
                esito = "vietato da robots.txt"
            else:
                elenco = leggi_feed(scarica(url))
                voci = len(elenco)
                for v in elenco:
                    link = (v["link"] or "").strip()
                    titolo = pulisci(v["titolo"])
                    if not link or not titolo:
                        continue
                    ident = hashlib.sha1(link.encode("utf-8")).hexdigest()
                    sommario = pulisci(v["sommario"])[:2000]
                    testo = titolo + ". " + sommario
                    importo, importo_num = estrai_importo(testo)
                    cur = db.execute(
                        "INSERT OR IGNORE INTO bandi "
                        "(id,titolo,link,ente,fonte,pubblicato,scadenza,importo,importo_num,sommario,trovato_il) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (ident, titolo, link, f.get("ente", ""), nome,
                         normalizza_data(v["pubblicato"]), estrai_scadenza(testo),
                         importo, importo_num, sommario, oggi))
                    nuovi += cur.rowcount
                if voci == 0:
                    esito = "nessuna voce (formato non riconosciuto)"
        except HTTPError as e:
            esito = "errore HTTP " + str(e.code)
        except URLError as e:
            esito = "irraggiungibile (" + str(e.reason) + ")"
        except ET.ParseError:
            esito = "non e' un feed valido"
        except Exception as e:
            esito = "errore: " + type(e).__name__

        db.execute(
            "INSERT INTO fonti_stato (nome,ultimo_giro,esito,voci,nuovi) VALUES (?,?,?,?,?) "
            "ON CONFLICT(nome) DO UPDATE SET ultimo_giro=?,esito=?,voci=?,nuovi=?",
            (nome, oggi, esito, voci, nuovi, oggi, esito, voci, nuovi))
        db.commit()
        report.append((nome, esito, voci, nuovi))
        segno = "OK" if esito == "ok" else "--"
        nota = "" if esito == "ok" else esito
        print("  %s %-26s %4d voci  %4d nuovi   %s" % (segno, nome[:26], voci, nuovi, nota))
        time.sleep(PAUSA)

    tot = db.execute("SELECT COUNT(*) FROM bandi").fetchone()[0]
    ok = sum(1 for r in report if r[1] == "ok")
    print("\nFonti funzionanti: %d/%d   Nuovi in questo giro: %d   Totale in archivio: %d"
          % (ok, len(report), sum(r[3] for r in report), tot))

    # Poi i siti aggiunti a mano, che non hanno un feed.
    import siti
    print()
    siti.giro_siti(db)
    # ...e il testo completo dei bandi che hanno solo il riassunto: e' quello che
    # la Fase 5 dara' da leggere a Groq.
    siti.approfondisci(db)

    # Il modello legge i bandi nuovi: scadenza, requisiti, riassunto, aperto o chiuso.
    import intelligenza
    print()
    intelligenza.leggi_bandi(db)

    # Con quelle informazioni il punteggio diventa molto piu' preciso.
    import profili
    print("\nBandi compatibili con i tuoi profili: %d" % profili.riabbina(db))

    # Solo sui pochi promossi, il modello grande dice se puoi davvero parteciparci.
    print()
    intelligenza.giudica_finalisti(db)
    db.close()

    # ...e avvisiamo su Telegram solo i bandi nuovi mai segnalati prima.
    import avvisi
    print()
    avvisi.invia()


if __name__ == "__main__":
    print("Giro di raccolta bandi\n")
    giro()
