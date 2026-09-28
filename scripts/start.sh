#!/usr/bin/env bash
# macOS / Linux Launch Script for Laya Pro
# This script spawns the FastAPI Backend and opens the dashboard

HOST="127.0.0.1"
PORT="8765"
STOP=false

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --stop) STOP=true ;;
        --host) HOST="$2"; shift ;;
        --port) PORT="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${PROJECT_ROOT}/backend/data/laya_backend.pid"

if [ "$STOP" = true ]; then
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        echo "Arresto del backend Laya Pro (PID: $PID)..."
        kill -TERM "$PID" 2>/dev/null || echo "Processo già terminato."
        rm -f "$PID_FILE"
        echo "Laya Pro arrestato con successo."
    else
        echo "Nessun processo Laya Pro in esecuzione trovato."
    fi
    exit 0
fi

cd "$PROJECT_ROOT" || exit 1

if [ ! -f "backend/.venv/bin/activate" ] && [ ! -f ".venv/bin/activate" ]; then
    echo "ERRORE: Ambiente virtuale (.venv) non trovato. Esegui la procedura di installazione."
    exit 1
fi

if [ -f ".venv/bin/activate" ]; then
    source ".venv/bin/activate"
elif [ -f "backend/.venv/bin/activate" ]; then
    source "backend/.venv/bin/activate"
fi

cd backend || exit 1

echo "Avvio Laya Pro FastAPI Backend su $HOST:$PORT..."
python -m uvicorn app.main:application --host "$HOST" --port "$PORT" &
BACKEND_PID=$!
echo $BACKEND_PID > "$PID_FILE"

echo "Backend in esecuzione (PID $BACKEND_PID)."
echo "Dashboard accessibile a: http://$HOST:$PORT/dashboard/"

# Open browser if on macOS
if [[ "$OSTYPE" == "darwin"* ]]; then
    sleep 2
    open "http://$HOST:$PORT/dashboard/"
fi
