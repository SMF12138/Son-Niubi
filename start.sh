#!/bin/bash
# Start: Flask + desktop pet (macOS/Linux)
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/python" ]; then
    echo "ERROR: .venv not found."
    echo "Run scripts/setup.sh first to create it and install dependencies."
    echo "Requires Python 3.10+ from https://www.python.org/downloads/"
    exit 1
fi

PY=".venv/bin/python"

# If service already running, just start pet
if curl -s --max-time 2 "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
    echo "Flask already running, starting pet only..."
    "$PY" floating_pet.py &
    exit 0
fi

# Launch Flask in background (no browser)
echo "Starting Flask server..."
nohup "$PY" -m app.cli serve --no-browser > /dev/null 2>&1 &
FLASK_PID=$!
echo "Flask PID: $FLASK_PID"

# Wait a moment for Flask to start
sleep 2

# Launch desktop pet
echo "Starting desktop pet..."
"$PY" floating_pet.py &
PET_PID=$!
echo "Pet PID: $PET_PID"

echo ""
echo "Son NiuBi is running."
echo "  Dashboard: http://127.0.0.1:8000"
echo "  Stop:      ./stop.sh"
