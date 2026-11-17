#!/usr/bin/env bash
# Second GroundedOps instance for long batch jobs (evals, reindexes), capped
# during the working day so it cannot starve the support instance.
#
# Shares the app's .venv (6 GB, path-independent). Its own code, index,
# documents and settings. Deliberately carries NO accounts, conversations,
# leads or provider keys.
set -euo pipefail

BASE=/srv/groundedops
APP=$BASE/app
LAB=$BASE/lab
SVC=groundedops
DAY_QUOTA=300%
NIGHT_QUOTA=800%

as_svc() { sudo -u "$SVC" -H "$@"; }
say() { printf '\n== %s\n' "$*"; }

# ── 1. Lab checkout ─────────────────────────────────────────────────────
say "Lab checkout at $LAB"
if [ -d "$LAB/.git" ]; then
  echo "   already there, leaving it"
else
  cd "$BASE"
  as_svc git clone -q "$APP" "$LAB"
  as_svc git -C "$LAB" remote set-url origin https://github.com/hamzahrizvi/groundedops.git
fi
as_svc git -C "$LAB" log -1 --format='   at %h %s'

# ── 2. Data: index, documents and the stores retrieval needs ────────────
# NOT copied, on purpose: accounts.json (staff password hashes),
# conversations.db and faq_gaps.json (visitor text), quota.db,
# widget_leads.json (customer contact details). The lab is for measuring
# retrieval, and none of that is needed to do it.
say "Copying index, documents and the stores retrieval needs"
if [ ! -d "$LAB/src/chroma_db" ]; then
  as_svc cp -a "$APP/src/chroma_db" "$LAB/src/chroma_db"
  echo "   index copied"
else
  echo "   index already there (delete $LAB/src/chroma_db to refresh)"
fi
if [ ! -d "$LAB/documents" ] || [ -z "$(sudo -u $SVC ls -A "$LAB/documents" 2>/dev/null)" ]; then
  as_svc mkdir -p "$LAB/documents"
  as_svc cp -a "$APP/documents/." "$LAB/documents/"
  echo "   documents copied"
else
  echo "   documents already there"
fi
for f in catalog_config.json confluence_sync.json doc_kinds.json doc_vocab.json \
         faq_store.json internal_sources.json policy.json spec_index.json; do
  [ -f "$APP/src/$f" ] && as_svc cp -a "$APP/src/$f" "$LAB/src/$f" || true
done
echo "   stores copied"

# ── 3. Settings, with every key blank ───────────────────────────────────
say "Lab settings (no provider key, no backup passphrase)"
if [ ! -f "$LAB/src/.env" ]; then
  as_svc cp "$APP/src/.env" "$LAB/src/.env"
  sudo -u "$SVC" sed -i -E 's/^(DEEPSEEK_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|BACKUP_PASSPHRASE|SESSION_SECRET|WIDGET_TOKEN_SECRET)=.*/\1=/' "$LAB/src/.env"
  chmod 600 "$LAB/src/.env"
  echo "   written with keys blanked"
else
  echo "   existing .env kept"
fi
echo -n "   keys still set (should be 0): "
sudo -u "$SVC" grep -cE '^(DEEPSEEK_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)=.+' "$LAB/src/.env" || true

# ── 4. The cap ──────────────────────────────────────────────────────────
# A slice, so every lab job inherits the limit without each unit repeating
# it. The day quota lives in the unit file and therefore survives reboots;
# the evening lift is applied with --runtime, so a crash at 02:00 cannot
# leave the cap off.
say "CPU and memory cap"
cat > /etc/systemd/system/$SVC-lab.slice <<EOF
[Unit]
Description=GroundedOps lab - batch work, capped during the working day

[Slice]
CPUQuota=$DAY_QUOTA
MemoryHigh=6G
EOF

cat > /etc/systemd/system/$SVC-lab-uncap.service <<EOF
[Unit]
Description=Lift the GroundedOps lab CPU cap for the evening

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl set-property --runtime $SVC-lab.slice CPUQuota=$NIGHT_QUOTA
EOF

cat > /etc/systemd/system/$SVC-lab-recap.service <<EOF
[Unit]
Description=Reinstate the GroundedOps lab CPU cap for the working day

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl set-property --runtime $SVC-lab.slice CPUQuota=$DAY_QUOTA
EOF

# UK time, so the window follows British Summer Time without editing. The
# server itself stays on UTC, which is what the AWS image will be.
cat > /etc/systemd/system/$SVC-lab-uncap.timer <<EOF
[Unit]
Description=Lift the lab cap at 19:00 UK time

[Timer]
OnCalendar=*-*-* 19:00:00 Europe/London

[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/$SVC-lab-recap.timer <<EOF
[Unit]
Description=Reinstate the lab cap at 08:00 UK time

[Timer]
OnCalendar=*-*-* 08:00:00 Europe/London

[Install]
WantedBy=timers.target
EOF

# ── 5. Nightly job ──────────────────────────────────────────────────────
# The script is yours to edit; systemd only calls it. Add further jobs by
# adding lines, not by touching unit files.
say "Nightly job"
if [ ! -f "$LAB/nightly.sh" ]; then
  cat > "$LAB/nightly.sh" <<'EOS'
#!/usr/bin/env bash
# What the lab runs each night. Edit freely - systemd just calls this.
# Runs after the CPU cap lifts at 19:00 UK time.
set -uo pipefail
cd /srv/groundedops/lab/src
PY=/srv/groundedops/app/.venv/bin/python
OUT=/srv/groundedops/lab/eval_runs
mkdir -p "$OUT"
STAMP=$(date +%Y%m%d-%H%M)

# Retrieval quality: embedder and reranker only, no provider calls, no cost.
# --compare prints the change against the previous run.
PREV=$(ls -1t "$OUT"/retrieval-*.json 2>/dev/null | head -1)
echo "=== eval_retrieval $STAMP ==="
if [ -n "$PREV" ]; then
  "$PY" eval_retrieval.py --cases eval_cases_retrieval.json \
      --json "$OUT/retrieval-$STAMP.json" --compare "$PREV"
else
  "$PY" eval_retrieval.py --cases eval_cases_retrieval.json \
      --json "$OUT/retrieval-$STAMP.json"
fi
echo "=== done, exit $? ==="

# Add more below. Anything needing a provider key will fail until one is
# set in /srv/groundedops/lab/src/.env - deliberately, so nothing spends
# money unattended by accident.
EOS
  chown "$SVC:$SVC" "$LAB/nightly.sh"
  chmod 750 "$LAB/nightly.sh"
  echo "   written"
else
  echo "   existing nightly.sh kept"
fi

cat > /etc/systemd/system/$SVC-lab-nightly.service <<EOF
[Unit]
Description=GroundedOps lab nightly jobs

[Service]
Type=oneshot
User=$SVC
Group=$SVC
Slice=$SVC-lab.slice
WorkingDirectory=$LAB/src
Environment=HF_HOME=$BASE/hf-cache
Environment=HF_HUB_OFFLINE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=$LAB/nightly.sh
TimeoutStartSec=6h
EOF

cat > /etc/systemd/system/$SVC-lab-nightly.timer <<EOF
[Unit]
Description=Run the lab nightly jobs at 19:30 UK time

[Timer]
OnCalendar=*-*-* 19:30:00 Europe/London
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable -q $SVC-lab-uncap.timer $SVC-lab-recap.timer $SVC-lab-nightly.timer
systemctl start $SVC-lab-uncap.timer $SVC-lab-recap.timer $SVC-lab-nightly.timer

say "Done"
systemctl list-timers "$SVC*" --no-pager | head -6
echo
echo "Lab:      $LAB  (shares $APP/.venv)"
echo "Cap:      $DAY_QUOTA 08:00-19:00 UK, $NIGHT_QUOTA overnight, MemoryHigh 6G"
echo "Nightly:  $LAB/nightly.sh at 19:30 UK  ->  $LAB/eval_runs/"
echo "Run now:  sudo systemctl start $SVC-lab-nightly"
echo "Watch:    journalctl -u $SVC-lab-nightly -f"
