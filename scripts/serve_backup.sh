#!/usr/bin/env bash
# Run the pulp website on the BACKUP server (Mac Studio).
# Assumes scripts/backup_server_setup.sh was run first, so the project is
# at ~/pulp_backup/pulp_fiction_corpus and the secret files are at
# ~/pulp_backup/secrets. Starts the site on port 8092 and restarts it by
# itself if it ever crashes. Stop with Ctrl-C in this Terminal window.
set -u
BASE="$HOME/pulp_backup"
cd "$BASE/pulp_fiction_corpus"

export PULP_SITE_PASSWORD_FILE="$BASE/secrets/.pulp_site_password"
export PULP_SECRET_FILE="$BASE/secrets/.pulp_webapp_secret"
export PULP_USERS_FILE="$BASE/secrets/.pulp_users.json"
export PULP_API_TOKEN_FILE="$BASE/secrets/.pulp_api_token"

echo "pulp backup site starting on http://127.0.0.1:8092 (Ctrl-C stops it)"
while true; do
  python3 webapp/app.py --port 8092 >> "$BASE/pulpsite_backup.log" 2>&1
  echo "site process ended $(date) - restarting in 3 seconds (log: $BASE/pulpsite_backup.log)"
  sleep 3
done
