# Guida allo Sviluppo

Questa documentazione fornisce le linee guida per sviluppare ed estendere Laya Pro.

## Setup dell'Ambiente
Raccomandiamo l'uso di ambienti virtuali Python.
1. Crea l'ambiente: \python -m venv .venv\
2. Attiva l'ambiente: 
   - Windows: \.\.venv\Scripts\Activate.ps1\
   - macOS: \source .venv/bin/activate\
3. Installa le dipendenze: \pip install -r requirements.txt\

## Architettura del Codice
Fare riferimento a [Architettura](ARCHITECTURE.md) per capire come i moduli System 1 e System 2 si integrano con l'orchestratore.

## Test
Esegui i test prima di aprire una Pull Request. Utilizziamo \pytest\ per i test unitari e di integrazione.
