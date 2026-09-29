#!/usr/bin/env python3
"""
Il portiere: Jev (TypeSafe) guarda ogni bando PRIMA di Groq.

Jev non scrive e non estrae niente: risponde a domande chiuse con una probabilita'.
Qui gli si chiede, per ogni bando non ancora letto, UNA cosa: «e' un bando/avviso/
audizione a cui ci si puo' candidare, o una notizia?». Con la risposta si decide
l'ORDINE in cui Groq legge: prima i bandi veri. Nessun bando viene saltato: le
notizie finiscono in fondo alla coda e Groq le legge se avanza tempo. Groq continua a
fare tutto il resto (scadenza, importo, riassunto, giudizio col perche').

La prova del 29 set 2026, su 217 bandi gia' letti da Groq (costata 1,7 centesimi):
  - «bando o notizia?»: sotto il 10% Jev mette meta' delle notizie (61 su 121) e
    nessun bando vero (l'unico «aperto» finito li' era una notizia letta male da Groq);
  - «interessa a questo profilo?»: debole su Garage68 e Labo Art (il conteggio di
    parole fa meglio), buono solo su Mati. Tolta: costava il 60% dei gettoni,
    perche' si portava dietro i racconti dei profili.

REGOLA CHE COMANDA TUTTO: il credito e' di 5 dollari, prepagato, e non si ricarica.
Prezzo ufficiale (docs.typesafe.ai/models, 29 set 2026): 0,042 $ per milione di
gettoni in ingresso, uscita gratis. Quindi:
  1. si tiene il conto di ogni gettone nella tabella `consumo` (modello "jev");
  2. c'e' un tetto al giorno e un tetto totale: superati, ci si ferma e basta;
  3. senza chiave, o se Jev non risponde, tutto va avanti come prima.

Punti deboli dichiarati da TypeSafe per jev-1.13: date e numeri (il calendario resta
al codice), testi lunghi pieni di rumore (si manda solo l'inizio del bando), lingue
diverse dall'inglese «gestite ma non ugualmente bene».

Si usa cosi':
  python jev.py --conto     quanto si e' speso finora e quanto resta
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = Path(__file__).parent
DB = BASE / "dati.db"
API = "https://api.typesafe.ai/v1"
MODELLO = "jev-latest"
NOME_CONSUMO = "jev"

PREZZO_PER_GETTONE = 0.042 / 1_000_000   # dollari, solo i gettoni in ingresso

# Tetti di spesa. Il credito e' 5 $ = ~119 milioni di gettoni.
#  - al giorno 400.000 gettoni = 1,7 centesimi: un giorno normale ne usa ~20.000;
#  - in tutto 100 milioni = 4,20 $: si lascia un margine sul credito vero.
PREDEFINITE = {
    "chiave": "",
    "gettoni_al_giorno": 400_000,
    "gettoni_totali": 100_000_000,
    "per_giro": 150,             # bandi guardati al massimo in un giro
}

CARATTERI_STATO = 1500          # quanto testo del bando si manda
INTESTAZIONI = {"User-Agent": "MonitorBandi/0.1", "Content-Type": "application/json"}


class Tetto(Exception):
    """Si e' arrivati a un tetto di spesa: ci si ferma senza errore."""


# ---------------------------------------------------------------- impostazioni

def impostazioni():
    """La chiave sta in impostazioni.json (mai nel repository) o, su GitHub, nella
    variabile d'ambiente MONITOR_JEV_CHIAVE."""
    imp = dict(PREDEFINITE)
    percorso = BASE / "impostazioni.json"
    if percorso.exists():
        imp.update(json.loads(percorso.read_text(encoding="utf-8")).get("jev", {}))
    if os.environ.get("MONITOR_JEV_CHIAVE"):
        imp["chiave"] = os.environ["MONITOR_JEV_CHIAVE"]
    return imp


# ---------------------------------------------------------------- il conto

def _consumo(db, solo_oggi):
    sql = "SELECT COALESCE(SUM(gettoni),0), COALESCE(SUM(richieste),0) FROM consumo WHERE modello=?"
    arg = [NOME_CONSUMO]
    if solo_oggi:
        sql += " AND giorno=?"
        arg.append(date.today().isoformat())
    return db.execute(sql, arg).fetchone()


def segna(db, gettoni):
    oggi = date.today().isoformat()
    db.execute("INSERT INTO consumo (giorno,modello,richieste,gettoni) VALUES (?,?,1,?) "
               "ON CONFLICT(giorno,modello) DO UPDATE SET richieste=richieste+1, gettoni=gettoni+?",
               (oggi, NOME_CONSUMO, gettoni, gettoni))
    db.commit()


def conto(db):
    oggi, _ = _consumo(db, True)
    tutto, richieste = _consumo(db, False)
    return {"oggi": oggi, "totale": tutto, "richieste": richieste,
            "dollari": round(tutto * PREZZO_PER_GETTONE, 4)}


# ---------------------------------------------------------------- la chiamata

def chiedi(db, stato, domande, imp=None):
    """Una richiesta a Jev. Solleva Tetto se la spesa andrebbe oltre i limiti."""
    imp = imp or impostazioni()
    if not imp["chiave"]:
        raise Tetto("manca la chiave di Jev")
    corpo = json.dumps({"model": MODELLO, "state": stato, "questions": domande},
                       ensure_ascii=False)
    # Stima prudente PRIMA di spendere: 1 gettone ogni 3 caratteri.
    stima = len(corpo) // 3
    oggi, _ = _consumo(db, True)
    tutto, _ = _consumo(db, False)
    if oggi + stima > imp["gettoni_al_giorno"]:
        raise Tetto("tetto di oggi raggiunto (%d gettoni)" % oggi)
    if tutto + stima > imp["gettoni_totali"]:
        raise Tetto("tetto totale raggiunto (%d gettoni, %.2f $)"
                    % (tutto, tutto * PREZZO_PER_GETTONE))

    req = Request(API + "/systemone", data=corpo.encode("utf-8"),
                  headers=dict(INTESTAZIONI, Authorization="Bearer " + imp["chiave"]))
    for tentativo in (1, 2, 3):
        try:
            with urlopen(req, timeout=60) as r:
                risposta = json.loads(r.read())
            break
        except HTTPError as e:
            if e.code == 429 and tentativo < 3:
                attesa = e.headers.get("retry-after")
                time.sleep(min(float(attesa) if attesa else 5.0 * tentativo, 30))
                continue
            if e.code == 402:      # credito finito
                raise Tetto("credito di Jev esaurito")
            raise
    usati = risposta.get("usage", {}).get("input_tokens") or stima
    segna(db, usati)
    return risposta["answers"], usati


# ---------------------------------------------------------------- le domande

def stato_bando(b):
    """Titolo, ente e l'inizio del testo, ripulito: Jev peggiora con il rumore."""
    testo = " ".join((b.get("testo") or b.get("sommario") or "").split())
    return {"titolo": b.get("titolo") or "",
            "ente": b.get("ente") or "",
            "testo": testo[:CARATTERI_STATO]}


# Jev legge alla lettera: le condizioni vanno scritte per intero, casi limite compresi.
DOMANDA_BANDO = {
    "type": "noul",
    "instructions": ("Is this text an open call that someone can still apply to: a grant, "
                     "funding call, public notice for applications (bando, avviso), prize, "
                     "residency, open call, or a dance/theatre audition or casting?"),
    "criteria": {
        "true": ("It invites applications or candidates: it explains who can apply, how, "
                 "or by when. Also true for audition and casting announcements."),
        "false": ("It is news, an article, an event report, a ranking or list of winners "
                  "(graduatoria, esiti, vincitori), an approval of results, a decree that "
                  "does not open applications, a tender for suppliers (gara d'appalto), "
                  "or a generic page with no call."),
    },
}


def smista(db, imp=None):
    """Il passaggio del portiere: Jev guarda i bandi non ancora letti da Groq e non
    ancora passati di qui. Si ferma da solo ai tetti di spesa; ogni errore si
    stampa e basta, perche' senza portiere il giro funziona lo stesso."""
    imp = imp or impostazioni()
    if not imp["chiave"]:
        return 0
    elenco = [dict(zip(("id", "titolo", "ente", "testo", "sommario"), r)) for r in db.execute(
        "SELECT id,titolo,ente,testo,sommario FROM bandi "
        "WHERE analizzato_il IS NULL AND jev_il IS NULL AND archiviato = 0 "
        "AND COALESCE(testo, sommario, '') <> '' "
        "ORDER BY trovato_il DESC LIMIT ?", (imp["per_giro"],))]
    if not elenco:
        return 0
    adesso = date.today().isoformat()
    fatti = notizie = errori = 0
    for b in elenco:
        try:
            risposte, _ = chiedi(db, stato_bando(b), {"bando": DOMANDA_BANDO}, imp)
            errori = 0
        except Tetto as e:
            print("Jev: mi fermo, %s." % e)
            break
        except (HTTPError, URLError, OSError, KeyError, ValueError) as e:
            # Un errore su un bando non ferma il giro (il 29 set 2026 uno si e' fermato
            # dopo 25 bandi su 150): ci si arrende solo dopo tre errori di fila.
            errori += 1
            print("Jev: errore su un bando (%s)." % type(e).__name__)
            if errori >= 3:
                print("Jev non risponde: gli altri si leggono nell'ordine di sempre.")
                break
            time.sleep(3 * errori)
            continue
        p = risposte["bando"]["noul"]
        db.execute("UPDATE bandi SET jev_bando=?, jev_il=? WHERE id=?", (p, adesso, b["id"]))
        db.commit()
        fatti += 1
        notizie += p < 0.1
    c = conto(db)
    print("Jev: guardati %d bandi, %d sembrano notizie e vanno in fondo alla coda. "
          "Speso in tutto %.4f $ su 5 $." % (fatti, notizie, c["dollari"]))
    return fatti


if __name__ == "__main__":
    db = sqlite3.connect(DB)
    if "--conto" in sys.argv:
        c = conto(db)
        print("Jev: speso %d gettoni in %d richieste = %.4f $ (oggi %d gettoni)."
              % (c["totale"], c["richieste"], c["dollari"], c["oggi"]))
        print("Credito caricato: 5 $. Resta circa %.2f $." % (5 - c["dollari"]))
