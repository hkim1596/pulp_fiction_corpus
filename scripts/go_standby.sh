#!/usr/bin/env bash
# Hand the site back to the main server after the outage. Run ON the
# Mac Studio once the main server is up again. Copies the outage
# period's annotations, feedback, and accounts back to the main server,
# returns the public address, and resumes the nightly pull.
set -u
BASE="$HOME/pulp_backup"
SRV=$(cat "$BASE/server_address.txt" 2>/dev/null || true)
if [ -z "$SRV" ]; then
  echo "no server_address.txt - run scripts/backup_server_setup.sh first"
  exit 1
fi
read -p "Is the MAIN server back online and reachable? type yes to continue: " OK
test "$OK" = "yes" || { echo "stopped - nothing changed"; exit 1; }

echo "-- copying the outage period's work back to the main server --"
rsync -a "$BASE/pulp_fiction_corpus/data/annotations/" \
  "$SRV:~/shared/khj/pulp_fiction_corpus/data/annotations/" \
  && echo "annotations copied"
rsync -a "$BASE/pulp_fiction_corpus/data/feedback.jsonl" \
  "$SRV:~/shared/khj/pulp_fiction_corpus/data/feedback.jsonl" \
  && echo "feedback copied"
rsync -a "$BASE/secrets/.pulp_users.json" \
  "$SRV:~/shared/khj/.pulp_users.json" \
  && echo "accounts copied"

echo "-- returning the public address to the main server --"
cloudflared tunnel route dns --overwrite-dns cihd-site pulp.digihumeng.org
echo standby > "$BASE/MODE"
echo "DONE: MODE=standby, nightly pull resumes, public address returns"
echo "to the main server within a minute or two. You can close the two"
echo "Terminal windows here (Ctrl-C in each)."
echo "ON THE MAIN SERVER, check the site and tunnel sessions are up:"
echo "  ssh into it and run: tmux ls   (expect pulpsite and tunnel)"
