# OB1 — Dove siamo e dove andiamo

**10 luglio 2026**

Questo documento fa due cose: guarda in faccia cosa il sistema ha davvero prodotto in
cinque mesi (senza raccontarsela), e disegna la versione che vogliamo — quella che un club
non riesce a rifiutare.

Una premessa che cambia tutto: **il pilota non è mai partito davvero.** K-Sport ha visto una
demo, gli è piaciuta, ma non c'è stato nessun test formale né vincolo contrattuale. Le
regole rigide che ci eravamo dati ("non toccare niente") erano una disciplina utile per non
fare pasticci, non un obbligo verso nessuno. Quindi da qui in poi **abbiamo mano libera**.

---

## In una riga

Oggi OB1 è un radar che gira bene ma spara nomi di cui spesso non possiamo garantire nulla.
La v2 è un radar che ti dà **solo nomi che reggono una telefonata di verifica** — e su
ognuno ti mette in mano le prove.

---

## Da dove partiamo: cosa ha prodotto il sistema

Ho analizzato il database vero di produzione: 67 giocatori raccolti da febbraio a oggi.
Ecco cosa funziona e cosa no, con i numeri alla mano.

### Quello che funziona (e teniamo)

- **Costa quasi zero e non si rompe.** Gira da solo 4 volte al giorno da mesi, su
  infrastruttura gratuita. Questa parte è solida: non è da buttare.
- **Sa seguire un giocatore nel tempo.** Molti nomi vengono ritrovati run dopo run — Bruno
  Baldini è stato visto 79 volte. La memoria funziona.
- **Sa fare una scheda seria quando gliela chiediamo a mano.** Il dossier su Callegari
  (quello per Cenci) l'abbiamo costruito con le stesse fonti gratuite in circa un'ora. Quello
  è il pezzo "premium" del sistema, ed è già lì.

### I quattro problemi veri

**1. Non riusciamo a dimostrare la promessa principale.**
La tesi di OB1 è "te lo diciamo prima degli altri". La misura di questo si chiama *lead
time* (giorni di anticipo sul mainstream). Bene: su 33 casi misurati, **24 hanno anticipo
zero**. E la misura stessa è rotta — il sistema crede che André Maia sia "diventato famoso"
per via di un articolo di **arti marziali (UFC)**, e Pirituba per un articolo della **BBC del
2013**. In pratica oggi non abbiamo una prova difendibile del nostro valore. Questa è la cosa
più grave.

**2. Il "radar globale" in realtà guarda quasi solo il Brasile.**
39 giocatori su 67 sono brasiliani (58%). Africa: **zero**. Giappone e Corea: **zero**.
Eppure quasi metà delle ricerche puntano proprio su Africa e Asia — semplicemente non tornano
con niente. Cerchiamo dappertutto ma vediamo solo dove il web è più rumoroso.

**3. Metà dei nomi non sono nomi.**
24 giocatori su 67 hanno un nome solo o un soprannome: "Sorriso", "Pirituba", "KG9",
"Cauazinn_.08". Roba invendibile a un direttore sportivo, che non può nemmeno verificarla.
E sono entrate anche **calciatrici** (Dulce Maria, Tainá, Clarinha — Corinthians femminile),
perché nessuno ha mai deciso se cerchiamo maschi, femmine o entrambi.

**4. In cima alla lista mettiamo i nomi più deboli.**
I tre giocatori col punteggio massimo (100) — Pirituba, Dulce Maria, Dinics — sono tutti
visti **una volta sola, da una fonte sola**. Intanto Baldini, confermato **79 volte**, sta
sotto di loro. Cioè: la prima cosa che un club vede aprendo la dashboard sono i tre nomi meno
affidabili. È l'esatto opposto di "irresistibile".

---

## Perché succede

Non è colpa della tecnologia "vecchia" (i cron, il database semplice, le pagine statiche:
quelle vanno benissimo, costano zero e non si rompono). Il problema è **come il sistema
ragiona sull'informazione**:

- **Cerca a caso invece di sapere dove guardare.** 20 ricerche fisse su Google & simili: vede
  quello che l'algoritmo di Google spinge in alto, non quello che conta. Da qui il Brasile
  ovunque.
- **Tratta un giocatore come "un nome in un articolo", non come una persona.** Ogni giro
  riparte da zero, non accumula prove su nessuno. Da qui i soprannomi e i punteggi campati in
  aria su un singolo articolo.
- **Non impara dai propri errori.** Il lead time doveva essere il modo in cui il sistema si
  auto-valuta, ma è rotto — quindi il sistema non sa mai se ha avuto ragione, e non migliora.

---

## La v2: cosa cambia per chi la usa

Quattro cambiamenti. Sono tecnici sotto il cofano, ma per il club si traducono in cose
concrete.

**Sapere dove guardare, invece di cercare a caso.**
Invece di 20 ricerche generiche, una lista curata di fonti che contano davvero (referti delle
federazioni, campionati giovanili, testate locali affidabili), controllate solo per le
novità. La ricerca generica resta, ma serve a *scoprire fonti nuove*, non a fare il lavoro di
tutti i giorni. Risultato: copertura vera anche fuori dal Brasile, e la vediamo regione per
regione.

**Ogni giocatore è una scheda che accumula prove.**
Un nome entra in dashboard **solo** se ha identità completa (nome vero + club + età) **e
almeno due fonti indipendenti**. L'intelligenza artificiale legge e estrae; il codice mette
insieme le prove e decide. Così il punteggio sale con l'evidenza, non nonostante l'evidenza —
e i soprannomi e le schede vuote non entrano proprio.

**Il sistema impara.**
Confronto col mainstream solo su nomi veri e verificabili (mai più il match con l'articolo
UFC). E teniamo traccia di cosa succede davvero ai giocatori segnalati — chi viene comprato,
convocato, fatto esordire — così il sistema ha un metro oggettivo per tararsi.

**Il dossier su richiesta, in automatico.**
Quello che ho fatto a mano per Callegari diventa un pulsante: il club chiede di un giocatore,
e in pochi minuti riceve una scheda verificata da più fonti. Il pezzo è già prototipato, va
solo industrializzato.

---

## Il prodotto irresistibile

Il punto di vendita non è "abbiamo l'intelligenza artificiale" — quella ce l'hanno tutti. Il
punto è uno solo:

> **Ogni nome che ti diamo regge una telefonata di verifica.**

Da lì scendono quattro promesse che nessun concorrente fa con le prove in mano:

1. **Ogni nome con le sue prove.** Niente nomi nudi: per ognuno, le fonti linkate, età, club,
   e il perché in linguaggio da addetto ai lavori. Se non è difendibile, non lo mostriamo.
2. **Dossier in minuti, non in giorni.** Chiedi di un giocatore qualsiasi — anche fuori dal
   nostro radar — e ricevi una scheda verificata in pochi minuti. È il lavoro per cui oggi si
   pagano giorni di uno scout. (Già dimostrato: Callegari.)
3. **L'anticipo, con le ricevute.** "Te l'abbiamo segnalato il giorno X; la stampa ne ha
   parlato il giorno Y — ecco i link." Verificabile da chiunque. È la prova nera su bianco che
   arriviamo prima.
4. **Un digest fatto su misura.** Ogni club riceve solo ciò che gli serve — ruoli che cerca,
   budget, aree — non un elenco indistinto.

---

## Come ci arriviamo

Il sistema attuale continua a girare (ci serve come fonte di dati) mentre costruiamo la v2 di
fianco. Nessun rischio, nessuna attesa di permessi.

1. **Fondamenta** — nuova struttura dati "per giocatore", e sistemiamo i 67 già raccolti
   segnando quali hanno identità buona e quali no.
2. **Estrazione seria** — l'AI estrae i dati da ogni fonte, il codice pretende almeno due
   fonti prima di pubblicare. Già qui la dashboard smette di essere rumorosa.
3. **Le fonti giuste** — costruiamo la lista di fonti curate per 2-3 aree prioritarie.
4. **La prova del valore** — anticipo misurato bene, tracciamento degli esiti, dossier
   automatico. Qui il prodotto diventa vendibile.

---

## Come sapremo che ha funzionato

| | Oggi | Obiettivo v2 |
|---|---|---|
| Nomi completi e verificabili in dashboard | ~2 su 3 | **tutti** |
| Giocatori con almeno 2 fonti | poco più della metà | **tutti** |
| L'anticipo si può dimostrare coi link? | no (misura rotta) | **sì** |
| Aree davvero coperte | 1 (Brasile) | almeno 3 |
| Falsi allarmi (tipo l'articolo UFC) | ci sono | ~zero |
| Tempo per un dossier su richiesta | ~1 ora, a mano | **minuti**, in automatico |
| Costo | ~zero | ~zero |

---

## Le scelte che restano tue

1. **Maschile, femminile o entrambi?** Oggi il femminile entra per sbaglio. Va deciso.
2. **Quali aree per prime?** Proposta: Sud America + Africa Occidentale + una terza a scelta.
3. **K-Sport:** anche se sono silenti, vale la pena riagganciarli — non per chiedere permesso,
   ma perché arrivare con "abbiamo capito cosa non andava e stiamo facendo la v2" è un aggancio
   molto più forte del silenzio.

---

*I numeri di questo documento vengono dal database di produzione al 10/07/2026 e sono tutti
verificabili.*
