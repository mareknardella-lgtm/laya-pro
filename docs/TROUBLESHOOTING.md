# Risoluzione dei Problemi

## `503` su MEDIUM o HARD: `laya-coreml transport failure`
- **Causa**: il runtime di ragionamento non è raggiungibile all'indirizzo `LAYA_COREML_URL`.
- **Soluzione**: verifica che il runtime sia attivo e che l'endpoint `/v1/reason` risponda. Controlla l'ultimo errore su `GET /status`.
- **Nota**: è il comportamento voluto. Il sistema non ricade su Nemotron, perché una risposta senza ragionamento che si presenta come ragionata è peggio di un errore.

## `503` su LOW: `Nemotron is not configured`
- **Causa**: `NEMOTRON_API_KEY` mancante o vuota.
- **Soluzione**: esporta la chiave e riavvia il backend. `GET /status` deve mostrare `configured: true` per `nemotron`.

## `502` su una modalità qualsiasi
- **Causa**: un motore ha risposto, ma non nel formato atteso (per esempio un piano con `steps` vuoti, o un `request_id` diverso da quello inviato).
- **Soluzione**: il problema è nel runtime, non nell'orchestratore. Confronta la risposta grezza con il contratto in [PROVIDERS.md](PROVIDERS.md).

## Risposta `status: "degraded"`
- **Causa 1**: il budget `LAYA_MAX_REFINEMENTS` è esaurito e il critico segnala ancora problemi.
- **Causa 2**: il critico ha emesso verdetto `rejected` sulla bozza.
- **Soluzione**: il testo è comunque la risposta migliore disponibile. Per più profondità alza `LAYA_MAX_REFINEMENTS`, oppure semplifica il problema da porre al tier veloce.

## Modalità HARD lentissima
- **Causa**: ogni ciclo aggiunge una chiamata al tier di ragionamento, che è deliberatamente lento (timeout di default 120s).
- **Soluzione**: usa `MEDIUM` quando la verifica finale non serve, e abbassa `LAYA_MAX_REFINEMENTS` a `1` se il piano è già sufficiente.

## Memoria che non ricorda nulla
- **Causa 1**: `auto_memory` è `false` nella richiesta.
- **Causa 2**: il tier veloce ha risposto `NONE` all'estrazione, cioè non ha trovato fatti durevoli nella conversazione.
- **Causa 3**: il retrieval lessicale non ha trovato sovrapposizione tra la domanda e le voci salvate.
- **Soluzione**: verifica con `POST /memory/query` e con `GET /memory` se la voce esiste.