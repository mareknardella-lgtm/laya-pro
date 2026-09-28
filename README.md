# Laya Pro

Laya Pro è una piattaforma AI ibrida e modulare progettata per coordinare motori di decisione strutturati (System 1), modelli linguistici generativi (System 2) e strumenti di orchestrazione automatica.

Il progetto nasce per risolvere la dicotomia tra "ragionamento veloce/strutturato" e "generazione testuale profonda", offrendo un unico ecosistema dove l'utente può conversare e far eseguire workflow complessi sfruttando il meglio di entrambi i mondi.

Laya Pro integra un **sistema di orchestrazione AI ibrido** che combina analisi strutturata, generazione linguistica e gestione persistente del contesto (memoria globale a lungo termine e history della chat) per trasformare le richieste dell'utente in risposte sicure ed effettive.

## Architettura Ibrida (System 1 & System 2)

* **System 1 (JEV / Laya Local)**: Si basa sul modello decisionale veloce [Laya](docs/guides/DOWNLOAD_LAYA.md), ideale per policy, decisioni rapide e generazione di piani d'azione (ragionamento strutturato).
* **System 2 (Nemotron)**: Sfrutta il modello `nemotron-4-340b-instruct` tramite NVIDIA AI per elaborare il linguaggio naturale e gestire ragionamenti complessi.

Maggiori dettagli architetturali (inclusi i diagrammi di flusso e il ruolo di **Core ML** limitato all'ecosistema macOS e delegato via PyTorch su Windows) sono disponibili in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Modalità di Conversazione AI (AI Chat)
Laya Pro espone tre modalità operative all'interno della dashboard:
1. **LOW**: Risposta diretta tramite System 2 (Nemotron).
2. **MEDIUM**: Collaborazione (JEV crea il piano, Nemotron genera la risposta).
3. **HARD**: Pianificazione strutturata profonda con vincoli via JEV.

*(Nota: Allo stato attuale, il backend per le modalità Medium/Hard solleva elegantemente l'eccezione costruttiva `JEVUnavailable` in attesa del collegamento fisico del motore locale completo).*

## Memoria e Cronologia
Laya Pro dispone di una gestione memoria avanzata (Memory RAG) basata su database SQLite. La dashboard offre l'opzione "Memoria Auto" per permettere all'Orchestratore di estrarre in background le preferenze dell'utente dalla conversazione e riutilizzarle nelle chat successive. Per dettagli vedi [MEMORY.md](docs/MEMORY.md).

---

## 🚀 Quickstart & Guide all'Installazione

Scegli la guida in base al tuo ambiente operativo e scopri come configurare le API:

1. 🔑 **[Ottenere la API Key di NVIDIA Nemotron](docs/guides/NVIDIA_NEMOTRON_API_KEY.md)**
2. 🧠 **[Scaricare e configurare il modello Laya originale](docs/guides/DOWNLOAD_LAYA.md)** (Riconoscimenti all'autore)
3. 🪟 **[Guida all'installazione su Windows (tramite .venv)](docs/guides/INSTALL_LAYA_WINDOWS_VENV.md)**
4. 🍏 **[Guida all'installazione su macOS](docs/INSTALLATION_MACOS.md)**

## Struttura del Repository

```text
README.md               # Questo file
LICENSE                 # Licenza Apache 2.0
backend/                # Core FastAPI, Orchestratore, Database
frontend/               # Dashboard UI (HTML, CSS, Vanilla JS)
docs/                   # Documentazione estesa e guide pratiche
  ARCHITECTURE.md
  AI_MODES.md
  ...
scripts/                # Script di avvio per Windows e macOS
```

## Requisiti di Sistema
- **Windows 10/11** (o **macOS 12+**)
- **Python 3.9+** (consigliato 3.11)
- Git e PowerShell (o Terminale per macOS)
- Connessione a Internet per contattare le API NVIDIA

## Limiti Noti
- L'indicizzazione vettoriale della memoria è semplificata tramite query SQL standard ed LLM text-filtering per evitare l'uso di engine vettoriali pesanti (es. ChromaDB).
- Il routing verso Core ML per il modello Laya non è supportato in ambiente Windows.

## Test
L'intero stack prevede test isolati (`pytest` per il backend e `jest` per il DOM frontend):
```powershell
python -m pytest backend/tests -v
```

## Licenza
Distribuito sotto **Apache License 2.0**. Vedi [LICENSE](LICENSE) e [NOTICE](NOTICE). Il modello Laya originale e le sue dipendenze PyTorch seguono i termini definiti dal creatore e dai rispettivi fornitori.
