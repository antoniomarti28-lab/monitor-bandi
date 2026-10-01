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
import os
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
    # Limite vero del piano gratuito (console.groq.com/docs/rate-limits, 29 set 2026):
    # 200.000 gettoni al giorno PER MODELLO, 8.000 al minuto. Si tiene un margine.
    "gettoni_al_giorno": 190000,   # 140.000 fino al 29 set 2026
    "gettoni_al_minuto": 7000,
    # 60 fino al 29 set 2026. Con 148 bandi in coda leggeva solo il modello piccolo;
    # ora quando lui finisce i gettoni continua il grande (vedi `leggi_bandi`).
    "letture_per_giro": 160,
    # Il grande legge solo fino a lasciare questa scorta per i giudizi di «puoi
    # parteciparci?», che contano di piu'. Di solito ne usano 5.000-25.000; il 28 set,
    # con le parole di un profilo cambiate, 121.000 (e quel giorno il grande non legge).
    "scorta_giudizi": 70000,
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
    "tipo_aiuto": "TEXT",
    "contributo": "TEXT",
    "riassunto": "TEXT",
    "requisiti": "TEXT",
    "aperto": "INTEGER",
    "giudizio": "TEXT",
    "origine_scadenza": "TEXT",
    "analizzato_il": "TEXT",
    # Dove vale il bando, letto dal documento intero. Le regole non ce la fanno: nei due
    # bandi di Fondazione Cariplo la parola «Lombardia» compariva solo dopo il
    # tremillesimo carattere, e sono arrivate due notifiche per bandi di un'altra regione.
    "territorio": "TEXT",
    # Per le audizioni e i lavori da artista (profilo «Mati», 28 set 2026): chi cerca,
    # dove, e soprattutto se si viene pagati. Per un bando normale restano vuoti.
    "compagnia": "TEXT",
    "citta": "TEXT",
    "ingaggio": "TEXT",     # pagato | non pagato | stage | a pagamento
    "lavoro": "TEXT",       # in compagnia | lavoro singolo
    # Il portiere (jev.py, 29 set 2026): probabilita' che sia davvero un bando a cui
    # candidarsi e non una notizia. Decide solo l'ORDINE di lettura di Groq.
    "jev_bando": "REAL",
    "jev_il": "TEXT",
}

VALORI_INGAGGIO = ("pagato", "non pagato", "stage", "a pagamento")
VALORI_LAVORO = ("in compagnia", "lavoro singolo")


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
    # 1 = il giudizio c'e' ma va rifatto con le istruzioni nuove. Fino a quel momento resta
    # quello vecchio (non si azzera: i bandi non devono sparire dalla pagina per qualche giorno).
    if "rigiudica" not in colonne_abb:
        db.execute("ALTER TABLE abbinamenti ADD COLUMN rigiudica INTEGER DEFAULT 0")
    db.commit()
    migrazioni_una_tantum(db)


def migrazioni_una_tantum(db):
    """Cose da rifare UNA volta dopo aver migliorato il lettore o il giudice.

    Girano solo su GitHub: in locale toccherebbero una copia dell'archivio che poi non
    coincide piu' con quella buona (regola d'oro: l'archivio vero e' quello su GitHub).
    Ognuna si segna in `telegram_stato` e non si ripete."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    db.executescript("CREATE TABLE IF NOT EXISTS telegram_stato "
                     "(chiave TEXT PRIMARY KEY, valore TEXT);")

    def da_fare(chiave):
        return db.execute("SELECT 1 FROM telegram_stato WHERE chiave=?", (chiave,)).fetchone() is None

    def fatta(chiave, n):
        db.execute("INSERT OR REPLACE INTO telegram_stato (chiave,valore) VALUES (?,?)", (chiave, str(n)))
        db.commit()

    # 30 set 2026: il ritaglio non conosceva le parole inglesi e buttava il paragrafo con la
    # scadenza («Perform Europe»). I bandi aperti, lunghi e senza scadenza si rileggono.
    k = "migr_2026-09-30_rilettura_scadenze"
    if da_fare(k):
        n = db.execute(
            "UPDATE bandi SET analizzato_il=NULL WHERE aperto=1 AND archiviato=0 "
            "AND scadenza IS NULL AND length(testo) > ?", (BUDGET_CARATTERI,)).rowcount
        fatta(k, n)
        print("Da rileggere per la scadenza: %d bandi." % n)

    # 30 set 2026: il giudice scambiava i beneficiari con chi presenta la domanda e leggeva
    # «compagnie teatrali» come escluso per un'associazione che fa teatro. Le istruzioni sono
    # cambiate: i «no» di Garage68 e Labo Art sui bandi ancora aperti si rigiudicano.
    k = "migr_2026-09-30_rigiudizio_no"
    if da_fare(k):
        n = db.execute(
            "UPDATE abbinamenti SET rigiudica=1 "
            "WHERE llm_verdetto='no' AND profilo_id IN (1,2) AND avvisato=0 AND bando_id IN ("
            "SELECT id FROM bandi WHERE aperto=1 AND archiviato=0 "
            "AND (scadenza IS NULL OR scadenza >= date('now')))").rowcount
        fatta(k, n)
        print("Da rigiudicare con le istruzioni nuove: %d abbinamenti." % n)

    # 1 ott 2026: il giudice guardava la forma giuridica ma non l'ambito del progetto, e
    # dava «si» a un bando di conformita' normativa per un'associazione culturale. Il
    # controllo dell'ambito e' nelle istruzioni nuove: anche «si» e «forse» si rifanno. Chi
    # e' gia' stato avvisato non riceve un secondo avviso, ma se il giudizio cambia in «no»
    # sparisce dai bandi a cui si puo' partecipare.
    k = "migr_2026-10-01_rigiudizio_si_forse"
    if da_fare(k):
        n = db.execute(
            "UPDATE abbinamenti SET rigiudica=1 "
            "WHERE llm_verdetto IN ('si','forse') AND profilo_id IN (1,2) AND bando_id IN ("
            "SELECT id FROM bandi WHERE aperto=1 AND archiviato=0 "
            "AND (scadenza IS NULL OR scadenza >= date('now')))").rowcount
        fatta(k, n)
        print("Da rigiudicare anche i si e i forse: %d abbinamenti." % n)


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
         "chiusura", "apertura", "finestra",
         # Pagine in inglese, francese, spagnolo, tedesco: fino al 30 set 2026 solo le
         # parole italiane davano punti, e in «Perform Europe» il paragrafo con
         # «submit ... before 22 October 2026» valeva zero e veniva buttato: la pagina
         # aveva la scadenza e l'app non la riportava.
         "deadline", "closing date", "closes on", "close on", "apply by", "apply before",
         "submit", "before 2", "until 2", "due date", "call opens", "application period",
         "conditions of the call", "how to apply", "timeline",
         "date limite", "date de cloture", "fecha limite", "plazo", "convocatoria abierta",
         "bewerbungsfrist", "einsendeschluss", "anmeldeschluss"]),
    (3, ["possono partecipare", "possono presentare", "destinatari", "beneficiari",
         "soggetti ammissibili", "requisiti", "chi puo", "sono ammessi", "ammissibilita",
         "eligib", "who can apply", "applicants", "open to", "criteria",
         "admissible", "elegible", "pueden presentar", "teilnahmeberechtigt"]),
    (2, ["importo", "dotazione", "stanziament", "risorse", "contributo massimo",
         "budget", "plafond", "euro", "grant", "funding", "maximum", "subvenci"]),
    (2, ["oggetto", "finalit", "obiettiv", "il presente bando", "il presente avviso",
         "interventi ammissibili"]),
]

SEPARA = re.compile(r"\n\s*\n|\n(?=[A-ZÀ-Ü0-9])")


# Una data di quest'anno o del prossimo, scritta con il nome del mese (in piu' lingue) o
# come 22/10/2026: un paragrafo che ne ha una e' il candidato naturale per la scadenza.
MESI = (r"gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|"
        r"novembre|dicembre|january|february|march|april|may|june|july|august|september|"
        r"october|november|december|janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|"
        r"octobre|d[eé]cembre|enero|febrero|abril|mayo|junio|julio|septiembre|octubre|"
        r"noviembre|diciembre|januar|februar|m[aä]rz|juni|juli|oktober|dezember")
DATA_VICINA = re.compile(
    r"(?i)\b(?:%s)\b[^\n]{0,12}\b(?:2026|2027)\b|\b\d{1,2}[/.-]\d{1,2}[/.-](?:2026|2027)\b" % MESI)


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
        if DATA_VICINA.search(p):
            punti += 3
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
        #    resta a meta' e Groq risponde 400 "json_validate_failed". Successo di nuovo
        #    con 2500 quando ho aggiunto un campo alla risposta: alzato a 4000. Non costa
        #    nulla tenerlo alto, si paga solo quello che il modello scrive davvero.
        "reasoning_effort": "low",
        "max_completion_tokens": max(gettoni_max, 4000),
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

CAMPI_ARTISTA = """Solo se e' un'audizione, un casting o un'offerta di lavoro per artisti
(altrimenti metti null in tutti e quattro):
  "compagnia"   : il nome della compagnia, del coreografo o di chi cerca, come scritto.
  "citta"       : la citta' dove si lavora (o, se manca, dove si fa l'audizione);
                  se e' all'estero aggiungi il paese, es. "Salisburgo, Austria".
  "ingaggio"    : UNA fra queste parole esatte:
                  "pagato"      contratto o compenso per l'artista;
                  "non pagato"  lavoro gratuito, a titolo volontario o solo rimborso spese;
                  "stage"       tirocinio, apprendistato, junior company, formazione in
                                compagnia senza vero contratto;
                  "a pagamento" e' l'artista a dover pagare (workshop-audition, quota
                                di iscrizione, corso travestito da audizione).
                  null se il testo non lo dice.
  "lavoro"      : "in compagnia" se si entra in una compagnia o in una produzione con
                  piu' date o una stagione; "lavoro singolo" se e' un ingaggio isolato
                  (una serata, un evento, un video, uno spot, una singola performance)."""

SISTEMA_LETTURA = """Sei un assistente che legge bandi e avvisi pubblici italiani.
Rispondi SOLO con un oggetto JSON, senza spiegazioni prima o dopo.

Non inventare mai nulla: se un dato non c'e' nel testo, metti null.
In particolare NON dedurre la scadenza da date generiche: deve essere il termine
per presentare la domanda, scritto nel testo.

Il testo puo' anche essere un'AUDIZIONE o un casting (una compagnia che cerca danzatori,
attori, musicisti), in italiano o in un'altra lingua: per un artista vale come un bando.

Campi richiesti:
  "e_un_bando"  : true se e' un bando/avviso/concorso a cui ci si puo' candidare, oppure
                  un'audizione, un casting o una open call per artisti;
                  false se e' una notizia, un articolo, una graduatoria o un resoconto.
  "aperto"      : true se le domande si possono ancora presentare, false se e' chiuso
                  o gia' assegnato, null se non si capisce.
  "scadenza"    : data in formato AAAA-MM-GG, oppure null. Il testo puo' essere in
                  inglese, francese, spagnolo o tedesco: «submit before 22 October 2026,
                  23:59» vale 2026-10-22, «deadline: 3 March» con l'anno del bando vale
                  quella data. Cerca in particolare sotto titoli come «Conditions of the
                  call», «Deadline», «How to apply», «Scadenza». Per un'audizione: il
                  termine per mandare la candidatura; se non c'e', il giorno dell'audizione.
  "importo"     : la dotazione COMPLESSIVA del bando, cioe' quanto mette a disposizione
                  l'ente in tutto, come testo (es. "3.000.000 €"), oppure null.
  "contributo"  : quanto puo' ottenere al massimo UN SINGOLO richiedente per il suo
                  progetto, come testo (es. "fino a 50.000 €", "80% delle spese fino a
                  30.000 €"), oppure null se il bando non lo dice. Non ripetere qui la
                  dotazione complessiva: se c'e' solo quella, metti null.
  "tipo_aiuto"  : che forma ha l'aiuto, UNA sola fra queste parole esatte:
                  "fondo perduto" (non si restituisce), "prestito agevolato",
                  "voucher", "premio", "servizi" (consulenza, spazi, formazione),
                  "misto", "audizione" (una compagnia o produzione cerca artisti).
                  Se dal testo non si capisce, metti null.
  "settori"     : da 1 a 4 parole sull'ambito (es. ["cultura", "teatro"]).
  "destinatari" : elenco breve di CHI PUO' PRESENTARE LA DOMANDA, come scritto nel testo
                  (es. ["associazioni di promozione sociale", "ODV iscritte al RUNTS"],
                  o per un'audizione ["danzatrici 20-30 anni", "tecnica contemporanea"]).
                  Attenzione: non scambiare chi presenta la domanda con chi beneficia dei
                  progetti. In un bando che finanzia progetti per ragazzi, anziani o
                  famiglie, i ragazzi sono i beneficiari: chi fa domanda sono gli enti
                  (associazioni, cooperative, partenariati). Se il testo non dice che enti
                  possono candidarsi, scrivi i beneficiari ma aggiungi «enti proponenti non
                  specificati».
  "territorio"  : DOVE vale il bando, cioe' dove devono avere sede o operare i
                  partecipanti. Elenco di nomi di regioni italiane (es. ["Lombardia"],
                  ["Calabria", "Puglia"]), oppure ["Italia"] se vale su tutto il
                  territorio nazionale, oppure ["Europa"] per i programmi europei.
                  Per un bando di un altro paese metti il nome del paese in italiano
                  (["Spagna"], ["Regno Unito"], ["Francia"]) e, se e' di una citta', il
                  paese e la citta' (["Spagna", "Barcellona"]).
                  Attenzione: molti enti finanziano SOLO la propria zona anche quando
                  non lo ripetono a ogni riga. Metti null solo se dal testo non si
                  capisce proprio. Per un'audizione metti null, a meno che il testo
                  non chieda espressamente di risiedere in un certo posto.
  "riassunto"   : massimo 100 parole, in italiano semplice, su cosa finanzia il bando;
                  per un'audizione: quale compagnia, che genere di danza o spettacolo,
                  per quale produzione o contratto, dove e quando, e i requisiti.
""" + CAMPI_ARTISTA

# Gli stessi quattro campi servono anche da soli, per completare le audizioni gia'
# lette prima che esistessero (vedi `completa_audizioni`).
SISTEMA_ARTISTA = """Leggi un annuncio per artisti (audizione, casting, offerta di lavoro).
Rispondi SOLO con un oggetto JSON, senza spiegazioni prima o dopo. Non inventare: se un
dato non c'e' nel testo, metti null.

""" + CAMPI_ARTISTA

SISTEMA_GIUDIZIO = """Valuti se un soggetto puo' partecipare a un bando.
Rispondi SOLO con un oggetto JSON, senza spiegazioni prima o dopo.

Se il profilo racconta cosa fa a parole sue, tienine conto: un soggetto puo' rientrare
per quello che fa davvero anche se la sua categoria formale non e' nominata nel bando.
Se il racconto dice COSA cerca (per esempio solo audizioni di un certo genere), un
annuncio di altro genere e' "no" anche se formalmente potrebbe candidarsi.
Per un'audizione, la citta' o il paese dove si svolge NON e' un requisito di residenza:
chiunque puo' andarci. E' un requisito solo se il testo chiede espressamente di
risiedere li' o di avere un permesso di lavoro che il profilo non ha.

Distingui CHI PRESENTA LA DOMANDA da CHI BENEFICIA. Se il bando finanzia progetti rivolti
a ragazzi, anziani, famiglie o scuole, i destinatari elencati sono spesso i beneficiari:
a candidarsi sono enti, associazioni, cooperative o partenariati. In quel caso un ente
che lavora davvero con quelle persone non e' «no»: e' «si» o «forse».

Sei tu a dover essere preciso sulla forma giuridica: e' «no» per questo motivo solo se il
testo la esclude espressamente (solo enti pubblici, solo imprese, solo persone fisiche,
solo enti iscritti a un albo che il profilo non ha). Un bando per «compagnie teatrali» o
«organizzazioni culturali» non esclude un'associazione che fa teatro e cultura.

Il territorio del profilo e' l'elenco «territori»: un bando di un paese o di una citta'
che compare li' (per esempio Spagna, Regno Unito, Europa) e' valido per il profilo.

Conta anche l'AMBITO: il profilo deve poter presentare un progetto della sua attivita'
(settori e racconto). Se l'oggetto del bando e' lontano da cio' che il profilo fa
(informatica e conformita' normativa, agricoltura, imprese e filiere industriali, sanita',
lavori pubblici, consumatori privati), e' «no» anche se la forma giuridica sarebbe
ammessa. Se l'oggetto e' vicino (cultura, arti, sociale, giovani, comunita', territorio)
o il testo non basta per capirlo, e' «si» o «forse».

  "verdetto" : "si" se il profilo rientra chiaramente tra chi puo' presentare domanda,
               "forse" se il testo non basta per escluderlo,
               "no" se il profilo e' escluso (tipo di ente sbagliato, territorio sbagliato,
               requisiti che non ha, oppure non e' quello che il profilo cerca).
  "motivo"   : una frase breve in italiano, che cita il punto del bando da cui si capisce."""


def domanda_lettura(b):
    return "TITOLO: %s\nENTE: %s\n\nTESTO:\n%s" % (
        b["titolo"], b["ente"] or "sconosciuto", ritaglia(b["testo"]))


def domanda_giudizio(b, profilo):
    # Il racconto libero si manda davvero: il prompt diceva al modello di tenerne conto,
    # ma fino al 28 set 2026 non gli arrivava. Per un profilo come «Mati» (cerca SOLO
    # audizioni di compagnie contemporanee) e' la parte che decide.
    racconto = " ".join((profilo.get("racconto") or "").split())[:600]
    return ("PROFILO\n  tipo: %s\n  settori: %s\n  territori: %s\n  racconto: %s\n\n"
            "BANDO\n  titolo: %s\n  destinatari: %s\n  riassunto: %s") % (
        profilo.get("tipo_ente") or "non specificato",
        ", ".join(profilo.get("settori", [])) or "qualsiasi",
        ", ".join(profilo.get("regioni", [])) or "qualsiasi",
        racconto or "non scritto",
        b["titolo"],
        "; ".join(json.loads(b["requisiti"] or "[]")) or "non indicati",
        (b["riassunto"] or "")[:800])


# ---------------------------------------------------------------- primo passaggio

def profilo_prioritario():
    """Il profilo che passa avanti nelle code di lettura e di giudizio: `profilo_prioritario`
    in configurazione.json (assente = nessuno). Lo ha chiesto Antonio il 1 ott 2026 per LaboArt."""
    try:
        import configurazione
        v = configurazione.leggi_file().get("profilo_prioritario")
        return int(v) if v else None
    except Exception:
        return None


def da_leggere(db, limite):
    db.row_factory = sqlite3.Row
    righe = [dict(r) for r in db.execute(
        "SELECT id,titolo,ente,fonte,testo FROM bandi "
        "WHERE analizzato_il IS NULL AND testo IS NOT NULL AND testo <> '' "
        "AND archiviato = 0 "
        # Prima quelli che Jev riconosce come bandi veri; chi non e' passato dal
        # portiere (niente chiave, credito finito) sta a meta', come prima.
        "ORDER BY COALESCE(jev_bando, 0.5) DESC, trovato_il DESC")]
    prio = profilo_prioritario()
    if prio:
        # I bandi delle fonti che servono al profilo prioritario passano avanti; dentro ogni
        # gruppo resta l'ordine di Jev (l'ordinamento di Python e' stabile). Una fonte che non
        # dichiara «profili» serve a tutti.
        import profili
        padroni = profili.profili_delle_fonti()
        righe.sort(key=lambda b: 0 if (padroni.get(b["fonte"]) is None
                                       or prio in padroni[b["fonte"]]) else 1)
    return righe[:limite]


def leggi_bandi(db, prova=False):
    """Primo passaggio: ogni bando si legge UNA volta sola. Legge il modello piccolo;
    quando finisce i suoi gettoni del giorno continua il grande, che ha una quota sua
    e di solito la usa poco, lasciandogli una scorta per i giudizi."""
    imp = impostazioni()
    migra(db)
    if not prova:
        # Il portiere guarda i bandi nuovi prima di Groq. Se non c'e' la chiave o
        # Jev non risponde, non cambia niente: si legge nell'ordine di sempre.
        import jev
        jev.smista(db)
    elenco = da_leggere(db, imp["letture_per_giro"])
    if not elenco:
        print("Nessun bando nuovo da leggere.")
        return 0

    # I modelli in ordine, ognuno col suo tetto per le letture di oggi.
    turni = [(imp["modello_piccolo"], imp["gettoni_al_giorno"]),
             (imp["modello_grande"], imp["gettoni_al_giorno"] - imp["scorta_giudizi"])]
    modello, tetto = turni.pop(0)
    speso = consumo_oggi(db, modello)
    letti = 0
    per_modello = {}
    for b in elenco:
        domanda = domanda_lettura(b)
        # Misurato sui bandi veri: ~1.160 gettoni in tutto, di cui ~215 di risposta.
        costo = stima_gettoni(SISTEMA_LETTURA + domanda) + 250
        while speso + costo > tetto and turni:
            modello, tetto = turni.pop(0)
            speso = consumo_oggi(db, modello)
        if speso + costo > tetto:
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
            # Un 429 qui vuol dire che il conto di oggi e' finito davvero (quello al
            # minuto lo gestisce `chiedi`): si passa al modello dopo, se c'e'.
            if e.code == 429 and turni:
                print("  %s ha finito i gettoni di oggi: continuo con il modello dopo." % modello)
                modello, tetto = turni.pop(0)
                speso = consumo_oggi(db, modello)
                continue
            print("  Groq ha risposto %s: mi fermo qui." % e.code)
            break
        except Exception as e:
            print("  errore su «%s»: %s" % (b["titolo"][:40], type(e).__name__))
            continue

        speso += usati or costo
        segna_consumo(db, modello, usati or costo)
        salva_lettura(db, b["id"], risposta)
        letti += 1
        per_modello[modello] = per_modello.get(modello, 0) + 1
        # Si aspetta in proporzione a quanto si e' appena consumato, per restare
        # sotto il limite al minuto invece di sbatterci contro e prendere un 429.
        time.sleep(60.0 * (usati or costo) / imp["gettoni_al_minuto"])

    if prova:
        print("=" * 66)
        print("\nProva: %d bandi pronti da leggere, ~%d gettoni in tutto." % (letti, speso))
    else:
        print("Bandi letti: %d (%s)" % (letti, ", ".join(
            "%d da %s" % (n, m) for m, n in per_modello.items()) or "nessuno"))
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
        "contributo=?, tipo_aiuto=?, requisiti=?, riassunto=?, territorio=?, "
        "analizzato_il=? WHERE id=?",
        (1 if r.get("aperto") and r.get("e_un_bando") else 0,
         scadenza, "letto dal modello" if scadenza else None,
         r.get("importo"), r.get("importo"), r.get("contributo"), r.get("tipo_aiuto"),
         json.dumps(r.get("destinatari") or [], ensure_ascii=False),
         (r.get("riassunto") or "").strip() or None,
         json.dumps(r.get("territorio") or [], ensure_ascii=False) if r.get("territorio") else None,
         datetime.now(timezone.utc).isoformat(timespec="seconds"), ident))
    salva_artista(db, ident, r)
    db.commit()


def salva_artista(db, ident, r):
    """Compagnia, citta', ingaggio e tipo di lavoro. I valori fuori elenco si buttano:
    un'etichetta «pagato» sbagliata e' peggio di nessuna etichetta."""
    # Qualche volta il modello risponde con una lista di un solo oggetto invece che con
    # l'oggetto: il 28 set ha fermato il recupero a meta'.
    if isinstance(r, list):
        r = next((x for x in r if isinstance(x, dict)), {})
    if not isinstance(r, dict):
        r = {}
    testo = lambda k: (str(r.get(k) or "").strip()[:120] or None)
    ingaggio = (r.get("ingaggio") or "").strip().lower()
    lavoro = (r.get("lavoro") or "").strip().lower()
    db.execute("UPDATE bandi SET compagnia=?, citta=?, ingaggio=?, lavoro=? WHERE id=?",
               (testo("compagnia"), testo("citta"),
                ingaggio if ingaggio in VALORI_INGAGGIO else None,
                lavoro if lavoro in VALORI_LAVORO else None, ident))


def completa_audizioni(db, limite=25, modello=None):
    """Chiede i quattro campi dell'artista per le audizioni APERTE lette prima che
    esistessero. Costa poco (titolo, riassunto e un ritaglio corto) e si ferma da sola
    quando non ne restano: e' un lavoro di recupero, non un passaggio fisso."""
    imp = impostazioni()
    migra(db)
    modello = modello or imp["modello_piccolo"]
    db.row_factory = sqlite3.Row
    elenco = [dict(r) for r in db.execute(
        "SELECT id, titolo, ente, riassunto, testo FROM bandi "
        "WHERE aperto = 1 AND archiviato = 0 AND analizzato_il IS NOT NULL "
        "AND compagnia IS NULL AND ingaggio IS NULL "
        "AND (tipo_aiuto = 'audizione' OR fonte LIKE 'Audizioni -%' OR fonte LIKE 'Auditions -%') "
        "ORDER BY trovato_il DESC LIMIT ?", (limite,))]
    fatti = 0
    speso = consumo_oggi(db, modello)
    for b in elenco:
        domanda = "TITOLO: %s\nENTE: %s\nRIASSUNTO: %s\n\nTESTO:\n%s" % (
            b["titolo"], b["ente"] or "sconosciuto", b["riassunto"] or "",
            (b["testo"] or "")[:2500])
        costo = stima_gettoni(SISTEMA_ARTISTA + domanda) + 200
        if speso + costo > imp["gettoni_al_giorno"]:
            break
        try:
            r, usati = chiedi(imp["chiave"], modello, SISTEMA_ARTISTA, domanda, 1500)
        except Exception as e:
            print("  completamento saltato (%s)" % type(e).__name__)
            break
        speso += usati or costo
        segna_consumo(db, modello, usati or costo)
        salva_artista(db, b["id"], r)
        db.commit()
        fatti += 1
        time.sleep(60.0 * (usati or costo) / imp["gettoni_al_minuto"])
    if elenco:
        print("Audizioni completate con compagnia, citta' e ingaggio: %d" % fatti)
    return fatti


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
        "WHERE ((a.llm_verdetto IS NULL AND a.punteggio >= ?) OR (a.rigiudica = 1 AND a.punteggio > 0)) "
        "AND b.aperto = 1 AND b.analizzato_il IS NOT NULL AND b.archiviato = 0 "
        # Prima quelli mai giudicati, poi i rifacimenti; dentro ognuno i piu' promettenti.
        "ORDER BY (a.profilo_id = ?) DESC, (a.llm_verdetto IS NULL) DESC, a.punteggio DESC LIMIT 40",
        (soglia, profilo_prioritario() or -1))]
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
        db.execute("UPDATE abbinamenti SET llm_verdetto=?, llm_motivo=?, rigiudica=0 "
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
