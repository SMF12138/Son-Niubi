#!/bin/bash
# Run the dashboard (macOS/Linux equivalent of run.ps1)
set -e
cd "$(dirname "$0")/.."

if [ ! -f ".venv/bin/python" ]; then
    echo "ERROR: .venv not found. Run scripts/setup.sh first."
    exit 1
fi

.venv/bin/python -m app.cli serve
