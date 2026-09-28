# Download e Configurazione del Modello Laya

**Laya** è un motore decisionale **System 1** non autoregressivo, originariamente creato dallo sviluppatore **Nandakishor M**.  
In Laya Pro, questo modello costituisce il nucleo di ragionamento locale per l'analisi strutturata e la valutazione dei workflow, distinguendosi dai classici LLM per la sua velocità di inferenza (single forward pass) e la sua capacità di eseguire calcoli strutturati in maniera deterministica.

> **Crediti e Riferimenti Ufficiali**  
> Autore e Sviluppatore Originale: [Nandakishor M](https://dev.to/nandakishor_m_6cc0adfde9f/i-built-non-autoregressive-decision-models-a-year-ago-then-a-frontier-lab-called-it-a-18me)  
> Documentazione ufficiale e package: Modello `laya` su pip/PyPI e Hugging Face.

Laya Pro non distribuisce direttamente i pesi o il codice di inferenza nativo del modello originale per rispetto della licenza del creatore. Richiediamo invece di configurarlo in locale e di "agganciarlo" (tramite il bridge locale) al nostro Orchestratore.

## 1. Architettura (Windows vs macOS)
- **macOS:** Il modello nativo supporta l'esecuzione accelerata hardware tramite **Core ML**, permettendo inferenza a bassissima latenza.
- **Windows:** Non essendoci Core ML su Windows, Laya Pro aggancia il modello Laya appoggiandosi su un'installazione PyTorch (CPU o CUDA) separata tramite un *Local Bridge* (`laya_torch.py`).

## 2. Installare l'ambiente `laya-coreml` (Windows)
Invece di appesantire l'ambiente virtuale principale di Laya Pro, la procedura standard per Windows prevede la creazione di un folder dedicato parallelo.

1. Sul Desktop, crea una cartella chiamata `laya-coreml`.
2. Apri PowerShell e naviga nella nuova cartella:
   ```powershell
   cd C:\Users\Marek\Desktop\laya-coreml
   ```
3. Crea un ambiente virtuale separato e attivalo:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
4. Installa il pacchetto ufficiale creato da Nandakishor M:
   ```powershell
   pip install laya torch torchvision torchaudio
   ```
5. Crea nella stessa cartella il file `laya_torch.py` (lo script wrapper) richiesto da Laya Pro per inviare input e ricevere JSON via subprocess.

## 3. Configurare Laya Pro per collegarsi al modello
Nel file `.env` di **Laya Pro**, configura il Local Bridge affinché chiami l'eseguibile Python e il wrapper appena installati:

```env
# Configurazione Bridge Locale per Laya System 1 (Esempio Windows)
LAYA_LOCAL_PATH=C:\Users\Marek\Desktop\laya-coreml
LAYA_PYTHON_PATH=C:\Users\Marek\Desktop\laya-coreml\.venv\Scripts\python.exe
LAYA_WRAPPER_PATH=C:\Users\Marek\Desktop\laya-coreml\laya_torch.py
```

## 4. Verifica
Una volta avviato Laya Pro (tramite `start.ps1`), il backend verificherà la presenza del file `laya_torch.py` all'interno dell'ambiente specificato. Se disponibile, il servizio **System 1 (Local Laya AI)** figurerà come online nella Dashboard. 
