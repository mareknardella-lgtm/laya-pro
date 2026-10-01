# Provider Supportati

Laya Pro usa due motori con profili opposti. Nessuno dei due è opzionale in senso funzionale: senza almeno System 1 la piattaforma non risponde.

## System 1 — Nemotron 3.5 Lightning

| Variabile | Default | Note |
|---|---|---|
| `NEMOTRON_API_KEY` | *(vuota)* | Chiave NVIDIA. Obbligatoria. |
| `NEMOTRON_BASE_URL` | `https://integrate.api.nvidia.com/v1` | Endpoint OpenAI-compatible. |
| `NEMOTRON_MODEL` | `nvidia/nemotron-3.5-lightning-30b-a3b` | MoE 30B (3B attivi): il modello più veloce della famiglia Nemotron 3.5. |
| `NEMOTRON_TIMEOUT_SECONDS` | `30` | |
| `NEMOTRON_MAX_OUTPUT_TOKENS` | `2048` | |
| `NEMOTRON_TEMPERATURE` | `0.3` | Temperatura bassa: la creatività non è il compito di questo motore. |

Il client parla il formato `chat/completions`, quindi la stessa configurazione funziona con vLLM, OpenRouter o qualsiasi gateway OpenAI-compatible: basta cambiare `NEMOTRON_BASE_URL` e `NEMOTRON_MODEL`.

## System 2 — laya-coreml

| Variabile | Default | Note |
|---|---|---|
| `LAYA_COREML_URL` | `http://127.0.0.1:8081` | Runtime di ragionamento locale. |
| `LAYA_COREML_API_KEY` | *(vuota)* | Facoltativa. |
| `LAYA_COREML_TIMEOUT_SECONDS` | `120` | Il ragionamento profondo è lento per definizione. |
| `LAYA_MAX_INPUT_CHARS` | `12000` | Oltre questa soglia: errore esplicito. |
| `LAYA_MAX_REFINEMENTS` | `2` | Tetto del ciclo di revisione in modalità HARD. |

### Contratto richiesto al runtime
Due endpoint JSON.

`POST /v1/reason` — riceve `request_id`, `problem`, `depth` (`standard` | `deep`), `intent`, `context`. Deve rispondere con:
```json
{
  "steps": [
    { "goal": "...", "rationale": "...", "expected_output": "..." }
  ],
  "conclusion": "...",
  "confidence": 0.0,
  "depth": "deep",
  "assumptions": [],
  "open_questions": []
}
```
Un piano `deep` con un solo passo viene rifiutato dal validatore. Una risposta che contiene un rifiuto esplicito (`refused`, `cannot_plan`, `insufficient_information`) viene tradotta in `LayaRefusal`, non compilata con un piano inventato.

`POST /v1/critique` — riceve `draft` e `plan`. Deve rispondere con:
```json
{ "verdict": "approved | revisions_required | rejected", "issues": ["..."], "summary": "..." }
```
`revisions_required` senza `issues` è un contratto violato.

## Storage
| Variabile | Default |
|---|---|
| `LAYA_DB_PATH` | `./data/laya.db` |

## Avvio rapido
```bash
pip install -r requirements.txt
cp .env.example .env      # poi riempi NEMOTRON_API_KEY e LAYA_COREML_URL
python -m uvicorn backend.app.main:app --reload
curl http://127.0.0.1:8000/status
```
`/status` riporta `configured` reale per ciascun motore: se la chiave manca, lo dice.