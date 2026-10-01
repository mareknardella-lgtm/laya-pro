# Installazione su macOS

Questa guida vale per macOS e Linux.

## Prerequisiti

- macOS 12 (Monterey) o superiore.
- Python 3.9+ installato e nel PATH:

  ```bash
  brew install python
  ```

## Passaggi

1. Clona il repository ed entra nella cartella:

   ```bash
   git clone https://github.com/mareknardella-lgtm/laya-pro.git
   cd laya-pro
   ```

2. Crea l'ambiente virtuale e attivalo:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Installa le dipendenze:

   ```bash
   pip install -r requirements.txt
   ```

4. Crea il file di configurazione e inserisci la tua chiave NVIDIA (la trovi su [build.nvidia.com](https://build.nvidia.com/)):

   ```bash
   cp .env.example .env
   nano .env
   ```

   ```dotenv
   NEMOTRON_API_KEY=nvapi-...
   ```

5. Avvia tutto:

   ```bash
   python run.py --with-stub
   ```

6. Apri **http://127.0.0.1:8000** nel browser.

## Note

- `run.py --with-stub` avvia anche lo stand-in di laya-coreml in un processo separato. Senza quel flag funziona solo la modalita' LOW.
- Non esiste uno `start.sh`: il launcher Python e' lo stesso su tutti i sistemi operativi.
- La prima risposta dopo una pausa puo' impiegare anche due minuti: l'endpoint NVIDIA trial e' un'istanza condivisa che va in cold start. Vedi [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
- Su macOS un eventuale runtime Core ML per laya-coreml potra' sfruttare l'accelerazione neurale locale; oggi nel repository c'e' solo lo stand-in (vedi [LAYA_COREML_STUB.md](LAYA_COREML_STUB.md)).
