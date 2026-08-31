#!/usr/bin/env bash
# Run ON THE MACBOOK, once, after scripts/studio_ssh_enable.sh ran on the
# Studio and its tunnel was restarted. Afterwards "ssh studio" works from
# anywhere with internet, exactly like "ssh rtx6000":
#     ssh studio 'bash -s' <<'EOS'
#     cd ~/pulp_backup/pulp_fiction_corpus && git pull
#     EOS
# Usage:  bash scripts/mac_ssh_studio_setup.sh <studio-account-name>
set -u
HOST="studio-ssh.digihumeng.org"
USER_NAME="${1:-}"
case "$USER_NAME" in
  ""|STUDIO-ACCOUNT|*ACCOUNT*|*NAME*)
    read -r -p "Account name on the Studio (printed by studio_ssh_enable.sh, e.g. the name before @ in its prompt): " USER_NAME ;;
esac
if [ -z "$USER_NAME" ]; then echo "no account name given - stopping"; exit 1; fi

echo "== 1. cloudflared on this Mac"
if ! command -v cloudflared >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install cloudflared
  else
    echo "   Homebrew not found. Install cloudflared by direct download (see docs/backup-server.md), then rerun."
    exit 1
  fi
fi
CF="$(command -v cloudflared)"
echo "   using $CF ($($CF --version 2>/dev/null | head -1))"

echo "== 2. ssh shortcut 'studio'"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/config" && chmod 600 "$HOME/.ssh/config"
if grep -q "^Host studio$" "$HOME/.ssh/config"; then
  echo "   'Host studio' already in ~/.ssh/config - updating its User line to $USER_NAME"
  python3 - "$HOME/.ssh/config" "$USER_NAME" <<'PYEOF'
import sys, re
cfg, user = sys.argv[1], sys.argv[2]
s = open(cfg, encoding="utf-8").read()
s = re.sub(r"(Host studio[ \t]*\n(?:(?:[ \t]+\S.*)?\n)*?)([ \t]+User[ \t]+)\S+", lambda m: m.group(1) + m.group(2) + user, s, count=1)
open(cfg, "w", encoding="utf-8").write(s)
PYEOF
else
  printf '%s\n' "" "Host studio" "  HostName $HOST" "  User $USER_NAME" \
    "  ProxyCommand $CF access ssh --hostname %h" \
    "  ServerAliveInterval 30" >> "$HOME/.ssh/config"
  echo "   added"
fi

echo "== 3. key"
test -f "$HOME/.ssh/id_ed25519" || ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519"
echo "   copying the public key to the Studio (asks for the Studio password once):"
ssh-copy-id -o StrictHostKeyChecking=accept-new studio

grep -A5 "^Host studio" "$HOME/.ssh/config" | sed 's/^/   /'

echo "== 4. test"
ssh -o BatchMode=yes studio 'echo "connected to $(hostname) as $(whoami)"' \
  && echo "DONE: use  ssh studio  from now on." \
  || echo "FAILED: check Remote Login on the Studio and that the tunnel was restarted after the ssh rule was added."
