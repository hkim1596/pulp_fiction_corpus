#!/usr/bin/env bash
# Pulp Fiction Corpus site on 127.0.0.1:8092, auto-restarting on crash.
# Run inside tmux:  tmux new -d -s pulpsite 'bash ~/shared/khj/pulp_fiction_corpus/webapp/serve_pulp.sh'
cd "$(dirname "$0")"
LOG=~/shared/khj/log_pulpsite.txt
[ -f ~/shared/khj/.pulp_env ] && source ~/shared/khj/.pulp_env
while true; do
  echo "$(date) starting pulp site" >> "$LOG"
  python3 app.py --port 8092 >> "$LOG" 2>&1
  echo "$(date) pulp site exited ($?) — restarting in 3s" >> "$LOG"
  sleep 3
done
