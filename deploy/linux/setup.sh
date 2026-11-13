#!/usr/bin/env bash
# GroundedOps server setup for Ubuntu Server 24.04 (a spare PC now, AWS later).
#
#   sudo bash setup.sh                    install or update
#   sudo bash setup.sh you@company.com    ...and create the Owner account
#
# Safe to run again: every step checks before it acts. Run it again to update
# the app to the latest commit of BRANCH.
#
# Everything lives under /srv/groundedops, so on AWS that one path becomes an
# EBS volume and nothing else changes:
#   /srv/groundedops/app        the git checkout (.venv, src/.env, index, documents)
#   /srv/groundedops/hf-cache   Hugging Face models, at the commits in src/model_pins.py
#
# Steps mirror install.sh (same lock file, same model loaders), minus its
# prompts, plus a service account and a systemd unit.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"

REPO_URL="${REPO_URL:-https://github.com/hamzahrizvi/groundedops.git}"
BRANCH="${BRANCH:-experimental/v17.3}"
PORT="${PORT:-8000}"
OWNER_EMAIL="${1:-}"

SVC=groundedops
BASE=/srv/groundedops
APP=$BASE/app
HF=$BASE/hf-cache
PY=$APP/.venv/bin/python

say() { printf '\n== %s\n' "$*"; }
as_svc() { sudo -u "$SVC" -H env HF_HOME="$HF" "$@"; }

# ── 1. System packages ──────────────────────────────────────────────────
# libgl1 + libglib: opencv-python (pulled in by rapidocr) will not import
# without them on a server install.
say "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git curl ca-certificates openssl \
  python3 python3-venv libgl1 libglib2.0-0t64
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python 3.11+ needed; this is $(python3 --version)."; exit 1; }

# ── 2. Service account and folders ──────────────────────────────────────
say "Service account '$SVC'"
id "$SVC" >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin "$SVC"
install -d -o "$SVC" -g "$SVC" -m 750 "$BASE" "$HF"
cd "$BASE"   # sudo -u keeps the cwd; the service user cannot read $HOME of whoever ran this

# ── 3. Code ─────────────────────────────────────────────────────────────
say "Code: $REPO_URL ($BRANCH)"
if [ -d "$APP/.git" ]; then
  # An update is the one moment this install can be made worse, so take the
  # two things a revert needs FIRST: the data as it stands, and the commit
  # it is running. SKIP_BACKUP=1 for a second run minutes after the first.
  OLD_SHA=$(as_svc git -C "$APP" rev-parse HEAD)
  if [ "${SKIP_BACKUP:-0}" != "1" ] && [ -f /etc/systemd/system/$SVC-backup.service ]; then
    echo "   backing up before the update..."
    if systemctl start $SVC-backup.service; then
      echo "   backed up to $BASE/backups"
    else
      echo
      echo "   BACKUP FAILED - not updating. Fix it, or re-run with SKIP_BACKUP=1"
      echo "   if you accept updating with no way back. Last lines:"
      journalctl -u $SVC-backup -n 10 --no-pager | sed 's/^/     /'
      exit 1
    fi
  fi
  as_svc git -C "$APP" fetch -q origin "$BRANCH"
  as_svc git -C "$APP" checkout -q "$BRANCH"
  as_svc git -C "$APP" merge -q --ff-only "origin/$BRANCH"
  NEW_SHA=$(as_svc git -C "$APP" rev-parse HEAD)
  if [ "$OLD_SHA" != "$NEW_SHA" ]; then
    printf '%s  %s -> %s\n' "$(date -Is)" "${OLD_SHA:0:12}" "${NEW_SHA:0:12}" \
      >> "$BASE/releases.log"
    echo "   updated from ${OLD_SHA:0:12}; to go back:"
    echo "     sudo -u $SVC git -C $APP checkout $OLD_SHA && sudo systemctl restart $SVC"
  fi
else
  as_svc git clone -q --branch "$BRANCH" "$REPO_URL" "$APP"
fi
as_svc git -C "$APP" log -1 --format='   at %h %s'
install -d -o "$SVC" -g "$SVC" -m 750 "$APP/documents"

# ── 4. Python environment (~6 GB: the Linux torch wheel brings CUDA libs) ─
say "Python environment"
[ -x "$PY" ] || as_svc python3 -m venv "$APP/.venv"
as_svc "$PY" -m pip install -q --upgrade pip
as_svc "$PY" -m pip install -q --require-hashes -r "$APP/src/requirements.lock"

# ── 5. Models ───────────────────────────────────────────────────────────
# The backend's own loaders, so the cache holds exactly what it runs at the
# pinned commits. Both reranker profiles, so switching profile in the console
# works with the service offline. First try offline (already cached = no
# network), else download, then prove an offline load works, because the
# service runs with HF_HUB_OFFLINE=1.
say "Models (~2 GB on first run)"
load_models() {
  (cd "$APP/src" && as_svc HF_HUB_OFFLINE="$1" "$PY" -c '
import embeddings, reranker, grounding
embeddings._get_model()
for m in sorted(set(reranker.PROFILES.values())):
    reranker._get(m)
grounding._get_nli_model()
print("   models ready")')
}
load_models 1 2>/dev/null || { load_models 0 && load_models 1; }

# ── 6. Settings file (no provider keys) ─────────────────────────────────
# From .env.example. The two signing secrets are generated here because the
# console refuses every sign-in without SESSION_SECRET. TRUST_PROXY goes to
# 0: nothing sits in front of this box yet, so the header would be forgeable.
# Set it back to 1 when a Cloudflare tunnel or AWS load balancer is added.
ENVF=$APP/src/.env
if [ ! -f "$ENVF" ]; then
  say "Writing $ENVF (add a provider key later, in the console or here)"
  install -o "$SVC" -g "$SVC" -m 600 "$APP/.env.example" "$ENVF"
  sed -i -e "s/^SESSION_SECRET=\$/SESSION_SECRET=$(openssl rand -hex 32)/" \
         -e "s/^WIDGET_TOKEN_SECRET=\$/WIDGET_TOKEN_SECRET=$(openssl rand -hex 32)/" \
         -e "s/^TRUST_PROXY=1\$/TRUST_PROXY=0/" "$ENVF"
else
  say "Keeping existing $ENVF"
fi

# ── 7. Owner account ────────────────────────────────────────────────────
# Until one exists, the first person on the network to open /admin becomes
# the Owner, so the service is not started without one.
has_owner() { (cd "$APP/src" && as_svc "$PY" manage_accounts.py list 2>/dev/null) | grep -Eq '^EMAIL +LEVEL'; }
if ! has_owner && [ -n "$OWNER_EMAIL" ]; then
  say "Creating Owner account $OWNER_EMAIL (you will be asked for a password)"
  (cd "$APP/src" && as_svc "$PY" manage_accounts.py create "$OWNER_EMAIL" --level root)
fi

# ── 8. systemd: the backend, plus a health check every minute ───────────
# Restart= covers a crash (backing off 5 s -> 5 min, as serve.ps1 does).
# The timer covers a hang: 3 failed /health probes 30 s apart = restart.
# Plain /health only, never ?deep=1, so a provider outage never restarts it.
say "systemd units"
cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=GroundedOps backend
After=network-online.target
Wants=network-online.target

[Service]
User=$SVC
Group=$SVC
WorkingDirectory=$APP/src
Environment=HF_HOME=$HF
Environment=HF_HUB_OFFLINE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=$PY -m uvicorn main:app --host 0.0.0.0 --port $PORT
# "started" means /health answers, not just that the process exists.
ExecStartPost=/usr/bin/timeout 180 /bin/sh -c 'until curl -fsS -o /dev/null http://127.0.0.1:$PORT/health; do sleep 2; done'
TimeoutStartSec=240
Restart=always
RestartSec=5
RestartSteps=6
RestartMaxDelaySec=300
NoNewPrivileges=yes
ProtectSystem=full
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/$SVC-health.service <<EOF
[Unit]
Description=GroundedOps hang check

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'systemctl is-active --quiet $SVC || exit 0; for i in 1 2 3; do curl -fsS -o /dev/null --max-time 10 http://127.0.0.1:$PORT/health && exit 0; sleep 30; done; echo "3 failed /health probes - restarting $SVC"; systemctl restart $SVC'
EOF

cat > /etc/systemd/system/$SVC-health.timer <<EOF
[Unit]
Description=Run the GroundedOps hang check every minute

[Timer]
OnBootSec=5min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable -q $SVC $SVC-health.timer
systemctl start $SVC-health.timer

if has_owner; then
  say "Starting GroundedOps (waits for /health, up to 3 min)"
  systemctl restart $SVC
  systemctl --no-pager --lines=0 status $SVC || true
  echo
  echo "Up:       http://$(hostname -I | awk '{print $1}'):$PORT/admin"
  echo "Ready?    curl -s 'http://127.0.0.1:$PORT/health?deep=1'"
  echo "Logs:     journalctl -u $SVC -f"
else
  say "Not started: no Owner account yet"
  echo "Run again with your email:  sudo bash $0 you@company.com"
fi
