# Handbook — how everything runs

Everything an administrator or developer needs to operate and extend the
project. Written so that a person who has never seen the system can take
over. Plain facts, no assumed knowledge.

## The machines

THE CONTROL DESK is Heejin's Mac. Code is edited here (or arrives here
from a Claude session writing into this Dropbox folder), committed to
git, and pushed to GitHub (`hkim1596/pulp_fiction_corpus`, private,
reached through a deploy key). The `rtx` helper tool on the Mac pushes,
updates the server clone (`rtx update pulp_fiction_corpus`), runs
commands in server tmux sessions (`rtx run <name> -- <command>`), and
opens a shell (`rtx ssh`). Multi-step server work is normally done with
a heredoc paste: `ssh rtx6000 'bash -s' <<'EOS' … EOS`.

THE MAIN SERVER is the lab's rtx6000 (shared, four GPUs). The project
lives at `~/shared/khj/pulp_fiction_corpus`. The pipeline runs here; the
website runs here; the Cloudflare tunnel that makes the site public runs
here. GPU etiquette: this project may use GPU 0 or 1 only; GPUs hold
other lab members' vLLM models — never free "someone else's" memory,
never remove docker images or containers you do not own, and expect the
disk to be tight (it has filled to 100% twice; docker holds most of it).

THE BACKUP SERVER is a Mac Studio (no GPU — website only, no pipeline).
Setup and switchover: `docs/backup-server.md` and
`scripts/backup_server_setup.sh` / `scripts/serve_backup.sh`.

## The website

`webapp/app.py` is the whole site: one dependency-free Python file
(standard library only; Pillow is used opportunistically for page
previews and everything degrades gracefully without it). It serves on
127.0.0.1:8092 and reads the pipeline's `data/` tree in place — it never
copies or transforms the data it displays.

Paths and files the site uses, all overridable by environment variable:

    PULP_SITE_PASSWORD_FILE  ~/shared/khj/.pulp_site_password  guest passcode
    PULP_SECRET_FILE         ~/shared/khj/.pulp_webapp_secret  cookie-signing secret (auto-created)
    PULP_USERS_FILE          ~/shared/khj/.pulp_users.json     annotator accounts
    PULP_CONFIG              config/pilot_issues.json          issue list

Accounts: visitors can sign up on the site; new accounts wait as
"pending" until an admin approves them on /users. The first admin is
created on the server with `python3 scripts/add_user.py heejin "Heejin
Kim" --admin`. The shared passcode logs in as read-only "guest".

The annotation layer is append-only event sourcing: the machine's
article assembly (under `data/articles/`) is never edited; every human
action (reorder, detach, move, merge, text correction, role marking,
verify) is one JSON line with username and time in
`data/annotations/<issue>.jsonl`, and the site replays those events over
the machine output on every page view. Deleting an annotation file
returns that issue to the pure machine state; nothing else changes.

Page images: full scans are served at `/img/...` (2–5 MB each), small
cached previews at `/thumb/...` (built on demand into `data/thumbs/`,
pre-buildable with `scripts/make_thumbs.py`). Images are sent with a
one-day browser cache; all HTML is sent uncacheable. Do not remove the
preview layer: loading 150 full scans on one page through the tunnel is
what broke the workbench once (see the journal, 2026-08-22).

## Public access — the tunnel

A Cloudflare tunnel named `cihd-site` runs on the main server in the
tmux session `tunnel` and carries BOTH `causal.digihumeng.org` and
`pulp.digihumeng.org` (to 127.0.0.1:8092). Two hard rules. Never restart
it by sending Ctrl-C into the tmux session — the session is
command-form, Ctrl-C kills the whole session and takes BOTH sites down
(public error 1033). Restart is: `tmux kill-session -t tunnel` then
`tmux new -d -s tunnel "~/bin/cloudflared tunnel run cihd-site"`. And
before editing its config, back the file up and run `cloudflared tunnel
ingress validate`.

## The site process and the deploy routine

The site runs in tmux session `pulpsite` as a wrapper loop that restarts
python within ~3 seconds if it dies, logging to `~/shared/khj/pulpsite.log`.
To pick up new code, kill ONLY the python process — the wrapper respawns
it. Never `pkill -f` a pattern that also matches the wrapper's own
command line; the safe recipe kills only processes whose executable is
python.

The standard deploy, after editing `webapp/app.py` (bump `APP_VERSION`):

    Mac, in the repo folder:
      git add <named files>
      git commit -m "pNN: what changed"
      git push
      rtx update pulp_fiction_corpus
    Then restart and verify (Mac):
      ssh rtx6000 'bash -s' <<'EOS'
      for p in $(pgrep -f "webapp/app.py"); do
        if readlink /proc/$p/exe 2>/dev/null | grep -q python; then kill $p; fi
      done
      sleep 6
      pgrep -af "webapp/app.py" | grep -v "while true" || echo "SITE-NOT-RUNNING"
      curl -s https://pulp.digihumeng.org/ | grep -o "v[0-9.]*" | head -1
      EOS

The printed version must match the new `APP_VERSION`.

## Writing terminal pastes for Heejin (learned the hard way)

Heejin pastes whole blocks into zsh, so every line must be safe to
EXECUTE. No bare `#` comment lines (zsh once executed one and its `>`
created junk files that got committed). Instruction lines must be a
colon plus ONE single-quoted string with no apostrophes: `: 'like
this'`. Never put placeholder text in a paste — "PUT-YOUR-PASSCODE-HERE"
once became the actual site passcode; bake real values in or use
`read -p`. For one-line config files prefer `echo value > file` over
nano. Put a quoted STOP line before any step that needs a human choice.

## The pipeline, stage by stage

Each stage is one file in `pipeline/`, runnable per issue or `--all`,
resumable, and writes timing rows to `data/timings.jsonl`. Server tmux
sessions started with `rtx run` do NOT read `~/shared/khj/.pulp_env`;
the stages load it themselves (`timing_util.load_pulp_env`).

    s01_download    IA metadata, Archive OCR text, positional OCR, JP2 scans
                    → data/raw/<id>/, working PNGs → data/pages/<id>/.
                    REFUSES to run unless config/pilot_issues.json has
                    approved=true (Registered Report audit gate).
                    Gold texts from Project Gutenberg → data/gold/<id>/.
    s01b_ia_pages   splits the Archive's text into pages using its
                    positional OCR (the plain text has no page breaks).
    s02_layout_ocr  route A: Surya layout detection + region reading
                    → data/layout/<id>/page_NNNN.json (regions with
                    boxes, labels, text, reading order). Surya 0.22+
                    starts its own vLLM docker container on GPU 0.
    s03_vlm_ocr     route B: whole-page reading by a vision language
                    model (lane not yet stood up; PULP_VLM_* in .pulp_env).
    s04_rules       deterministic cleanup: normalize characters, drop
                    scan noise, remove running heads, rejoin hyphenated
                    words, unwrap paragraphs → data/text/<id>/rules_*/.
    s05_llm_clean   LLM cleanup of rule-cleaned pages, two backends
                    (local qwen lane; Claude API) with a similarity
                    guard that rejects overlarge changes. Qwen quirk:
                    the lane runs "thinking" mode by default and returns
                    empty answers unless the request sends
                    chat_template_kwargs {"enable_thinking": false};
                    strip any <think> block; treat empty replies as
                    retryable; degrade per page rather than crash.
    s06_metrics     error rates against gold + dictionary-word rates
                    → data/metrics.json. Uses rapidfuzz (exact edit
                    distances at full-issue size in seconds). Also
                    reports sub_share: how much of the error is misread
                    characters versus missing/extra content.
    s07_articles    article assembly: pass A classifies each page's
                    segments into units, pass B stitches units across
                    pages → data/articles/<id>/articles.json + index.
                    --force re-assembles and archives that issue's
                    human annotations to .bak first — use with care.

## Troubleshooting

Site unreachable (browser cannot connect, public error 1033): check the
tunnel session first (`tmux ls` on the server), then the site process
(`pgrep -af webapp/app.py`). Site up but images broken: check
`data/pages/` and `data/thumbs/` exist for that issue. `git pull` fails
or logs are empty on the server: the disk is probably full — check
`df -h /`, and remember docker owns most of the disk; coordinate before
deleting anything. A pipeline stage dies on page 1 for both LLM
backends: `.pulp_env` was not loaded (see above). Qwen returns empty
text: thinking mode (see s05 note). Metrics run silent for minutes:
you are running the pre-p17 script; update the server clone.

## Secrets checklist (never in git, never in Dropbox)

    ~/shared/khj/.pulp_env            API keys and lane addresses
    ~/shared/khj/.pulp_site_password  guest passcode
    ~/shared/khj/.pulp_webapp_secret  cookie signing
    ~/shared/khj/.pulp_users.json     accounts (hashed passwords)

To hand the project to a new administrator, transfer these four files
directly (not through the shared folder), or recreate them: a new env
file from the template in `scripts/server_setup.sh`, a new passcode by
echoing into the file, a new admin with `scripts/add_user.py`.
