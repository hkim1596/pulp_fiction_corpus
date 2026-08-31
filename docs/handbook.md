# Handbook — how everything runs

Everything an administrator or developer needs to operate and extend the
project. Written so that a person who has never seen the system can take
over. Plain facts, no assumed knowledge.

## The machines

THE CONTROL DESK is Heejin's Mac. Code is edited here (or arrives here
from a Claude session writing into this Dropbox folder), committed to
git, and pushed to GitHub (`hkim1596/pulp_fiction_corpus`; the
repository is public, which is why secrets live only in Dropbox; the
main server pulls through a deploy key, the Studio over plain HTTPS). The `rtx` helper tool on the Mac pushes,
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

## The read-only data door (the /api paths)

Since v0.8.1 the site has a second, machine-friendly way in, built so
that a development session (Claude, or any tool a developer trusts) can
READ the server's data directly over the web instead of asking a human
to run terminal commands. It is separate from the human passcode and
from annotator accounts, and it can only read — nothing behind it can
change, delete, or approve anything.

    /api/<token>/ls?path=annotations          list a folder under data/
    /api/<token>/get?path=annotations/x.jsonl read one file (8 MB cap)
    /api/<token>/doc/<issue-id>               an issue's full assembled
                                              state as JSON: articles with
                                              fragment keys, unsorted,
                                              furniture, roles, and every
                                              box with its owner

The token is a long random string in `~/shared/khj/.pulp_api_token`
(env override `PULP_API_TOKEN_FILE`). Delete that file and the whole
door is off; recreate it (any long random string, one line) and it is
back. It is never in git; its VALUE is kept, with every other secret,
in the `secrets/` folder one level above this repository in the
Dropbox share (project decision 2026-08-22: whoever has the Dropbox
folder has full access). Only paths inside `data/` are
reachable (path traversal is blocked), so passwords, keys, and code are
not exposed even to a token holder. The backup-server scripts copy and
use the same token file, so the door works during an outage too.

Why it exists: debugging from a chat session used to mean sending
Heejin diagnostic pastes and waiting for output. With the door, the
session fetches `…/doc/wt_1925_11` itself and sees exactly what the
replay engine sees. The duplicate-region bug of 2026-08-22 was the
first thing verified this way.

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

While the Studio is the live server (main server down), the deploy is
a GitHub round trip — the repository is public, so the Studio pulls
over HTTPS with no key. On the Mac: the same add / commit / push as
above. Then on the Studio, in a new Terminal window (the serve window
keeps running):

    cd ~/pulp_backup/pulp_fiction_corpus
    git remote set-url origin https://github.com/hkim1596/pulp_fiction_corpus.git
    git stash push -m "studio-local-before-pull"
    git fetch origin
    git checkout -B main origin/main
    python3 -m py_compile webapp/app.py webapp/reuse_pages.py && echo COMPILE-OK
    pkill -f "webapp/app.py --port 8092"
    sleep 6
    curl -s https://pulp.digihumeng.org/ | grep -o "v[0-9.]*" | head -1

The serve loop (scripts/serve_backup.sh) restarts python within three
seconds of the kill; the last line must print the new version. The
stash line only matters if the Studio's copy was ever edited by hand;
normally it says "No local changes to save". Files the pipeline wrote
under data/ are untracked and untouched by the checkout.

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

## The text-reuse pipeline (the r-series) and the reuse pages

Protocol sections 3 and 4, rehearsed on the ten development-set
issues. Every stage is one file in `pipeline/`, CPU only, stdlib plus
numpy/pandas/scikit-learn/statsmodels and the `fastembed` package
(ONNX runtime; it downloads the embedding model on first use). The
stages read `data/pilot_stories.jsonl` and write under `data/reuse/`;
the website reads that folder in place. All settings below were
decided by Heejin on 2026-08-31 and are the development-set defaults;
the protocol says the paraphrase settings are re-set on a
hand-reviewed validation set and then frozen.

    r00_export_stories  runs where data/ lives (the live server). Asks
                        the site's own replay (machine assembly plus
                        every human action) for every article and
                        writes one JSON line per article: metadata,
                        status, region keys, text.
                        → data/pilot_stories.jsonl (+ .gz)
    r01_normalize       conservative normalization (protocol 3.1):
                        NFKC, soft hyphens out, line-break hyphenation
                        rejoined, quotes and dashes straightened,
                        whitespace collapsed; tokens = letter/digit runs
                        with internal apostrophes, folded to lowercase,
                        with offsets into the canonical text.
                        No stemming, no stopword removal.
    r02_verbatim        exact reuse: shingle index at seeds 6, 7, 8
                        (separate passes), maximal left/right extension,
                        commonplace cap MAX_DF=50 stories per shingle
                        (skipped shingles are written out), union-find
                        clusters. Only pairs from DIFFERENT issues form
                        the inventory; same-issue matches go to
                        *_sameissue.jsonl labelled "shared-region
                        duplicate" (the two records own the same scan
                        regions) or "same-issue repeat". Records of one
                        issue that share regions form a "family";
                        witness counts are reported raw and collapsed.
                        → data/reuse/<set>_k<k>_{stats,clusters,pairs,
                          story_share,skipped_shingles}.json,
                          _matches.jsonl, _sameissue.jsonl,
                          <set>_region_overlap.json
    r03_synthetic       a SEPARATE copy of the story corpus with planted
                        reuse (20 verbatim, 20 near-verbatim with 8% of
                        words damaged, 20 heavily edited), ids suffixed
                        "~synth", scored for recall at each seed.
                        → data/reuse/synthetic/
    r04_paraphrase      near-verbatim and rewritten reuse (protocol 3.2):
                        passages of 50 words, step 25; BAAI/bge-small-
                        en-v1.5 embeddings; K nearest neighbours in
                        other stories (K=10 main, 5 and 20 sensitivity,
                        all from one K=20 retrieval); plus every exact
                        match as a candidate (the lexical near-match
                        tier). Candidates widened by 25 words are
                        aligned word by word (local alignment, match +2,
                        mismatch -1, gap -1; words equal if identical or,
                        at 5+ letters, one edit apart); touching
                        alignments are joined and re-aligned. Keep rule:
                        20+ columns and identity 0.60+. Same-issue
                        alignments are diagnostics, as in r02.
                        Embeddings are cached (emb_*.npz, never in git).
                        --synthetic runs the planted copy and scores it;
                        --window 100 --stride 50 is the sensitivity run.
                        → data/reuse/para/<set>_w50s25_k<K>_*
    r05_background      protocol 4.1: one row per story pair (all pairs
                        at pilot scale) with exact and paraphrase extent,
                        later date, years apart, topic similarity
                        (TF-IDF cosine on reuse-masked text, plus the
                        embedding cosine), same author / magazine /
                        publisher / genre / format flags (publishers from
                        pipeline/publishers.json). Same-issue pairs are
                        flagged and excluded from the background. Also:
                        survival curves P(longest >= L | stratum), the
                        stratified sampler run against the full table as
                        a check, the most unusual pairs, and a first
                        full two-part hierarchical model (any reuse:
                        logistic; extent: Poisson; one random intercept
                        per story entering for both members of a pair;
                        variational Bayes via statsmodels) as a proposal
                        for Dennis.
                        → data/reuse/background/pairs_<set>.csv.gz,
                          summary_<set>.json

Story sets: `machine` (every assembled story, 50+ words), `verified`
(human-verified only) and `corrected` (verified or modified). The
2026-08-31 run used machine and corrected because only one story was
verified.

Typical run, in order, on a machine holding the export:

    python3 pipeline/r02_verbatim.py --set machine
    python3 pipeline/r02_verbatim.py --set corrected
    python3 pipeline/r03_synthetic.py
    python3 pipeline/r04_paraphrase.py --set machine      (~15 min CPU, first time)
    python3 pipeline/r04_paraphrase.py --synthetic
    python3 pipeline/r05_background.py --set machine      (~3 min)

Each file has `--selftest`. Outputs are small JSON and travel with the
repository (data/reuse is the one data folder git tracks); the site on
the Studio gets them by `git pull`.

The reuse pages (webapp/reuse_pages.py, v0.9.0): `/reuse` overview
with server-drawn SVG charts (no libraries), `/reuse/clusters` with
filters, `/reuse/cluster/<set>/<kind>/<k>/<n>` with witnesses grouped
by story and located live in the current article text ("open on
workbench" is the article page with `?sel=<region key>`, which
highlights that region), `/reuse/progress` with annotation progress,
the pipeline status board and the corpus-building board. Member login
required, like the rest of the site.

Two facts the first run established (details in docs/pilot-results.md
and the journal): the machine assembly lists some scan regions under
two records — td_1932_02_a001 holds 2,235 of the issue's 2,474 regions
including five complete stories that also exist as their own records
— and department columns and contents pages are typed as stories.
Both are assembly-v2 work (rules 6 and 7 in docs/assembly-notes.md).

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
    ~/shared/khj/.pulp_api_token      read-only data door token

By project decision (2026-08-22), the values of all five are mirrored
in the `secrets/` folder one level above this repository in the
Dropbox share — sharing the Dropbox folder hands over everything, and
that folder's README explains each value and how to refresh the
mirrors. They stay out of git so the repository itself can someday be
published. To rotate or recreate one: a new env
file from the template in `scripts/server_setup.sh`, a new passcode or
data-door token by echoing a value into the file, a new admin with
`scripts/add_user.py`. A person with server access can always read the
current values with `rtx ssh` — nothing is lost if a handover note goes
missing.
