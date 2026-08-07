#!/usr/bin/env bash
# GroundedOps launcher — macOS / Linux
#
#   ./start.sh          local only  (http://127.0.0.1:8000)
#   ./start.sh --lan    share with everyone on your network
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
# The Python package may sit beside this script or in src/.
APP=""
for c in "." "src"; do
  [ -f "$c/main.py" ] && { APP="$c"; break; }
done
if [ -z "$APP" ]; then
  echo "Could not find main.py here or in src/."
  exit 1
fi
APP_DIR="$(cd "$APP" && pwd)"


if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "Not installed yet - run ./install.sh first."
  exit 1
fi

# 0.0.0.0 listens on every interface; 127.0.0.1 accepts only this machine.
# Local-only is the default because there is no user authentication.
BIND="127.0.0.1"
LAN=0
[ "${1:-}" = "--lan" ] && { BIND="0.0.0.0"; LAN=1; }

lan_ip() {
  if command -v ipconfig >/dev/null 2>&1; then
    ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null
  else
    hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^(192\.168|10\.|172\.(1[6-9]|2[0-9]|3[01]))\.' | head -1
  fi
}

echo
echo "  Starting GroundedOps..."
echo "  Loading models - the first start takes 30-60 seconds."
echo

if [ "$LAN" = "1" ]; then
  IP="$(lan_ip || true)"
  echo "  SHARE THIS ADDRESS:"
  if [ -n "${IP:-}" ]; then
    echo "     http://$IP:8000"
  else
    echo "     Could not detect your LAN IP - check 'ifconfig' / 'ip addr'."
  fi
  echo
  echo "  Anyone on this network can now use it. There is no login, and"
  echo "  ingested documents are downloadable by anyone with the address."
  echo "  On Linux you may also need:  sudo ufw allow 8000/tcp"
else
  echo "  Open:  http://127.0.0.1:8000"
  echo "  (run './start.sh --lan' to share with your network)"
fi
echo
echo "  Stop: Ctrl+C. This machine must stay awake while others use it."
echo

(
  for _ in $(seq 1 90); do
    sleep 2
    if curl -fsS -m 2 http://127.0.0.1:8000/settings >/dev/null 2>&1; then
      command -v open >/dev/null 2>&1 && open http://127.0.0.1:8000
      command -v xdg-open >/dev/null 2>&1 && xdg-open http://127.0.0.1:8000
      break
    fi
  done
) &

cd "$APP_DIR"
exec "$ROOT/.venv/bin/python" -m uvicorn main:app --host "$BIND" --port 8000
