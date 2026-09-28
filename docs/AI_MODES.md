# Modalità AI

Laya Pro implementa tre modalità operative principali, bilanciando l'uso tra System 1 (JEV) e System 2 (Nemotron).

## LOW
- **Flusso**: Diretto a Nemotron.
- **Stato**: Operativa.
- **Descrizione**: La richiesta dell'utente viene inviata direttamente al modello di linguaggio (System 2) senza pianificazione intermedia. Ideale per compiti conversazionali e risposte dirette.

## MEDIUM
- **Flusso**: Pianificazione JEV -> Nemotron.
- **Stato**: Attualmente fallisce con l'errore \JEVUnavailable\.
- **Descrizione**: Il System 1 genera un piano d'azione che viene poi interpretato e arricchito da Nemotron. *Nota: Poiché il backend JEV non è ancora completamente connesso, questa modalità restituirà un'eccezione.*

## HARD
- **Flusso**: Pianificazione JEV Profonda -> Nemotron.
- **Stato**: Attualmente fallisce con l'errore \JEVUnavailable\.
- **Descrizione**: Coinvolge un'analisi multi-step e iterativa da parte di JEV prima di passare i risultati a Nemotron. *Nota: Condivide le stesse limitazioni attuali della modalità MEDIUM.*
