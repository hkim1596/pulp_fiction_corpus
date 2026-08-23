#!/usr/bin/env bash
# One-time setup of the BACKUP web server on the Mac Studio.
# Run this ON THE MAC STUDIO, in Terminal, while the main server (rtx6000)
# is still reachable. It copies the whole project (code + data) and the
# three account/secret files, then checks that the site can start.
# Full explanation: docs/backup-server.md
set -u

echo "== pulp backup server setup =="

echo "-- python check --"
command -v python3 || { echo "python3 missing: install Xcode command line tools first (xcode-select --install)"; exit 1; }
python3 -m pip install --user --quiet pillow && echo "pillow ok" \
  || echo "pillow install failed - site still works, page previews fall back to full-size scans"

read -p "ssh address of the main server (for example user@rtx6000.university.ac.kr): " SRV
test -n "$SRV" || { echo "no address given, stopping"; exit 1; }

mkdir -p "$HOME/pulp_backup"
printf '%s\n' "$SRV" > "$HOME/pulp_backup/server_address.txt"
echo standby > "$HOME/pulp_backup/MODE"

echo "-- copying project (code + data; several GB, takes a while) --"
rsync -a --progress "$SRV:~/shared/khj/pulp_fiction_corpus/" "$HOME/pulp_backup/pulp_fiction_corpus/"

echo "-- copying the account and secret files (kept OUTSIDE the project folder) --"
mkdir -p "$HOME/pulp_backup/secrets"
for f in .pulp_site_password .pulp_webapp_secret .pulp_users.json .pulp_api_token .pulp_env; do
  rsync -a "$SRV:~/shared/khj/$f" "$HOME/pulp_backup/secrets/" && echo "  $f ok" || echo "  $f MISSING on server"
done
chmod 600 "$HOME/pulp_backup/secrets/".pulp_* 2>/dev/null

echo "-- start check --"
cd "$HOME/pulp_backup/pulp_fiction_corpus"
python3 -m py_compile webapp/app.py && echo "site code compiles"

echo "-- nightly sync job (3:00 am, pull-only, pauses itself when live) --"
mkdir -p "$HOME/Library/LaunchAgents"
cp scripts/com.pulp.backupsync.plist "$HOME/Library/LaunchAgents/"
launchctl unload "$HOME/Library/LaunchAgents/com.pulp.backupsync.plist" 2>/dev/null
launchctl load "$HOME/Library/LaunchAgents/com.pulp.backupsync.plist" \
  && echo "nightly sync installed" || echo "could not install nightly sync - see docs/backup-server.md"

echo
echo "SETUP DONE. MODE=standby; the Studio now refreshes itself every"
echo "night at 3:00 am while the main server is up. During an outage:"
echo "run serve_backup.sh and the tunnel, then go_live.sh; afterwards"
echo "go_standby.sh hands everything back. Details: docs/backup-server.md"
