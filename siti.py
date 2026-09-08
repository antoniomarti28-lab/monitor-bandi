#!/usr/bin/env python3
"""
Siti aggiunti a mano e testo completo dei bandi - Fase 4.

Due lavori:
  1. i siti che NON hanno un feed: si apre la pagina degli elenchi, si guardano i
     collegamenti e si aprono quelli che sembrano bandi (anche PDF);
  2. l'approfondimento: per i bandi gia' in archivio si apre la pagina vera e si
     tiene il testo completo. Serve alla Fase 5, che dara' quel testo a Groq.

Si usa cosi':
  python siti.py                  controlla i siti aggiunti a mano
  python siti.py --approfondisci  scarica il testo completo dei bandi che ne sono privi
"""
import hashlib
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import estrattore
from raccogli import UA, estrai_importo

BASE = Path(__file__).parent
DB = BASE / "dati.db"
PAUSA = 2.0
MAX_NUOVI_PER_SITO = 15
MAX_APPROFONDIMENTI = 40

SCHEMA = """
CREATE TABLE IF NOT EXISTS siti (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  nome        TEXT,
  url         TEXT UNIQUE,
  ente        TEXT,
  attivo      INTEGER DEFAULT 1,
  aggiunto_il TEXT,
  ultimo_giro TEXT,
  esito       TEXT,
  trovati     INTEGER DEFAULT 0
);
"""


def migra(db):
    """Aggiunge quello che manca al database senza toccare i dati gia' dentro."""
    db.executescript(SCHEMA)
    colonne = {r[1] for r in db.execute("PRAGMA table_info(bandi)")}
    if "testo" not in colonne:
        db.execute("ALTER TABLE bandi ADD COLUMN testo TEXT")
    if "nota" not in colonne:
        db.execute("ALTER TABLE bandi ADD COLUMN nota TEXT")
    db.commit()


def robots_permette(url):
    try:
        p = urlparse(url)
        rp = RobotFileParser()
        rp.set_url(p.scheme + "://" + p.netloc + "/robots.txt")
        rp.read()
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def _ident(link):
    return hashlib.sha1(link.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- siti a mano

def controlla_sito(db, sito):
    """Apre la pagina degli elenchi e segue i collegamenti che sembrano bandi."""
    url = sito["url"]
    if not robots_permette(url):
        return "vietato da robots.txt", 0

    pagina = estrattore.leggi(url)
    if pagina["nota"] and not pagina["testo"]:
        return pagina["nota"], 0

    candidati = estrattore.link_interessanti(pagina["link"], url)
    if not candidati:
        return "nessun collegamento a bandi trovato in questa pagina", 0

    oggi = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nuovi = 0
    for link, testo_link in candidati:
        if nuovi >= MAX_NUOVI_PER_SITO:
            break
        ident = _ident(link)
        if db.execute("SELECT 1 FROM bandi WHERE id=?", (ident,)).fetchone():
            continue

        time.sleep(PAUSA)
        doc = estrattore.leggi_con_allegati(link)
        if not doc["testo"]:
            continue

        titolo = (doc["titolo"] or testo_link).strip()[:300]
        # Il titolo del PDF e' spesso una riga di intestazione: meglio il testo del link.
        if doc["tipo"] == "pdf" and len(testo_link) > 20:
            titolo = testo_link[:300]
        sommario = doc["testo"][:900]
        importo, importo_num = estrai_importo(doc["testo"][:6000])

        db.execute(
            "INSERT OR IGNORE INTO bandi (id,titolo,link,ente,fonte,pubblicato,scadenza,"
            "importo,importo_num,sommario,testo,nota,trovato_il) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, titolo, link, sito["ente"] or "", sito["nome"], None, None,
             importo, importo_num, sommario, doc["testo"], doc["nota"], oggi))
        nuovi += 1

    db.commit()
    return "ok", nuovi


def giro_siti(db):
    migra(db)
    db.row_factory = sqlite3.Row
    elenco = [dict(r) for r in db.execute("SELECT * FROM siti WHERE attivo=1 ORDER BY id")]
    if not elenco:
        print("Nessun sito aggiunto a mano. Si aggiungono dalla pagina, in fondo.")
        return 0

    totale = 0
    for s in elenco:
        esito, nuovi = controlla_sito(db, s)
        totale += nuovi
        db.execute("UPDATE siti SET ultimo_giro=?, esito=?, trovati=? WHERE id=?",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    esito, nuovi, s["id"]))
        db.commit()
        segno = "OK" if esito == "ok" else "--"
        print("  %s %-30s %3d nuovi   %s" % (segno, s["nome"][:30], nuovi,
                                             "" if esito == "ok" else esito))
        time.sleep(PAUSA)
    return totale


# ---------------------------------------------------------------- testo completo

def approfondisci(db, limite=MAX_APPROFONDIMENTI):
    """Scarica la pagina vera dei bandi che hanno solo il riassunto del feed.

    NON si estrae la scadenza da qui: provato su pagine vere, le date trovate erano
    quelle del menu del sito, sbagliate 2 volte su 2. La scadenza la ricava la Fase 5
    leggendo il testo con Groq. Qui si prende l'importo, che invece funziona.
    """
    migra(db)
    db.row_factory = sqlite3.Row
    righe = [dict(r) for r in db.execute(
        "SELECT id,link,titolo,importo FROM bandi "
        "WHERE (testo IS NULL OR testo='') AND archiviato=0 "
        "ORDER BY trovato_il DESC LIMIT ?", (limite,))]
    if not righe:
        print("Tutti i bandi hanno gia' il testo completo.")
        return 0

    fatti = 0
    for b in righe:
        if not robots_permette(b["link"]):
            db.execute("UPDATE bandi SET nota=? WHERE id=?", ("vietato da robots.txt", b["id"]))
            continue
        doc = estrattore.leggi_con_allegati(b["link"])
        if doc["testo"]:
            importo, importo_num = (estrai_importo(doc["testo"][:6000])
                                    if not b["importo"] else (b["importo"], None))
            db.execute("UPDATE bandi SET testo=?, nota=?, importo=COALESCE(?,importo), "
                       "importo_num=COALESCE(?,importo_num) WHERE id=?",
                       (doc["testo"], doc["nota"], importo, importo_num, b["id"]))
            fatti += 1
        else:
            db.execute("UPDATE bandi SET nota=? WHERE id=?", (doc["nota"], b["id"]))
        db.commit()
        time.sleep(PAUSA)

    print("Testo completo scaricato per %d bandi su %d." % (fatti, len(righe)))
    return fatti


if __name__ == "__main__":
    con = sqlite3.connect(DB)
    if "--approfondisci" in sys.argv:
        approfondisci(con)
    else:
        print("Giro sui siti aggiunti a mano\n")
        print("\nBandi nuovi trovati: %d" % giro_siti(con))
    con.close()
