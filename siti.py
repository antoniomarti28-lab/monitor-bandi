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
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import estrattore
# robots.txt e ritmo delle richieste si decidono in un posto solo: prima qui ce n'era
# una seconda copia, con lo stesso difetto della prima.
from raccogli import UA, estrai_importo, pausa_per, robots_permette

BASE = Path(__file__).parent
DB = BASE / "dati.db"
PAUSA = 2.0
MAX_NUOVI_PER_SITO = 15
MAX_APPROFONDIMENTI = 60   # come le letture del modello: senza testo non si legge

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
    if "immagine" not in colonne:
        db.execute("ALTER TABLE bandi ADD COLUMN immagine TEXT")
    db.commit()




def _ident(link):
    return hashlib.sha1(link.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- siti a mano

def controlla_sito(db, sito, opzioni=None):
    """Apre la pagina degli elenchi e segue i collegamenti che sembrano bandi.

    `opzioni` e' la voce della fonte in configurazione.json. Per i siti delle compagnie:
      "cerca": "audizioni"     si seguono solo i collegamenti su audizioni e lavoro, e
                               una home senza audizioni oggi e' normale, non un errore;
      "pagina_audizioni": true l'indirizzo E' la pagina audizioni: molte compagnie la
                               riscrivono invece di pubblicarne una nuova, quindi si
                               sorveglia il suo contenuto e ogni versione nuova diventa
                               un annuncio da leggere.
    """
    opzioni = opzioni or {}
    solo_audizioni = opzioni.get("cerca") == "audizioni"
    url = sito["url"]
    if not robots_permette(url):
        return "vietato da robots.txt", 0

    pagina = estrattore.leggi(url)
    if pagina["nota"] and not pagina["testo"]:
        return pagina["nota"], 0

    oggi = datetime.now(timezone.utc).isoformat(timespec="seconds")
    nuovi = 0
    if opzioni.get("pagina_audizioni"):
        nuovi += versione_nuova(db, sito, pagina, oggi)

    candidati = estrattore.link_interessanti(
        pagina["link"], url, parole=estrattore.PAROLE_AUDIZIONE if solo_audizioni else None)
    if not candidati:
        if solo_audizioni:
            db.commit()
            return "ok", nuovi
        return "nessun collegamento a bandi trovato in questa pagina", 0

    for link, testo_link in candidati:
        if nuovi >= MAX_NUOVI_PER_SITO:
            break
        # Gli archivi delle compagnie elencano anni di audizioni: il 28 set 2026, su 15
        # collegamenti di Aterballetto, 13 erano del 2020-2025. Se il collegamento cita
        # solo anni passati si salta, senza spendere una lettura del modello.
        anni = [int(a) for a in re.findall(r"20\d\d", link + " " + testo_link)]
        if solo_audizioni and anni and max(anni) < datetime.now().year:
            continue
        ident = _ident(link)
        if db.execute("SELECT 1 FROM bandi WHERE id=?", (ident,)).fetchone():
            continue

        time.sleep(pausa_per(link))
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
            "importo,importo_num,sommario,testo,nota,immagine,trovato_il) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, titolo, link, sito["ente"] or "", sito["nome"], None, None,
             importo, importo_num, sommario, doc["testo"], doc["nota"],
             doc.get("immagine") or None, oggi))
        nuovi += 1

    db.commit()
    return "ok", nuovi


def versione_nuova(db, sito, pagina, oggi):
    """La pagina «Audizioni» di una compagnia e' cambiata? Allora e' un annuncio nuovo.

    L'impronta si fa sul testo senza cifre e senza spazi doppi: cosi' un orologio o un
    contatore nella pagina non fanno scattare un annuncio finto ogni giorno."""
    testo = pagina["testo"] or ""
    pulito = " ".join(re.sub(r"\d+", "", testo.lower()).split())
    if len(pulito) < 200:
        return 0
    impronta = hashlib.sha1(pulito.encode("utf-8")).hexdigest()[:16]
    ident = _ident(sito["url"] + "#" + impronta)
    if db.execute("SELECT 1 FROM bandi WHERE id=?", (ident,)).fetchone():
        return 0
    titolo = (pagina["titolo"] or ("Audizioni - " + sito["nome"])).strip()[:300]
    db.execute(
        "INSERT OR IGNORE INTO bandi (id,titolo,link,ente,fonte,sommario,testo,immagine,trovato_il) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (ident, titolo, sito["url"], sito["ente"] or sito["nome"], sito["nome"],
         testo[:900], testo, pagina.get("immagine") or None, oggi))
    return 1


def giro_siti(db):
    migra(db)
    db.row_factory = sqlite3.Row
    elenco = [dict(r) for r in db.execute("SELECT * FROM siti WHERE attivo=1 ORDER BY id")]
    if not elenco:
        print("Nessun sito aggiunto a mano. Si aggiungono dalla pagina, in fondo.")
        return 0

    # Le opzioni di ogni fonte (solo audizioni, pagina da sorvegliare) stanno nel file.
    import configurazione
    opzioni = {f["url"]: f for f in configurazione.leggi_file()["siti"]}

    totale = 0
    primo_giro = []   # le fonti mai lette prima: su quelle si risponde su Telegram
    for s in elenco:
        mai_letta = not s.get("ultimo_giro")
        esito, nuovi = controlla_sito(db, s, opzioni.get(s["url"]))
        totale += nuovi
        if mai_letta:
            primo_giro.append((s["nome"], s["url"], esito, nuovi))
        db.execute("UPDATE siti SET ultimo_giro=?, esito=?, trovati=? WHERE id=?",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    esito, nuovi, s["id"]))
        db.commit()
        segno = "OK" if esito == "ok" else "--"
        print("  %s %-30s %3d nuovi   %s" % (segno, s["nome"][:30], nuovi,
                                             "" if esito == "ok" else esito))
        time.sleep(pausa_per(s["url"]))

    if primo_giro:
        racconta_fonti_nuove(db, primo_giro)
    return totale


def racconta_fonti_nuove(db, elenco):
    """Dice su Telegram com'e' andata la PRIMA lettura di una fonte appena aggiunta.

    Serve a rispondere alla domanda «ho dato la pagina giusta?»: se la risposta e'
    zero bandi, quasi sempre l'indirizzo punta troppo in alto e i bandi stanno in
    una sottosezione.
    """
    import avvisi
    imp = avvisi.carica()
    token = imp["telegram"]["token"].strip()
    chat = str(imp["telegram"]["chat_id"]).strip()
    if not token or not chat:
        return
    from html import escape
    a_capo = chr(10)
    righe = ["<b>Fonti nuove: com'e' andata</b>"]
    # Con molte fonti nuove insieme (109 compagnie di danza il 28 set 2026) un elenco
    # riga per riga supera il limite di Telegram: si riassume, e si elencano solo
    # quelle che non hanno funzionato.
    if len(elenco) > 12:
        andate = sum(1 for _, _, e, _ in elenco if e == "ok")
        trovati = sum(n for _, _, e, n in elenco if e == "ok")
        righe.append("%d fonti lette per la prima volta: %d funzionano, %d annunci trovati."
                     % (len(elenco), andate, trovati))
        elenco = [x for x in elenco if x[2] != "ok"][:15]
    for nome, url, esito, nuovi in elenco:
        if esito == "ok" and nuovi:
            coda = "%d bandi trovati: pagina giusta." % nuovi
        elif esito == "ok":
            coda = ("raggiunta, ma nessun bando. Se e' la prima volta, di solito "
                    "l'indirizzo punta troppo in alto: cerca la sottosezione dove "
                    "stanno davvero gli avvisi.")
        else:
            coda = "non ha funzionato: " + escape(esito)
        righe.append("<b>%s</b>%s   %s" % (escape(nome), a_capo, coda))
    try:
        avvisi.manda_telegram(token, chat, (a_capo * 2).join(righe))
        print("Mandato il resoconto delle fonti nuove.")
    except Exception as e:
        print("Resoconto non inviato:", type(e).__name__)


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
                       "importo_num=COALESCE(?,importo_num), "
                       "immagine=COALESCE(immagine,?) WHERE id=?",
                       (doc["testo"], doc["nota"], importo, importo_num,
                        doc.get("immagine") or None, b["id"]))
            fatti += 1
        else:
            db.execute("UPDATE bandi SET nota=? WHERE id=?", (doc["nota"], b["id"]))
        db.commit()
        time.sleep(pausa_per(b["link"]))

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
