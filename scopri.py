#!/usr/bin/env python3
"""
Ricerca di nuove fonti - non le aggiunge, te le propone.

Come funziona, in tre passi:
  1. RACCOLTA. Dalle pagine dei bandi gia' scaricati si prendono i collegamenti che
     escono verso altri enti (.gov.it, .it di regioni, comuni, fondazioni) e che
     sembrano portare a pagine di bandi. Sono indirizzi VERI, trovati su pagine vere:
     nessuno se li e' inventati.
  2. VERIFICA. Ogni candidato viene aperto davvero: risponde? contiene collegamenti
     che sembrano bandi? quanti? Chi non passa la prova viene scartato subito.
  3. GIUDIZIO. Sui superstiti il modello dice se sembra la pagina bandi di un ente
     serio e a chi si rivolge. Costa poco: sono poche righe per candidato.

Poi finiscono in `configurazione.json` sotto "proposte", e compaiono sulla pagina
con un pulsante per accettarle. **Niente viene aggiunto da solo**: se lo facesse,
in una settimana l'archivio sarebbe pieno di spazzatura e il modello brucerebbe
il budget a leggerla.

  python scopri.py            cerca e propone
  python scopri.py --prova    mostra cosa proporrebbe senza scrivere niente
"""
import json
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import configurazione
import estrattore

BASE = Path(__file__).parent
DB = BASE / "dati.db"

MAX_CANDIDATI = 40      # quanti indirizzi provare ad aprire in un giro
MAX_PROPOSTE = 8        # quante proposte tenere alla fine
PAUSA = 2.0

# Un ente che pubblica bandi sta quasi sempre su uno di questi domini.
DOMINI_BUONI = (".gov.it", ".regione.", "comune.", "provincia.", "camcom.",
                "fondazione", "consorzio", ".eu", "europa.eu", ".it")

# Parole che, nell'indirizzo, promettono una pagina di elenco e non un singolo bando.
PROMETTONO = ("/bandi", "/avvisi", "/contributi", "/finanziamenti", "/opportunita",
              "/incentivi", "/agevolazioni", "/bandi-e-avvisi", "/bandi-avvisi",
              "/concorsi", "/call", "/opportunities")

# Roba che non e' mai una fonte di bandi.
MAI = ("facebook.", "twitter.", "x.com", "instagram.", "linkedin.", "youtube.",
       "google.", "wikipedia.", "amazon.", "mailto:", "javascript:", ".pdf",
       "/privacy", "/cookie", "/accessibilit", "/login", "/contatti", "/faq")


def gia_conosciuto(cfg, url):
    n = configurazione.normalizza(url)
    for f in cfg["feed"] + cfg["siti"] + cfg.get("proposte", []):
        conosciuto = configurazione.normalizza(f.get("url"))
        if conosciuto and (n == conosciuto or n.startswith(conosciuto + "/")):
            return True
    return False


# ---------------------------------------------------------------- 1. raccolta

def candidati_dai_bandi(db, cfg, quante_pagine=25):
    """Riapre le pagine dei bandi recenti e guarda dove rimandano.

    Il testo salvato in archivio non ha piu' i collegamenti (li togliamo apposta per
    darlo al modello), quindi qui le pagine vanno riaperte davvero. Sono indirizzi
    trovati su pagine vere: nessuno se li e' inventati.
    """
    db.row_factory = sqlite3.Row
    righe = db.execute(
        "SELECT link FROM bandi WHERE archiviato=0 AND (aperto=1 OR aperto IS NULL) "
        "ORDER BY trovato_il DESC LIMIT ?", (quante_pagine,)).fetchall()

    visti, fuori = set(), []
    for r in righe:
        doc = estrattore.leggi(r["link"])
        time.sleep(PAUSA)
        dominio_suo = urlparse(r["link"]).netloc
        for href, _testo in doc.get("link", []):
            pulito = href.split("#")[0].split("?")[0].rstrip("/")
            basso = pulito.lower()
            if not pulito.startswith("http") or any(x in basso for x in MAI):
                continue
            if not any(d in basso for d in DOMINI_BUONI):
                continue
            if not any(x in basso for x in PROMETTONO):
                continue
            if urlparse(pulito).netloc == dominio_suo:
                continue          # non riproponiamo il sito da cui veniamo
            if pulito in visti or gia_conosciuto(cfg, pulito):
                continue
            visti.add(pulito)
            fuori.append(pulito)
        if len(fuori) >= MAX_CANDIDATI:
            break
    return fuori[:MAX_CANDIDATI]


def candidati_dalle_pagine_fonte(db, cfg):
    """Riapre le pagine di elenco che gia' conosciamo e guarda dove rimandano."""
    fuori, visti = [], set()
    for s in cfg["siti"][:6]:
        if not s.get("url") or not s.get("attivo", True):
            continue
        doc = estrattore.leggi(s["url"])
        time.sleep(PAUSA)
        for href, _ in doc.get("link", []):
            pulito = href.split("#")[0].rstrip("/")
            basso = pulito.lower()
            if any(x in basso for x in MAI) or not pulito.startswith("http"):
                continue
            if not any(p in basso for p in PROMETTONO):
                continue
            if urlparse(pulito).netloc == urlparse(s["url"]).netloc:
                continue
            if pulito in visti or gia_conosciuto(cfg, pulito):
                continue
            visti.add(pulito)
            fuori.append(pulito)
    return fuori


SISTEMA_PROPOSTE = """Conosci i portali italiani che pubblicano bandi e contributi.
Rispondi SOLO con un oggetto JSON, senza spiegazioni prima o dopo.

  "portali": elenco di 15 oggetti {"nome": ..., "url": ...} con l'indirizzo della
             PAGINA CHE ELENCA I BANDI (non la home del sito).

Regole:
  - solo enti veri: regioni, comuni, ministeri, camere di commercio, fondazioni
    bancarie e d'impresa, programmi europei;
  - indirizzi che credi esistano davvero, senza inventare percorsi improbabili;
  - niente aggregatori commerciali o siti di consulenza a pagamento."""


def candidati_dal_modello(cfg, chiave, modello, db):
    """Chiede al modello dei portali, per allargare oltre a cio' che gia' conosciamo.

    I modelli SI INVENTANO gli indirizzi: per questo ogni proposta passa comunque
    dalla verifica, che apre la pagina davvero. Qui si raccolgono solo candidati.
    """
    import intelligenza
    profili_txt = []
    for pr in cfg.get("profili", []):
        profili_txt.append("%s: %s, settori %s, territori %s" % (
            pr.get("nome"), pr.get("tipo_ente"),
            ", ".join(pr.get("settori") or []) or "qualsiasi",
            ", ".join(pr.get("regioni") or []) or "Italia"))
    gia = [configurazione.normalizza(f.get("url")) for f in cfg["feed"] + cfg["siti"]]
    domanda = ("PROFILI DA SERVIRE:" + chr(10) + (chr(10).join(profili_txt) or "generico")
               + chr(10) + chr(10) + "GIA CONOSCIUTI (non ripeterli):" + chr(10)
               + chr(10).join(gia[:25]))
    try:
        r, usati = intelligenza.chiedi(chiave, modello, SISTEMA_PROPOSTE, domanda, 1200)
        intelligenza.segna_consumo(db, modello, usati)
    except Exception as e:
        print("   il modello non ha risposto (%s)" % type(e).__name__)
        return []
    fuori = []
    for x in r.get("portali") or []:
        url = (x.get("url") or "").strip().rstrip("/")
        if url.startswith("http") and not gia_conosciuto(cfg, url):
            fuori.append(url)
    return fuori


# ---------------------------------------------------------------- 2. verifica

def verifica(url):
    """Apre davvero l'indirizzo. Restituisce (va bene, quanti bandi sembra avere, nota)."""
    doc = estrattore.leggi(url)
    if not doc["testo"]:
        return False, 0, doc["nota"] or "non risponde"
    quanti = len(estrattore.link_interessanti(doc["link"], url, massimo=60))
    if quanti < 3:
        return False, quanti, "raggiunta ma con pochi collegamenti a bandi"
    return True, quanti, doc["titolo"][:120] or ""


# ---------------------------------------------------------------- 3. giudizio

SISTEMA_SCOPERTA = """Guardi la pagina di un sito italiano e dici se e' una pagina che
elenca bandi, avvisi o contributi pubblicati da un ente.
Rispondi SOLO con un oggetto JSON, senza spiegazioni prima o dopo.

  "e_pagina_bandi" : true se elenca bandi/avvisi/contributi a cui ci si puo' candidare,
                     false se e' altro (notizie, servizi al cittadino, pagina vetrina).
  "ente"           : il nome dell'ente che la pubblica, come si legge nella pagina.
  "a_chi_serve"    : una frase breve su chi puo' trovarci qualcosa di utile.
  "nome"           : un nome corto per l'elenco delle fonti, massimo 40 caratteri."""


def giudica(chiave, modello, url, titolo, testo):
    import intelligenza
    domanda = "INDIRIZZO: %s%sTITOLO: %s%s%sTESTO:%s%s" % (
        url, chr(10), titolo, chr(10), chr(10), chr(10), testo[:2500])
    return intelligenza.chiedi(chiave, modello, SISTEMA_SCOPERTA, domanda, 500)


# ---------------------------------------------------------------- giro

def scrivi_esito(cfg_esaminate, proposte, nota=""):
    """Lascia scritto nel file com'e' andata, e aggiunge le proposte nuove.

    E' l'unico modo che ha la pagina di sapere che la ricerca e' finita e cosa e'
    saltato fuori: i registri di GitHub non li puo' leggere. Va scritto SEMPRE,
    anche quando non si trova niente, altrimenti la pagina aspetta per sempre.
    """
    cfg = configurazione.leggi_file()
    nuove = [p for p in proposte if not gia_conosciuto(cfg, p["url"])]
    if nuove:
        cfg["proposte"] = (cfg.get("proposte") or []) + nuove
    cfg["esito_scoperta"] = {
        "quando": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "esaminate": cfg_esaminate,
        "trovate": len(nuove),
        "nomi": [p["nome"] for p in nuove],
        "nota": nota,
    }
    configurazione.FILE.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return nuove


def giro(prova=False):
    import avvisi
    import intelligenza
    cfg = configurazione.leggi_file()
    db = sqlite3.connect(DB)

    print("1. Raccolgo indirizzi dai bandi gia' letti...")
    candidati = candidati_dai_bandi(db, cfg)
    print("   trovati %d candidati" % len(candidati))
    imp = intelligenza.impostazioni()
    if len(candidati) < 12 and imp.get("chiave"):
        print("2. Pochi: chiedo anche al modello quali portali conosce...")
        proposti = candidati_dal_modello(cfg, imp["chiave"], imp["modello_piccolo"], db)
        candidati += [c for c in proposti if c not in candidati]
        print("   ora sono %d (verranno comunque aperti uno per uno)" % len(candidati))

    print("3. Apro ognuno per vedere se e' davvero una pagina di bandi...")
    buoni, esaminate = [], 0
    for url in candidati[:MAX_CANDIDATI]:
        esaminate += 1
        ok, quanti, nota = verifica(url)
        print("   %s %-64s %s" % ("OK" if ok else "--", url[:64],
                                  ("%d collegamenti" % quanti) if ok else nota))
        if ok:
            buoni.append((url, quanti))
        time.sleep(PAUSA)
        if len(buoni) >= MAX_PROPOSTE:
            break

    if not buoni:
        print("Nessuna fonte nuova che valga la pena proporti.")
        if not prova:
            scrivi_esito(esaminate, [], "nessuno degli indirizzi aperti aveva bandi dentro")
        db.close()
        return 0

    print("4. Faccio giudicare le superstiti al modello...")
    proposte = []
    for url, quanti in buoni:
        doc = estrattore.leggi(url)
        nome = urlparse(url).netloc.replace("www.", "")
        ente, a_chi = "", ""
        if imp.get("chiave") and not prova:
            try:
                r, usati = giudica(imp["chiave"], imp["modello_piccolo"], url,
                                   doc["titolo"], doc["testo"])
                intelligenza.segna_consumo(db, imp["modello_piccolo"], usati)
                if not r.get("e_pagina_bandi"):
                    print("   scartata dal modello: %s" % url[:60])
                    continue
                nome = (r.get("nome") or nome)[:40]
                ente, a_chi = r.get("ente") or "", r.get("a_chi_serve") or ""
                time.sleep(60.0 * usati / imp["gettoni_al_minuto"])
            except Exception as e:
                print("   giudizio saltato (%s)" % type(e).__name__)
        proposte.append({"nome": nome, "url": url, "ente": ente,
                         "a_chi_serve": a_chi, "bandi_visti": quanti,
                         "trovata_il": date.today().isoformat()})

    if prova:
        print()
        for p in proposte:
            print("  %-40s %s" % (p["nome"][:40], p["url"][:70]))
            if p["a_chi_serve"]:
                print("     %s" % p["a_chi_serve"][:100])
        print("%sProva: %d proposte, non scritte." % (chr(10), len(proposte)))
        db.close()
        return len(proposte)

    nuove = scrivi_esito(esaminate, proposte)
    print("%sProposte scritte: %d. Le trovi sulla pagina, da accettare o scartare."
          % (chr(10), len(nuove)))

    avvisa(nuove)
    db.close()
    return len(nuove)


def avvisa(proposte):
    import avvisi
    imp = avvisi.carica()
    token = imp["telegram"]["token"].strip()
    chat = str(imp["telegram"]["chat_id"]).strip()
    if not token or not chat or not proposte:
        return
    from html import escape
    a_capo = chr(10)
    righe = ["<b>Ho trovato %d fonti nuove da proporti</b>" % len(proposte), ""]
    for p in proposte[:6]:
        righe.append("<b>%s</b>%s   %s" % (escape(p["nome"]), a_capo,
                                           escape(p["a_chi_serve"] or p["url"])[:140]))
    righe.append("")
    righe.append("Sono nella pagina, sotto «Fonti che controllo»: accetta quelle che ti "
                 "interessano. Non ne ho aggiunta nessuna da solo.")
    try:
        avvisi.manda_telegram(token, chat, a_capo.join(righe))
    except Exception:
        pass


if __name__ == "__main__":
    giro(prova="--prova" in sys.argv)
