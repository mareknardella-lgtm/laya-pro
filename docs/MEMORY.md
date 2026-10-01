# Gestione della Memoria

La memoria di Laya è un archivio di **fatti durevoli** su SQLite, non un database vettoriale. Ogni fatto è una coppia `chiave: valore` che viene iniettata nel prompt del motore veloce.

## Due origini, due trattamenti

| Origine | Come nasce | Quando entra nel prompt |
|---|---|---|
| **Pinnata** | Aggiunta a mano dall'utente (`POST /memory`) | **Sempre**, entro il limite di 4 voci |
| **Automatica** | Estratta dal tier veloce a fine conversazione | Solo se il testo della domanda ha parole in comune |

La distinzione è deliberata. Un fatto che l'utente ha scritto di sua iniziativa è pertinente per costruzione, quindi non ha senso filtriarlo per parole chiave. Un fatto estratto automaticamente, invece, entra solo se la domanda lo tocca: altrimenti ogni chat si trascinerebbe dietro l'intero archivio.

## Estrazione automatica

A ogni scambio riuscito, il tier veloce riceve un prompt invisibile con i due turni appena conclusi e risponde in formato `chiave: valore`, uno per riga, oppure `NONE` se non c'è nulla di durevole. Il parser accetta anche `- chiave: valore`, scarta le righe vuote e quelle troppo lunghe.

Se il tier veloce non risponde, l'estrazione fallisce **in silenzio**: la memoria degrada la risposta, non la impedisce. Una chat non deve fallire perché non si è riusciti a salvare un ricordo.

## Retrieval

Il filtro è lessicale e deterministico, non una chiamata extra all'LLM:

1. Si tokenizzano la domanda e gli ultimi 5 turni, rimuovendo le stopword italiane e inglesi.
2. Ogni voce non pinnata viene messa in punteggio per sovrapposizione normalizzata.
3. Restano solo le voci con punteggio > 0, fino a un massimo di 8 voci totali nel prompt.

Il vantaggio è la latenza: il percorso della memoria resta fuori dal budget del tier veloce. Il limite è noto e dichiarato — due fatti con parole completamente diverse non si incontrano mai. Se servisse vera ricerca semantica, il passo successivo è un indice di embedding, non un prompt più furbo.

## Scrittura e conflitto

La chiave primaria è `(key, scope)`. Scrivere di nuovo la stessa chiave **aggiorna** il valore esistente e non crea duplicati. Il flag `pinned` usa la stessa regola: un'estrazione automatica che riscrive una voce pinnata ne aggiorna il testo ma **non le toglie il pin**. Un fatto scelto dall'utente non può essere declassato silenziosamente.

## API

| Endpoint | Effetto |
|---|---|
| `GET /memory` | Tutte le voci, pinnate per prime |
| `POST /memory` | Crea o aggiorna una voce, **pinnandola** |
| `PUT /memory` | Modifica una voce esistente; `404` se non esiste |
| `DELETE /memory?key=...` | Dimentica una voce; `404` se non esiste |
| `POST /memory/query` | Ricerca per somiglianza lessicale |

Nella web UI il pannello **Memoria** fa tutto questo e mostra per ogni voce se è `PINNATO` o `AUTO`, con la data dell'ultimo aggiornamento.

## Storico conversazioni

Ogni turno è in `chat_messages` con il suo `session_id`. Gli ultimi 10 messaggi finiscono nel prompt successivo; il trace completo della pipeline è in `orchestration_traces` e può essere riletto anche a distanza di giorni.