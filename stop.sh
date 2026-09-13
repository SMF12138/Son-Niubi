#!/bin/bash
# Stop: Flask + desktop pet (macOS/Linux)
echo "Stopping Son NiuBi..."

# Kill Flask (app.cli serve)
pkill -f "app.cli serve" 2>/dev/null && echo "  Flask stopped" || echo "  Flask not running"

# Kill desktop pet (floating_pet.py)
pkill -f "floating_pet.py" 2>/dev/null && echo "  Pet stopped" || echo "  Pet not running"

echo "All stopped."
