#!/usr/bin/env python3
"""
Ricerca di nuove fonti - non le aggiunge, te le propone.

Come funziona, in tre passi:
  0. ENTI PENSATI PER I PROFILI. Il modello grande legge il racconto dei profili e
     propone gli enti che POTREBBERO servire, anche se oggi non hanno niente di
     aperto (chi pubblica una volta l'anno, chi fa call per artisti o residenze).
     Lui l'ha chiesto esplicitamente il 23 set 2026: non solo chi ha bandi attivi.
  1. RACCOLTA. Dalle pagine dei bandi gia' scaricati si prendono i collegamenti che
     escono verso altri enti (.gov.it, .it di regioni, comuni, fondazioni) e che
     sembrano portare a pagine di bandi. Sono indirizzi VERI, trovati su pagine vere:
     nessuno se li e' inventati.
  2. VERIFICA. Ogni candidato viene aperto davvero e si cerca il posto da sorvegliare:
     la pagina stessa se ha collegamenti a bandi, altrimenti la sua sezione «Bandi»,
     altrimenti il suo feed. Chi non risponde o non ha dove pubblicare, fuori.
  3. GIUDIZIO. Sui superstiti il modello dice se servono A QUESTI profili, ora o in
     futuro, e perche'. Costa poco: sono poche righe per candidato.

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
MAX_PROPOSTE = 10       # quante proposte tenere alla fine
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
    # Anche quelle che hai gia' scartato con «No»: riproportele ogni lunedi' sarebbe
    # solo rumore.
    for f in cfg["feed"] + cfg["siti"] + cfg.get("proposte", []) + [
            {"url": u} for u in cfg.get("proposte_scartate", [])]:
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


SISTEMA_PROPOSTE = """Conosci gli enti italiani ed europei che finanziano, premiano o
sostengono attivita' come quelle descritte. Rispondi SOLO con un oggetto JSON, senza
spiegazioni prima o dopo.

  "portali": elenco di 20 oggetti {"nome": ..., "url": ..., "perche": ...}
             "url"    = la pagina dove l'ente pubblica bandi, avvisi, call o premi;
                        se non la conosci, la home del sito (la sezione la cerco io);
             "perche" = una frase su cosa potrebbe offrire A QUESTI profili.

Non cercare solo chi ha un bando aperto oggi: servono gli enti che POTREBBERO
pubblicare qualcosa di utile, anche una volta l'anno o in futuro. Per esempio:
  - fondazioni bancarie, di comunita' e d'impresa attive nel loro territorio;
  - ministeri e direzioni generali del loro settore, agenzie e programmi regionali;
  - GAL, parchi, province, comuni e unioni di comuni della loro zona;
  - programmi europei e i loro sportelli italiani;
  - reti, festival, teatri e centri che fanno call per artisti, residenze, premi.

Regole: solo enti veri; indirizzi che credi esistano davvero; niente aggregatori
commerciali o consulenti a pagamento; niente enti che finanziano solo altre regioni."""


# Enti veri che finanziano cultura, sociale, giovani e Mezzogiorno, anche solo una volta
# l'anno. Il modello da solo ne conosce pochi e s'inventa i domini (il 23 set 2026, 7
# su 12 non esistevano): questi sono stati aperti uno per uno il 23 set 2026 (e ne sono
# stati tolti sei che non rispondevano, piu' funder35.it, diventato un sito di
# casino'). Costano zero gettoni, e passano dalla
# stessa verifica e dallo stesso giudizio di tutti gli altri. Chi non serve ai profili
# viene scartato li'. Si indica la home: la sezione dei bandi la cerca `verifica`.
SEMI = [
    ("https://spettacolo.cultura.gov.it", "MiC - Direzione generale Spettacolo: contributi a teatro, danza, musica, festival"),
    ("https://www.politichegiovanili.gov.it", "Dipartimento Politiche giovanili: bandi per progetti dei e con i giovani"),
    ("https://www.agenziagiovani.it", "Agenzia italiana per la Gioventu': Erasmus+ Gioventu' e Corpo europeo di solidarieta'"),
    ("https://www.fondazioneunipolis.org", "Fondazione Unipolis: culturability, rigenerazione di spazi con la cultura"),
    ("https://www.nuovoimaie.it", "Nuovo IMAIE: bandi per artisti interpreti, spettacoli e festival"),
    ("https://www.perchicrea.it", "SIAE Per Chi Crea: bandi per giovani autori e artisti"),
    ("https://www.italiafestival.it", "Italiafestival: rete dei festival italiani, bandi e opportunita'"),
    ("https://www.fondazioneterzopilastrointernazionale.it", "Fondazione Terzo Pilastro: cultura e sociale nel Mezzogiorno"),
    ("https://www.csvcalabriacentro.it", "CSV Calabria Centro: bandi e opportunita' per le associazioni del territorio"),
    ("https://www.8xmille.it", "8xmille Chiesa cattolica: progetti sociali e culturali"),
    ("https://www.anci.it", "ANCI: bandi per i Comuni e i loro partner, spesso su giovani e cultura"),
]


def descrivi_profili(cfg):
    """I profili in poche righe: caselle, parole e il racconto libero accorciato."""
    profili_txt = []
    for pr in cfg.get("profili", []):
        riga = "%s: %s, settori %s, territori %s" % (
            pr.get("nome"), pr.get("tipo_ente"),
            ", ".join(pr.get("settori") or []) or "qualsiasi",
            ", ".join(pr.get("regioni") or []) or "Italia")
        if pr.get("parole"):
            riga += ", parole che gli interessano: " + ", ".join(pr["parole"])
        profili_txt.append(riga)
        # Il racconto libero dice cose che le caselle non prevedono («residenze
        # artistiche nei borghi», «lavoriamo con le scuole»): e' proprio li' che
        # stanno gli enti giusti da cercare. Si manda accorciato, perche' qui
        # servono i portali, non la biografia.
        racconto = " ".join((pr.get("racconto") or "").split())
        if racconto:
            profili_txt.append("   cosa fanno davvero: " + racconto[:500])
    return chr(10).join(profili_txt) or "generico"


def candidati_dal_modello(cfg, chiave, modello, db):
    """Chiede al modello gli enti che potrebbero servire, per allargare oltre a cio'
    che gia' conosciamo. Restituisce {url: perche}.

    I modelli SI INVENTANO gli indirizzi: per questo ogni proposta passa comunque
    dalla verifica, che apre la pagina davvero. Qui si raccolgono solo candidati.
    """
    import intelligenza
    gia = [configurazione.normalizza(f.get("url")) for f in cfg["feed"] + cfg["siti"]]
    domanda = ("PROFILI DA SERVIRE:" + chr(10) + descrivi_profili(cfg)
               + chr(10) + chr(10) + "GIA CONOSCIUTI (non ripeterli):" + chr(10)
               + chr(10).join(gia[:40]))
    try:
        r, usati = intelligenza.chiedi(chiave, modello, SISTEMA_PROPOSTE, domanda, 4000)
        intelligenza.segna_consumo(db, modello, usati)
    except Exception as e:
        print("   il modello non ha risposto (%s)" % type(e).__name__)
        return {}
    # Un ente che seguiamo gia' non si ripropone, anche se il modello indica un'altra
    # pagina dello stesso sito: la sezione dei bandi e' comunque quella.
    dominio = lambda u: urlparse(u).netloc.lower().replace("www.", "")
    domini_noti = {dominio(f.get("url") or "") for f in cfg["feed"] + cfg["siti"]}
    fuori = {}
    for x in r.get("portali") or []:
        url = (x.get("url") or "").strip().rstrip("/")
        if (url.startswith("http") and dominio(url) not in domini_noti
                and not gia_conosciuto(cfg, url)):
            fuori[url] = (x.get("perche") or "").strip()
    return fuori


# ---------------------------------------------------------------- 2. verifica

def verifica(url):
    """Apre davvero l'indirizzo e cerca il punto giusto da tenere d'occhio.

    Restituisce (url da seguire, tipo, quanti bandi si vedono oggi, nota), oppure
    url None se l'indirizzo non risponde o non c'e' niente da sorvegliare.

    Prima si scartava tutto cio' che oggi aveva meno di tre bandi: cosi' si perdevano
    proprio gli enti che pubblicano una volta l'anno. Ora basta che ci sia un posto
    dove i bandi compariranno: una pagina con qualche collegamento a bandi (anche
    chiusi), la sezione «Bandi» del sito, oppure il suo feed.
    """
    import ripara
    doc = estrattore.leggi(url)
    radice = ripara._radice(url)
    if (not doc["testo"] and "errore HTTP 4" in (doc["nota"] or "")
            and url.rstrip("/") != radice):
        # Il modello indovina l'ente ma s'inventa il percorso (misurato il 23 set:
        # 8 indirizzi su 18 davano 404 su siti veri). Si riparte dalla home e la
        # sezione dei bandi la si cerca da se'.
        time.sleep(PAUSA)
        url = radice
        doc = estrattore.leggi(url)
    if not doc["testo"]:
        return None, None, 0, doc["nota"] or "non risponde"
    quanti = len(estrattore.link_interessanti(doc["link"], url, massimo=60))
    if quanti >= 3:
        return url, "sito", quanti, ""

    html, finale = ripara._html(url)
    for sezione in ripara.sezioni_bandi(html, finale)[:3]:
        time.sleep(PAUSA)
        n = ripara.funziona_come_pagina(sezione)
        if n:
            return sezione.rstrip("/"), "sito", n, ""
    if quanti:
        return url, "sito", quanti, ""
    for feed in ripara.feed_dichiarati(html, finale)[:2]:
        time.sleep(PAUSA)
        if ripara.funziona_come_feed(feed):
            return feed, "feed", 0, ""
    return None, None, 0, "raggiunta, ma senza una sezione di bandi da sorvegliare"


# ---------------------------------------------------------------- 3. giudizio

SISTEMA_SCOPERTA = """Guardi la pagina di un ente e decidi se vale la pena sorvegliarla
per i profili descritti: non conta solo se oggi c'e' un bando aperto, conta se
quell'ente pubblica, o potrebbe pubblicare in futuro, bandi, contributi, premi, call
o residenze a cui questi profili potrebbero candidarsi.
Rispondi SOLO con un oggetto JSON, senza spiegazioni prima o dopo.

  "utile"  : "ora"       se ci sono gia' bandi aperti adatti a loro;
             "in_futuro" se l'ente finanzia o premia cose come le loro, anche se oggi
                         non ha niente di aperto (bandi annuali, edizioni passate...);
             "no"        se non c'entra: altro settore, riservato a imprese o a enti
                         che loro non sono, pagina di sole notizie, oppure pagina di
                         gare d'appalto e forniture (non sono contributi).
                         E' "no" anche quando l'ente finanzia solo il SUO territorio e
                         quel territorio non e' il loro: una fondazione di Trento, di
                         Verona o di Firenze non da' soldi a chi sta in Calabria.
  "perche" : una frase concreta su cosa potrebbe offrire A LORO (non all'ente in generale).
  "ente"   : il nome dell'ente, come si legge nella pagina.
  "nome"   : un nome corto per l'elenco delle fonti, massimo 40 caratteri."""


def giudica(chiave, modello, url, titolo, testo, profili_txt):
    import intelligenza
    domanda = "PROFILI:%s%s%s%sINDIRIZZO: %s%sTITOLO: %s%s%sTESTO:%s%s" % (
        chr(10), profili_txt, chr(10), chr(10),
        url, chr(10), titolo, chr(10), chr(10), chr(10), testo[:2500])
    return intelligenza.chiedi(chiave, modello, SISTEMA_SCOPERTA, domanda, 2500)


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

    imp = intelligenza.impostazioni()
    profili_txt = descrivi_profili(cfg)

    # Prima gli enti pensati per i SUOI profili (anche senza bandi aperti oggi), poi
    # quelli trovati seguendo i collegamenti dei bandi gia' letti, che invece portano
    # solo a chi ha qualcosa di aperto adesso. Il modello si interpella sempre: prima
    # solo quando i candidati «veri» erano pochi, e la ricerca vedeva solo l'oggi.
    perche = {}
    if imp.get("chiave"):
        print("1. Chiedo al modello gli enti che potrebbero servire ai tuoi profili...")
        perche = candidati_dal_modello(cfg, imp["chiave"], imp["modello_grande"], db)
        print("   ne ha proposti %d (verranno comunque aperti uno per uno)" % len(perche))
    print("2. Raccolgo indirizzi dai bandi gia' letti...")
    dai_bandi = candidati_dai_bandi(db, cfg)
    print("   trovati %d candidati" % len(dai_bandi))
    dominio = lambda u: urlparse(u).netloc.lower().replace("www.", "")
    domini_noti = {dominio(f.get("url") or "")
                   for f in cfg["feed"] + cfg["siti"] + cfg.get("proposte", [])}
    for url, motivo in SEMI:
        if dominio(url) not in domini_noti and not gia_conosciuto(cfg, url):
            perche.setdefault(url, motivo)
    candidati = list(perche) + [c for c in dai_bandi if c not in perche]

    print("3. Apro ognuno e cerco dove l'ente pubblica i bandi...")
    buoni, esaminate, visti = [], 0, set()
    for url in candidati[:MAX_CANDIDATI]:
        esaminate += 1
        da_seguire, tipo, quanti, nota = verifica(url)
        print("   %s %-64s %s" % ("OK" if da_seguire else "--", url[:64],
                                  (da_seguire[:60] if da_seguire != url else "%d bandi visti" % quanti)
                                  if da_seguire else nota))
        chiave = configurazione.normalizza(da_seguire or "")
        if da_seguire and chiave not in visti and not gia_conosciuto(cfg, da_seguire):
            visti.add(chiave)
            buoni.append((url, da_seguire, tipo, quanti))
        time.sleep(PAUSA)
        if len(buoni) >= MAX_PROPOSTE + 4:     # margine per quelli che il giudizio scarta
            break

    if not buoni:
        print("Nessuna fonte nuova che valga la pena proporti.")
        if not prova:
            scrivi_esito(esaminate, [], "nessuno degli indirizzi aperti aveva un posto dove pubblica bandi")
        db.close()
        return 0

    print("4. Faccio giudicare al modello se servono davvero ai tuoi profili...")
    proposte = []
    for url, da_seguire, tipo, quanti in buoni:
        if len(proposte) >= MAX_PROPOSTE:
            break
        doc = estrattore.leggi(da_seguire) if tipo == "sito" else estrattore.leggi(url)
        nome = urlparse(da_seguire).netloc.replace("www.", "")
        ente, motivo, quando = "", perche.get(url, ""), "ora" if quanti >= 3 else "in_futuro"
        if imp.get("chiave") and not prova:
            try:
                r, usati = giudica(imp["chiave"], imp["modello_piccolo"], da_seguire,
                                   doc["titolo"], doc["testo"], profili_txt)
                intelligenza.segna_consumo(db, imp["modello_piccolo"], usati)
                if r.get("utile") not in ("ora", "in_futuro"):
                    print("   scartata dal modello: %s" % da_seguire[:60])
                    continue
                nome = (r.get("nome") or nome)[:40]
                ente = r.get("ente") or ""
                motivo = r.get("perche") or motivo
                quando = r.get("utile")
                time.sleep(60.0 * usati / imp["gettoni_al_minuto"])
            except Exception as e:
                print("   giudizio saltato (%s)" % type(e).__name__)
        proposte.append({"nome": nome, "url": da_seguire, "tipo": tipo, "ente": ente,
                         "a_chi_serve": motivo, "quando_serve": quando,
                         "bandi_visti": quanti, "trovata_il": date.today().isoformat()})

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
