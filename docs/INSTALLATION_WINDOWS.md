# Installazione su Windows

L'installazione su Windows richiede PowerShell. Tutte le dipendenze, incluso il bridge CPU PyTorch per laya-coreml, verranno gestite automaticamente.

## Prerequisiti
- Windows 10 o superiore.
- Python 3.10+ installato e disponibile nel PATH.

## Passaggi
1. Clona il repository o estrai i file del progetto.
2. Apri PowerShell come amministratore (o assicurati di avere i permessi di esecuzione).
3. Esegui il seguente comando dalla root del progetto:

\\\powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start.ps1"
\\\
"@ -Encoding UTF8

Set-Content -Path "c:\Users\Marek\Desktop\Laya Pro\docs\INSTALLATION_MACOS.md" -Value @"
# Installazione su macOS

L'installazione su macOS sfrutta l'ecosistema Unix nativo e può teoricamente sfruttare l'accelerazione backend Core ML dove appropriato.

## Prerequisiti
- macOS 12 (Monterey) o superiore.
- Python 3.10+ installato (preferibilmente tramite Homebrew).

## Passaggi
1. Clona il repository o estrai i file del progetto.
2. Apri il Terminale.
3. Esegui il seguente comando dalla root del progetto:

\\\ash
bash ./scripts/start.sh
\\\
"@ -Encoding UTF8

Set-Content -Path "c:\Users\Marek\Desktop\Laya Pro\docs\QUICKSTART.md" -Value @"
# Guida Rapida

Benvenuti in Laya Pro! Segui questi passaggi per avviare rapidamente la piattaforma.

1. Installa Laya Pro seguendo le istruzioni per il tuo sistema operativo ([Windows](INSTALLATION_WINDOWS.md) o [macOS](INSTALLATION_MACOS.md)).
2. Configura i tuoi provider AI (vedi [Provider Supportati](PROVIDERS.md)).
3. Avvia la piattaforma e inizia a interagire con l'assistente.

Esplora le [Modalità AI](AI_MODES.md) per capire come Laya Pro gestisce le richieste.
