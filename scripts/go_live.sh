#!/usr/bin/env bash
# Make THIS Mac Studio the live pulp server. Run on the day the main
# server goes down, AFTER starting the two Terminal windows:
#   window 1:  bash ~/pulp_backup/pulp_fiction_corpus/scripts/serve_backup.sh
#   window 2:  cloudflared tunnel run pulp-backup
# It marks this machine live (which pauses the nightly pull) and points
# the public address here.
set -u
BASE="$HOME/pulp_backup"
if ! curl -s --max-time 5 http://127.0.0.1:8092/healthz | grep -q ok; then
  echo "the site is not running on this machine yet - start it first:"
  echo "  bash $BASE/pulp_fiction_corpus/scripts/serve_backup.sh"
  exit 1
fi
echo live > "$BASE/MODE"
cloudflared tunnel route dns --overwrite-dns pulp-backup pulp.digihumeng.org
echo "DONE: this Studio is now the live server."
echo "MODE=live (nightly pull paused). pulp.digihumeng.org points here"
echo "within a minute or two. Keep both Terminal windows open and keep"
echo "this Mac awake (System Settings > Energy > prevent sleeping)."
