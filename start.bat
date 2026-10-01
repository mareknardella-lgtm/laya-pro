@echo off
REM Avvia Laya Pro. Funziona anche con doppio clic: si posiziona nella cartella dello script.
cd /d "%~dp0"

echo.
echo  Laya Pro
echo  ---------------
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  ERRORE: python non trovato nel PATH.
    echo  Installa Python 3.9+ e riprova.
    pause
    exit /b 1
)

python run.py %*
if errorlevel 1 (
    echo.
    echo  Avvio fallito. Se mancano dipendenze, prova:
    echo      pip install -r requirements.txt
    pause
)