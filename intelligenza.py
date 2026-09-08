#!/usr/bin/env python3
"""
Lettura dei bandi con Groq - Fase 5.

Fa le tre cose che le regole non sanno fare:
  - capire se e' davvero un bando ANCORA APERTO o solo una notizia;
  - tirare fuori scadenza, importo, requisiti e un riassunto;
  - dire se un certo profilo puo' davvero parteciparci.

REGOLA CHE COMANDA TUTTO: il piano gratuito di Groq da' circa 100.000 gettoni al
giorno PER MODELLO. Un bando intero ne pesa 6.000: mandandolo tutto si esaurirebbe
il gratis dopo 16 bandi. Quindi qui dentro:
  1. non si manda mai il testo intero, ma solo i paragrafi che contano (~1.200 gettoni);
  2. il modello piccolo fa la prima lettura di tutti, quello grande solo i finalisti;
  3. si tiene il conto dei gettoni e ci si ferma prima del limite: quel che avanza
     si legge domani. Nessun bando va perso, le scadenze sono a settimane.

Si usa cosi':
  python intelligenza.py --modelli   elenca i modelli che il tuo codice Groq puo' usare
  python intelligenza.py --prova     mostra cosa verrebbe mandato, SENZA mandarlo
  python intelligenza.py             legge i bandi non ancora letti
"""
import json
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import avvisi

BASE = Path(__file__).parent
DB = BASE / "dati.db"
API = "https://api.groq.com/openai/v1"

# Quanto testo si manda: circa 1.200 gettoni, non 6.000.
BUDGET_CARATTERI = 4500
GETTONI_PER_CARATTERE = 1 / 3.7   # stima buona per l'italiano

# I nomi dei modelli cambiano: Groq ha ritirato i llama-3.x dal piano gratuito.
# Per sapere quali sono validi oggi: python intelligenza.py --modelli
PREDEFINITE = {
    "chiave": "",
    "modello_piccolo": "openai/gpt-oss-20b",
    "modello_grande": "openai/gpt-oss-120b",
    "gettoni_al_giorno": 90000,    # sotto i 100.000 dichiarati, per stare larghi
    "gettoni_al_minuto": 7000,     # il limite vero e' 8.000: si tiene un margine
    "letture_per_giro": 40,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS consumo (
  giorno    TEXT NOT NULL,
  modello   TEXT NOT NULL,
  richieste INTEGER DEFAULT 0,
  gettoni   INTEGER DEFAULT 0,
  PRIMARY KEY (giorno, modello)
);
"""

COLONNE_NUOVE = {
    "contributo": "TEXT",
    "riassunto": "TEXT",
    "requisiti": "TEXT",
    "aperto": "INTEGER",
    "giudizio": "TEXT",
    "origine_scadenza": "TEXT",
    "analizzato_il": "TEXT",
}


def migra(db):
    db.executescript(SCHEMA)
    colonne = {r[1] for r in db.execute("PRAGMA table_info(bandi)")}
    for nome, tipo in COLONNE_NUOVE.items():
        if nome not in colonne:
            db.execute("ALTER TABLE bandi ADD COLUMN %s %s" % (nome, tipo))
    colonne_abb = {r[1] for r in db.execute("PRAGMA table_info(abbinamenti)")}
    if "llm_verdetto" not in colonne_abb:
        db.execute("ALTER TABLE abbinamenti ADD COLUMN llm_verdetto TEXT")
    if "llm_motivo" not in colonne_abb:
        db.execute("ALTER TABLE abbinamenti ADD COLUMN llm_motivo TEXT")
    db.commit()


def impostazioni():
    imp = avvisi.carica()
    groq = dict(PREDEFINITE)
    groq.update(imp.get("groq", {}))
    if "groq" not in imp:
        imp["groq"] = groq
        avvisi.salva(imp)
    return groq


# ---------------------------------------------------------------- il ritaglio

PESI_PARAGRAFO = [
    # Il piu' pesante: quanto puo' ottenere il singolo richiedente. Sta quasi sempre
    # nel PDF, in fondo, e senza questo gruppo il ritaglio lo buttava via.
    (4, ["importo del contributo", "misura del contributo", "entita del contributo",
         "contributo di", "contributo pari a", "contributo massimo", "richiesta di contributo",
         "non superiore a", "fino a un massimo", "massimo erogabile", "cofinanziamento"]),
    (3, ["scadenz", "entro il", "entro e non oltre", "termine per", "presentazione delle domand",
         "chiusura", "apertura", "finestra"]),
    (3, ["possono partecipare", "possono presentare", "destinatari", "beneficiari",
         "soggetti ammissibili", "requisiti", "chi puo", "sono ammessi", "ammissibilita"]),
    (2, ["importo", "dotazione", "stanziament", "risorse", "contributo massimo",
         "budget", "plafond", "euro"]),
    (2, ["oggetto", "finalit", "obiettiv", "il presente bando", "il presente avviso",
         "interventi ammissibili"]),
]

SEPARA = re.compile(r"\n\s*\n|\n(?=[A-ZÀ-Ü0-9])")


MARCATORE = "--- dalla pagina web ---"


def ritaglia(testo, budget=BUDGET_CARATTERI):
    """Tiene solo i paragrafi che contengono le risposte, non tutto il bando."""
    testo = (testo or "").strip()
    if len(testo) <= budget:
        return testo

    # Quando c'e' sia il PDF sia la pagina, ognuno tiene la sua quota: il PDF ha il
    # contributo per richiedente, la pagina ha quasi sempre la scadenza. Ritagliando
    # tutto insieme se ne perdeva sempre uno dei due.
    if MARCATORE in testo:
        documento, pagina = testo.split(MARCATORE, 1)
        return (ritaglia(documento.strip(), int(budget * 0.65)) + "\n\n" + MARCATORE + "\n\n"
                + ritaglia(pagina.strip(), int(budget * 0.35)))

    # I PDF arrivano spesso come un unico blocco lunghissimo: se non lo si spezza,
    # ogni "paragrafo" supera da solo il budget e viene scartato tutto.
    paragrafi = []
    for grezzo in SEPARA.split(testo):
        grezzo = grezzo.strip()
        if len(grezzo) <= 40:
            continue
        for i in range(0, len(grezzo), 700):
            pezzo = grezzo[i:i + 700]
            if len(pezzo) > 40:
                paragrafi.append(pezzo)
    if not paragrafi:
        return testo[:budget]

    punteggiati = []
    for i, p in enumerate(paragrafi):
        basso = p.lower()
        punti = sum(peso for peso, parole in PESI_PARAGRAFO
                    if any(k in basso for k in parole))
        if i < 2:
            punti += 5  # l'inizio dice quasi sempre di cosa si tratta
        punteggiati.append((punti, i, p))

    scelti, usati = [], 0
    for punti, i, p in sorted(punteggiati, key=lambda x: (-x[0], x[1])):
        if punti == 0 or usati + len(p) > budget:
            continue
        scelti.append((i, p))
        usati += len(p)
    scelti.sort()
    return "\n\n".join(p for _, p in scelti) or testo[:budget]


def stima_gettoni(testo):
    return int(len(testo) * GETTONI_PER_CARATTERE)


# ---------------------------------------------------------------- il conto dei gettoni

def consumo_oggi(db, modello):
    oggi = date.today().isoformat()
    r = db.execute("SELECT gettoni FROM consumo WHERE giorno=? AND modello=?",
                   (oggi, modello)).fetchone()
    return r[0] if r else 0


def segna_consumo(db, modello, gettoni):
    oggi = date.today().isoformat()
    db.execute("INSERT INTO consumo (giorno,modello,richieste,gettoni) VALUES (?,?,1,?) "
               "ON CONFLICT(giorno,modello) DO UPDATE SET richieste=richieste+1, gettoni=gettoni+?",
               (oggi, modello, gettoni, gettoni))
    db.commit()


# ---------------------------------------------------------------- Groq

# Senza un nome nell'intestazione, Cloudflare davanti a Groq risponde 403 (errore 1010)
# a qualunque richiesta fatta da Python. Non e' la chiave sbagliata: e' questo.
INTESTAZIONI = {"User-Agent": "MonitorBandi/0.1"}


def elenca_modelli(chiave):
    req = Request(API + "/models",
                  headers=dict(INTESTAZIONI, Authorization="Bearer " + chiave))
    with urlopen(req, timeout=30) as r:
        return [m["id"] for m in json.loads(r.read())["data"]]


def chiedi(chiave, modello, sistema, domanda, gettoni_max=700):
    corpo = json.dumps({
        "model": modello,
        "messages": [{"role": "system", "content": sistema},
                     {"role": "user", "content": domanda}],
        "temperature": 0,
        # I modelli gpt-oss "ragionano" prima di rispondere. Due cose imparate a caro prezzo:
        # 1) con lo sforzo basso il costo scende da 1853 a 1162 gettoni, stessa risposta;
        # 2) il tetto dei gettoni deve essere LARGO: se taglia il ragionamento, il JSON
        #    resta a meta' e Groq risponde 400 "json_validate_failed".
        "reasoning_effort": "low",
        "max_completion_tokens": max(gettoni_max, 2500),
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = Request(API + "/chat/completions", data=corpo, headers=dict(
        INTESTAZIONI, Authorization="Bearer " + chiave,
        **{"Content-Type": "application/json"}))

    # Groq limita anche i gettoni AL MINUTO. Quando lo si supera risponde 429 e dice
    # quanto aspettare: si aspetta e si riprova una volta, invece di buttare via il giro.
    for tentativo in (1, 2):
        try:
            with urlopen(req, timeout=120) as r:
                risposta = json.loads(r.read())
            break
        except HTTPError as e:
            if e.code == 429 and tentativo == 1:
                attesa = e.headers.get("retry-after")
                pausa = float(attesa) if attesa else 20.0
                print("    Groq chiede di rallentare: aspetto %.0f secondi." % pausa)
                time.sleep(min(pausa + 2, 70))
                continue
            raise

    testo = risposta["choices"][0]["message"]["content"]
    usati = risposta.get("usage", {}).get("total_tokens", 0)
    return json.loads(testo), usati


# ---------------------------------------------------------------- le domande

SISTEMA_LETTURA = """Sei un assistente che legge bandi e avvisi pubblici italiani.
Rispondi SOLO con un oggetto JSON, senza spiegazioni prima o dopo.

Non inventare mai nulla: se un dato non c'e' nel testo, metti null.
In particolare NON dedurre la scadenza da date generiche: deve essere il termine
per presentare la domanda, scritto nel testo.

Campi richiesti:
  "e_un_bando"  : true se e' un bando/avviso/concorso a cui ci si puo' candidare;
                  false se e' una notizia, un articolo, una graduatoria o un resoconto.
  "aperto"      : true se le domande si possono ancora presentare, false se e' chiuso
                  o gia' assegnato, null se non si capisce.
  "scadenza"    : data in formato AAAA-MM-GG, oppure null.
  "importo"     : la dotazione COMPLESSIVA del bando, cioe' quanto mette a disposizione
                  l'ente in tutto, come testo (es. "3.000.000 €"), oppure null.
  "contributo"  : quanto puo' ottenere al massimo UN SINGOLO richiedente per il suo
                  progetto, come testo (es. "fino a 50.000 €", "80% delle spese fino a
                  30.000 €"), oppure null se il bando non lo dice. Non ripetere qui la
                  dotazione complessiva: se c'e' solo quella, metti null.
  "settori"     : da 1 a 4 parole sull'ambito (es. ["cultura", "teatro"]).
  "destinatari" : elenco breve di chi puo' partecipare, come scritto nel testo
                  (es. ["associazioni di promozione sociale", "ODV iscritte al RUNTS"]).
  "riassunto"   : massimo 100 parole, in italiano semplice, su cosa finanzia il bando."""

SISTEMA_GIUDIZIO = """Valuti se un soggetto puo' partecipare a un bando.
Rispondi SOLO con un oggetto JSON, senza spiegazioni prima o dopo.

  "verdetto" : "si" se il profilo rientra chiaramente tra i destinatari,
               "forse" se il testo non basta per escluderlo,
               "no" se il profilo e' escluso (tipo di ente sbagliato, territorio sbagliato).
  "motivo"   : una frase breve in italiano, che cita il punto del bando da cui si capisce."""


def domanda_lettura(b):
    return "TITOLO: %s\nENTE: %s\n\nTESTO:\n%s" % (
        b["titolo"], b["ente"] or "sconosciuto", ritaglia(b["testo"]))


def domanda_giudizio(b, profilo):
    return ("PROFILO\n  tipo: %s\n  settori: %s\n  territori: %s\n\n"
            "BANDO\n  titolo: %s\n  destinatari: %s\n  riassunto: %s") % (
        profilo.get("tipo_ente") or "non specificato",
        ", ".join(profilo.get("settori", [])) or "qualsiasi",
        ", ".join(profilo.get("regioni", [])) or "qualsiasi",
        b["titolo"],
        "; ".join(json.loads(b["requisiti"] or "[]")) or "non indicati",
        (b["riassunto"] or "")[:800])


# ---------------------------------------------------------------- primo passaggio

def da_leggere(db, limite):
    db.row_factory = sqlite3.Row
    return [dict(r) for r in db.execute(
        "SELECT id,titolo,ente,testo FROM bandi "
        "WHERE analizzato_il IS NULL AND testo IS NOT NULL AND testo <> '' "
        "AND archiviato = 0 ORDER BY trovato_il DESC LIMIT ?", (limite,))]


def leggi_bandi(db, prova=False):
    """Primo passaggio: il modello piccolo legge ogni bando UNA volta sola."""
    imp = impostazioni()
    migra(db)
    modello = imp["modello_piccolo"]
    elenco = da_leggere(db, imp["letture_per_giro"])
    if not elenco:
        print("Nessun bando nuovo da leggere.")
        return 0

    speso = consumo_oggi(db, modello)
    letti = 0
    for b in elenco:
        domanda = domanda_lettura(b)
        # Misurato sui bandi veri: ~1.160 gettoni in tutto, di cui ~215 di risposta.
        costo = stima_gettoni(SISTEMA_LETTURA + domanda) + 250
        if speso + costo > imp["gettoni_al_giorno"]:
            print("Limite giornaliero vicino: mi fermo, gli altri li leggo domani.")
            break

        if prova:
            print("=" * 66)
            print(b["titolo"][:70])
            print("  testo intero: %d caratteri (~%d gettoni)"
                  % (len(b["testo"]), stima_gettoni(b["testo"])))
            print("  ritagliato  : %d caratteri (~%d gettoni)  -> risparmio %d%%"
                  % (len(ritaglia(b["testo"])), stima_gettoni(ritaglia(b["testo"])),
                     100 - 100 * len(ritaglia(b["testo"])) // max(len(b["testo"]), 1)))
            letti += 1
            speso += costo
            continue

        try:
            risposta, usati = chiedi(imp["chiave"], modello, SISTEMA_LETTURA, domanda)
        except HTTPError as e:
            print("  Groq ha risposto %s: mi fermo qui." % e.code)
            break
        except Exception as e:
            print("  errore su «%s»: %s" % (b["titolo"][:40], type(e).__name__))
            continue

        speso += usati or costo
        segna_consumo(db, modello, usati or costo)
        salva_lettura(db, b["id"], risposta)
        letti += 1
        # Si aspetta in proporzione a quanto si e' appena consumato, per restare
        # sotto il limite al minuto invece di sbatterci contro e prendere un 429.
        time.sleep(60.0 * (usati or costo) / imp["gettoni_al_minuto"])

    if prova:
        print("=" * 66)
        print("\nProva: %d bandi pronti da leggere, ~%d gettoni in tutto." % (letti, speso))
    else:
        print("Bandi letti: %d   Gettoni usati oggi su %s: %d / %d"
              % (letti, modello, speso, imp["gettoni_al_giorno"]))
    return letti


def salva_lettura(db, ident, r):
    scadenza = r.get("scadenza")
    if scadenza and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(scadenza)):
        scadenza = None  # se non e' una data vera, meglio niente che sbagliata

    # Il calendario batte il modello: capitava che desse per aperto un bando
    # scaduto nel 2024, perche' il testo della pagina non dice che e' chiuso.
    if scadenza and scadenza < date.today().isoformat():
        r = dict(r, aperto=False)

    db.execute(
        "UPDATE bandi SET aperto=?, scadenza=COALESCE(?,scadenza), origine_scadenza=?, "
        # L'importo del modello SOSTITUISCE quello trovato dalle regole: la regola
        # pescava la cifra della prima edizione elencata nella pagina, non di questa.
        "importo=COALESCE(?,importo), importo_num=CASE WHEN ? IS NULL THEN importo_num END, "
        "contributo=?, requisiti=?, riassunto=?, analizzato_il=? WHERE id=?",
        (1 if r.get("aperto") and r.get("e_un_bando") else 0,
         scadenza, "letto dal modello" if scadenza else None,
         r.get("importo"), r.get("importo"), r.get("contributo"),
         json.dumps(r.get("destinatari") or [], ensure_ascii=False),
         (r.get("riassunto") or "").strip() or None,
         datetime.now(timezone.utc).isoformat(timespec="seconds"), ident))
    db.commit()


# ---------------------------------------------------------------- secondo passaggio

def giudica_finalisti(db, prova=False, soglia=40):
    """Secondo passaggio: il modello grande solo sui pochi gia' promossi dal filtro."""
    import profili as mod_profili
    imp = impostazioni()
    migra(db)
    modello = imp["modello_grande"]
    db.row_factory = sqlite3.Row

    candidati = [dict(r) for r in db.execute(
        "SELECT b.*, a.profilo_id, a.punteggio FROM abbinamenti a "
        "JOIN bandi b ON b.id = a.bando_id "
        "WHERE a.llm_verdetto IS NULL AND a.punteggio >= ? AND b.aperto = 1 "
        "AND b.analizzato_il IS NOT NULL AND b.archiviato = 0 "
        "ORDER BY a.punteggio DESC LIMIT 20", (soglia,))]
    if not candidati:
        print("Nessun finalista da giudicare.")
        return 0

    per_id = {p["id"]: p for p in mod_profili.leggi_profili(db)}
    speso = consumo_oggi(db, modello)
    fatti = 0
    for b in candidati:
        profilo = per_id.get(b["profilo_id"])
        if not profilo:
            continue
        domanda = domanda_giudizio(b, profilo)
        costo = stima_gettoni(SISTEMA_GIUDIZIO + domanda) + 200
        if speso + costo > imp["gettoni_al_giorno"]:
            print("Limite giornaliero vicino sul modello grande: mi fermo.")
            break

        if prova:
            print("-" * 66)
            print(b["titolo"][:70])
            print(domanda[:500])
            fatti += 1
            speso += costo
            continue

        try:
            risposta, usati = chiedi(imp["chiave"], modello, SISTEMA_GIUDIZIO, domanda, 300)
        except Exception as e:
            print("  errore sul giudizio: %s" % type(e).__name__)
            break
        speso += usati or costo
        segna_consumo(db, modello, usati or costo)
        db.execute("UPDATE abbinamenti SET llm_verdetto=?, llm_motivo=? "
                   "WHERE bando_id=? AND profilo_id=?",
                   (risposta.get("verdetto"), risposta.get("motivo"),
                    b["id"], b["profilo_id"]))
        db.commit()
        fatti += 1
        time.sleep(60.0 * (usati or costo) / imp["gettoni_al_minuto"])

    print("Giudizi %s: %d" % ("simulati" if prova else "dati", fatti))
    return fatti


# ---------------------------------------------------------------- giro completo

def giro(prova=False):
    db = sqlite3.connect(DB)
    migra(db)
    imp = impostazioni()
    if not imp["chiave"] and not prova:
        print("Manca il codice Groq: eseguo solo la prova.")
        print("Si prende gratis su console.groq.com/keys e si incolla in impostazioni.json.\n")
        prova = True
    leggi_bandi(db, prova)
    print()
    giudica_finalisti(db, prova)
    db.close()


if __name__ == "__main__":
    if "--modelli" in sys.argv:
        print("\n".join(elenca_modelli(impostazioni()["chiave"])))
    else:
        giro(prova="--prova" in sys.argv)
