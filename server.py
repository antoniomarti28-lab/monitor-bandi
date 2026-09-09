#!/usr/bin/env python3
"""
Pagina di consultazione dei bandi raccolti.
Si avvia con:  python server.py     e poi si apre  http://localhost:8077
Usa SOLO la libreria standard di Python.
"""
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import configurazione
import profili
import siti

BASE = Path(__file__).parent
DB = BASE / "dati.db"
PAGINA = BASE / "pagina"
PORTA = 8077

# «Aperto» vuol dire: il modello non l'ha dato per chiuso E la scadenza non e' passata.
# I bandi non ancora letti restano visibili: meglio uno di troppo che perderne uno.
APERTI = ("AND (b.aperto IS NULL OR b.aperto = 1) "
          "AND (b.scadenza IS NULL OR b.scadenza >= date('now'))")


def connetti():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    profili.prepara(c)
    siti.migra(c)
    return c


def query(sql, args=()):
    c = connetti()
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        c.close()


class Gestore(BaseHTTPRequestHandler):

    def log_message(self, *a):
        pass  # niente rumore nel terminale

    # ---------------------------------------------------------- risposte

    def _json(self, dati, codice=200):
        corpo = json.dumps(dati, ensure_ascii=False).encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _file(self, percorso, tipo):
        try:
            corpo = percorso.read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _fine_scrittura(self, c):
        """Dopo ogni modifica il file delle impostazioni torna allineato al database:
        e' lui la fonte di verita' per la pagina pubblicata e per i giri su GitHub."""
        c.commit()
        configurazione.esporta(c)
        c.close()

    def _corpo_richiesta(self):
        lunghezza = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(lunghezza) or b"{}")

    # ---------------------------------------------------------- elenco bandi

    def _elenco_bandi(self, p):
        cerca = (p.get("q", [""])[0] or "").strip()
        fonte = (p.get("fonte", [""])[0] or "").strip()
        profilo = (p.get("profilo", [""])[0] or "").strip()
        mostra_chiusi = p.get("chiusi", ["0"])[0] == "1"
        archivio = p.get("archiviati", ["0"])[0] == "1"

        # Il testo intero puo' essere di 60.000 caratteri: non serve nell'elenco.
        # Ne mandiamo un estratto, che basta per il «Leggi di piu'».
        CAMPI = ("b.id, b.titolo, b.link, b.ente, b.fonte, b.pubblicato, b.scadenza, "
                 "b.importo, b.importo_num, b.contributo, b.sommario, b.archiviato, "
                 "b.riassunto, b.requisiti, b.aperto, b.origine_scadenza, b.analizzato_il, "
                 "b.nota, substr(b.testo, 1, 3000) AS estratto, length(b.testo) AS quanto_testo")
        if profilo:
            sql = ("SELECT " + CAMPI + ", a.punteggio, a.motivi, a.llm_verdetto, a.llm_motivo "
                   "FROM bandi b "
                   "JOIN abbinamenti a ON a.bando_id = b.id AND a.profilo_id = ? "
                   "WHERE b.archiviato = ?")
            args = [int(profilo), 1 if archivio else 0]
        else:
            sql = ("SELECT " + CAMPI + ", NULL AS punteggio, NULL AS motivi, "
                   "NULL AS llm_verdetto, NULL AS llm_motivo FROM bandi b WHERE b.archiviato = ?")
            args = [1 if archivio else 0]

        if cerca:
            sql += " AND (b.titolo LIKE ? OR b.sommario LIKE ? OR b.ente LIKE ?)"
            args += ["%" + cerca + "%"] * 3
        if fonte:
            sql += " AND b.fonte = ?"
            args.append(fonte)
        if not mostra_chiusi:
            sql += " " + APERTI

        if profilo:
            sql += " ORDER BY a.punteggio DESC,"
        else:
            sql += " ORDER BY"
        sql += (" CASE WHEN b.scadenza IS NULL THEN 1 ELSE 0 END,"
                " b.scadenza ASC, b.pubblicato DESC, b.trovato_il DESC LIMIT 400")

        righe = query(sql, args)
        for r in righe:
            r["motivi"] = json.loads(r["motivi"]) if r.get("motivi") else []
        return righe

    def _riepilogo(self, profilo):
        if profilo:
            base = ("FROM bandi b JOIN abbinamenti a ON a.bando_id=b.id AND a.profilo_id=%d "
                    "WHERE b.archiviato=0" % int(profilo))
        else:
            base = "FROM bandi b WHERE b.archiviato=0"
        uno = lambda sql, args=(): query(sql, args)[0]["n"]
        return {
            "totale": uno("SELECT COUNT(*) n " + base),
            "aperti": uno("SELECT COUNT(*) n " + base + " " + APERTI),
            "archiviati": uno("SELECT COUNT(*) n FROM bandi WHERE archiviato=1"),
            "in_scadenza": uno("SELECT COUNT(*) n " + base + " " + APERTI +
                               " AND b.scadenza BETWEEN date('now') AND date('now','+30 day')"),
            "letti": uno("SELECT COUNT(*) n FROM bandi WHERE analizzato_il IS NOT NULL"),
            "da_leggere": uno("SELECT COUNT(*) n FROM bandi WHERE analizzato_il IS NULL "
                              "AND testo IS NOT NULL AND testo <> ''"),
            "fonti_ok": uno("SELECT COUNT(*) n FROM fonti_stato WHERE esito='ok'"),
            "fonti_totali": uno("SELECT COUNT(*) n FROM fonti_stato"),
        }

    # ---------------------------------------------------------- rotte

    def do_GET(self):
        u = urlparse(self.path)
        p = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            return self._file(PAGINA / "index.html", "text/html; charset=utf-8")
        if u.path == "/stile.css":
            return self._file(PAGINA / "stile.css", "text/css; charset=utf-8")

        if u.path == "/api/bandi":
            return self._json(self._elenco_bandi(p))

        if u.path == "/api/riepilogo":
            return self._json(self._riepilogo((p.get("profilo", [""])[0] or "").strip()))

        if u.path == "/api/fonti":
            return self._json(query("SELECT * FROM fonti_stato ORDER BY esito='ok' DESC, nome"))

        if u.path == "/api/vocabolario":
            return self._json({
                "settori": list(profili.SETTORI.keys()),
                "regioni": list(profili.REGIONI.keys()),
                "tipi_ente": profili.TIPI_ENTE,
            })

        if u.path == "/api/siti":
            return self._json(query("SELECT * FROM siti ORDER BY id"))

        if u.path == "/api/notifiche":
            return self._json(query(
                "SELECT n.quando, n.canale, n.esito, p.nome AS profilo, b.titolo, b.link "
                "FROM notifiche n "
                "LEFT JOIN bandi b   ON b.id = n.bando_id "
                "LEFT JOIN profili p ON p.id = n.profilo_id "
                "ORDER BY n.quando DESC LIMIT 100"))

        if u.path == "/api/profili":
            c = connetti()
            try:
                elenco = profili.leggi_profili(c)
                for pr in elenco:
                    pr["quanti"] = c.execute(
                        "SELECT COUNT(*) FROM abbinamenti a JOIN bandi b ON b.id=a.bando_id "
                        "WHERE a.profilo_id=? AND b.archiviato=0", (pr["id"],)).fetchone()[0]
                return self._json(elenco)
            finally:
                c.close()

        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)

        if u.path == "/api/archivia":
            corpo = self._corpo_richiesta()
            c = connetti()
            c.execute("UPDATE bandi SET archiviato=? WHERE id=?",
                      (1 if corpo.get("archivia", True) else 0, corpo.get("id", "")))
            self._fine_scrittura(c)
            return self._json({"ok": True})

        if u.path == "/api/profili/salva":
            d = self._corpo_richiesta()
            campi = (d.get("nome", "").strip() or "Senza nome",
                     d.get("tipo_ente", ""),
                     json.dumps(d.get("settori", []), ensure_ascii=False),
                     json.dumps(d.get("regioni", []), ensure_ascii=False),
                     json.dumps(d.get("parole", []), ensure_ascii=False),
                     json.dumps(d.get("escluse", []), ensure_ascii=False),
                     d.get("importo_min"), d.get("importo_max"))
            c = connetti()
            if d.get("id"):
                c.execute("UPDATE profili SET nome=?,tipo_ente=?,settori=?,regioni=?,parole=?,"
                          "escluse=?,importo_min=?,importo_max=? WHERE id=?",
                          campi + (int(d["id"]),))
                ident = int(d["id"])
            else:
                cur = c.execute("INSERT INTO profili (nome,tipo_ente,settori,regioni,parole,"
                                "escluse,importo_min,importo_max,creato_il) VALUES (?,?,?,?,?,?,?,?,?)",
                                campi + (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
                ident = cur.lastrowid
            trovati = profili.riabbina(c, ident)
            self._fine_scrittura(c)
            return self._json({"ok": True, "id": ident, "trovati": trovati})

        if u.path == "/api/siti/aggiungi":
            d = self._corpo_richiesta()
            url = (d.get("url") or "").strip()
            if not url.startswith("http"):
                url = "https://" + url
            nome = (d.get("nome") or "").strip() or urlparse(url).netloc.replace("www.", "")
            c = connetti()
            c.execute("INSERT OR IGNORE INTO siti (nome,url,ente,attivo,aggiunto_il) "
                      "VALUES (?,?,?,1,?)",
                      (nome, url, (d.get("ente") or "").strip(),
                       datetime.now(timezone.utc).isoformat(timespec="seconds")))
            self._fine_scrittura(c)
            return self._json({"ok": True})

        if u.path == "/api/siti/rimuovi":
            c = connetti()
            c.execute("DELETE FROM siti WHERE id=?", (int(self._corpo_richiesta().get("id", 0)),))
            self._fine_scrittura(c)
            return self._json({"ok": True})

        if u.path == "/api/profili/elimina":
            ident = int(self._corpo_richiesta().get("id", 0))
            c = connetti()
            c.execute("DELETE FROM abbinamenti WHERE profilo_id=?", (ident,))
            c.execute("DELETE FROM profili WHERE id=?", (ident,))
            self._fine_scrittura(c)
            return self._json({"ok": True})

        self.send_error(404)


if __name__ == "__main__":
    print("Monitor Bandi e' in ascolto.")
    print("Apri nel browser:  http://localhost:%d" % PORTA)
    print("Per fermarlo: Ctrl+C\n")
    ThreadingHTTPServer(("127.0.0.1", PORTA), Gestore).serve_forever()
