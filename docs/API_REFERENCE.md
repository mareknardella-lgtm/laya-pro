# Riferimento API

Tutti gli endpoint sono JSON. Le risposte che passano dalla pipeline ibrida includono sempre un `trace[]`.

## POST /chat
Invia un messaggio all'orchestratore.

**Richiesta**
```json
{
  "message": "Progetta un refactor del modulo auth",
  "session_id": "uuid-opzionale",
  "tier": "LOW | MEDIUM | HARD",
  "auto_memory": true
}
```

**Risposta 200**
```json
{
  "text": "...",
  "session_id": "...",
  "tier": "MEDIUM",
  "status": "success",
  "engines_used": ["laya-coreml", "nemotron-3.5-lightning-30b-a3b"],
  "confidence": 0.82,
  "refinements": 0,
  "trace": [{ "stage": "laya.reason", "engine": "laya-coreml", "latency_ms": 812, "detail": "2 steps, confidence 0.82" }],
  "metadata": { "memory_hits": 2, "memory_written": 1, "model": "nvidia/nemotron-3.5-lightning-30b-a3b" }
}
```

**Errori**
| Codice | Causa |
|---|---|
| `422` | Messaggio vuoto o troppo lungo, tier sconosciuto. |
| `503` | laya-coreml irraggiungibile (MEDIUM/HARD) o Nemotron non configurato. |
| `502` | Un motore ha risposto in modo incompatibile con il contratto. |

## GET /status
Stato reale dei due motori: `configured`, tentativi, successi, fallimenti, latenza media e ultimo errore.

```json
{
  "engines": {
    "nemotron": { "configured": true, "model": "...", "role": "fast generation", "attempts": 3, "average_latency_ms": 142 },
    "laya-coreml": { "configured": true, "model": "laya-coreml", "role": "deep reasoning", "attempts": 2, "average_latency_ms": 810 }
  },
  "tiers": { "LOW": "...", "MEDIUM": "...", "HARD": "..." },
  "database": "./data/laya.db"
}
```

## Memoria
Ogni voce ha `key`, `value`, `scope`, `pinned` e `updated_at`.

| Endpoint | Effetto |
|---|---|
| `GET /memory` | Tutte le voci, pinnate per prime. |
| `POST /memory` | Body `{"key": "...", "value": "..."}`. Crea o aggiorna e **pina** la voce. Risposta `{"created": bool, "stored": {...}}`. |
| `PUT /memory` | Stesso body. Modifica una voce esistente, `404` altrimenti. |
| `DELETE /memory?key=...` | Dimentica una voce, `404` se non esiste. |
| `POST /memory/query` | Body `{"query": "...", "limit": 10}` → voci pertinenti per somiglianza lessicale. |

Le voci pinnate finiscono nel prompt **sempre**, anche se la domanda non ha parole in comune: vedi [MEMORY.md](MEMORY.md).

## Sessioni
- `GET /sessions?limit=20` → sessioni con `message_count`.
- `GET /sessions/{session_id}/messages?limit=50&direction=desc` → cronologia. `direction=asc` restituisce i messaggi più vecchi: serve alla sidebar per etichettare una chat con il suo primo messaggio.
- `GET /sessions/{session_id}/traces?limit=20` → trace persistiti, con la risposta completa in `payload`.