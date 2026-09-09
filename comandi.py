#!/usr/bin/env python3
"""
Comandi da Telegram - il telefono diventa il pannello di controllo.

Ogni quarto d'ora GitHub esegue questo file: legge i messaggi che sono arrivati al
bot, esegue i comandi e risponde. Cosi' si cambia il profilo e si aggiungono fonti
senza accendere il computer.

Comandi:
  /aiuto             l'elenco dei comandi
  /profilo           come sei impostato adesso
  /sono aps          cambia che cosa sei (associazione, aps, odv, ets, impresa...)
  /fonte <indirizzo> aggiunge un sito da controllare, e lo controlla subito
  /fonti             i siti che sto controllando
  /aperti            i bandi aperti adesso, con scadenza e quanto puoi ottenere

Solo la chat configurata puo' dare comandi: i messaggi di chiunque altro
vengono ignorati.
"""
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

import avvisi
import profili
import siti

BASE = Path(__file__).parent
DB = BASE / "dati.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_stato (
  chiave TEXT PRIMARY KEY,
  valore TEXT
);
"""

AIUTO = """<b>Cosa so fare</b>

/profilo — come sei impostato adesso
/sono aps — cambia che cosa sei
   (associazione, aps, odv, ets, fondazione, impresa, cooperativa,
    societa, ditta, privato, comune, scuola)
/fonte https://... — aggiungo il sito e lo controllo subito
/fonti — i siti che sto controllando
/aperti — i bandi aperti adesso
/aiuto — questo elenco

Ogni mattina alle 7 guardo tutte le fonti e ti scrivo se trovo qualcosa per te."""


# ---------------------------------------------------------------- memoria

def leggi_stato(db, chiave, predefinito=None):
    db.executescript(SCHEMA)
    r = db.execute("SELECT valore FROM telegram_stato WHERE chiave=?", (chiave,)).fetchone()
    return r[0] if r else predefinito


def scrivi_stato(db, chiave, valore):
    db.execute("INSERT INTO telegram_stato (chiave,valore) VALUES (?,?) "
               "ON CONFLICT(chiave) DO UPDATE SET valore=?", (chiave, str(valore), str(valore)))
    db.commit()


# ---------------------------------------------------------------- risposte

def italiana(iso):
    if not iso:
        return "senza scadenza"
    a, m, g = iso.split("-")
    return "%s/%s/%s" % (g, m, a)


def dice_profilo(db):
    elenco = profili.leggi_profili(db)
    if not elenco:
        return "Non hai ancora nessun profilo."
    fuori = []
    for p in elenco:
        quanti = db.execute(
            "SELECT COUNT(*) FROM abbinamenti a JOIN bandi b ON b.id=a.bando_id "
            "WHERE a.profilo_id=? AND b.archiviato=0", (p["id"],)).fetchone()[0]
        fuori.append(
            "<b>%s</b>\nSei: %s\nTi occupi di: %s\nDove: %s\nBandi compatibili: %d" % (
                escape(p["nome"]), escape(p["tipo_ente"] or "non impostato"),
                escape(", ".join(p["settori"]) or "tutto"),
                escape(", ".join(p["regioni"]) or "ovunque"), quanti))
    return "\n\n".join(fuori) + "\n\nPer cambiare: <code>/sono aps</code>"


SINONIMI = {
    "associazione": "Associazione non riconosciuta",
    "non riconosciuta": "Associazione non riconosciuta",
    "aps": "Associazione di promozione sociale (APS)",
    "odv": "Organizzazione di volontariato (ODV)",
    "volontariato": "Organizzazione di volontariato (ODV)",
    "ets": "Ente del Terzo Settore (ETS/ONLUS)",
    "onlus": "Ente del Terzo Settore (ETS/ONLUS)",
    "terzo settore": "Ente del Terzo Settore (ETS/ONLUS)",
    "fondazione": "Fondazione",
    "impresa sociale": "Impresa sociale",
    "impresa": "Impresa sociale",
    "cooperativa": "Cooperativa",
    "coop": "Cooperativa",
    "societa": "Societa' (SRL, SPA)",
    "srl": "Societa' (SRL, SPA)",
    "spa": "Societa' (SRL, SPA)",
    "ditta": "Ditta individuale / libero professionista",
    "professionista": "Ditta individuale / libero professionista",
    "partita iva": "Ditta individuale / libero professionista",
    "privato": "Privato cittadino",
    "comune": "Comune o ente pubblico",
    "scuola": "Scuola o universita'",
    "universita": "Scuola o universita'",
}


def cambia_tipo(db, testo):
    voluto = profili._norm(testo).strip()
    if not voluto:
        return "Scrivimi che cosa sei, per esempio <code>/sono aps</code>."
    scelto = None
    for chiave, valore in SINONIMI.items():
        if profili._norm(chiave) in voluto:
            scelto = valore
            break
    if not scelto:
        return ("Non ho capito «%s». Puoi scrivere: associazione, aps, odv, ets,\n"
                "fondazione, impresa, cooperativa, societa, ditta, privato, comune, scuola."
                % escape(testo))

    elenco = profili.leggi_profili(db)
    if not elenco:
        return "Non hai ancora nessun profilo da cambiare."
    p = elenco[0]
    db.execute("UPDATE profili SET tipo_ente=? WHERE id=?", (scelto, p["id"]))
    # I giudizi del modello erano dati sul profilo vecchio: vanno rifatti.
    db.execute("UPDATE abbinamenti SET llm_verdetto=NULL, llm_motivo=NULL WHERE profilo_id=?",
               (p["id"],))
    db.commit()
    quanti = profili.riabbina(db, p["id"])
    return ("Fatto: adesso sei <b>%s</b>.\nBandi compatibili: %d.\n\n"
            "Rileggo i giudizi «puoi parteciparci» al prossimo giro." % (escape(scelto), quanti))


def aggiungi_fonte(db, testo):
    url = testo.strip().split()[0] if testo.strip() else ""
    if not url:
        return "Mandami anche l'indirizzo, per esempio <code>/fonte www.comune.tropea.vv.it/bandi</code>"
    if not url.startswith("http"):
        url = "https://" + url
    siti.migra(db)
    gia = db.execute("SELECT nome FROM siti WHERE url=?", (url,)).fetchone()
    if gia:
        return "Quel sito lo sto gia' controllando."

    from urllib.parse import urlparse
    nome = urlparse(url).netloc.replace("www.", "")
    db.execute("INSERT INTO siti (nome,url,ente,attivo,aggiunto_il) VALUES (?,?,'',1,?)",
               (nome, url, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    db.commit()

    db.row_factory = sqlite3.Row
    sito = dict(db.execute("SELECT * FROM siti WHERE url=?", (url,)).fetchone())
    esito, nuovi = siti.controlla_sito(db, sito)
    db.execute("UPDATE siti SET ultimo_giro=?, esito=?, trovati=? WHERE id=?",
               (datetime.now(timezone.utc).isoformat(timespec="seconds"), esito, nuovi, sito["id"]))
    db.commit()
    if esito != "ok":
        return ("Ho aggiunto <b>%s</b>, ma leggendolo ho avuto un problema:\n%s\n\n"
                "Riprovo domani mattina." % (escape(nome), escape(esito)))
    profili.riabbina(db)
    return ("Aggiunto <b>%s</b>.\nCi ho trovato subito %d bandi.\n\n"
            "Li leggo stanotte e ti avviso se qualcuno ti riguarda." % (escape(nome), nuovi))


def dice_fonti(db):
    siti.migra(db)
    db.row_factory = sqlite3.Row
    righe = db.execute("SELECT nome,esito,trovati FROM siti WHERE attivo=1 ORDER BY id").fetchall()
    feed = db.execute("SELECT COUNT(*) FROM fonti_stato WHERE esito='ok'").fetchone()[0]
    parti = ["<b>Sto controllando %d feed</b> di regioni e fondazioni." % feed]
    if righe:
        parti.append("\n<b>Piu' questi siti aggiunti da te:</b>")
        for r in righe:
            stato = "ok, %d bandi" % (r["trovati"] or 0) if r["esito"] == "ok" else (r["esito"] or "mai letto")
            parti.append("• %s — %s" % (escape(r["nome"]), escape(stato)))
    else:
        parti.append("\nNessun sito aggiunto da te. Usa <code>/fonte indirizzo</code>.")
    return "\n".join(parti)


def dice_aperti(db):
    db.row_factory = sqlite3.Row
    # Il verdetto si prende con una sottoquery, non con una JOIN: con piu' di un
    # profilo la JOIN duplicherebbe lo stesso bando una volta per profilo.
    righe = db.execute(
        "SELECT b.titolo, b.link, b.scadenza, b.contributo, b.importo, "
        "  (SELECT a.llm_verdetto FROM abbinamenti a WHERE a.bando_id = b.id "
        "   ORDER BY a.punteggio DESC LIMIT 1) AS llm_verdetto "
        "FROM bandi b "
        "WHERE b.archiviato = 0 AND b.aperto = 1 "
        "AND b.scadenza IS NOT NULL AND b.scadenza >= date('now') "
        "ORDER BY b.scadenza LIMIT 12").fetchall()
    if not righe:
        return "In questo momento non ho bandi aperti con una scadenza sicura."
    oggi = date.today()
    parti = ["<b>Bandi aperti adesso</b>\n"]
    for r in righe:
        giorni = (date.fromisoformat(r["scadenza"]) - oggi).days
        riga = '• <a href="%s">%s</a>\n  scade il %s (tra %d giorni)' % (
            escape(r["link"]), escape(r["titolo"][:90]), italiana(r["scadenza"]), giorni)
        if r["contributo"]:
            riga += "\n  puoi ottenere: %s" % escape(r["contributo"])
        elif r["importo"]:
            riga += "\n  fondi totali: %s" % escape(r["importo"])
        if r["llm_verdetto"] == "no":
            riga += "\n  (non sembra per te)"
        parti.append(riga)
    return "\n".join(parti)


# ---------------------------------------------------------------- smistamento

def esegui(db, comando, resto):
    if comando in ("/aiuto", "/start", "/help"):
        return AIUTO
    if comando == "/profilo":
        return dice_profilo(db)
    if comando == "/sono":
        return cambia_tipo(db, resto)
    if comando == "/fonte":
        return aggiungi_fonte(db, resto)
    if comando == "/fonti":
        return dice_fonti(db)
    if comando == "/aperti":
        return dice_aperti(db)
    return "Non conosco «%s».\n\n%s" % (escape(comando), AIUTO)


def giro():
    imp = avvisi.carica()
    token = imp["telegram"]["token"].strip()
    mia_chat = str(imp["telegram"]["chat_id"]).strip()
    if not token or not mia_chat:
        print("Telegram non configurato.")
        return 0

    db = sqlite3.connect(DB)
    profili.prepara(db)
    db.executescript(SCHEMA)
    import configurazione
    configurazione.importa(db)   # il file comanda anche qui

    ultimo = int(leggi_stato(db, "ultimo_update", 0))
    try:
        agg = avvisi._telegram(token, "getUpdates",
                               {"offset": ultimo + 1, "timeout": 0}).get("result", [])
    except Exception as e:
        print("Non riesco a leggere i messaggi:", type(e).__name__)
        db.close()
        return 0

    fatti = 0
    for a in agg:
        scrivi_stato(db, "ultimo_update", a["update_id"])
        msg = a.get("message") or a.get("edited_message")
        if not msg:
            continue
        # Solo la chat configurata comanda: chiunque altro viene ignorato.
        if str(msg.get("chat", {}).get("id")) != mia_chat:
            continue
        testo = (msg.get("text") or "").strip()
        if not testo.startswith("/"):
            continue

        pezzi = testo.split(None, 1)
        comando = pezzi[0].split("@")[0].lower()
        resto = pezzi[1] if len(pezzi) > 1 else ""
        print("comando ricevuto:", comando)
        try:
            risposta = esegui(db, comando, resto)
        except Exception as e:
            risposta = "Qualcosa e' andato storto eseguendo il comando (%s)." % type(e).__name__
            print("  errore:", type(e).__name__, e)
        try:
            avvisi.manda_telegram(token, mia_chat, risposta)
        except Exception as e:
            print("  non sono riuscito a rispondere:", type(e).__name__)
        fatti += 1

    if fatti:
        import configurazione
        configurazione.esporta(db)
    db.close()
    print("Comandi eseguiti: %d" % fatti)
    return fatti


def registra_menu():
    """Mette i comandi nel menu del bot, cosi' non c'e' niente da ricordare a memoria."""
    token = avvisi.carica()["telegram"]["token"].strip()
    elenco = [
        {"command": "aperti", "description": "I bandi aperti adesso"},
        {"command": "profilo", "description": "Come sei impostato"},
        {"command": "sono", "description": "Cambia che cosa sei (es. /sono aps)"},
        {"command": "fonte", "description": "Aggiungi un sito da controllare"},
        {"command": "fonti", "description": "I siti che sto controllando"},
        {"command": "aiuto", "description": "L'elenco dei comandi"},
    ]
    r = avvisi._telegram(token, "setMyCommands", {"commands": json.dumps(elenco)})
    print("menu del bot registrato:", r.get("ok"))


if __name__ == "__main__":
    if "--registra" in sys.argv:
        registra_menu()
    else:
        giro()
