#!/usr/bin/env bash
# GroundedOps installer — macOS / Linux
set -e
echo "=== GroundedOps installer ==="
command -v python3 >/dev/null || { echo "Python 3.11+ required"; exit 1; }
command -v node >/dev/null || { echo "Node.js 18+ required"; exit 1; }
echo "Installing Python dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
echo "Installing frontend dependencies..."
(cd frontend && npm install)
if command -v ollama >/dev/null; then
  read -p "Ollama found. Download local models now (mistral ~4GB, phi ~2GB)? [y/N] " yn
  [ "$yn" = "y" ] && { ollama pull mistral; ollama pull phi; }
else
  echo "Ollama not found — Online mode works without it."
  echo "For Free/offline mode: https://ollama.com/download"
fi
echo "=== Install complete ===  Start with: ./start.sh"
