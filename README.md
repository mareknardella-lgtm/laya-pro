# Laya Pro

Orchestratore **ibrido System 1 / System 2** con interfaccia ChatGPT-style.

Due motori con ruoli opposti, un solo contratto:

- **laya-coreml** — ragionamento profondo: scompone il problema, produce un piano verificabile, giudica la bozza.
- **Nemotron 3.5 Lightning** (`nvidia/nemotron-3.5-lightning-30b-a3b`) — generazione veloce: trasforma il piano in risposta naturale.

L'orchestratore non *sceglie* un motore: li **fuse**. Il ragionamento decide *cosa* dire, il modello veloce si limita a *dirlo*.

---

## Avvio rapido

```bash
git clone https://github.com/mareknardella-lgtm/laya-pro.git
cd laya-pro

python -m venv .venv
# Windows:   .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt

copy .env.example .env       # Windows
cp .env.example .env         # macOS / Linux
# poi metti la tua chiave NVIDIA in NEMOTRON_API_KEY

python run.py --with-stub
```

Apri **http://127.0.0.1:8000**. Su Windows c'è anche `start.bat` (doppio clic).

`--with-stub` avvia anche lo **stand-in di laya-coreml** in un processo separato: senza di esso funziona solo LOW, MEDIUM e HARD rispondono `503`. Vedi [docs/LAYA_COREML_STUB.md](docs/LAYA_COREML_STUB.md).

---

## Modalità

| Modalità | Pipeline | Chiamate |
|---|---|---|
| **LOW** | Nemotron | 1 |
| **MEDIUM** | `laya.reason` → Nemotron | 2 |
| **HARD** | `laya.reason` → Nemotron → `laya.critique` → Nemotron | 3–5 |

Ogni risposta include un `trace[]` con engine, latenza e dettaglio di ogni hop: il costo della fusione è visibile, non sottinteso.

---

## Stato onesto del progetto

- **laya-coreml non è ancora un runtime Core ML locale.** Il repository contiene uno stand-in (`tools/laya_coreml_stub.py`) che rispetta il contratto HTTP ma chiama a sua volta Nemotron su internet. La separazione System 1 / System 2 è quindi oggi **architetturale, non di calcolo**: sostituire lo stand-in con il runtime vero è un cambio di implementazione dietro la stessa interfaccia.
- **L'endpoint NVIDIA trial è un'istanza condivisa**: la prima risposta dopo some secondi di inattività costa 100–150 s. Il timeout di default è 120 s proprio per questo. Con un endpoint NIM dedicato la latenza scende di ordini di grandezza — misurala e abbassa `NEMOTRON_TIMEOUT_SECONDS`.
- **La persistenza è SQLite** in `./data/laya.db` (gitignored). Nessun account, nessun servizio esterno oltre NVIDIA.

---

## Struttura

```
run.py                      launcher (funziona da qualsiasi directory)
tools/laya_coreml_stub.py   stand-in del runtime di ragionamento
backend/app/
  chat/orchestrator.py      pipeline ibrida, cicli di revisione, trace
  systems/laya/             adattore System 2 (contratto fail-closed)
  systems/nemotron/         adattore System 1 (contratto fail-closed)
  memory/                   estrazione fatti + retrieval lessicale
  storage/sqlite.py         schema e connessione
  observability/runtime.py  stato e latenze dei motori
  api/routes/               chat, status, memory, sessions
  static/                   UI ChatGPT-style
backend/tests/              102 test
docs/                       documentazione
```

---

## Documentazione

| Documento | Contenuto |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | come i due motori si integrano, fail-closed, diagrammi |
| [AI_MODES.md](docs/AI_MODES.md) | LOW / MEDIUM / HARD in dettaglio |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | endpoint, payload, codici di errore |
| [MEMORY.md](docs/MEMORY.md) | estrazione, retrieval, voci pinnate |
| [PROVIDERS.md](docs/PROVIDERS.md) | configurazione dei provider |
| [LAYA_COREML_STUB.md](docs/LAYA_COREML_STUB.md) | il contratto del runtime di ragionamento |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | errori comuni e come leggerli |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | ambiente di sviluppo e test |
| [INSTALLATION_WINDOWS.md](docs/INSTALLATION_WINDOWS.md) / [INSTALLATION_MACOS.md](docs/INSTALLATION_MACOS.md) | installazione passo passo |

---

## Test

```bash
python -m pytest backend/tests -q
```

La CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) esegue la suite su Python 3.9 e uno smoke test del launcher da un'altra directory.

---

## Sicurezza

`.env` e `data/` sono gitignored: la chiave NVIDIA non entra mai nel repository. Vedi [SECURITY.md](SECURITY.md).