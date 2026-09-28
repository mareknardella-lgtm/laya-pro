# Gestione della Memoria

Laya Pro implementa un sistema di memoria persistente e di estrazione del contesto basato su **SQLite**, assicurando che le conversazioni e le informazioni rilevanti vengano conservate tra le sessioni.

## Database SQLite Persistente
Tutti i dati storici sono archiviati in un database SQLite locale. Questo approccio garantisce:
- Accesso rapido in lettura e scrittura.
- Nessuna dipendenza da server esterni per la storicizzazione.
- Durabilità dei dati tra i riavvii della piattaforma.

## Cronologia delle Chat (Chat History)
La cronologia delle conversazioni viene salvata in tempo reale nel database. Quando una nuova sessione viene avviata, Laya Pro recupera la cronologia recente per fornire a Nemotron il contesto necessario a mantenere un dialogo fluido e coerente.

## Estrazione di Memoria RAG
Per richieste che richiedono informazioni specifiche condivise in precedenza, Laya Pro utilizza un meccanismo RAG (Retrieval-Augmented Generation). 
- I frammenti chiave delle conversazioni passate vengono indicizzati o rintracciati dinamicamente.
- Durante una query complessa, il sistema estrae dal database SQLite solo le informazioni pertinenti, iniettandole nel prompt finale (System 2) per generare risposte accurate senza sovraccaricare la finestra di contesto del LLM.
