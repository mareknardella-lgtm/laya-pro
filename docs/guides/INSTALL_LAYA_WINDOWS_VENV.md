# Installazione di Laya Pro su Windows (con .venv)

Questa guida illustra la procedura esatta (utilizzata durante lo sviluppo) per installare, configurare ed eseguire il sistema **Laya Pro** su macchine Windows 10/11.

## 1. Prerequisiti
* **Python**: Si richiede Python 3.9 o superiore installato sul sistema (e aggiunto al PATH).
* **Git**: Per clonare il repository.
* **NVIDIA Nemotron API Key**: (Vedi la guida su [come ottenerla](NVIDIA_NEMOTRON_API_KEY.md)).
* *Opzionale (se vuoi usare System 1)*: Modello locale Laya scaricato e configurato via bridge PyTorch (vedi [Download Laya](DOWNLOAD_LAYA.md)).

## 2. Clonazione del Repository
Apri **PowerShell** e clona il progetto dal repository GitHub ufficiale (se pubblicato) o accedi alla cartella.
```powershell
git clone https://github.com/mareknardella-lgtm/laya-pro.git
cd "laya-pro"
```

## 3. Creazione e configurazione del Virtual Environment
Per evitare conflitti di pacchetti di sistema, **Laya Pro** usa tassativamente un ambiente virtuale locale.

1. Esegui la creazione dell'ambiente:
   ```powershell
   python -m venv .venv
   ```
2. Attiva l'ambiente virtuale:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   > **Nota sulla Execution Policy**: Se PowerShell ti impedisce l'esecuzione dello script mostrando un errore (rosso) di blocco criteri, puoi aggirarlo temporaneamente riaprendo PowerShell con questo comando: `powershell -ExecutionPolicy Bypass` e riprovando.

3. Installa le dipendenze essenziali del backend:
   ```powershell
   pip install -r requirements.txt
   ```

## 4. Configurazione (.env)
Duplica il file di esempio per creare la configurazione locale vera e propria.
```powershell
Copy-Item .env.example .env
```
Apri il file `.env` con il Blocco Note (o VSCode) e assicurati di compilare le variabili pertinenti per **Nemotron** (NVIDIA AI) e, se presente nel computer, i path del **Local Bridge per Laya**:
```env
NVIDIA_AI_ENABLED=true
NVIDIA_AI_API_KEY=nvapi-XXXXXX
```

## 5. Avvio del Sistema
Il progetto include uno script unificato che avvia contemporaneamente il Backend FastAPI (e opzionalmente il bridge di System 1 se configurato correttamente). Questo script bypassa automaticamente i problemi di *Execution Policy*.

Assicurati di trovarti nella cartella root di Laya Pro (`C:\Percorso\Laya Pro`) ed esegui:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start.ps1"
```

Se tutto è andato a buon fine, il terminale ti indicherà che il server è in esecuzione su **http://127.0.0.1:8765**.
Puoi ora aprire l'indirizzo dal browser per accedere alla **Dashboard UI**.

## 6. Arresto e Riavvio
Per arrestare i processi attivi di Laya Pro in background (Backend FastAPI, Supervisor e subprocess System 1), esegui sempre e solo lo script ufficiale fornendo lo switch `-Stop`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start.ps1" -Stop
```
Ciò eliminerà la lock al file PID del supervisor in modo pulito.

## 7. Troubleshooting

* **Errore "Impossibile caricare il file .venv\Scripts\Activate.ps1..."**  
  Questo è il tipico blocco di esecuzione di Windows. Usa `powershell -ExecutionPolicy Bypass` prima di entrare nell'ambiente.
* **ModuleNotFoundError: No module named 'fastapi'**  
  Hai avviato lo script senza prima installare i requisiti (`pip install -r requirements.txt`) o lo script PowerShell non è riuscito ad attivare il contesto corretto dell'ambiente virtuale. Se continui a ricevere l'errore con `start.ps1`, prova ad avviare manualmente: `.\.venv\Scripts\python.exe -m uvicorn backend.app.main:application --port 8765`.
* **Porta già occupata (Error [WinError 10048])**  
  Assicurati di aver lanciato `start.ps1 -Stop` per chiudere l'istanza precedente.
* **La dashboard carica ma dice "Autenticazione necessaria" o "Offline"**  
  Il backend potrebbe essersi arrestato per un errore `.env` critico. Controlla i log in PowerShell. Controlla di aver compilato `NVIDIA_AI_ENABLED=true`.
