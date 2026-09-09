# Monitor Bandi

App che ogni giorno controlla una lista di fonti, riconosce i bandi utili e avvisa su Telegram.
Utente: Antonio, non programmatore. Tutto in italiano, interfaccia compresa.

## Decisioni prese (non rimetterle in discussione senza chiedere)

- **Notifiche: Telegram.** WhatsApp scartato: richiede azienda verificata, modelli approvati da Meta
  e dal 1 ottobre 2026 fa pagare anche i messaggi utility dentro le 24 ore.
- **LLM: solo Groq**, niente Ollama. Il PC dell'utente non deve lavorare.
  Limite vero del piano gratuito: **~100k gettoni al giorno per modello** (non le 14.400 richieste).
  Quindi: all'LLM non si manda mai il bando intero, solo i paragrafi che servono; modello piccolo
  per la scrematura, modello grande solo sui finalisti.
- **Dove gira: GitHub Actions**, repository PUBBLICO `antoniomarti28-lab/monitor-bandi`,
  pagina su `https://antoniomarti28-lab.github.io/monitor-bandi/`. Un giro alle 7 del mattino.
  Primo giro riuscito l'8 set 2026: 111 bandi, 8 avvisi Telegram partiti davvero. Oracle e' stato scartato: non riusciva a creare l'account. Conseguenza da ricordare:
  la pagina pubblicata su GitHub Pages e' **di sola lettura**; «Archivia» e la modifica dei
  profili funzionano solo nell'app locale, e le modifiche vanno rimesse nel repository.
  I codici segreti stanno nelle Secrets di GitHub e si leggono dalle variabili d'ambiente
  `MONITOR_TELEGRAM_TOKEN`, `MONITOR_TELEGRAM_CHAT`, `MONITOR_GROQ_CHIAVE`
  (vedi `avvisi.carica()`). `impostazioni.json` e' in `.gitignore` e non va mai committato.
- **Zero dipendenze, con UNA eccezione: `pypdf`**, installata in Fase 4 perche' i PDF
  non si leggono con la libreria standard. Niente altro: nessun framework, nessun bundler.
  Sul server servira' quindi `pip install pypdf` e nient'altro.

## Come e' fatto

- `configurazione.json` — **la fonte di verita'**: profili, feed, siti, soglie, archiviati.
  La pagina online lo riscrive tramite l'API di GitHub; l'app locale tramite
  `/api/configurazione`. `dati.db` lo ricopia a ogni giro (`configurazione.importa`).
- `fonti.json` — serve solo come elenco iniziale dei feed alla prima installazione:
  dopo, i feed vivono dentro `configurazione.json` e si accendono/spengono dalla pagina.
- `raccogli.py` — un giro di raccolta: legge i feed, estrae, salva in `dati.db` (SQLite).
  Rispetta robots.txt, 2 secondi di pausa tra una richiesta e l'altra.
- `server.py` — la pagina di consultazione su `http://localhost:8077`.
- `pagina/` — interfaccia (HTML + CSS, nessun framework).
- `anteprima.html` — copia della pagina con i dati dentro, da mandargli per farla guardare
  senza avviare niente. Si rigenera quando serve. E' anche la pagina pubblicata su GitHub
  Pages, e nasce in **sola lettura**: `SOLA_LETTURA` toglie i pulsanti che scrivono invece
  di lasciarli fingere di funzionare.
- `comandi.py` — i comandi Telegram (`/profilo`, `/sono`, `/fonte`, `/fonti`, `/aperti`).
  Girano ogni quarto d'ora con `.github/workflows/comandi-telegram.yml`. Solo la chat
  configurata puo' dare comandi. Il menu del bot si registra con `--registra`.

## Le sei fasi

1. **fatta** — fonti ufficiali, database, elenco visibile, archiviazione.
2. **fatta** — profili a caselle da spuntare, punteggio 0-100 e motivo mostrato accanto a ogni bando
   (`profili.py`). Nessun LLM: sono conteggi di parole. Soglia di ammissione: 40.
3. **fatta** — avvisi Telegram (`avvisi.py`), email facoltativa, storico in tabella `notifiche`.
   Un bando si avvisa UNA volta per profilo (colonna `avvisato`). Sopra 8 bandi in un giro,
   un solo messaggio di riepilogo invece di otto notifiche. Soglia di avviso: 55.
   Il codice del bot sta in `impostazioni.json`: file locale, non finisce mai nell'anteprima.
4. **fatta** — siti senza feed (`estrattore.py` + `siti.py`), lettura dei PDF, e
   l'approfondimento che scarica il testo completo di ogni bando (materiale per la Fase 5).
   I siti si aggiungono dalla pagina, in fondo, incollando un indirizzo.
5. **fatta e funzionante** (`intelligenza.py`). Due passaggi: il modello
   piccolo legge ogni bando una volta (aperto/chiuso, scadenza, importo, destinatari,
   riassunto); il grande giudica «puoi parteciparci?» solo sui promossi dal filtro.
   Il conto dei gettoni sta nella tabella `consumo`: sopra il limite ci si ferma e si
   riprende domani. `python intelligenza.py --prova` mostra tutto senza spendere niente.
6. **fatta** — la pagina mostra di default SOLO i bandi ancora aperti (spunta «Mostra anche
   i chiusi» per vedere gli altri), riquadri Aperti / Entro 30 giorni / Letti dal modello /
   Archiviati, e la sezione «Notifiche che ti ho mandato».

## Cosa si e' gia' imparato sul campo

- **8 fonti su 14 funzionano.** Le altre non hanno un feed: vanno prese con lo scraping (Fase 4).
- **Le scadenze non si estraggono con le regole.** Provato su 8 pagine intere: 2 trovate e
  **entrambe sbagliate** (date del menu del sito). Gli importi invece si trovano (5 su 8).
  Conclusione: scadenza e requisiti sono lavoro della Fase 5, non della regex.
- **I feed mescolano notizie e bandi.** Serve un passaggio "questo e' davvero un bando?" prima
  di notificare qualcosa, altrimenti arrivano avvisi per articoli di giornale.
- Alcuni feed (Regione Calabria) iniziano con uno spazio prima dell'XML: va tolto o il lettore
  va in errore.
- Le pagine dei bandi non sono tutte in UTF-8: quando in Fase 4 si leggera' l'HTML,
  usare la codifica dichiarata nelle intestazioni, non presumere UTF-8.
- **Le parole si cercano solo a inizio parola** (`contiene()` in `profili.py`). Cercandole
  in mezzo, "arte" si trovava dentro "parte" e "sport" dentro "trasporto": due bandi su
  ventisette erano falsi. La fine resta libera, cosi' "cultura" trova ancora "culturale".
- **I codici HTML si sciolgono con `html.unescape`, due volte.** Con una lista scritta a mano
  ne sfuggivano parecchi (`&#x27;`) e finivano dritti nel titolo del messaggio Telegram.
- **«Graduatoria» e «vincitori» sono indizi di NOTIZIA, non di bando.** Se c'e' la graduatoria
  il bando e' gia' chiuso. Quando in un testo compaiono insieme parole da bando e parole da
  articolo, si tolgono 20 punti invece di scartare: cosi' i tre messaggi di prova sono
  scesi da 5 a 3 e i due spariti erano proprio quelli sbagliati.
- **Una pagina «Bandi» vale piu' di un feed.** Dal feed di Fondazione Con il Sud arrivavano
  notizie miste; dalla sua pagina `/bandi/` sono arrivati 15 bandi veri con l'importo giusto
  in 14 casi su 15. Quindi: quando si aggiunge un ente, cercare la sua pagina degli elenchi,
  non il suo feed di notizie.
- **Dal testo completo NON si estrae la scadenza** (`siti.approfondisci`): provato, le date
  trovate erano quelle del menu del sito. Dal testo completo si prende solo l'importo,
  che invece funziona. La scadenza e' lavoro della Fase 5.
- **Il ritaglio dei paragrafi taglia il 53-76%** sui testi lunghi (misurato sui bandi veri):
  da ~1.450 gettoni a ~400. Sotto i 4.500 caratteri il testo si manda intero, e va bene cosi'.
- **Quando il modello conferma «aperto», i conteggi di parole vanno SPENTI, non sommati.**
  Al primo innesto un bando confermato aperto poteva ancora essere scartato dalla regola
  sulle notizie, perche' il ramo `elif notizia` restava raggiungibile. Il verdetto del
  modello ha l'ultima parola.
- **Mai lasciare dati finti nell'archivio.** Per mostrargli la forma della Fase 5 sono stati
  inseriti riassunti inventati, poi rimossi con una UPDATE che azzera le colonne del modello.
  Se serve rifarlo: la pagina di esempio si marca con una fascia rossa e un nome diverso.
- **Il difetto noto della Fase 2 sono i mancati, non i falsi.** Un bando vero il cui titolo
  non nomina nessun settore (es. «Bando Riabitare il Sud») si ferma a 33 punti e resta fuori.
  Non si aggiusta abbassando la soglia: si aggiusta in Fase 5, leggendo la pagina intera.
  Da rimisurare quando la Fase 5 e' pronta.


## Groq: le cose che costano un pomeriggio se non si sanno

- **403 «error code: 1010» non e' la chiave sbagliata: e' Cloudflare.** Rifiuta le richieste
  senza `User-Agent`. Basta metterne uno qualsiasi.
- **I modelli cambiano.** I `llama-3.x` non sono piu' sul piano gratuito. Oggi si usano
  `openai/gpt-oss-20b` (lettura) e `openai/gpt-oss-120b` (giudizio).
  Per l'elenco valido oggi: `python intelligenza.py --modelli`.
- **gpt-oss «ragiona» prima di rispondere.** Due conseguenze: `max_completion_tokens` deve
  essere LARGO (2500), altrimenti il ragionamento mangia il budget, il JSON resta a meta' e
  Groq risponde 400 `json_validate_failed`; e `reasoning_effort: "low"` fa scendere il costo
  da 1853 a 1162 gettoni a bando, con la stessa risposta.
- **Esiste anche un limite AL MINUTO** (~8.000 gettoni). Andando a raffica si prende un 429
  dopo 5 bandi. Si aspetta in proporzione a quanto si e' appena speso: ~10 secondi a bando.

## Cosa si e' imparato leggendo i bandi veri

- **Il calendario batte il modello.** Dava per aperto un bando scaduto nel 2024. Se la
  scadenza e' passata, `aperto` va forzato a 0 comunque.
- **Il contributo per il singolo richiedente sta nel PDF, non nella pagina.** Da qui
  `estrattore.leggi_con_allegati`: il PDF va messo PRIMA e la pagina troncata a 1500
  caratteri, perche' molte pagine «Bandi» sono archivi che elencano anche le edizioni
  vecchie e il modello ne pescava la scadenza sbagliata.
- **Il ritaglio si fa a quote separate** (65% documento, 35% pagina): il PDF ha il
  contributo, la pagina ha la scadenza. Ritagliando tutto insieme se ne perdeva sempre uno.
- **I paragrafi dei PDF vanno spezzati** in pezzi da 700 caratteri, altrimenti superano da
  soli il budget e vengono scartati interi.
- **L'importo letto dal modello SOSTITUISCE quello delle regole**, non il contrario: la
  regola pescava la cifra della prima edizione elencata nella pagina.
- **La pagina «Bandi» della Fondazione Con il Sud e' un archivio storico**: dei 15 bandi
  presi, 13 erano gia' scaduti. Il modello li ha riconosciuti tutti.


## Regola di sincronizzazione (importante, ci si sbaglia facilmente)

Da quando gira su GitHub, **l'archivio buono e' quello su GitHub**, non quello locale:
ogni giro aggiunge bandi e riscrive `dati.db`. Quindi, prima di toccare qualcosa in locale:

    git pull

E dopo aver cambiato i profili con `Avvia Monitor Bandi.bat`:

    git add dati.db && git commit -m "profili aggiornati" && git push

Senza il `pull` prima, il push viene rifiutato e si rischia di sovrascrivere un giro intero.


## Tutto si modifica dalla pagina (9 set 2026)

Profili, feed, siti, soglie di avviso e archivio si cambiano dalla pagina pubblicata,
non solo dall'app locale. Meccanica:

- la pagina legge e riscrive `configurazione.json` con l'API di GitHub;
- il codice di accesso (token fine-grained, permesso Contents) sta nel `localStorage`
  del suo browser, mai nel repository e mai nella pagina;
- il push su `configurazione.json` fa partire `aggiorna-impostazioni.yml`, che importa,
  controlla subito le fonti nuove, rivaluta i giudizi e ripubblica (~2 minuti);
- quel workflow si esclude da solo quando il commit viene dai giri automatici,
  altrimenti si richiamerebbero a vicenda all'infinito.

**Trappole trovate costruendolo:**
- la finta rete dell'anteprima (`window.fetch` sostituito) intercettava anche le chiamate
  a GitHub: ora passa oltre tutto cio' che non inizia per `/api/`;
- gli errori di salvataggio finivano in una promise non gestita e l'utente non vedeva
  niente: ogni scrittura passa da `prova()`, che li mostra a schermo;
- `document.querySelector(".aggiungi-sito")` prendeva il modulo sbagliato quando i moduli
  con quella classe sono diventati due: usare gli id.

## Ritardi di GitHub (misurati il 9 set 2026)

Le pianificazioni gratuite partono MOLTO in ritardo: il giro delle 7 e' partito alle 11:28,
e i comandi Telegram «ogni 15 minuti» sono partiti ogni 4-5 ore. Non e' aggirabile.
Se un giorno diventa un problema, l'unica cura e' un server vero (~5 €/mese): gliel'ho
proposto tre volte e ha sempre scelto il gratis.


## Aspetto (9 set 2026) — riferimento: incentivicalabria.it

Lui ha indicato quel sito. Preso per la FORMA, non copiato: caratteri **Sora** (titoli)
e **Inter** (testo, 17px) da Google Fonts, sfondo bianco, nero caldo `#14140f`,
accento **terracotta** `#c1440e`, **verde** `#1c7a3a` per aperto e per «puoi
parteciparci», righe **beige** `#e9e7dc`. Il tema scuro e' derivato dagli stessi colori.

L'idea rubata che vale: **l'etichetta del tipo di aiuto** sopra il titolo
(«fondo perduto», «prestito agevolato», «voucher», «premio», «servizi»). La ricava
Groq insieme al resto: campo `tipo_aiuto`, null quando non si capisce dal testo.

**Groq, terza trappola sui gettoni:** aggiungendo un campo alla risposta il JSON ha
ricominciato a troncarsi (400 `json_validate_failed`) con `max_completion_tokens` a
2500. Alzato a 4000: non costa nulla, si paga solo quello che il modello scrive.
Il budget giornaliero autoimposto e' passato da 90.000 a 140.000 gettoni; se il limite
vero arriva prima, il 429 ferma il giro da solo senza rompere niente.


## Ricerca di fonti nuove (`scopri.py`, 9 set 2026)

Tre passi: raccolta, verifica, giudizio. **Le fonti non vengono mai aggiunte da sole**:
finiscono in `configurazione.json` sotto `proposte` e compaiono sulla pagina con
«Accetta» / «No». Gira il lunedi', dentro il giro quotidiano.

**Il dato che giustifica tutta l'architettura:** alla prima prova vera il modello ha
proposto 13 portali e **11 erano inventati** (404 o domini inesistenti come
`fondazionecarica.it`, `mintern.gov.it`). La verifica — aprire davvero ogni indirizzo e
contare i collegamenti a bandi — li ha scartati tutti. Senza quel passo, l'elenco fonti
si riempirebbe di indirizzi morti. **La verifica non e' un di piu': e' il pezzo che
rende usabile la ricerca.**

La raccolta «grounded» (seguire i collegamenti dei bandi gia' letti) e' onesta ma rende
poco: da 128 pagine e' uscito **1 candidato**. Serve insieme all'altra, non da sola.

## Da fare quando i token diventeranno stretti (chiesto da lui, NON implementato)

Oggi il modello legge tutti i bandi nuovi senza distinzioni. Quando le fonti cresceranno
servira' una **priorita' di lettura**. Ordine sensato, dal piu' al meno promettente:
1. bandi da fonti che in passato hanno prodotto compatibilita' alte;
2. bandi il cui punteggio a parole e' gia' sopra soglia;
3. bandi con scadenza vicina (leggere prima quelli che stanno per chiudere);
4. tutto il resto, a scalare, nei giorni successivi.
Il conto dei gettoni (`consumo`) c'e' gia' e si ferma da solo: manca solo l'ordinamento
in `intelligenza.da_leggere`, che oggi ordina per `trovato_il DESC`.


## Collaudo del 9 set 2026 (giro forzato dalla pagina)

Il meccanismo del «Cerca adesso» funziona: scrivere `richiesta_giro` in
`configurazione.json` fa partire il lavoro, che esegue il giro intero.
Risultato: 143 bandi (32 nuovi), 118 letti, 14 aperti, 3 avvisi partiti.

**Due difetti trovati proprio grazie al collaudo:**

1. **Il salvataggio finale falliva in silenzio** (il giro riusciva, ma l'archivio non
   tornava su GitHub). Causa: `configurazione.json` veniva riscritto durante il giro
   ma non messo fra i file da salvare, e `git pull --rebase` si rifiuta di partire con
   modifiche non salvate. Ora tutti e tre i lavori usano **`salva.sh`**, che mette da
   parte tutti i file generati, riprova il push tre volte e — se proprio non riesce —
   **manda l'errore su Telegram**, invece di lasciarlo in un registro che l'utente non
   puo' leggere (i log delle Actions richiedono permessi che il suo token non ha).
2. **Si avvisava di bandi non ancora letti dal modello.** Su tre avvisi, due erano di
   questo tipo e uno era un articolo («le foto selezionate di un festival»). Ora si
   avvisa **solo** con `aperto = 1`, cioe' letto e confermato. Meglio un giorno di
   ritardo che un avviso sbagliato.

**Trappola Windows→Linux:** gli script `.sh` vanno committati con fini riga Unix, o
bash su Ubuntu si ferma su ogni riga. Risolto con `.gitattributes` (`*.sh text eol=lf`).
