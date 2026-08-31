#!/usr/bin/env bash
# Run ON THE MAC STUDIO, once. Makes the Studio reachable by ssh from
# anywhere through the Cloudflare tunnel that already serves the site,
# so that from then on the Studio is operated from the MacBook
# ("ssh studio ...") and nobody has to sit at its keyboard.
#
# What it does: 1) turns on Remote Login (macOS sshd) if it can;
# 2) adds an ssh hostname to ~/.cloudflared/config.yml, routed to the
# local sshd; 3) creates the DNS name for it; 4) tells you to restart
# the tunnel window. Idempotent: safe to run twice.
set -u
HOST="studio-ssh.digihumeng.org"
TUNNEL="pulp-backup"
CFG="$HOME/.cloudflared/config.yml"

echo "== 1. Remote Login (sshd)"
if sudo systemsetup -getremotelogin 2>/dev/null | grep -qi "on"; then
  echo "   Remote Login is already on."
else
  sudo systemsetup -setremotelogin on 2>&1 | sed 's/^/   /'
  if sudo systemsetup -getremotelogin 2>/dev/null | grep -qi "on"; then
    echo "   Remote Login is now on."
  else
    echo "   COULD NOT turn it on from the terminal (recent macOS wants Full Disk Access for that)."
    echo "   Do it by hand: System Settings > General > Sharing > Remote Login: ON,"
    echo "   allow access for: All users (or this account). Then run this script again."
  fi
fi

echo "== 2. tunnel ingress rule for ssh"
if [ ! -f "$CFG" ]; then
  echo "   $CFG not found - the tunnel was not set up on this machine as documented. Stopping."
  exit 1
fi
python3 - "$CFG" "$HOST" <<'PYEOF'
import sys, re
cfg, host = sys.argv[1], sys.argv[2]
s = open(cfg, encoding="utf-8").read()
if host in s:
    print("   rule already present")
else:
    lines = s.splitlines()
    indent = "  "
    for ln in lines:
        m = re.match(r"^(\s*)- hostname:", ln)
        if m:
            indent = m.group(1)
            break
    out = []
    for ln in lines:
        out.append(ln)
        if ln.strip() == "ingress:":
            out.append(f"{indent}- hostname: {host}")
            out.append(f"{indent}  service: ssh://localhost:22")
    open(cfg, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("   rule added")
print("   --- config.yml now reads:")
print("".join("   " + l for l in open(cfg, encoding="utf-8").readlines()))
PYEOF
cloudflared tunnel ingress validate || { echo "   config invalid - fix $CFG before restarting the tunnel"; exit 1; }

echo "== 3. DNS name $HOST -> tunnel $TUNNEL"
cloudflared tunnel route dns "$TUNNEL" "$HOST" 2>&1 | sed 's/^/   /' || true

echo "== 4. account name for the MacBook side"
echo "   ssh user on this Studio: $(whoami)"
echo
echo "LAST STEP, by hand: in the Terminal window where the tunnel runs, press Ctrl-C,"
echo "then run:   cloudflared tunnel run $TUNNEL"
echo "(the site is unreachable for the few seconds in between). Then continue on the MacBook."
