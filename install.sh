#!/usr/bin/env bash
# GroundedOps installer — macOS / Linux
#   chmod +x install.sh && ./install.sh
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
# The Python package may sit beside this script or in src/.
APP=""
for c in "." "src"; do
  [ -f "$c/requirements.txt" ] && { APP="$c"; break; }
done
if [ -z "$APP" ]; then
  echo "Could not find requirements.txt here or in src/."
  exit 1
fi
APP_DIR="$(cd "$APP" && pwd)"


echo
echo "  GroundedOps installer"
echo "  ====================="
echo

# ── 1. Python ────────────────────────────────────────────────────────
PY=""
for c in python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PY="$c"; echo "Found $($c --version)"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11+ is required."
  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  Install with:  brew install python@3.12"
    echo "  or download:   https://www.python.org/downloads/"
  else
    echo "  Install with:  sudo apt install python3.11 python3.11-venv"
  fi
  exit 1
fi

# ── 2. Virtual environment ───────────────────────────────────────────
# Keeps ~2GB of ML dependencies out of the system Python, and avoids the
# "externally-managed-environment" pip error on modern distros and macOS.
[ -d "$ROOT/.venv" ] || { echo; echo "Creating virtual environment..."; "$PY" -m venv "$ROOT/.venv"; }
VENV_PY="$ROOT/.venv/bin/python"

echo
echo "Installing dependencies (~2GB, several minutes on first run)..."
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r "$APP_DIR/requirements.txt"

# ── 3. Frontend ──────────────────────────────────────────────────────
# The release ships a pre-built frontend/dist, so Node is NOT required.
if [ -f "$APP_DIR/frontend/dist/index.html" ]; then
  echo "Frontend build found."
elif command -v node >/dev/null 2>&1; then
  echo "Building frontend..."
  (cd "$APP_DIR/frontend" && npm install --silent && npm run build)
else
  echo "No frontend build and Node.js not installed."
  echo "The API will run but the web page won't - re-download the release"
  echo "package, which includes the built frontend."
fi

# ── 4. Configuration ─────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
  echo
  echo "  Configuration"
  echo "  -------------"
  echo "GroundedOps can answer using a cloud AI provider (fast, needs an API"
  echo "key) or entirely offline on this machine (private, slower, needs"
  echo "Ollama and about 8GB of free memory)."
  echo
  read -r -p "Use a cloud provider? [Y/n] " MODE
  read -r -p "Choose an admin password (for uploading documents): " ADMINPW
  ADMINPW="${ADMINPW:-admin}"
  { echo "ADMIN_PASSWORD=$ADMINPW"; } > "$APP_DIR/.env"

  if [[ "${MODE:-y}" != "n" ]]; then
    echo
    echo "Which provider? 1) DeepSeek (cheapest)  2) OpenAI  3) Anthropic"
    read -r -p "Enter 1, 2 or 3: " P
    case "$P" in
      2) PROV=openai;    KEYNAME=OPENAI_API_KEY ;;
      3) PROV=anthropic; KEYNAME=ANTHROPIC_API_KEY ;;
      *) PROV=deepseek;  KEYNAME=DEEPSEEK_API_KEY ;;
    esac
    read -r -p "Paste your $PROV API key: " APIKEY
    { echo "GENERATION_MODE=api"
      echo "ONLINE_PROVIDER=$PROV"
      echo "$KEYNAME=$APIKEY"; } >> "$APP_DIR/.env"
  else
    echo "GENERATION_MODE=local" >> "$APP_DIR/.env"
    echo
    echo "Offline mode needs Ollama: https://ollama.com/download"
    echo "After installing it, run:  ollama pull mistral"
  fi
  { echo "WIDGET_ALLOWED_ORIGINS=*"
    echo "ONLINE_DEEPSEEK_MODEL=deepseek-v4-flash"; } >> "$APP_DIR/.env"
  echo
  echo "Wrote .env"
else
  echo "Existing .env kept - delete it and re-run to reconfigure."
fi

# ── 5. Pre-download models ───────────────────────────────────────────
# Otherwise the first question stalls for minutes with no explanation.
echo
echo "Downloading language models (~500MB, one time)..."
"$VENV_PY" - <<'PYEOF'
from sentence_transformers import SentenceTransformer, CrossEncoder
SentenceTransformer('all-MiniLM-L6-v2')
CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
print('models ready')
PYEOF

echo
echo "  Install complete"
echo "  ================"
echo
echo "Start GroundedOps with:  ./start.sh"
echo
