# Come ottenere e configurare la API Key di NVIDIA Nemotron

In **Laya Pro**, Nemotron funge da **System 2**. È il modello linguistico avanzato (LLM) incaricato di generare le risposte discorsive finali, formulare il testo e interagire fluidamente con l'utente, ricevendo in input i piani strutturati elaborati da JEV (System 1). 

NVIDIA offre i propri modelli (come `nemotron-4-340b-instruct`) attraverso la piattaforma **NVIDIA Build / API**.

## 1. Ottenere la API Key ufficiale
Per utilizzare Nemotron in Laya Pro:

1. Visita la pagina ufficiale di NVIDIA AI (NVIDIA Build / API Catalog): 
   [https://build.nvidia.com/](https://build.nvidia.com/)
2. Crea un account NVIDIA (o effettua l'accesso se ne possiedi già uno).
3. NVIDIA offre solitamente una quota di **crediti gratuiti** iniziali (es. 1000 crediti) per testare i modelli.
4. Vai nella sezione **Generative AI Models** e cerca **Nemotron-4 340B Instruct** (o varianti simili).
5. Clicca su **Get API Key** (Ottieni Chiave API) nella dashboard.
6. Clicca su **Generate Key**.
7. **Copia immediatamente la chiave** (inizierà generalmente con `nvapi-`). Non ti verrà mostrata nuovamente.

> **⚠️ ATTENZIONE:** Non salvare mai questa chiave nel codice sorgente e non caricarla su GitHub. Se una chiave API viene compromessa, NVIDIA la revocherà automaticamente.

## 2. Configurazione in Laya Pro
Una volta ottenuta la chiave, apri il file `.env` (se non esiste, duplica `.env.example` e rinominalo `.env`) nella root del progetto Laya Pro e compila i campi dedicati:

```env
NVIDIA_AI_ENABLED=true
NVIDIA_AI_PROVIDER=nvidia
NVIDIA_AI_MODEL=nemotron-4-340b-instruct
NVIDIA_AI_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_AI_API_KEY=nvapi-TUA_CHIAVE_COPIATA_QUI
NVIDIA_AI_TIMEOUT_SECONDS=30
```

## 3. Risoluzione dei problemi (Troubleshooting)

- **Errore 401 Unauthorized**: La chiave non è valida o non è stata inserita correttamente nel file `.env`. Assicurati che non ci siano spazi vuoti prima o dopo la stringa `nvapi-...`.
- **Errore 402 Payment Required**: Hai esaurito i crediti gratuiti del tuo account NVIDIA. Dovrai aggiungere un metodo di pagamento nella console NVIDIA.
- **Laya Pro restituisce JEVUnavailable o Errori su Nemotron**: Assicurati che `NVIDIA_AI_ENABLED=true` sia effettivamente abilitato nel `.env` e di aver riavviato il backend FastAPI dopo aver modificato il file.
