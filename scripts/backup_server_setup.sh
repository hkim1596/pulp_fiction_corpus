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

echo "-- copying project (code + data; several GB, takes a while) --"
mkdir -p "$HOME/pulp_backup"
rsync -a --progress "$SRV:~/shared/khj/pulp_fiction_corpus/" "$HOME/pulp_backup/pulp_fiction_corpus/"

echo "-- copying the account and secret files (kept OUTSIDE the project folder) --"
mkdir -p "$HOME/pulp_backup/secrets"
for f in .pulp_site_password .pulp_webapp_secret .pulp_users.json; do
  rsync -a "$SRV:~/shared/khj/$f" "$HOME/pulp_backup/secrets/" && echo "  $f ok" || echo "  $f MISSING on server"
done
chmod 600 "$HOME/pulp_backup/secrets/".pulp_* 2>/dev/null

echo "-- start check --"
cd "$HOME/pulp_backup/pulp_fiction_corpus"
python3 -m py_compile webapp/app.py && echo "site code compiles"
echo
echo "SETUP DONE. To run the site: bash scripts/serve_backup.sh"
echo "To make it reachable at pulp.digihumeng.org during the outage,"
echo "follow the cloudflared section of docs/backup-server.md"
