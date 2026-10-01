# Modalità AI

Ogni richiesta dichiara una modalità, che decide **quanto della pipeline ibrida** può spendere.

## LOW — il tier veloce da solo
- **Flusso**: `Nemotron 3.5 Lightning`.
- **Chiamate**: 1 (più 1 estrazione memoria se attiva).
- **Quando**: conversazione, domande fattuali, risposte brevi.
- **Stato**: operativa.

## MEDIUM — pianifica, poi parla
- **Flusso**: `laya-coreml` produce il piano → `Nemotron` lo rende in linguaggio naturale.
- **Chiamate**: 2 (più 1 estrazione memoria).
- **Quando**: problemi che richiedono struttura ma non riscrittute ripetute.
- **Stato**: operativa.

## HARD — pianifica, parla, si fa correggere
- **Flusso**: `laya-coreml` ragiona in profondità → `Nemotron` genera → `laya-coreml` **critica** la bozza → `Nemotron` riscrive se il critico trova problemi.
- **Chiamate**: da 3 a 5, in base al numero di revisioni concesse (`LAYA_MAX_REFINEMENTS`, default 2).
- **Quando**: refactor, analisi con vincoli incrociati, revisione di codice.
- **Stato**: operativa.

### Come leggere la risposta
Ogni risposta include `trace[]` con un passo per ogni hop:

```json
{
  "text": "...",
  "tier": "HARD",
  "status": "success",
  "engines_used": ["laya-coreml", "nemotron-3.5-lightning-30b-a3b"],
  "confidence": 0.82,
  "refinements": 1,
  "trace": [
    { "stage": "laya.reason",       "engine": "laya-coreml", "latency_ms": 812 },
    { "stage": "nemotron.generate", "engine": "nemotron-3.5-lightning-30b-a3b", "latency_ms": 140 },
    { "stage": "laya.critique",     "engine": "laya-coreml", "latency_ms": 233 },
    { "stage": "nemotron.refine",   "engine": "nemotron-3.5-lightning-30b-a3b", "latency_ms": 131 }
  ]
}
```

### Valori di `status`
| Status | Significato |
|---|---|
| `success` | La risposta ha superato i controlli previsti. |
| `degraded` | Budget di revisione esaurito, o il critico ha rifiutato la bozza. La risposta è comunque la migliore disponibile. |

## Fall-closed
Se il motore di ragionamento è irraggiungibile o risponde in modo incompatibile con il contratto, MEDIUM e HARD falliscono con `503`. Il sistema **non** degrada silenziosamente su LOW: una risposta senza ragionamento che si spaccia per ragionata è un bug, non un fallback.