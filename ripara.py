#!/usr/bin/env python3
"""
Riparazione automatica delle fonti.

Dopo ogni giro guarda le fonti che hanno dato errore e prova a sistemarle da sola,
come farebbe una persona paziente col browser:

  1. stesso indirizzo, tipo opposto: un «feed» che in realta' e' una pagina, o una
     «pagina» che in realta' e' un feed (MIMIT e Comune di Vibo, 23 set 2026);
  2. la sezione «Bandi» / «Avvisi» del sito, cercata fra i collegamenti della pagina
     e della home: una pagina di bandi vale piu' di un feed di notizie;
  3. il feed che il sito dichiara nel suo codice (<link rel="alternate">);
  4. gli indirizzi dove i programmi piu' usati mettono il feed (WordPress, Plone,
     Joomla, Drupal).

Ogni candidato si APRE davvero e si tiene solo se funziona: un feed con delle voci,
una pagina con dei collegamenti a bandi. La prima che funziona sostituisce quella
rotta in configurazione.json, la fonte si rilegge subito, e sotto la fonte la pagina
racconta cosa e' cambiato. Se niente funziona, la pagina dice che ci ha provato.

Non si ripara quello che non dipende da noi: un sito in avaria (5xx) o che non
risponde si riprova domani col giro normale; un sito che respinge i programmi (403)
non si forza.

Si usa cosi':
  python ripara.py            ripara le fonti che hanno dato errore nell'ultimo giro
  python ripara.py --prova    dice cosa proverebbe e cosa troverebbe, senza cambiare niente
"""
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse

import configurazione
import estrattore
from raccogli import DB, leggi_feed, leggi_un_feed, pausa_per, robots_permette, scarica

# Errori che una riparazione puo' curare: l'indirizzo o il tipo sono sbagliati.
RIPARABILI = ("404", "410", "non e' un feed", "nessuna voce", "nessun collegamento",
              "formato non riconosciuto")

# Dove i programmi per siti piu' diffusi mettono il feed, relativo alla radice del sito.
PERCORSI_FEED = ["/feed/", "/rss.xml", "/rss", "/feed.xml", "/feed",
                 "/index.php?format=feed&type=rss", "/atom.xml"]

# Parole che, in un collegamento, fanno pensare alla sezione dei bandi del sito.
PAROLE_SEZIONE = ["bandi", "avvisi", "contributi", "finanziament", "opportunit",
                  "agevolazion", "incentivi", "call", "sovvenzion"]

MAX_PROVE = 10          # indirizzi aperti al massimo per ogni fonte rotta
GIORNI_RICORDO = 14     # per quanto la pagina mostra una riparazione riuscita


def _ora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _radice(url):
    p = urlparse(url)
    return p.scheme + "://" + p.netloc


def riparabile(esito):
    e = (esito or "").lower()
    return bool(e) and e != "ok" and any(r in e for r in RIPARABILI)


# ---------------------------------------------------------------- le due prove

def funziona_come_feed(url):
    """Quante voci ha il feed a quell'indirizzo (0 se non e' un feed o non risponde)."""
    try:
        if not robots_permette(url):
            return 0
        voci = leggi_feed(scarica(url))
        return sum(1 for v in voci if (v.get("link") or "").strip() and v.get("titolo"))
    except Exception:
        return 0


def funziona_come_pagina(url):
    """Quanti collegamenti a bandi ha la pagina (0 se non risponde o non ne ha)."""
    try:
        if not robots_permette(url):
            return 0
        pagina = estrattore.leggi(url)
        if not pagina["testo"]:
            return 0
        return len(estrattore.link_interessanti(pagina["link"], url))
    except Exception:
        return 0


# ---------------------------------------------------------------- i candidati

def _html(url):
    """L'HTML grezzo di una pagina, o stringa vuota. Serve a leggere i <link> del <head>,
    che il lettore di testo giustamente butta via."""
    try:
        corpo, intestazioni, finale = estrattore.scarica(url)
        return corpo.decode(estrattore._codifica(intestazioni, corpo), "replace"), finale
    except Exception:
        return "", url


def feed_dichiarati(html, base):
    """I feed che il sito dichiara: <link rel="alternate" type="application/rss+xml">."""
    trovati = []
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        if not re.search(r"(rss|atom)\+xml", tag, re.I):
            continue
        m = re.search(r'href\s*=\s*["\']([^"\']+)', tag, re.I)
        if m:
            trovati.append(urljoin(base, unescape(m.group(1))))
    # I feed dei commenti non portano bandi.
    return [u for u in trovati if "comment" not in u.lower()]


def sezioni_bandi(html, base):
    """Collegamenti dello stesso sito che sembrano la sezione dei bandi, i piu'
    promettenti per primi: prima quelli con la parola nell'indirizzo E nel testo."""
    dominio = urlparse(base).netloc.replace("www.", "")
    visti, scelti = set(), []
    for href, testo in re.findall(r'<a\b[^>]*href\s*=\s*["\']([^"\'#]+)["\'][^>]*>(.*?)</a>',
                                  html, re.I | re.S):
        url = urljoin(base, unescape(href)).split("#")[0]
        if not url.startswith("http") or urlparse(url).netloc.replace("www.", "") != dominio:
            continue
        if url.lower().endswith((".pdf", ".doc", ".docx", ".zip")) or url in visti:
            continue
        visti.add(url)
        testo = re.sub(r"<[^>]+>", " ", testo).lower()
        nel_link = any(p in url.lower() for p in PAROLE_SEZIONE)
        nel_testo = any(p in testo for p in PAROLE_SEZIONE)
        # Un collegamento lungo e' quasi sempre un singolo bando, non la sezione.
        if (nel_link or nel_testo) and len(urlparse(url).path) < 80:
            scelti.append((2 * nel_link + nel_testo, url))
    scelti.sort(key=lambda x: -x[0])
    return [u for _, u in scelti]


def candidati(url, tipo, esito):
    """Gli indirizzi da provare, nell'ordine in cui conviene provarli: (url, tipo, come)."""
    altro = "feed" if tipo == "sito" else "sito"
    fuori = [(url, altro, "era segnata come %s ma e' %s" % (
        "pagina" if tipo == "sito" else "feed", "un feed" if altro == "feed" else "una pagina"))]

    radice = _radice(url)
    html, finale = _html(url)
    html_home, _ = _html(radice + "/") if finale.rstrip("/") != radice else (html, finale)

    for u in (sezioni_bandi(html, finale) + sezioni_bandi(html_home, radice))[:4]:
        fuori.append((u, "sito", "trovata la sezione dei bandi del sito"))
    for u in feed_dichiarati(html, finale) + feed_dichiarati(html_home, radice):
        fuori.append((u, "feed", "trovato il feed che il sito dichiara"))
    for percorso in PERCORSI_FEED:
        fuori.append((radice + percorso, "feed", "trovato il feed del sito"))
    # Joomla: qualunque elenco diventa feed aggiungendo due parametri (MIMIT).
    if "?" not in url:
        fuori.append((url + "?format=feed&type=rss", "feed", "trovato il feed di questo elenco"))

    visti, unici = set(), []
    for u, t, come in fuori:
        chiave = (configurazione.normalizza(u), t)
        if chiave not in visti and not (u == url and t == tipo):
            visti.add(chiave)
            unici.append((u, t, come))
    return unici


# ---------------------------------------------------------------- la riparazione

def cerca_riparazione(url, tipo, esito, occupati):
    """Prova i candidati uno alla volta. Restituisce (url, tipo, come) o None, e quanti
    indirizzi ha aperto."""
    aperti = 0
    for u, t, come in candidati(url, tipo, esito):
        if configurazione.normalizza(u) in occupati:
            continue            # e' gia' un'altra fonte: sarebbe un doppione
        if aperti >= MAX_PROVE:
            break
        aperti += 1
        time.sleep(pausa_per(u))
        if (funziona_come_feed(u) if t == "feed" else funziona_come_pagina(u)) > 0:
            return (u, t, come), aperti
    return None, aperti


def stato_fonti(db):
    """Esito dell'ultimo giro per ogni fonte, preso dalle due tabelle."""
    db.row_factory = sqlite3.Row
    feed = {r["nome"]: r["esito"] for r in db.execute("SELECT nome, esito FROM fonti_stato")}
    try:
        siti = {r["url"]: r["esito"] for r in db.execute("SELECT url, esito FROM siti")}
    except sqlite3.OperationalError:
        siti = {}
    return feed, siti


def togli_fantasmi(db, cfg):
    """Lo stato di feed che non esistono piu' (tolti, o diventati pagine) va cancellato:
    restava nel conto «fonti attive su N» e, peggio, riappariva con l'errore vecchio
    quando una fonte con lo stesso nome tornava a essere un feed."""
    nomi = {f["nome"] for f in cfg["feed"]}
    tolti = 0
    for (nome,) in db.execute("SELECT nome FROM fonti_stato").fetchall():
        if nome not in nomi:
            db.execute("DELETE FROM fonti_stato WHERE nome=?", (nome,))
            tolti += 1
    db.commit()
    return tolti


def rileggi(db, fonte, tipo):
    """Dopo la riparazione la fonte si legge subito: i bandi arrivano oggi, e la pagina
    mostra l'esito nuovo invece dell'errore di ieri."""
    if tipo == "feed":
        return leggi_un_feed(db, fonte)[0]
    import siti
    riga = db.execute("SELECT * FROM siti WHERE url=?", (fonte["url"],)).fetchone()
    if not riga:
        return "?"
    esito, nuovi = siti.controlla_sito(db, dict(riga))
    db.execute("UPDATE siti SET ultimo_giro=?, esito=?, trovati=? WHERE id=?",
               (_ora(), esito, nuovi, riga["id"]))
    db.commit()
    return esito


def ripara(db, prova=False):
    """Ripara le fonti rotte. Restituisce l'elenco delle riparazioni riuscite."""
    cfg = configurazione.leggi_file()
    stato_feed, stato_siti = stato_fonti(db)
    registro = cfg.get("riparazioni") or {}
    riuscite = []

    rotte = ([(f, "feed", stato_feed.get(f["nome"])) for f in cfg["feed"]] +
             [(s, "sito", stato_siti.get(s["url"])) for s in cfg["siti"]])
    rotte = [(f, t, e) for f, t, e in rotte if f.get("attivo", True) and riparabile(e)]
    if not rotte:
        print("Riparazione fonti: nessuna fonte da riparare.")
    for fonte, tipo, esito in rotte:
        # Gli indirizzi delle ALTRE fonti: riusarli farebbe un doppione. Il suo no,
        # perche' la prima cura e' proprio lo stesso indirizzo col tipo giusto.
        occupati = {configurazione.normalizza(x["url"]) for x in cfg["feed"] + cfg["siti"]
                    if x is not fonte}
        trovata, aperti = cerca_riparazione(fonte["url"], tipo, esito, occupati)
        if not trovata:
            print("  -- %-30s nessuna strada trovata (%d indirizzi provati)"
                  % (fonte["nome"][:30], aperti))
            registro[fonte["nome"]] = {"quando": _ora(), "riuscita": False,
                                       "errore": esito, "provati": aperti}
            continue

        nuovo_url, nuovo_tipo, come = trovata
        print("  OK %-30s %s: %s (%s)" % (fonte["nome"][:30], come, nuovo_url,
                                          "feed" if nuovo_tipo == "feed" else "pagina"))
        if prova:
            continue
        vecchia = (cfg["feed"] if tipo == "feed" else cfg["siti"])
        vecchia.remove(fonte)
        rifatta = dict(fonte, url=nuovo_url)
        (cfg["feed"] if nuovo_tipo == "feed" else cfg["siti"]).append(rifatta)
        registro[fonte["nome"]] = {
            "quando": _ora(), "riuscita": True, "errore": esito, "come": come,
            "prima": {"url": fonte["url"], "tipo": tipo},
            "dopo": {"url": nuovo_url, "tipo": nuovo_tipo},
        }
        riuscite.append((fonte["nome"], come, nuovo_url, nuovo_tipo, rifatta))

    if prova:
        return riuscite

    # Il registro tiene l'ultima parola su ogni fonte ancora presente, e dimentica le
    # riparazioni riuscite dopo due settimane: a quel punto sono storia vecchia.
    esito_di = {f["nome"]: stato_feed.get(f["nome"]) for f in cfg["feed"]}
    esito_di.update({s["nome"]: stato_siti.get(s["url"]) for s in cfg["siti"]})
    limite = datetime.now(timezone.utc).timestamp() - GIORNI_RICORDO * 86400
    for nome in list(registro):
        voce = registro[nome]
        scaduta = voce["riuscita"] and datetime.fromisoformat(voce["quando"]).timestamp() < limite
        # Un tentativo fallito si dimentica quando la fonte torna a funzionare.
        guarita = not voce["riuscita"] and not riparabile(esito_di.get(nome))
        if nome not in esito_di or scaduta or guarita:
            del registro[nome]
    cfg["riparazioni"] = registro
    configurazione.FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                                   encoding="utf-8")

    # Il database si riallinea al file (i siti stanno anche li'), e le fonti riparate
    # si leggono subito.
    configurazione.importa(db)
    togli_fantasmi(db, cfg)
    for nome, come, url, tipo, fonte in riuscite:
        print("     riletta subito: %s" % rileggi(db, fonte, tipo))
    if riuscite:
        avvisa(riuscite)
    return riuscite


def avvisa(riuscite):
    """Un messaggio breve su Telegram: una fonte che cambia indirizzo da sola va detta."""
    import avvisi
    imp = avvisi.carica()
    token = imp["telegram"]["token"].strip()
    chat = str(imp["telegram"]["chat_id"]).strip()
    if not token or not chat:
        return
    righe = ["<b>Ho riparato da solo %s</b>" % (
        "una fonte" if len(riuscite) == 1 else "%d fonti" % len(riuscite)), ""]
    for nome, come, url, tipo, _ in riuscite:
        righe.append("<b>%s</b>: %s." % (avvisi.escape(nome), avvisi.escape(come)))
    righe.append("")
    righe.append("Se qualcosa non ti torna, la trovi nella pagina sotto «Fonti che controllo».")
    try:
        avvisi.manda_telegram(token, chat, "\n".join(righe))
    except Exception:
        pass


if __name__ == "__main__":
    db = sqlite3.connect(DB)
    ripara(db, prova="--prova" in sys.argv)
