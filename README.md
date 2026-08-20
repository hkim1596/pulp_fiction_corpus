# pulp_fiction_corpus

Pilot pipeline and collaborator website for the Pulp Fiction Corpus project.

Heejin Kim (Kyungpook National University, Digital Humanities Engineering Center)
with Dennis Yi Tenen (Columbia University). Target publication: *Computational
Humanities Research* (Registered Report).

## What this repository does

Processes pulp magazine issues from the Internet Archive end to end, and serves a
passcode-protected website where collaborators review every step:

1. `pipeline/s01_download.py` — download one issue's metadata, existing OCR text,
   and page images from the Internet Archive (official client, throttled, logged).
2. `pipeline/s02_layout_ocr.py` — route A: layout detection + region OCR
   (American Stories style; every line keeps page + box coordinates).
3. `pipeline/s03_vlm_ocr.py` — route B: whole-page reading by a vision LLM
   served from our vLLM lanes.
4. `pipeline/s04_rules.py` — rule-based cleanup (running heads, page numbers,
   hyphenation, ligatures, garbage lines, paragraph joining). Every change logged.
5. `pipeline/s05_llm_clean.py` — LLM post-correction, two backends compared:
   local Qwen (vLLM lane) and Claude API. Guardrails reject rewrites.
6. `pipeline/s06_metrics.py` — character/word error rates against Project
   Gutenberg proofread text; dictionary-word rate everywhere else.
7. `webapp/app.py` — the website: one dependency-free Python file (stdlib
   only) that reads the `data/` tree in place — nothing is exported.

Every stage writes wall-clock timing to `data/timings.jsonl` — the pilot's other
job is to measure speed and cost for the full-corpus decision.

## The pilot

Ten issues, defined in `config/pilot_issues.json` (status: proposed until Heejin
sets `"approved": true`). Four have human-proofread overlap for accuracy
measurement. These ten are the project's declared development set: they will be
named in the Registered Report protocol and excluded from all study analyses.
Nothing downloads until approved.

## Quickstart (server)

```bash
bash scripts/server_setup.sh              # one-time: python deps + checks
python3 pipeline/s01_download.py --all    # after approval flag is set
python3 pipeline/s02_layout_ocr.py --all
python3 pipeline/s03_vlm_ocr.py --all
python3 pipeline/s04_rules.py --all
python3 pipeline/s05_llm_clean.py --all --backend qwen
python3 pipeline/s05_llm_clean.py --all --backend claude
python3 pipeline/s06_metrics.py
tmux new -d -s pulpsite 'bash webapp/serve_pulp.sh'
curl -s http://127.0.0.1:8092/healthz     # -> ok
```

See `HANDBOOK.md` for the deploy pattern, access control, and conventions.
See `METHOD.md` for the method description (also rendered at /method on the site).
