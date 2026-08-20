# HANDBOOK — Pulp Fiction Corpus
Started 2026-08-20. The one document a new session reads first for this project.
Companion documents: `README.md` (what the code does), `METHOD.md` (the method,
rendered on the site), `../pulp-corpus-design-v1.md` and `../pulp-pilot-spec-v1.md`
(the agreed designs). Keep this handbook and Cowork project memory current when
anything structural changes.

## 1 · Division of labor (same doctrine as the causal project)

- The MAC is the control desk only: this repo clone (inside the Dropbox
  `Pulp Fiction/` folder), the docs, nothing heavy. Heejin pushes to GitHub with
  the `rtx` tool and runs pastes.
- The GPU SERVER (rtx6000, `ssh rtx6000`, user tailab) does all work: downloads
  from the Internet Archive, OCR, cleanup, metrics, and the website. Project home:
  `~/shared/khj/pulp_fiction_corpus/`.
- GITHUB holds the code privately: `github.com/hkim1596/pulp_fiction_corpus`
  (an earlier repo named pulp-fiction was created and deleted on 2026-08-20;
  rtx would support a repo name different from the folder name, but matching
  names are simpler), deployed with `rtx new / push / clone / update`
  (per-project deploy key — see `rtx6000-tools/README.md` in the Causal
  Inference folder).
- THE WEBSITE holds and shows all pilot data and the method, and is the main
  channel to collaborators. Public URL: https://pulp.digihumeng.org
  (landing open and capability-only; everything else passcode-gated).
  Data files never travel through git (`data/` is gitignored); the server
  downloads directly from the Internet Archive.

## 2 · The live system (server side)

- Site: `webapp/app.py`, dependency-free, on 127.0.0.1:8092, run by
  `webapp/serve_pulp.sh` in tmux session `pulpsite` (auto-restarts). `/healthz`
  answers open; footer shows APP_VERSION — bump it for every shipped change;
  the footer is how deployment is verified from outside.
- Tunnel: add to the EXISTING Cloudflare tunnel config `~/.cloudflared/config.yml`,
  above the causal entries:
    - hostname: pulp.digihumeng.org
      service: http://127.0.0.1:8092
  then restart the named tunnel in tmux session `tunnel` (the tunnel does not
  hot-reload), and add the DNS route: `cloudflared tunnel route dns cihd-site
  pulp.digihumeng.org` (one-time).
- Access control (own files, separate from the causal site):
  `~/shared/khj/.pulp_site_password` — plain text passcode, read on EVERY
  request; create/change/remove takes effect instantly. File absent = site fully
  open. Cookie `pfauth`, HMAC-signed with `~/shared/khj/.pulp_webapp_secret`
  (auto-created on first run), 45 days, HttpOnly. Delete the secret + restart to
  force global logout.
- LLM lanes: reuse the causal project's vLLM endpoints for Qwen cleanup (low
  concurrency — do not crowd the newspaper extraction daemon). Route B needs a
  vision model lane (e.g., olmOCR or a Qwen-VL) — coordinate GPU use with the
  extraction schedule; the pilot's volume is ~1,300 pages, one evening of light
  GPU work. Claude cleanup needs ANTHROPIC_API_KEY in the server environment
  (put it in `~/shared/khj/.pulp_env`, sourced by scripts; never in git).

## 3 · Deploy pattern (how every change ships)

Numbered updates, per-project numbering: `scripts/update_pN.sh`, reports to
`_server_check/report_pN.txt` (in the repo folder on the server, gitignored),
pulled back to the Mac with `rtx sync pull _server_check`. No report file = the
paste was never run. Recipe for a change:
1. Edit code in the cloud session; `python3 -m py_compile` AND run a live test
   server + curl every affected endpoint (py_compile does not catch NameErrors).
2. Commit files to the Mac clone; Heejin runs `rtx push` then `rtx update`.
3. `scripts/update_pN.sh` restarts `pulpsite` tmux, greps the change on
   localhost, writes the report. Heejin runs it via
   `rtx run update_pN -- bash scripts/update_pN.sh`.
4. Every paste ends with the git block (add/commit/push from the Mac).

## 4 · Site conventions (Heejin's standing rules, carried over)

- No bold text anywhere on the site (no b/strong tags).
- Every page: a "HOW TO READ THIS PAGE" box in plain English; every count
  clickable down to the records behind it; empty states explained with live
  numbers (the site goes live before its data, on purpose).
- Text shown verbatim with OCR errors, anchored to the scan (page + box).
- Landing page: capability-only — no method detail public until Heejin decides
  otherwise. Access requests by email: hkim1596@knu.ac.kr.
- Plain English everywhere; no metaphor; no jargon.

## 5 · Registered Report constraints (governs all data work)

The CHR protocol (Stage 1) must be accepted before STUDY data is collected or
analyzed. Therefore:
- The 10 pilot issues in `config/pilot_issues.json` are the DEVELOPMENT SET:
  they will be named in the protocol and excluded from all study analyses.
  They exist so we can build tools and measure speed/cost for the protocol's
  feasibility and timeline sections.
- The full-corpus download, the study evaluations, and everything reported as a
  registered result happen only after protocol acceptance.
- `s01_download.py` refuses to run until `"approved": true` is set in the
  config by Heejin — the approval flag is our own audit trail.

## 6 · State (2026-08-20)

Repo scaffolded in the cloud session; webapp tested locally with demo data;
nothing yet on GitHub or the server; nothing downloaded from the Internet
Archive. Next: Heejin approves the 10-issue list → `rtx new/push/clone` →
`scripts/server_setup.sh` → run pipeline stages in order → tunnel + passcode →
share URL with collaborators.
