#!/usr/bin/env bash
# Rimette su GitHub l'archivio aggiornato dopo un giro.
#
# Perche' un file a parte e non tre righe dentro il workflow: qui si puo' riprovare
# con ordine e, se proprio non riesce, raccontare l'errore su Telegram invece di
# fallire in silenzio dentro un registro che l'utente non sa leggere.
set -uo pipefail

MESSAGGIO="${1:-Giro automatico}"
REGISTRO=$(mktemp)

salva() {
  git config user.name "Monitor Bandi"
  git config user.email "noreply@github.com"

  # Si mettono da parte TUTTI i file che il giro puo' aver riscritto: se ne resta
  # uno fuori, «git pull --rebase» si rifiuta di partire per le modifiche non salvate.
  git add dati.db anteprima.html configurazione.json 2>/dev/null || true

  if git diff --staged --quiet; then
    echo "niente di cambiato: non c'e' nulla da salvare"
    return 0
  fi
  git commit -m "$MESSAGGIO" || return 1

  # Tre tentativi: fra un giro e l'altro puo' essere arrivata una modifica dalla pagina.
  for tentativo in 1 2 3; do
    if git push; then
      echo "salvato al tentativo $tentativo"
      return 0
    fi
    echo "push rifiutato, riprovo dopo aver scaricato le novita' (tentativo $tentativo)"
    git fetch origin main || return 1
    # I file generati non si fondono: si tiene quello che abbiamo appena prodotto,
    # perche' e' il piu' aggiornato (contiene anche cio' che c'era prima).
    git rebase origin/main -X ours || {
      git checkout --ours dati.db anteprima.html configurazione.json 2>/dev/null || true
      git add dati.db anteprima.html configurazione.json 2>/dev/null || true
      git -c core.editor=true rebase --continue || { git rebase --abort; return 1; }
    }
    sleep 3
  done
  return 1
}

salva 2>&1 | tee "$REGISTRO"
ESITO=${PIPESTATUS[0]}

if [ "$ESITO" -ne 0 ]; then
  echo "SALVATAGGIO FALLITO"
  python - "$REGISTRO" <<'PY'
import sys
import avvisi
imp = avvisi.carica()
token = imp["telegram"]["token"].strip()
chat = str(imp["telegram"]["chat_id"]).strip()
if token and chat:
    with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
        coda = f.read()[-1200:]
    from html import escape
    testo = ("<b>Il giro ha funzionato ma non sono riuscito a salvare l'archivio.</b>"
             + chr(10) + chr(10) + "I bandi trovati non sono andati persi: li rimetto al "
             "prossimo giro. Ecco cosa e' successo:" + chr(10) + chr(10)
             + "<pre>" + escape(coda) + "</pre>")
    try:
        avvisi.manda_telegram(token, chat, testo)
        print("errore raccontato su Telegram")
    except Exception as e:
        print("non sono riuscito nemmeno ad avvisare:", type(e).__name__)
PY
fi

exit "$ESITO"
