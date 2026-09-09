#!/usr/bin/env python3
"""
Le impostazioni come file, non come database - così la pagina può riscriverle.

`configurazione.json` contiene profili, siti da controllare e bandi archiviati.
E' la fonte di verita': il database `dati.db` la ricopia a ogni giro.

Perche' cosi': la pagina pubblicata su GitHub Pages e' un file fermo e non puo'
parlare con un database. Puo' pero' riscrivere un file dentro il repository, e
questo file fa da ponte. Vale per qualunque profilo e qualunque tipo di ente:
non c'e' niente di cucito addosso a un'associazione in particolare.
"""
import json
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
FILE = BASE / "configurazione.json"

CAMPI_PROFILO = ("nome", "tipo_ente", "settori", "regioni", "parole", "escluse",
                 "importo_min", "importo_max")
LISTE = ("settori", "regioni", "parole", "escluse")


def _profilo_da_riga(r):
    p = {"id": r["id"]}
    for c in CAMPI_PROFILO:
        p[c] = json.loads(r[c] or "[]") if c in LISTE else r[c]
    return p


IMPOSTAZIONI_PREDEFINITE = {"soglia_avviso": 55, "massimo_messaggi": 8}


def feed_iniziali():
    """La prima volta i feed vengono da fonti.json; poi vivono in configurazione.json,
    cosi' anche quelli si possono accendere e spegnere dalla pagina."""
    vecchio = BASE / "fonti.json"
    if not vecchio.exists():
        return []
    dati = json.loads(vecchio.read_text(encoding="utf-8"))
    return [{"nome": f["nome"], "url": f.get("url", ""), "ente": f.get("ente", ""),
             "attivo": bool(f.get("attiva", True)) and bool(f.get("url"))}
            for f in dati.get("fonti", [])]


def esporta(db):
    """Scrive il file a partire dal database (usato dall'app sul computer).

    Feed e impostazioni non stanno nel database: si conservano com'erano nel file.
    """
    db.row_factory = sqlite3.Row
    profili = [_profilo_da_riga(r) for r in db.execute("SELECT * FROM profili ORDER BY id")]
    try:
        siti = [{"nome": r["nome"], "url": r["url"], "ente": r["ente"],
                 "attivo": bool(r["attivo"])}
                for r in db.execute("SELECT * FROM siti ORDER BY id")]
    except sqlite3.OperationalError:
        siti = []
    archiviati = [r[0] for r in db.execute("SELECT id FROM bandi WHERE archiviato=1")]

    precedente = leggi_file()
    FILE.write_text(json.dumps({
        "profili": profili,
        "feed": precedente["feed"],
        "siti": siti,
        "impostazioni": precedente["impostazioni"],
        "archiviati": archiviati,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(profili), len(siti), len(archiviati)


def leggi_file():
    if not FILE.exists():
        return {"profili": [], "feed": feed_iniziali(), "siti": [],
                "impostazioni": dict(IMPOSTAZIONI_PREDEFINITE), "archiviati": []}
    d = json.loads(FILE.read_text(encoding="utf-8"))
    d.setdefault("profili", [])
    d.setdefault("siti", [])
    d.setdefault("archiviati", [])
    if not d.get("feed"):
        d["feed"] = feed_iniziali()
    imp = dict(IMPOSTAZIONI_PREDEFINITE)
    imp.update(d.get("impostazioni") or {})
    d["impostazioni"] = imp
    return d


def importa(db):
    """Riporta il file dentro il database. Restituisce i profili che sono cambiati.

    I profili cambiati vanno rivalutati: il giudizio «puoi parteciparci» era stato
    dato sulle impostazioni vecchie e non vale piu'.
    """
    import profili as mod_profili
    if not FILE.exists():
        return []

    dati = leggi_file()
    db.row_factory = sqlite3.Row
    mod_profili.prepara(db)

    prima = {r["id"]: _profilo_da_riga(r) for r in db.execute("SELECT * FROM profili")}
    cambiati, visti = [], set()

    for p in dati["profili"]:
        ident = p.get("id")
        valori = tuple(json.dumps(p.get(c) or [], ensure_ascii=False) if c in LISTE
                       else p.get(c) for c in CAMPI_PROFILO)
        if ident in prima:
            visti.add(ident)
            if prima[ident] != {**p, "id": ident}:
                cambiati.append(ident)
                db.execute("UPDATE profili SET nome=?,tipo_ente=?,settori=?,regioni=?,"
                           "parole=?,escluse=?,importo_min=?,importo_max=? WHERE id=?",
                           valori + (ident,))
        else:
            cur = db.execute(
                "INSERT INTO profili (nome,tipo_ente,settori,regioni,parole,escluse,"
                "importo_min,importo_max,creato_il) VALUES (?,?,?,?,?,?,?,?,datetime('now'))",
                valori)
            nuovo = cur.lastrowid
            visti.add(nuovo)
            cambiati.append(nuovo)

    # Profili spariti dal file: si tolgono anche dal database.
    for ident in prima:
        if ident not in visti:
            db.execute("DELETE FROM abbinamenti WHERE profilo_id=?", (ident,))
            db.execute("DELETE FROM profili WHERE id=?", (ident,))

    # Siti: si aggiungono i nuovi e si tolgono quelli spariti, tenendo l'esito
    # dell'ultimo giro di quelli che restano.
    try:
        esistenti = {r["url"]: r["id"] for r in db.execute("SELECT id,url FROM siti")}
    except sqlite3.OperationalError:
        import siti as mod_siti
        mod_siti.migra(db)
        esistenti = {}
    nel_file = set()
    for s in dati["siti"]:
        nel_file.add(s["url"])
        if s["url"] in esistenti:
            db.execute("UPDATE siti SET nome=?, ente=?, attivo=? WHERE url=?",
                       (s.get("nome") or s["url"], s.get("ente") or "",
                        1 if s.get("attivo", True) else 0, s["url"]))
        else:
            db.execute("INSERT INTO siti (nome,url,ente,attivo,aggiunto_il) "
                       "VALUES (?,?,?,?,datetime('now'))",
                       (s.get("nome") or s["url"], s["url"], s.get("ente") or "",
                        1 if s.get("attivo", True) else 0))
    for url in esistenti:
        if url not in nel_file:
            db.execute("DELETE FROM siti WHERE url=?", (url,))

    # Archiviati
    db.execute("UPDATE bandi SET archiviato=0")
    for ident in dati["archiviati"]:
        db.execute("UPDATE bandi SET archiviato=1 WHERE id=?", (ident,))

    # I giudizi dei profili cambiati non valgono piu'.
    for ident in cambiati:
        db.execute("UPDATE abbinamenti SET llm_verdetto=NULL, llm_motivo=NULL "
                   "WHERE profilo_id=?", (ident,))
    db.commit()
    return cambiati


if __name__ == "__main__":
    import sys
    con = sqlite3.connect(BASE / "dati.db")
    if "--importa" in sys.argv:
        cambiati = importa(con)
        import profili
        print("Profili cambiati: %s" % (cambiati or "nessuno"))
        print("Compatibilita' ricalcolata: %d" % profili.riabbina(con))
    else:
        p, s, a = esporta(con)
        print("configurazione.json scritto: %d profili, %d siti, %d archiviati" % (p, s, a))
    con.close()
