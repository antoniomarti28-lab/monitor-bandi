# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Una pagina sola, usata da persone diverse, ognuna col suo **profilo**:

- **Antonio**, che la gestisce. Non e' un programmatore. Segue Garage68
  (associazione culturale non riconosciuta, Vibo Valentia) e Labo Art Tropea (APS
  iscritta al RUNTS), e ha creato il profilo di Mati. E' l'unico che modifica
  profili e fonti dalla pagina (il codice di accesso a GitHub sta nel suo browser).
- **Le persone dei profili**, che aprono la pagina da sole (confermato il 28 set
  2026): oggi chi sta dietro Garage68 e Labo Art Tropea, e **Mati**, ballerina di
  danza contemporanea, che cerca audizioni di compagnie contemporanee in Italia e
  in Europa e lavori singoli in Italia.

Si apre **sia dal telefono** (quasi sempre dal link di un avviso Telegram) **sia dal
computer**, per leggere con calma e decidere.

Il lavoro che fanno sulla pagina, in ordine di importanza per chi la usa:
1. **Non perdersi niente**: vedere tutto quello che e' arrivato per il proprio
   profilo, compresi i «forse».
2. **Tenere in ordine le fonti**: controllare che funzionino, correggerle,
   aggiungerne, accettare o rifiutare quelle proposte.

## Product Purpose

Ogni giorno controlla da sola una lista di fonti (enti, fondazioni, ministeri,
Comuni, compagnie di danza, portali di audizioni), riconosce gli annunci utili a
ciascun profilo, li fa leggere per intero a un modello linguistico e avvisa su
Telegram. Esiste perche' i bandi e le audizioni sono sparsi su centinaia di siti
e scadono prima che qualcuno se ne accorga.

Funziona se nessun annuncio adatto a un profilo va perso, e se chi apre la pagina
capisce subito se puo' candidarsi, entro quando, e quanto ottiene o se viene pagato.

## Positioning

- **Dice se *tu* puoi parteciparci**, non solo che il bando esiste: il modello
  legge il documento intero (PDF compresi) e da' un verdetto si / forse / no con il
  motivo, che vince sul punteggio a parole.
- **Dice quanto puoi ottenere tu** (il contributo per il singolo), non solo la
  dotazione del bando; per le audizioni: compagnia, citta', pagato / non pagato /
  stage / a pagamento.
- **Costa zero** e gira da sola, senza computer acceso.
- **Funziona per qualunque tipo di soggetto**: associazioni, APS, persone singole.
- **Ogni profilo ha le sue fonti**, e le fonti rotte si riparano da sole.

## Operating Context

- Gira su GitHub Actions: un giro al giorno (in pratica parte con ore di ritardo) e
  un ricalcolo ogni volta che si cambia qualcosa dalla pagina (~2-3 minuti).
- La pagina pubblicata e' un file statico su GitHub Pages con i dati dentro; senza
  il codice di accesso e' in sola lettura. Esiste anche la versione locale
  (`server.py`), che Antonio non usa.
- Gli avvisi arrivano su una sola chat Telegram, quella di Antonio, per tutti i
  profili.
- Il modello (Groq, piano gratuito) ha un limite giornaliero: gli annunci nuovi
  vengono letti a scaglioni, al massimo 60 al giorno. Un annuncio non ancora letto
  e' visibile ma senza verdetto.
- La ricerca di fonti nuove propone e non aggiunge mai da sola.

## Capabilities and Constraints

- Tre profili oggi: Garage68, Labo Art Tropea, Mati. Profilo = tipo di soggetto,
  settori, regioni, parole, parole escluse, racconto libero.
- Filtri: profilo, verdetto, zona, fonte, «Solo i nuovi» (48 ore), chiusi,
  archiviati, ricerca a testo.
- **Tutto in italiano**, interfaccia compresa. Gli annunci stranieri restano nella
  loro lingua; riassunto e motivi sono in italiano.
- **Costo zero** (decisione ripetuta tre volte: niente server a pagamento), niente
  modelli locali, notifiche solo su Telegram (WhatsApp scartato).
- Zero dipendenze tranne `pypdf`; pagina in HTML e CSS senza framework.
- Il peso della pagina conta: e' gia' stata dimezzata una volta (da 1 MB a 504 KB).
- **Chi guarda senza poter modificare vede solo i suoi annunci** (deciso il 28 set
  2026): fonti, soglie di avviso e avvisi tecnici su GitHub sono per chi gestisce.
- Aperto: nessun dato su eta' e citta' di Mati (alcune audizioni li richiedono).

## Brand Commitments

Nessun marchio da rispettare. «Monitor Bandi» e' il nome di lavoro. Voce: italiano
semplice e diretto, che dice cosa succede e cosa puoi fare tu, e che racconta i
difetti invece di nasconderli.

## Evidence on Hand

- Archivio reale: circa 700 annunci in `dati.db`, 145 fonti in
  `configurazione.json` (di cui 109 compagnie di danza verificate il 28 set 2026).
- Storico delle notifiche partite (tabella `notifiche`).
- Nessuna testimonianza, nessun utente esterno, nessun dato d'uso: non vanno
  inventati.

## Product Principles

1. **Meglio mostrare troppo che perdere un annuncio.** Cio' che viene nascosto
   (scartati, chiusi) resta a un clic, e la pagina dice quanti sono.
2. **Il verdetto di chi ha letto il documento vince sul conteggio delle parole**, e
   deve sempre dire il perche'.
3. **Ogni numero promette esattamente cio' che mostra**: un conteggio che non
   coincide con l'elenco e' un difetto, non un dettaglio.
4. **Mostrare lo stato vero**: da quanto sono fermi i dati, cosa e' ancora da
   leggere, cosa sta succedendo mentre un lavoro gira, cosa si e' riparato da solo.
5. **Chi usa la pagina non deve toccare codice**: ogni correzione si fa con un clic
   o si fa da sola.
