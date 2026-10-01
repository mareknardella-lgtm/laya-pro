# Guida allo sviluppo

## Ambiente

```bash
python -m venv .venv
# Windows:     .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Il progetto richiede **Python 3.9**: niente sintassi `X | None` nelle firme.

## Avvio in sviluppo

```bash
python run.py --with-stub --reload
```

`--reload` ricarica l'app a ogni modifica. Il launcher fissa `sys.path` e la working directory sulla root del progetto, quindi funziona anche lanciato da un'altra cartella: i test coprono esattamente questo caso, perche' e' gia' successo di pubblicare un `run.py` rotto che si accorgeva solo con `--help`.

## Test

```bash
python -m pytest backend/tests -q
```

La suite non tocca la rete: il client Nemotron e lo stand-in di laya-coreml sono mockati in [conftest.py](../backend/tests/conftest.py). Prima di aprire una Pull Request i test devono essere verdi.

## Dove mettere le cose

| Cartella | Contenuto |
|---|---|
| `backend/app/chat/` | orchestratore, modelli di richiesta/risposta, cronologia |
| `backend/app/systems/` | adattatori dei motori: `laya/` (ragionamento), `nemotron/` (generazione) |
| `backend/app/memory/` | estrazione fatti e retrieval |
| `backend/app/api/routes/` | solo endpoint: validazione e traduzione HTTP, niente logica |
| `backend/app/observability/` | stato dei motori, latenze, trace |
| `backend/app/static/` | UI, senza build step |
| `tools/` | script e stand-in fuori dal pacchetto applicativo |
| `docs/` | questa documentazione |

## Regole che il codice rispetta

1. **Nessuna risposta fabbricata.** Ogni output di un motore e' validato da Pydantic; un contratto violato diventa `502`, non un default silenzioso.
2. **Il ragionamento e' fail-closed.** Se laya-coreml non risponde, MEDIUM e HARD falliscono con `503`. Non esiste un fallback in cui il tier veloce finge di aver ragionato.
3. **Il trace e' parte della risposta.** Se aggiungi un hop, registralo.
4. **La memoria non blocca mai.** Un errore di retrieval degrada la risposta, non la impedisce.
5. **Niente chiavi nel codice.** Solo `.env`, che e' gitignored.

## Documentazione

Tocca [ARCHITECTURE.md](ARCHITECTURE.md) quando cambi il modo in cui i motori si integrano, e [API_REFERENCE.md](API_REFERENCE.md) quando aggiungi o modifichi un endpoint.
