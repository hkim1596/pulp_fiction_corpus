#!/usr/bin/env bash
# Recovery after the 2026-08-20 disk-full incident. Runs every remaining
# pipeline step in order, in one tmux session. Safe to rerun: s05 skips
# pages that already exist. Run from the repo root on the server:
#   rtx run recover -- bash scripts/recover_p5.sh
set -e
cd "$(dirname "$0")/.."
echo "== recover start $(date) =="
df -h / | tail -1

echo "-- s01b: split IA baseline into pages (from hOCR) --"
python3 pipeline/s01b_ia_pages.py

echo "-- s04c: rules on the paged IA text --"
python3 pipeline/s04_rules.py --all --src ia

echo "-- s05 qwen: LLM cleanup on rules_routeA --"
python3 pipeline/s05_llm_clean.py --all --backend qwen

echo "-- s05 claude: LLM cleanup on rules_routeA --"
python3 pipeline/s05_llm_clean.py --all --backend claude

echo "-- s06: metrics --"
python3 pipeline/s06_metrics.py

echo "-- s07: article assembly --"
python3 pipeline/s07_articles.py --all

df -h / | tail -1
echo "== RECOVER-DONE $(date) =="
