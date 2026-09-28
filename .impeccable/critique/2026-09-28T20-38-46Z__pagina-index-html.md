---
target: pagina di Monitor Bandi
total_score: 21
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:C:\\Users\\anton\\Desktop\\Claude\\bandi\\pagina\\index.html"
target_fingerprint: "sha256:d2ab5b9abb842cae34f2b646af56596766b2a177e8a646cd4733a5c7b71f2c53"
target_path: "C:\\Users\\anton\\Desktop\\Claude\\bandi\\pagina\\index.html"
timestamp: 2026-09-28T20-38-46Z
slug: pagina-index-html
---
Metodo: due revisori separati (A: revisione di design; B: controlli automatici e misure nel browser).

## Voto: 21/40 (accettabile)
| # | Criterio | Voto | Problema |
|---|---|---|---|
| 1 | Stato del sistema | 2 | «ancora da leggere» sempre 0 (manca il testo nella pagina pubblicata) mentre 187 annunci non sono letti; fonti contate male |
| 2 | Lingua di chi usa | 2 | radici di parole in «Perché ti riguarda», punteggio non spiegato, lessico da bandi per Mati |
| 3 | Controllo e liberta' | 3 | manca «azzera filtri» e il link diretto a un annuncio |
| 4 | Coerenza | 2 | 3 etichette sopra il titolo + 2 sotto, date in due formati, aiuto che nomina «Correggi» |
| 5 | Prevenzione errori | 2 | modulo «Aggiungi fonte» visibile e usabile in sola lettura (.aggiungi-sito display:flex batte [hidden]) |
| 6 | Riconoscere | 2 | «forse» dentro una tendina da 6 voci, nessun riepilogo dei filtri |
| 7 | Scorciatoie | 2 | niente ordine per scadenza, vista compatta, link da Telegram |
| 8 | Essenzialita' | 1 | schede da 820-1160 px al telefono, «Leggi di piu'» ripete |
| 9 | Recupero errori | 2 | errori tradotti bene ma a volte fuori contesto («va tolta» a chi non puo') |
| 10 | Aiuto | 3 | spiegazioni generose, a volte verbose |

## Specificita'
Aspetto generico da sito di annunci (terracotta, Sora/Inter, schede arrotondate). Specifici e ottimi: riquadro verde del verdetto, punteggio tratteggiato se smentito, «Puoi ottenere», «a pagamento: paghi tu». Verdetto e scadenza non guidano la composizione; pagina identica per associazioni e ballerina.
Rilevatore: pagina/index.html 6 (side-tab x2: .collegamento stile.css:100 decorativo, .stato-ricerca :322 funzionale; low-contrast x4 bianco su #e2743f = 3,1:1 solo tema scuro, 0 nel chiaro). anteprima.html 7 (+ pulsing-dot, falso positivo: eccezione con ambito solo sul modello). Sovrapposizione nel browser non disponibile (ERR_BLOCKED_BY_CLIENT verso localhost).
Misure: 2,12 MB decodificati (455 KB compressi), prima 504 KB; tabella fonti sfora di 86 px a 375; 401 da api.github.com a ogni apertura; tocchi piccoli («Vai al bando» 24 px, spunte 22, summary 24).

## Problemi
- [P1] Numeri che non corrispondono: chip 77/64/104 vs elenchi 14/12/92; «Aperti» 62/60/101 conta gli scartati; «Entro 30 giorni» 35 vs 8; «15 fonti attive su 16» conta solo i feed (145 fonti); «da leggere» sempre 0; zone globali («Sicilia (227)» per Mati); tendina fonti senza le 129 pagine. -> harden
- [P1] Arrivo da Telegram al telefono: niente link all'annuncio, si apre su PROFILI[0], prima scheda a 1036 px su 812, scadenza in fondo, tocchi piccoli. -> adapt
- [P1] Mati: 81/92 senza verdetto, «nuovo» su audizioni 2024 (trovato_il, non pubblicazione) contate come aperte; audizione classica a New York «si»; Info Day Interreg primo per Garage68. -> distill
- [P2] Scheda lunga e ripetitiva: fino a 12 elementi, pillole ente/zona/fonte duplicate, radici di parole, «Leggi di piu'» ripete e mostra spazzatura dei siti, 3 link uguali. -> layout
- [P2] Sola lettura: avviso GitHub come prima cosa per chiunque, modulo Aggiungi visibile, «va tolta» a chi non puo', contrasto nel tema scuro. -> clarify

## Persone
Chi arriva da Telegram al telefono: link in cima, piu' di uno schermo prima del primo annuncio, «↑» copre i titoli, «Leggi di piu'» manda fuori schermo «Chiudi».
Chi la apre la prima volta: messaggio di esclusione, punteggio non spiegato, riquadri contraddittori, notizie in «Tutti i bandi».
Mati: parte da Garage68, 104/92/101, 81 senza verdetto, lessico da bandi, zone siciliane, radici di parole, nessun filtro per compagnia.

## Minori
«·» a capo nella riga artista; data ISO in «Letto dal modello il»; hover fisso #d8d4c4 nel tema scuro; «Solo i nuovi» senza numero a 0; titolo «Monitor Bandi - anteprima».

## Domande
1. Servono quattro riquadri o basta una frase? 2. Chi non modifica deve vedere fonti, soglie e avviso GitHub? 3. Scheda intorno a «entro quando e quanto prendo»? 4. «Nuovo» = trovato da noi o uscito?
