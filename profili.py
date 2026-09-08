#!/usr/bin/env python3
"""
Profili e compatibilita' - Fase 2.

Un profilo dice chi sei e cosa ti interessa. Per ogni bando calcoliamo un punteggio
da 0 a 100 e, soprattutto, il MOTIVO: cosi' si capisce a colpo d'occhio perche' un
bando e' finito nell'elenco, invece di doversi fidare di un numero.

Nessun LLM qui dentro: sono conteggi di parole. L'LLM arriva nella Fase 5,
e lavorera' solo sui bandi che questo filtro ha gia' promosso.
"""
import json
import re
import sqlite3
import unicodedata

# ---------------------------------------------------------------- vocabolari

# Settori: nome mostrato -> parole che lo rivelano nel testo del bando.
SETTORI = {
    "Cultura e arte": ["cultura", "culturale", "arte", "artistic", "patrimonio", "museo",
                       "mostra", "beni culturali", "creativ"],
    "Teatro e spettacolo": ["teatro", "teatral", "spettacolo", "scena", "danza", "circo",
                            "performing", "drammaturg", "festival"],
    "Musica": ["musica", "musical", "concerto", "banda", "coro", "orchestra", "discografic"],
    "Cinema e audiovisivo": ["cinema", "audiovisiv", "documentari", "cortometraggi", "film"],
    "Sociale e terzo settore": ["sociale", "solidariet", "volontariat", "terzo settore",
                                "inclusion", "poverta", "fragilit", "disabilit", "caregiver"],
    "Giovani": ["giovani", "giovanile", "under 35", "under 30", "studenti", "adolescen"],
    "Ambiente": ["ambiente", "ambientale", "sostenibil", "rifiuti", "energia", "clima",
                 "biodiversit", "verde"],
    "Tecnologia e digitale": ["digitale", "innovazione", "tecnolog", "startup", "software",
                              "intelligenza artificiale", "transizione digitale"],
    "Turismo": ["turismo", "turistic", "ospitalit", "borghi", "cammini"],
    "Sport": ["sport", "sportiv", "atleti", "palestra", "impianti sportivi"],
    "Formazione": ["formazione", "corso", "corsi", "tirocin", "apprendistato", "competenze"],
    "Ricerca": ["ricerca", "universit", "dottorat", "scientific"],
    "Agricoltura": ["agricol", "agroaliment", "rurale", "pesca", "allevamento"],
    "Imprese e lavoro": ["impresa", "imprese", "occupazione", "autoimpiego", "lavoro",
                         "microcredito", "pmi"],
}

REGIONI = {
    "Abruzzo": ["abruzzo", "abruzzese"], "Basilicata": ["basilicata", "lucan"],
    "Calabria": ["calabria", "calabres"], "Campania": ["campania", "campan"],
    "Emilia-Romagna": ["emilia", "romagna"], "Friuli-Venezia Giulia": ["friuli", "giulia"],
    "Lazio": ["lazio", "lazial"], "Liguria": ["liguria", "ligure"],
    "Lombardia": ["lombardia", "lombard"], "Marche": ["marche", "marchigian"],
    "Molise": ["molise", "molisan"], "Piemonte": ["piemonte", "piemontes"],
    "Puglia": ["puglia", "pugliese", "pugliesi"], "Sardegna": ["sardegna", "sard"],
    "Sicilia": ["sicilia", "sicilian"], "Toscana": ["toscana", "toscan"],
    "Trentino-Alto Adige": ["trentino", "bolzano", "trento"], "Umbria": ["umbria", "umbr"],
    "Valle d'Aosta": ["valle d aosta", "aosta"], "Veneto": ["veneto", "venet"],
    "Tutta Italia": ["nazionale", "tutto il territorio nazionale", "italia"],
    "Europa": ["europea", "europeo", "unione europea", "erasmus", "horizon", "interreg",
               "creative europe"],
}

TIPI_ENTE = [
    "Associazione non riconosciuta", "Associazione di promozione sociale (APS)",
    "Organizzazione di volontariato (ODV)", "Ente del Terzo Settore (ETS/ONLUS)",
    "Fondazione", "Impresa sociale", "Cooperativa", "Societa' (SRL, SPA)",
    "Ditta individuale / libero professionista", "Privato cittadino",
    "Comune o ente pubblico", "Scuola o universita'",
]

# Parole che fanno pensare a un bando vero e non a una notizia.
INDIZI_BANDO = ["bando", "avviso pubblico", "avviso", "contributo", "contributi",
                "finanziament", "sovvenzion", "call for", "concorso", "premio",
                "borsa di studio", "candidatur", "domanda di partecipazione", "scadenz",
                "dotazione finanziaria", "plafond", "presentare domanda",
                "sostegno economico", "voucher", "manifestazione di interesse"]

# Parole che tradiscono un articolo di giornale invece di un bando aperto.
# Attenzione: 'graduatoria' e 'vincitori' stanno QUI, non sopra: se c'e' la graduatoria
# il bando e' gia' chiuso, e avvisare sarebbe peggio che tacere.
INDIZI_NOTIZIA = ["intervista", "editoriale", "si e' svolto", "si e' svolta", "si e' tenuto",
                  "convegno", "webinar", "racconta", "rapporto annuale", "bilancio sociale",
                  "nomina", "assemblea", "comunicato stampa", "inaugurat",
                  "vincitor", "premiat", "assegnat", "graduatoria", "si candida",
                  "si e' conclus", "hanno partecipato", "erogat", "consegnat",
                  "presentata la", "presentato il", "firmato"]


def _norm(t):
    """Minuscolo e senza accenti: cosi' 'attivita'' e 'attivita' sono la stessa parola."""
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


_CACHE = {}


def contiene(testo, chiave):
    """Cerca la parola dall'INIZIO di una parola vera.

    Senza questo, 'arte' si trovava dentro 'parte' e 'sport' dentro 'trasporto':
    il profilo si riempiva di bandi che non c'entravano niente. La fine resta libera,
    cosi' 'cultura' continua a trovare 'culturale'.
    """
    r = _CACHE.get(chiave)
    if r is None:
        r = _CACHE[chiave] = re.compile(r"\b" + re.escape(_norm(chiave)))
    return r.search(testo) is not None


# ---------------------------------------------------------------- punteggio

PESI = {"bando": 25, "settore": 10, "settore_max": 30, "regione": 20,
        "regione_generica": 8, "parola": 10, "parola_max": 20, "importo": 5,
        "forse_notizia": 20, "confermato": 15}

SOGLIA = 40  # sotto questo punteggio il bando non viene mostrato nel profilo


def valuta(bando, profilo):
    """Restituisce (punteggio 0-100, elenco dei motivi). Punteggio 0 = da scartare."""
    # Quando la Fase 5 ha gia' letto il bando, il riassunto e i destinatari sono
    # testo molto piu' ricco del titolo: e' li' che si trovano settore e territorio.
    testo = _norm(" ".join(filter(None, [
        bando.get("titolo", ""), bando.get("sommario") or "", bando.get("ente") or "",
        bando.get("riassunto") or "", bando.get("requisiti") or ""])))
    motivi = []

    # Il verdetto del modello vale piu' di qualunque conteggio di parole.
    if bando.get("aperto") == 0:
        return 0, ["il modello lo da' per chiuso o per notizia"]

    # 1. Parole da escludere: se compare una di queste, il bando esce subito.
    for p in profilo.get("escluse", []):
        if p and contiene(testo, p):
            return 0, ["escluso dalla parola «%s»" % p]

    punti = 0

    # 2. Sembra un bando o una notizia?
    if bando.get("aperto") == 1:
        # Il modello ha letto il testo e conferma: qui i conteggi di parole non
        # servono piu' e non devono poter scartare un bando gia' confermato.
        punti += PESI["bando"] + PESI["confermato"]
        motivi.append("bando aperto, confermato dal modello")
    else:
        trovati = [k for k in INDIZI_BANDO if contiene(testo, k)]
        notizia = [k for k in INDIZI_NOTIZIA if contiene(testo, k)]
        if trovati:
            punti += PESI["bando"]
            motivi.append("sembra un bando")
            # Se compaiono ANCHE le parole da articolo di giornale, quasi sempre e'
            # una notizia su un bando chiuso: non si scarta, ma si perdono punti.
            if notizia:
                punti -= PESI["forse_notizia"]
                motivi.append("forse e' solo una notizia")
        elif notizia:
            return 0, ["sembra una notizia, non un bando"]

    # 3. Settori di interesse.
    colpiti = []
    for settore in profilo.get("settori", []):
        if any(contiene(testo, k) for k in SETTORI.get(settore, [])):
            colpiti.append(settore)
    if colpiti:
        punti += min(len(colpiti) * PESI["settore"], PESI["settore_max"])
        motivi.append(", ".join(colpiti).lower())

    # 4. Territorio.
    regioni_profilo = profilo.get("regioni", [])
    if regioni_profilo:
        trovata = next((r for r in regioni_profilo
                        if any(contiene(testo, k) for k in REGIONI.get(r, []))), None)
        if trovata:
            punti += PESI["regione"]
            motivi.append(trovata)
        else:
            citate = [r for r, chiavi in REGIONI.items()
                      if r not in ("Tutta Italia", "Europa") and any(contiene(testo, k) for k in chiavi)]
            if citate:
                return 0, ["riguarda un'altra zona (%s)" % citate[0]]
            punti += PESI["regione_generica"]
            motivi.append("nessuna zona indicata")

    # 5. Parole chiave scritte da te.
    tue = [p for p in profilo.get("parole", []) if p and contiene(testo, p)]
    if tue:
        punti += min(len(tue) * PESI["parola"], PESI["parola_max"])
        motivi.append("parole tue: " + ", ".join(tue))

    # 6. Importo dentro la fascia che ti interessa.
    imp = bando.get("importo_num")
    minimo, massimo = profilo.get("importo_min"), profilo.get("importo_max")
    if imp is not None and (minimo or massimo):
        if (minimo is None or imp >= minimo) and (massimo is None or imp <= massimo):
            punti += PESI["importo"]
            motivi.append("importo nella tua fascia")
        else:
            return 0, ["importo fuori dalla tua fascia"]

    return min(punti, 100), motivi


# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS profili (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  nome         TEXT NOT NULL,
  tipo_ente    TEXT,
  settori      TEXT DEFAULT '[]',
  regioni      TEXT DEFAULT '[]',
  parole       TEXT DEFAULT '[]',
  escluse      TEXT DEFAULT '[]',
  importo_min  REAL,
  importo_max  REAL,
  creato_il    TEXT
);
CREATE TABLE IF NOT EXISTS abbinamenti (
  bando_id   TEXT NOT NULL,
  profilo_id INTEGER NOT NULL,
  punteggio  INTEGER NOT NULL,
  motivi     TEXT,
  avvisato   INTEGER DEFAULT 0,
  PRIMARY KEY (bando_id, profilo_id)
);
CREATE INDEX IF NOT EXISTS idx_abb_profilo ON abbinamenti(profilo_id, punteggio DESC);
"""

LISTE = ("settori", "regioni", "parole", "escluse")


def prepara(db):
    db.executescript(SCHEMA)


def leggi_profili(db):
    db.row_factory = sqlite3.Row
    fuori = []
    for r in db.execute("SELECT * FROM profili ORDER BY id").fetchall():
        p = dict(r)
        for campo in LISTE:
            p[campo] = json.loads(p[campo] or "[]")
        fuori.append(p)
    return fuori


def riabbina(db, solo_profilo=None):
    """Ricalcola la compatibilita'. Si richiama dopo ogni raccolta e a ogni modifica di un profilo."""
    prepara(db)
    db.row_factory = sqlite3.Row
    profili = [p for p in leggi_profili(db)
               if solo_profilo is None or p["id"] == solo_profilo]
    if not profili:
        return 0
    bandi = [dict(r) for r in db.execute("SELECT * FROM bandi").fetchall()]
    scritti = 0
    for p in profili:
        compatibili = []
        for b in bandi:
            punti, motivi = valuta(b, p)
            if punti >= SOGLIA:
                compatibili.append(b["id"])
                # Si aggiorna solo il punteggio: il giudizio del modello (llm_verdetto)
                # e' costato gettoni e va conservato, non ricalcolato ogni volta.
                db.execute(
                    "INSERT INTO abbinamenti (bando_id,profilo_id,punteggio,motivi) VALUES (?,?,?,?) "
                    "ON CONFLICT(bando_id,profilo_id) DO UPDATE SET punteggio=?,motivi=?",
                    (b["id"], p["id"], punti, json.dumps(motivi, ensure_ascii=False),
                     punti, json.dumps(motivi, ensure_ascii=False)))
                scritti += 1

        # Si tolgono solo quelli che non sono piu' compatibili e di cui non era ancora
        # partito l'avviso.
        segnaposti = ",".join("?" * len(compatibili)) or "NULL"
        db.execute("DELETE FROM abbinamenti WHERE profilo_id=? AND avvisato=0 "
                   "AND bando_id NOT IN (%s)" % segnaposti, [p["id"]] + compatibili)
    db.commit()
    return scritti


if __name__ == "__main__":
    from pathlib import Path
    con = sqlite3.connect(Path(__file__).parent / "dati.db")
    print("Abbinamenti ricalcolati:", riabbina(con))
    con.close()
