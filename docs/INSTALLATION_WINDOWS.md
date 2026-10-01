# Installazione su Windows

## Prerequisiti

- Windows 10 o superiore.
- [Python 3.9+](https://www.python.org/downloads/windows/) installato e **nel PATH** (spunta *"Add python.exe to PATH"* durante l'installazione).

## Passaggi

1. Clona il repository ed entra nella cartella:

   ```powershell
   git clone https://github.com/mareknardella-lgtm/laya-pro.git
   cd laya-pro
   ```

2. Crea l'ambiente virtuale e attivalo:

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   Se PowerShell blocca lo script di attivazione, per la sessione corrente basta:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   ```

3. Installa le dipendenze:

   ```powershell
   pip install -r requirements.txt
   ```

4. Crea il file di configurazione e inserisci la tua chiave NVIDIA (la trovi su [build.nvidia.com](https://build.nvidia.com/)):

   ```powershell
   copy .env.example .env
   notepad .env
   ```

   ```dotenv
   NEMOTRON_API_KEY=nvapi-...
   ```

5. Avvia tutto:

   ```powershell
   python run.py --with-stub
   ```

   In alternativa fai doppio clic su **`start.bat`**, che fa gli stessi passi dalla cartella corrente.

6. Apri **http://127.0.0.1:8000** nel browser.

## Note

- `run.py --with-stub` avvia anche lo stand-in di laya-coreml in un processo separato. Senza quel flag funziona solo la modalita' LOW.
- La prima risposta dopo una pausa puo' impiegare anche due minuti: l'endpoint NVIDIA trial e' un'istanza condivisa che va in cold start. Vedi [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
- Se la porta 8000 e' occupata: `python run.py --with-stub --port 8080`.
