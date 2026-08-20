#!/usr/bin/env bash
# One-time server setup for the pulp pipeline. Run on rtx6000 from the repo root:
#   cd ~/shared/khj/pulp_fiction_corpus && bash scripts/server_setup.sh
# Writes its evidence to _server_check/report_setup.txt (pull back with:
#   rtx sync pull _server_check )
set -u
mkdir -p _server_check data
REPORT=_server_check/report_setup.txt
{
echo "== pulp server setup $(date) =="

echo "-- python deps (user site) --"
python3 -m pip install --user --upgrade pip
# pillow: JP2 decode + demo images | surya-ocr: route A layout+OCR
# internetarchive: optional 'ia' client | rapidfuzz reserved for later metrics
python3 -m pip install --user pillow surya-ocr internetarchive rapidfuzz

echo "-- poppler (pdftoppm) --"
command -v pdftoppm || echo "MISSING pdftoppm: sudo apt install poppler-utils"

echo "-- wordlist for the dehyphenation rule --"
ls /usr/share/dict/words 2>/dev/null || \
  echo "MISSING wordlist: sudo apt install wamerican (rules fall back to shape test)"

echo "-- environment file --"
if [ ! -f ~/shared/khj/.pulp_env ]; then
cat > ~/shared/khj/.pulp_env <<'EOF'
# pulp pipeline environment — fill these in (this file is never in git)
# route B vision model (a vLLM lane serving e.g. olmOCR or Qwen-VL):
export PULP_VLM_BASE_URL=http://127.0.0.1:8010/v1
export PULP_VLM_MODEL=CHANGE_ME
export PULP_VLM_KEY=none
# Qwen text lane for stage 5 (reuse a causal-project lane, LOW concurrency):
export PULP_QWEN_BASE_URL=http://127.0.0.1:8006/v1
export PULP_QWEN_MODEL=qwen3.5-9b
export PULP_QWEN_KEY=none
# Claude for stage 5 comparison:
export ANTHROPIC_API_KEY=CHANGE_ME
export PULP_CLAUDE_MODEL=claude-haiku-4-5
EOF
echo "wrote ~/shared/khj/.pulp_env — EDIT THE CHANGE_ME VALUES"
else
echo ".pulp_env already exists, untouched"
fi

echo "-- site passcode --"
if [ ! -f ~/shared/khj/.pulp_site_password ]; then
  echo "CHOOSE-A-PASSCODE" > ~/shared/khj/.pulp_site_password
  chmod 600 ~/shared/khj/.pulp_site_password
  echo "wrote ~/shared/khj/.pulp_site_password — EDIT IT (site is gated once set)"
else
  echo ".pulp_site_password already exists, untouched"
fi

echo "-- smoke tests --"
python3 -m py_compile pipeline/*.py webapp/app.py && echo "py_compile ok"
python3 - <<'EOF'
import importlib.util as u
for m in ("PIL", "surya"):
    print(m, "ok" if u.find_spec(m) else "MISSING")
EOF

echo "== setup done $(date) =="
} > "$REPORT" 2>&1
tail -5 "$REPORT"
echo "full report in $REPORT"
