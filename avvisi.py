#!/usr/bin/env python3
"""
Avvisi su Telegram - Fase 3.

Manda un messaggio quando un bando nuovo e' compatibile con un profilo.
Ogni bando viene avvisato UNA VOLTA SOLA per profilo (colonna 'avvisato').

Si usa cosi':
  python avvisi.py --collega   collega il bot: scrivi «ciao» al bot e lui trova da solo il codice
  python avvisi.py --prova     mostra i messaggi che manderebbe, SENZA mandarli
  python avvisi.py             manda davvero

Solo libreria standard di Python.
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = Path(__file__).parent
DB = BASE / "dati.db"
CONFIG = BASE / "impostazioni.json"

PREDEFINITE = {
    "_nota": "Il codice del bot e' una chiave privata: questo file non va condiviso ne' pubblicato.",
    "telegram": {"token": "", "chat_id": "", "attivo": True},
    "email": {"attivo": False, "mittente": "", "password_app": "",
              "destinatario": "", "server": "smtp.gmail.com", "porta": 465},
    "soglia_avviso": 55,
    "massimo_messaggi": 8,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifiche (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  quando     TEXT,
  canale     TEXT,
  profilo_id INTEGER,
  bando_id   TEXT,
  esito      TEXT,
  testo      TEXT
);
CREATE INDEX IF NOT EXISTS idx_notifiche_quando ON notifiche(quando DESC);
"""


# ---------------------------------------------------------------- impostazioni

def carica():
    """Le chiavi segrete stanno qui o nelle variabili d'ambiente; la soglia degli
    avvisi invece sta in configurazione.json, perche' si cambia dalla pagina."""
    if CONFIG.exists():
        dati = json.loads(CONFIG.read_text(encoding="utf-8"))
    else:
        CONFIG.write_text(json.dumps(PREDEFINITE, indent=2, ensure_ascii=False), encoding="utf-8")
        dati = {}
    unite = dict(PREDEFINITE)
    unite.update(dati)
    for chiave in ("telegram", "email"):
        base = dict(PREDEFINITE[chiave])
        base.update(dati.get(chiave, {}))
        unite[chiave] = base

    # Sul server i codici non stanno in un file ma nelle variabili d'ambiente:
    # cosi' non finiscono mai dentro il repository.
    import os
    if os.environ.get("MONITOR_TELEGRAM_TOKEN"):
        unite["telegram"]["token"] = os.environ["MONITOR_TELEGRAM_TOKEN"]
    if os.environ.get("MONITOR_TELEGRAM_CHAT"):
        unite["telegram"]["chat_id"] = os.environ["MONITOR_TELEGRAM_CHAT"]
    if os.environ.get("MONITOR_GROQ_CHIAVE"):
        unite.setdefault("groq", {})["chiave"] = os.environ["MONITOR_GROQ_CHIAVE"]

    try:
        import configurazione
        unite.update(configurazione.leggi_file()["impostazioni"])
    except Exception:
        pass
    return unite


def salva(imp):
    CONFIG.write_text(json.dumps(imp, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- Telegram

def _telegram(token, metodo, dati=None):
    url = "https://api.telegram.org/bot%s/%s" % (token, metodo)
    corpo = urlencode(dati).encode("utf-8") if dati else None
    req = Request(url, data=corpo, headers={"User-Agent": "MonitorBandi/0.1"})
    with urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def manda_telegram(token, chat_id, testo):
    return _telegram(token, "sendMessage", {
        "chat_id": chat_id, "text": testo,
        "parse_mode": "HTML", "disable_web_page_preview": "false",
    })


def collega():
    """Trova da solo il codice della chat: basta scrivere «ciao» al bot."""
    imp = carica()
    token = imp["telegram"]["token"].strip()
    if not token:
        print("Manca il codice del bot.")
        print("Su Telegram scrivi a @BotFather, comando /newbot, e incolla qui il codice che ti da'.")
        return
    try:
        me = _telegram(token, "getMe")
    except (HTTPError, URLError) as e:
        print("Il codice del bot non funziona:", e)
        return
    nome = me.get("result", {}).get("username", "?")
    print("Bot collegato: @%s" % nome)
    print("Ora apri Telegram, cerca @%s e mandagli un messaggio qualsiasi." % nome)
    print("Resto in attesa 90 secondi...\n")
    for _ in range(30):
        agg = _telegram(token, "getUpdates").get("result", [])
        for a in agg:
            chat = (a.get("message") or a.get("channel_post") or {}).get("chat")
            if chat:
                imp["telegram"]["chat_id"] = str(chat["id"])
                salva(imp)
                print("Trovato. Gli avvisi arriveranno a: %s" %
                      (chat.get("first_name") or chat.get("title") or chat["id"]))
                manda_telegram(token, chat["id"],
                               "<b>Monitor Bandi</b>\nCollegato. Da adesso ti avviso io.")
                return
        time.sleep(3)
    print("Nessun messaggio ricevuto. Riprova quando vuoi.")


# ---------------------------------------------------------------- messaggi

def italiana(iso):
    if not iso:
        return None
    a, m, g = iso.split("-")
    return "%s/%s/%s" % (g, m, a)


def soldi(n, grezzo):
    if n is None:
        return grezzo
    return "€ " + format(int(n), ",d").replace(",", ".")


def componi(b, profilo_nome):
    righe = ["<b>%s</b>" % escape(b["titolo"])]
    dettagli = []
    if b["ente"]:
        dettagli.append(escape(b["ente"]))
    if b["scadenza"]:
        dettagli.append("scade il " + italiana(b["scadenza"]))
    if dettagli:
        righe.append(" · ".join(dettagli))
    # Quanto puo' ottenere lui viene prima di quanto vale il bando in tutto.
    if b.get("contributo"):
        righe.append("Puoi ottenere: <b>%s</b>" % escape(b["contributo"]))
    if b["importo"]:
        righe.append("Fondi totali: %s" % escape(soldi(b["importo_num"], b["importo"])))
    if b.get("riassunto"):
        righe.append("\n" + escape(b["riassunto"][:400]))
    if b.get("llm_verdetto"):
        etichetta = {"si": "Puoi parteciparci", "forse": "Forse puoi parteciparci",
                     "no": "Non sembra per te"}.get(b["llm_verdetto"], b["llm_verdetto"])
        righe.append("\n<b>%s</b> — %s" % (etichetta, escape(b.get("llm_motivo") or "")))
    else:
        motivi = json.loads(b["motivi"] or "[]")
        if motivi:
            righe.append("<i>%s</i>" % escape(" · ".join(motivi)))
    righe.append("\n" + escape(b["link"]))
    righe.append("<i>profilo: %s</i>" % escape(profilo_nome))
    return "\n".join(righe)


def componi_riepilogo(bandi, profilo_nome, mostrati):
    righe = ["<b>%d nuovi bandi per «%s»</b>\n" % (len(bandi), escape(profilo_nome))]
    for b in bandi[:mostrati]:
        scad = " — scade il " + italiana(b["scadenza"]) if b["scadenza"] else ""
        righe.append("• <a href=\"%s\">%s</a>%s" % (escape(b["link"]), escape(b["titolo"]), scad))
    if len(bandi) > mostrati:
        righe.append("\n…e altri %d. Sono tutti nella pagina." % (len(bandi) - mostrati))
    return "\n".join(righe)


# ---------------------------------------------------------------- email

def manda_email(imp, oggetto, testo_html):
    import smtplib
    from email.message import EmailMessage
    e = imp["email"]
    msg = EmailMessage()
    msg["Subject"] = oggetto
    msg["From"] = e["mittente"]
    msg["To"] = e["destinatario"]
    msg.set_content("Apri il messaggio in HTML per vedere i bandi.")
    msg.add_alternative("<html><body>" + testo_html + "</body></html>", subtype="html")
    with smtplib.SMTP_SSL(e["server"], int(e["porta"]), timeout=30) as s:
        s.login(e["mittente"], e["password_app"])
        s.send_message(msg)


# ---------------------------------------------------------------- giro avvisi

def da_avvisare(db, soglia):
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute(
        "SELECT b.*, a.punteggio, a.motivi, a.profilo_id, a.llm_verdetto, a.llm_motivo, "
        "       p.nome AS profilo_nome "
        "FROM abbinamenti a "
        "JOIN bandi b   ON b.id = a.bando_id "
        "JOIN profili p ON p.id = a.profilo_id "
        "WHERE a.avvisato = 0 AND a.punteggio >= ? AND b.archiviato = 0 "
        # Mai avvisare di un bando che il modello ha dato per chiuso, ne' di uno
        # che ha letto e giudicato non adatto a questo profilo.
        "AND (b.aperto IS NULL OR b.aperto = 1) "
        "AND (a.llm_verdetto IS NULL OR a.llm_verdetto <> 'no') "
        "ORDER BY a.profilo_id, a.punteggio DESC", (soglia,)).fetchall()]


def registra(db, canale, b, esito, testo):
    db.execute("INSERT INTO notifiche (quando,canale,profilo_id,bando_id,esito,testo) "
               "VALUES (?,?,?,?,?,?)",
               (datetime.now(timezone.utc).isoformat(timespec="seconds"), canale,
                b.get("profilo_id"), b.get("id"), esito, testo))


def manda_riepilogo(db, imp):
    """Due righe il lunedi', per far sapere che il programma e' vivo."""
    token = imp["telegram"]["token"].strip()
    chat = str(imp["telegram"]["chat_id"]).strip()
    if not token or not chat:
        return
    uno = lambda sql: db.execute(sql).fetchone()[0]
    nuovi = uno("SELECT COUNT(*) FROM bandi WHERE trovato_il >= date('now','-7 day')")
    aperti = uno("SELECT COUNT(*) FROM bandi WHERE archiviato=0 AND aperto=1 "
                 "AND (scadenza IS NULL OR scadenza >= date('now'))")
    scartati = uno("SELECT COUNT(*) FROM abbinamenti WHERE llm_verdetto='no'")
    testo = ("<b>Riepilogo della settimana</b>\n\n"
             "Bandi nuovi trovati: %d\nBandi aperti in questo momento: %d\n"
             "Scartati perche' non adatti al tuo profilo: %d\n\n"
             "Non ti ho avvisato perche' non c'era niente che ti riguardasse.\n"
             "Con /aperti li vedi tutti, con /profilo controlli come sei impostato."
             % (nuovi, aperti, scartati))
    try:
        manda_telegram(token, chat, testo)
        registra(db, "telegram", {}, "ok", "riepilogo settimanale")
        db.commit()
        print("Mandato il riepilogo settimanale.")
    except Exception as e:
        print("Riepilogo non inviato:", type(e).__name__)


def invia(prova=False):
    imp = carica()
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)

    nuovi = da_avvisare(db, imp["soglia_avviso"])
    if not nuovi:
        print("Nessun bando nuovo da segnalare.")
        # Il silenzio e' ambiguo: non si distingue «niente per te» da «e' rotto».
        # Il lunedi' si manda comunque due righe di riepilogo.
        if datetime.now().weekday() == 0 and not prova:
            manda_riepilogo(db, imp)
        db.close()
        return 0

    token = imp["telegram"]["token"].strip()
    chat = imp["telegram"]["chat_id"].strip()
    acceso = imp["telegram"]["attivo"] and token and chat
    if not acceso and not prova:
        print("Telegram non e' ancora collegato: eseguo solo la prova.")
        print("Per collegarlo:  python avvisi.py --collega\n")
        prova = True

    # Raggruppiamo per profilo: se sono tanti, un solo messaggio di riepilogo
    # invece di dieci notifiche in fila.
    per_profilo = {}
    for b in nuovi:
        per_profilo.setdefault((b["profilo_id"], b["profilo_nome"]), []).append(b)

    inviati = 0
    for (pid, nome), bandi in per_profilo.items():
        if len(bandi) > imp["massimo_messaggi"]:
            messaggi = [(componi_riepilogo(bandi, nome, imp["massimo_messaggi"]), bandi)]
        else:
            messaggi = [(componi(b, nome), [b]) for b in bandi]

        for testo, riferiti in messaggi:
            if prova:
                print("-" * 62)
                print(testo.replace("<b>", "").replace("</b>", "")
                          .replace("<i>", "").replace("</i>", ""))
                continue
            try:
                manda_telegram(token, chat, testo)
                esito = "ok"
                inviati += 1
            except Exception as e:
                esito = "errore: " + type(e).__name__
                print("  invio fallito:", esito)
            for b in riferiti:
                registra(db, "telegram", b, esito, testo)
                if esito == "ok":
                    db.execute("UPDATE abbinamenti SET avvisato=1 WHERE bando_id=? AND profilo_id=?",
                               (b["id"], b["profilo_id"]))
            db.commit()
            time.sleep(1)  # gentilezza verso Telegram

        if not prova and imp["email"]["attivo"]:
            try:
                html = "<br>".join(componi(b, nome) for b in bandi)
                manda_email(imp, "Monitor Bandi — %d nuovi per %s" % (len(bandi), nome), html)
                registra(db, "email", bandi[0], "ok", "riepilogo di %d bandi" % len(bandi))
            except Exception as e:
                registra(db, "email", bandi[0], "errore: " + type(e).__name__, "")
            db.commit()

    if prova:
        print("-" * 62)
        quanti = sum(1 if len(b) > imp["massimo_messaggi"] else len(b)
                     for b in per_profilo.values())
        print("\nProva: %d messaggi pronti per %d bandi, nessuno inviato."
              % (quanti, len(nuovi)))
    else:
        print("Messaggi inviati: %d" % inviati)
    db.close()
    return inviati


if __name__ == "__main__":
    if "--collega" in sys.argv:
        collega()
    else:
        invia(prova="--prova" in sys.argv)
