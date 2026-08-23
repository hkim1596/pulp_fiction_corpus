#!/usr/bin/env bash
# Nightly one-way sync: MAIN server -> this backup Mac Studio.
# Conflict-safe by design: it only PULLS, and it refuses to run while
# this machine is the live server (the MODE file says "live"), so
# annotations made here during an outage can never be overwritten by a
# stale copy pulled from the returning main server.
# Installed as a 3:00 am job by the setup instructions; manual run:
#   bash ~/pulp_backup/pulp_fiction_corpus/scripts/backup_sync.sh
set -u
BASE="$HOME/pulp_backup"
LOG="$BASE/sync.log"
mkdir -p "$BASE"
echo "== sync start $(date) ==" >> "$LOG"

MODE=$(cat "$BASE/MODE" 2>/dev/null || echo standby)
if [ "$MODE" = "live" ]; then
  echo "this backup is LIVE - pull skipped (run go_standby.sh after the outage)" >> "$LOG"
  exit 0
fi

SRV=$(cat "$BASE/server_address.txt" 2>/dev/null || true)
if [ -z "$SRV" ]; then
  echo "no server_address.txt - run scripts/backup_server_setup.sh once first" >> "$LOG"
  exit 1
fi

if rsync -a --delete --timeout=60 --exclude "__pycache__" \
     "$SRV:~/shared/khj/pulp_fiction_corpus/" \
     "$BASE/pulp_fiction_corpus/" >> "$LOG" 2>&1; then
  echo "project synced (code + data)" >> "$LOG"
else
  echo "PROJECT SYNC FAILED - main server off or unreachable (normal during an outage)" >> "$LOG"
fi

mkdir -p "$BASE/secrets"
for f in .pulp_site_password .pulp_webapp_secret .pulp_users.json .pulp_api_token .pulp_env; do
  rsync -a --timeout=30 "$SRV:~/shared/khj/$f" "$BASE/secrets/" >> "$LOG" 2>&1 || true
done
chmod 600 "$BASE/secrets/".pulp_* 2>/dev/null
echo "== sync end $(date) ==" >> "$LOG"
