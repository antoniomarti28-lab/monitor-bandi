#!/usr/bin/env python3
"""
Crea 'anteprima.html': una copia della pagina con i dati gia' dentro,
da guardare con un doppio clic senza avviare nessun server.
Si rigenera con:  python anteprima.py
"""
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import profili

BASE = Path(__file__).parent


def repository():
    """«utente/repo» letto dal remoto git: serve alla pagina per sapere dove scrivere."""
    import re
    import subprocess
    try:
        url = subprocess.run(["git", "-C", str(BASE), "config", "--get", "remote.origin.url"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


def costruisci():
    db = sqlite3.connect(BASE / "dati.db")
    db.row_factory = sqlite3.Row
    profili.prepara(db)

    # Come nel server: nell'elenco va un estratto, non il testo intero, o il file
    # diventa di parecchi megabyte.
    bandi = [dict(r) for r in db.execute(
        "SELECT id,titolo,link,ente,fonte,pubblicato,scadenza,importo,importo_num,contributo,tipo_aiuto,"
        "sommario,archiviato,riassunto,requisiti,aperto,origine_scadenza,analizzato_il,nota,immagine,"
        "trovato_il,"
        "substr(testo,1,3000) AS estratto, length(testo) AS quanto_testo "
        "FROM bandi ORDER BY pubblicato DESC, trovato_il DESC")]
    per_bando = {}
    for r in db.execute("SELECT * FROM abbinamenti"):
        chiavi = r.keys()
        per_bando.setdefault(r["bando_id"], {})[str(r["profilo_id"])] = {
            "punteggio": r["punteggio"], "motivi": json.loads(r["motivi"] or "[]"),
            "llm_verdetto": r["llm_verdetto"] if "llm_verdetto" in chiavi else None,
            "llm_motivo": r["llm_motivo"] if "llm_motivo" in chiavi else None}
    # La pagina pubblicata si porta dentro tutti i dati, quindi ogni carattere pesa sul
    # tempo di apertura. Misurato sull'archivio vero: il testo dei bandi era il 58% del
    # file (503 KB su 865), e per cinque sesti era testo di bandi gia' CHIUSI, che
    # nessuno apre. Quindi: testo intero per i bandi ancora aperti, niente per gli altri
    # (resta il collegamento al sito dell'ente). Il sommario si taglia a 700 caratteri:
    # nella scheda se ne vedono 300.
    oggi = date.today().isoformat()
    for b in bandi:
        b["punteggi"] = per_bando.get(b["id"], {})
        b["zone"] = profili.zone(b)     # per il filtro «Dove»: costa niente, e' testo
        aperto = (b["aperto"] in (None, 1)) and (not b["scadenza"] or b["scadenza"] >= oggi)
        if not aperto or b["archiviato"]:
            b["estratto"] = ""
        if b.get("sommario") and len(b["sommario"]) > 700:
            b["sommario"] = b["sommario"][:700] + "…"

    elenco_profili = profili.leggi_profili(db)
    for p in elenco_profili:
        p["quanti"] = sum(1 for b in bandi if not b["archiviato"] and str(p["id"]) in b["punteggi"])

    fonti = [dict(r) for r in db.execute("SELECT * FROM fonti_stato ORDER BY esito='ok' DESC, nome")]
    try:
        elenco_siti = [dict(r) for r in db.execute("SELECT * FROM siti ORDER BY id")]
    except sqlite3.OperationalError:
        elenco_siti = []
    try:
        notifiche = [dict(r) for r in db.execute(
            "SELECT n.quando, n.canale, n.esito, p.nome AS profilo, b.titolo, b.link "
            "FROM notifiche n LEFT JOIN bandi b ON b.id=n.bando_id "
            "LEFT JOIN profili p ON p.id=n.profilo_id ORDER BY n.quando DESC LIMIT 100")]
    except sqlite3.OperationalError:
        notifiche = []
    db.close()

    dati = {
        "bandi": bandi,
        "profili": elenco_profili,
        "fonti": fonti,
        "siti": elenco_siti,
        "notifiche": notifiche,
        "configurazione": __import__("configurazione").leggi_file(),
        "vocabolario": {"settori": list(profili.SETTORI), "regioni": list(profili.REGIONI),
                        "tipi_ente": profili.TIPI_ENTE},
    }

    pagina = (BASE / "pagina" / "index.html").read_text(encoding="utf-8")
    stile = (BASE / "pagina" / "stile.css").read_text(encoding="utf-8")
    pagina = pagina.replace('<link rel="stylesheet" href="/stile.css">', "<style>\n" + stile + "\n</style>")
    pagina = pagina.replace("<title>Monitor Bandi</title>", "<title>Monitor Bandi - anteprima</title>")

    finto = """
<script>
// Copia da guardare: al posto del server risponde questo, con i dati gia' in pagina.
// SOLA_LETTURA toglie i pulsanti che scrivono, invece di lasciarli fingere.
// Con un codice di accesso a GitHub, pero', la pagina puo' riscrivere le impostazioni.
window.SOLA_LETTURA = true;
window.REPO = "%s";
window.GENERATA_IL = "%s";   // serve a dire da quanto sono fermi i dati
window.DATI_CONFIG = null;   // riempito qui sotto
const DATI = %s;
window.DATI_CONFIG = DATI.configurazione;
const reteVera = window.fetch.bind(window);
window.fetch = async (url, opzioni) => {
  const u = new URL(url, "http://x/");
  // Solo le chiamate all'app finta vengono intercettate: quelle a GitHub devono
  // passare davvero, altrimenti il salvataggio delle impostazioni non funziona.
  if (!u.pathname.startsWith("/api/")) return reteVera(url, opzioni);
  if (opzioni && opzioni.method === "POST") return { json: async () => ({ ok: true, id: 1 }) };
  const oggi = new Date().toISOString().slice(0, 10);
  let out = [];
  if (u.pathname === "/api/fonti") out = DATI.fonti;
  else if (u.pathname === "/api/zone") {
    const conta = {};
    DATI.bandi.filter((b) => !b.archiviato).forEach((b) => {
      (b.zone.length ? b.zone : ["-"]).forEach((z) => { conta[z] = (conta[z] || 0) + 1; });
    });
    out = Object.entries(conta).map(([zona, quanti]) => ({ zona, quanti }))
      .sort((a, b) => (a.zona === "-") - (b.zona === "-") || b.quanti - a.quanti);
  }
  else if (u.pathname === "/api/siti") out = DATI.siti;
  else if (u.pathname === "/api/vocabolario") out = DATI.vocabolario;
  else if (u.pathname === "/api/profili") out = DATI.profili;
  else if (u.pathname === "/api/notifiche") out = DATI.notifiche;
  else if (u.pathname === "/api/bandi" || u.pathname === "/api/riepilogo") {
    const prof = u.searchParams.get("profilo") || "";
    const q = (u.searchParams.get("q") || "").toLowerCase();
    const fonte = u.searchParams.get("fonte") || "";
    const arch = u.searchParams.get("archiviati") === "1";
    const chiusi = u.searchParams.get("chiusi") === "1";
    const zona = u.searchParams.get("zona") || "";
    const verdetto = u.searchParams.get("verdetto") || "";
    const fra30 = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
    // «Nuovo» = trovato da meno di due giorni, lo stesso taglio del bollino.
    const soloNuovi = u.searchParams.get("nuovi") === "1";
    const daQuando = Date.now() - 48 * 3600 * 1000;
    const eNuovo = (b) => Boolean(b.trovato_il) && new Date(b.trovato_il).getTime() >= daQuando;
    const aperto = (b) => (b.aperto === null || b.aperto === 1) && (!b.scadenza || b.scadenza >= oggi);
    let lista = DATI.bandi
      .filter((b) => (arch ? b.archiviato : !b.archiviato))
      .filter((b) => !prof || b.punteggi[prof])
      .map((b) => Object.assign({}, b, prof ? b.punteggi[prof] : { punteggio: null, motivi: [] }));
    if (u.pathname === "/api/riepilogo") {
      out = {
        totale: lista.length,
        aperti: lista.filter(aperto).length,
        archiviati: DATI.bandi.filter((b) => b.archiviato).length,
        in_scadenza: lista.filter((b) => aperto(b) && b.scadenza
                                        && b.scadenza >= oggi && b.scadenza <= fra30).length,
        nuovi: lista.filter((b) => eNuovo(b) && (chiusi || aperto(b))).length,
        letti: DATI.bandi.filter((b) => b.analizzato_il).length,
        da_leggere: DATI.bandi.filter((b) => !b.analizzato_il && b.testo).length,
        fonti_ok: DATI.fonti.filter((f) => f.esito === "ok").length,
        fonti_totali: DATI.fonti.length,
      };
    } else {
      out = lista
        .filter((b) => !fonte || b.fonte === fonte)
        .filter((b) => !zona || (zona === "-" ? !b.zone.length : b.zone.includes(zona)))
        .filter((b) => !verdetto || (verdetto === "-" ? !b.llm_verdetto : b.llm_verdetto === verdetto))
        .filter((b) => !soloNuovi || eNuovo(b))
        .filter((b) => chiusi || aperto(b))
        .filter((b) => !q || (b.titolo + " " + (b.sommario || "") + " " + (b.ente || "")).toLowerCase().includes(q))
        .sort((a, b) => (b.punteggio || 0) - (a.punteggio || 0));
    }
  }
  return { json: async () => out };
};
</script>
""" % (repository(), datetime.now(timezone.utc).isoformat(timespec="seconds"),
           json.dumps(dati, ensure_ascii=False))

    pagina = pagina.replace("<script>", finto + "<script>", 1)
    (BASE / "anteprima.html").write_text(pagina, encoding="utf-8")
    return len(bandi), len(elenco_profili)


if __name__ == "__main__":
    n, p = costruisci()
    print("anteprima.html rigenerata: %d bandi, %d profili" % (n, p))
