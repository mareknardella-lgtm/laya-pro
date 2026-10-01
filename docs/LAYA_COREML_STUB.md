# Avviare MEDIUM e HARD: lo stand-in di laya-coreml

## Il problema

`laya-coreml` è il runtime di **deep reasoning**. Non esiste ancora: nessuno lo ha implementato. Senza di lui, MEDIUM e HARD falliscono con `503 laya-coreml transport failure`. È deliberato — l'orchestratore non ricade sul modello veloce, perché una risposta senza ragionamento che si presenta come ragionata è peggio di un errore.

## La soluzione per sviluppare: `tools/laya_coreml_stub.py`

Uno stand-in locale che parla lo stesso contratto JSON del runtime reale, così MEDIUM e HARD si possono provare end to end.

```bash
python tools/laya_coreml_stub.py
```

Dejalo in un **secondo terminale**, poi nell'altro avvia l'app:

```bash
python run.py
```

All'avvio `run.py` controlla se il runtime risponde e te lo dice:

```
Laya Pro  ->  http://127.0.0.1:8000   (db: data\laya.db)
ATTENZIONE: laya-coreml non risponde su http://127.0.0.1:8081: solo LOW funziona.
              avvialo con:  python tools/laya_coreml_stub.py
```

Se la riga di avviso non compare, lo stand-in è attivo e MEDIUM/HARD funzionano.

## Cosa fa, e cosa NON è

**Non è un motore di ragionamento.** Delega a Nemotron 3.5 Lightning — lo stesso modello veloce che genera le risposte — chiedendogli un piano strutturato. Finché gira questo processo, la distinzione System 1 / System 2 è **solo simulata**.

Ogni piano che produce porta la firma `engine: "laya-coreml-stub"`, e l'orchestratore riporta **quello** nel trace, non il nome configurato. Nella UI lo vedi nella colonna engine della pipeline:

```
laya.reason        laya-coreml-stub     · 3 steps, confidence 0.95
nemotron.generate  nemotron-3.5-lightning-30b-a3b
```

Se nel trace leggi `laya-coreml` senza suffisso, è il runtime vero. Non c'è modo di confonderli.

## Comportamento fail-closed

Lo stand-in non inventa piani. Se il modello non restituisce JSON utilizzabile, risponde con una chiave di rifiuto e l'orchestratore solleva `LayaRefusal` → HTTP `422` con un messaggio leggibile. Se invece il piano è troncato a metà per esaurimento token, lo stand-in **recupera il prefisso completo** invece di rifiutare: un piano parziale ma onesto batte un rifiuto inutile.

## Il costo reale

Ogni chiamata MEDIUM fa **due richieste a Nemotron in sequenza** (il piano, poi la risposta). Con l'endpoint condiviso di NVIDIA, che va da 0.5s a oltre 100s a causa dei cold start, una richiesta MEDIUM misurata è stata:

```
laya.reason        laya-coreml-stub     18.9s
nemotron.generate  nemotron-3.5-lightning   146.6s
```

Prepara il fisico: **2-3 minuti a richiesta**, e la prima dopo una pausa è la peggiore. HARD fa una richiesta in più (revisione), quindi 3-5 minuti.

Per togliere questa attesa serve un endpoint NIM dedicato o i pesi in locale con vLLM: cambia solo `NEMOTRON_BASE_URL` e `NEMOTRON_MODEL` nel `.env`.

## Sostituirlo con il runtime vero

Quando laya-coreml sarà implementato, non cambia niente nell'app: basta che parli lo stesso contratto.

| Endpoint | Riceve | Deve rispondere |
|---|---|---|
| `POST /v1/reason` | `request_id`, `problem`, `depth`, `intent`, `context` | `steps[]`, `conclusion`, `confidence`, `depth`, `assumptions[]`, `open_questions[]` |
| `POST /v1/critique` | `draft`, `plan`, `requirements[]` | `verdict` (`approved` \| `revisions_required` \| `rejected`), `issues[]`, `summary` |

Dettagli e vincoli in [PROVIDERS.md](PROVIDERS.md).