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

Storage on the main server (since 2026-09-02): the root disk is 1.8 TB
and was 96% full when two 3.6 TB disks arrived (/mnt/sda, /mnt/sdb,
ext4, empty, root-owned). Decision: no mirror; project data goes on
/mnt/sda/pulp, and `data/pages` and `data/thumbs` are symbolic links to
/mnt/sda/pulp/pages and /mnt/sda/pulp/thumbs, so the pipeline and the
site keep their paths. Everything the pipeline writes for the full
corpus (page images, layout, text stages) belongs under /mnt/sda/pulp;
/mnt/sdb is unassigned. Docker on the root disk held about a terabyte
of stopped containers and unused images; pruning is done per container
and image, never blindly (the rule below).

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
Events as of v0.13.0 (2026-09-04): set_meta (title, author, type, and
the record facts ad_class, advertiser, excerpt_of, contains_excerpt,
department, part_label, part_n, part_total, illustrator; a type
serial_part from the old dropdown replays as story with the annotator
as the serial's source), set_text, set_frag_order, move_frag (to_id
"new" makes one record per box), new_article (several boxes into ONE
new record: frags, type, title, from_id — used by "Make a NEW record
from them", "Move them to a NEW record" and "Move to non-article"),
frag_furniture (with why = "text in picture" for words read inside a
drawing), edit_frag_text, set_role (title, subtitle, author, teaser,
synopsis, note, chapter_number, chapter_title, section; the workbench
act role_many writes one set_role per chosen card), add_manual, merge,
verify, unverify. The workbench's "Attach pages N to M" writes one
move_frag per box (page furniture left out, at most 30 pages). Machine
roles are seeded into the replay: title/subtitle/author/teaser/note/
synopsis/caption as they are, chapter as chapter_number or
chapter_title by the region's text, heading as section. The title,
subtitle, author and teaser fields hold the words of every box with
that role in reading order (a title set in two boxes is joined; the
same words set twice count once). Log-ins are written to
data/logins.jsonl (ts, user) and shown on the activity page; the
reading text of a record leaves out title, subtitle, author, teaser,
note and synopsis.

Archived logs (v0.12.1, 2026-09-03): the boards count the annotation
logs an assembly switch moved to data/assembly_archive/<stamp>/annotations/
as work done — the progress page's per-annotator table has an "of which
on the archived assembly" column and its "work done" line separates the
live counts; the activity page shades archived rows and names the stamp;
the explorer's events table has an `archive` column (NULL = live) and
issues carry events_archived. Archived article ids name records of the
archived assembly, so they are never linked to live records and a
record's own annotation history shows live events only. A rebuild of
the explorer that fails on a half-written source file (a pipeline stage
still running) keeps the old database and retries.

Routes since v0.14.0 (2026-09-04, built to the protocol as written):
/collection (the collection and the sample: transmission history,
provenance, the sample by decade / genre / magazine / publisher /
language, authors so far), /corpus (the story-level corpus and the
parallel corpus, downloads of data/export/*.jsonl), /datasheet (the
datasheet generated from the survey and the counts) in
webapp/collection_pages.py; /reuse/validate (the paraphrase review:
?view=mine|calibration|design, ?item=vNNN, ?after=vNNN; POST
/reuse/validate with item, set_id, judgment, note — named accounts
only) and /reuse/cases (POST /reuse/case with action add|note|close|
reopen; the "Mark as a case" form on cluster and pair pages) in
webapp/review_pages.py; the reuse overview gained 1b (extensive cases:
whole-story ≥ 80% and high-coverage ≥ 20% of the shorter story, the
proportion of each story involved) and "what each form of reuse
captures"; the pair and story pages place every match among comparable
pairs; the progress page reports the review tools; the guide has
sections 6 and 7; the export writes data/export/stories.jsonl,
paratext.jsonl, corpus_stats.json ("export" is a raw-file root). Logs:
data/reuse/validation/judgments.jsonl and data/reuse/cases.jsonl are
append-only like the annotation logs and travel with the data, not git.

Routes since v0.12.0: /issues is the explorer's paged issue list (built
for the whole corpus, filters by magazine, decade, genre, completeness);
the workroom's old table of the ten pilot issues with their processing
stages lives at /workroom/issues (linked from the guide and from the
explorer's issues page). Author names are stored as printed and shown
in title case everywhere (explore_pages.display_author); the workbench
shows the printed form in fine print next to the name, with the page's
own form when the contents page supplied the name.

Page images: full scans are served at `/img/...` (2–5 MB each), small
cached previews at `/thumb/...` (built on demand into `data/thumbs/`,
pre-buildable with `scripts/make_thumbs.py`). Images are sent with a
one-day browser cache; all HTML is sent uncacheable. Do not remove the
preview layer: loading 150 full scans on one page through the tunnel is
what broke the workbench once (see the journal, 2026-08-22).

Workbench page weight (v0.13.1, 2026-09-04): the page of a record is
about 1.7 MB for the issue (the region map of every scan page) plus
what its cards carry. Until v0.13.0 every card carried a "move to
position" list with one option per body segment and a "belongs in"
list with one option per record, and every button was a form with
five hidden inputs — The Demolished Man (1,095 segments) came to 48
MB and 1.2 million option elements. Now the position list holds its
own number until it is opened, the record list is written once
(`TO_OPTS`) and copied into a box when it is opened, and a button is
`<button class='mb' data-p='{json}'>` posted by one click handler in
WB_JS (`post(params, to)`; `data-sel` names a sibling select whose
value is added; `back` in the fields is where the page goes after the
post, used by "Verify, then next unverified"). The same record is 5 MB
now. Checked in headless Chromium (Playwright) on the sandbox: lists
fill on mousedown, focus and touch; role, body text, move to, verify
and unverify post and reload as before.

## Public access — the tunnel

A Cloudflare tunnel named `cihd-site` runs on the main server in a
tmux session (`tunnel`; after the September 2026 reboot it was started
as `tunnel2` — check `tmux ls`) and carries BOTH `causal.digihumeng.org`
and `pulp.digihumeng.org` (to 127.0.0.1:8092). Two hard rules. Never restart
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
    /api/<token>/get?path=annotations/x.jsonl read one file (64 MB cap)
    /api/<token>/doc/<issue-id>               an issue's full assembled
                                              state as JSON: articles with
                                              fragment keys, unsorted,
                                              furniture, roles, and every
                                              box with its owner
    /api/<token>/index                        what the explorer database
                                              holds and every result file
    /api/<token>/story/<id>  /pair/<a>/<b>    the same JSON the explorer's
    /api/<token>/author/<slug> /issue/<id>    raw pages show (v0.10.0):
    /api/<token>/magazine/<slug>              export record, live article,
    /api/<token>/authors /magazines           matches, alignments, pair
    /api/<token>/stories /pairs               row, annotation events; the
                                              lists take ?page=N&limit=M
    /api/<token>/locate/<story>?text=<words>  where a passage sits in the
                                              article now: region key,
                                              page, surrounding words

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

Two habits (learned 2026-09-04). Send `-A pulp-dev` — a curl with its
default User-Agent is turned away in front of the site, before the
door sees it. A missing path answers 404 with the body "no such
file"; a fetch loop that saves whatever comes back (`curl -o`) turns
that into a 12-byte file (two pilot issues have no annotation log and
got one that way) — use `curl -f`, or check the status, and `ls` first.

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
      for p in $(pgrep -f "app.py --port 8092"); do
        if readlink /proc/$p/exe 2>/dev/null | grep -q python; then kill $p; fi
      done
      sleep 8
      pgrep -af "app.py --port 8092" | grep -v "while true" || echo "SITE-NOT-RUNNING"
      curl -s https://pulp.digihumeng.org/ | grep -o "v[0-9][0-9.]*" | head -1
      EOS

The loop (webapp/serve_pulp.sh) changes into webapp/ and runs
`python3 app.py --port 8092`, so the python process's command line is
`app.py --port 8092`, not `webapp/app.py` — match on that. The
explorer database (data/explorer.sqlite) rebuilds itself when a source
file changes; after a pull that changes explore_pages.py, `python3
webapp/explore_pages.py --build` makes the first request fast.

The printed version must match the new `APP_VERSION`.

If the server has itself run a pipeline stage whose outputs git tracks
(the r-series writes data/reuse), `git checkout -B main origin/main`
refuses with "Your local changes … would be overwritten" and the rest
of the paste runs on the OLD code (2026-09-03). Put the server's copies
aside first and compare them with the commit:

      git stash push -m "server data/reuse before pNN" -- data/reuse
      git checkout -B main origin/main && git log --oneline -1
      if git diff --quiet stash@{0} -- data/reuse; then git stash drop && echo STASH-SAME-DROPPED; else echo STASH-KEPT-DIFFERS; git diff --stat stash@{0} -- data/reuse | tail -3; fi

STASH-SAME-DROPPED means the server's files were already committed from
the Mac (the usual case); STASH-KEPT-DIFFERS means the server holds a
newer run that still needs to travel to the Mac and be committed.

While the Studio is the live server (main server down), the deploy is
a GitHub round trip — the repository is public, so the Studio pulls
over HTTPS with no key. On the Mac: the same add / commit / push as
above, then the Studio update from the Mac through `ssh studio` (set
up once as described in docs/backup-server.md, "Reaching the Studio"):

    ssh studio 'bash -s' <<'EOS'
    cd ~/pulp_backup/pulp_fiction_corpus
    git fetch origin && git checkout -B main origin/main
    python3 -m py_compile webapp/app.py webapp/reuse_pages.py webapp/explore_pages.py && echo COMPILE-OK
    pkill -f "webapp/app.py --port 8092"
    sleep 6
    curl -s http://127.0.0.1:8092/ | grep -o "v[0-9][0-9.]*" | head -1
    EOS

The serve loop (scripts/serve_backup.sh) restarts python within three
seconds of the kill; the last line must print the new version. Files
the pipeline wrote under data/ are untracked and untouched by the
checkout. (Before `ssh studio` existed the same lines were pasted into
a Terminal window on the Studio, after `git remote set-url origin
https://github.com/hkim1596/pulp_fiction_corpus.git` once.)

## Writing terminal pastes for Heejin (learned the hard way)

Heejin pastes whole blocks into zsh, so every line must be safe to
EXECUTE. No bare `#` comment lines (zsh once executed one and its `>`
created junk files that got committed). Instruction lines must be a
colon plus ONE single-quoted string with no apostrophes: `: 'like
this'`. Never put placeholder text in a paste — "PUT-YOUR-PASSCODE-HERE"
once became the actual site passcode; bake real values in or use
`read -p`. For one-line config files prefer `echo value > file` over
nano. Put a quoted STOP line before any step that needs a human choice.
Long runs go into a tmux session whose command ends with `echo
REUSE-DONE; sleep 86400` — the sleep keeps the pane readable for a day
(`tmux capture-pane -pt <name> | grep -v "^$" | tail -2`: a pane with
fewer lines than its height ends in blank lines, so a plain `tail -2`
shows nothing — seen on 2026-09-04); a session that has already
ended is not "still running", so never guard a paste on the pane's
absence (2026-09-03: a guard read a finished run as running). The
result files' own timestamps, reachable through the data door, are the
sure sign that a run finished.

## The pipeline, stage by stage

Each stage is one file in `pipeline/`, runnable per issue or `--all`,
resumable, and writes timing rows to `data/timings.jsonl`. Server tmux
sessions started with `rtx run` do NOT read `~/shared/khj/.pulp_env`;
the stages load it themselves (`timing_util.load_pulp_env`).

    s00_survey      the survey of the archive's collection (2026-09-03):
                    metadata only, through the search API — one record
                    per item of collection:pulpmagazinearchive (28,286
                    items; 19,457 marked English, 2,875 unmarked, 5,954
                    other languages; the working corpus = English or
                    unmarked = 22,332, of which 14,424 fiction magazines)
                    → data/survey/items.jsonl, summary.json, magazines.json.
                    Derived fields: lang_class, kind (fiction magazine /
                    dime novel / film or general magazine / comic magazine),
                    genre (the archive sub-collection's genre in the
                    pilot's vocabulary), year_derived, magazine (name read
                    from the title). The site's boards read summary.json.
                    --enrich (2026-09-04, protocol 2 "history of
                    transmission"): one metadata call per item
                    (archive.org/metadata/<id>/metadata; 6 threads,
                    resumable, one to four hours) → data/survey/enrich.jsonl
                    with uploader, curation, collection_added, ocr,
                    ocr_module_version, ocr_detected_lang(+conf), scanner,
                    rights fields; summary() folds them into a
                    "provenance" section (items added by year, uploader
                    accounts as the part before the @, curators, OCR
                    engines overall and by upload decade, detected language
                    of the unmarked items, scanning-group tags read from
                    the titles, collection_added, rights) and a
                    fiction_by_publisher table from
                    config/publishers_magazines.json (pattern + periods,
                    reference knowledge to be confirmed; 59% of fiction
                    items assigned on 4 September). The site: /collection,
                    /datasheet (webapp/collection_pages.py). The rerun
                    order after a fresh --run is --run then --enrich
                    (enrich ends with summary()).
                    About a minute; rerun any time (--run; --summary
                    recomputes from items.jsonl). Downloads nothing; the
                    downloader's gate is untouched (decision of 2026-09-03).
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
    s08_assemble_rules   assembly v2, the rules engine (2026-09-02;
                    v2.1 on 2026-09-03): contents page, folios, running
                    heads, display title with by-line, chapter heads,
                    teaser, fillers, "continued from" notices →
                    data/assembly_v2/rules/<id>/articles.json (+
                    analysis.json: what the rules saw on every page) and
                    .../rules_on_model/. Never touches data/articles.
                    Seconds per issue. v2.1: the contents page has
                    authority over title and author (the page's forms are
                    kept as title_as_printed / author_as_printed, with
                    title_source / author_source); chapters listed with
                    number and title apart; advertisements classified
                    (ad_class house_next_issue · house_self · house_sibling
                    · house_form · trade · classified, advertiser) with
                    the works a house announcement names (announces) and
                    the excerpt it may quote (contains_excerpt, excerpt_of
                    — such records stay out of the reuse inventory);
                    ballots and coupons are their own record; a plain
                    advertisement page inside a piece suspends the piece;
                    matter under a "continued on" / "[turn page]" notice
                    is advertising; subheadings inside a piece carry the
                    role heading (the site shows it as "section").
                    v2.1.1 (3 September, evening): the fixes the audit
                    (s10) asked for. v2.2 (2026-09-04, the third round of
                    feedback): types house (ad_class house_*) and no
                    serial_part — an instalment is a story with serial
                    {part_label, part_n, part_total, source, prev, next}
                    and work_title / work_id, linked across issues by the
                    cross_issue pass that runs after --all (or alone with
                    --link-only; it also sets contains_excerpt / excerpt_of
                    when a house record's 8-word runs occur in a story of
                    the same magazine); roles note (credits, "Illustrated
                    by" → illustrator, tail notes) and synopsis (the recap
                    on a later instalment → the synopsis field, out of the
                    text); a chapter title is a display line of a series
                    on different pages; titles split over two boxes or the
                    facing page are joined from the contents page (the
                    facing page's first paragraphs come along); "(Poem)"
                    → poem, capitals → title case; an author's name
                    repeated at the end is furniture (or the author of an
                    unsigned column), "The End" and "To be continued" are
                    furniture; departments from config/departments.json
                    (department field, type, conductor; a house head
                    starts a house block whose next display lines are the
                    title); a letters page needs signed letters; column
                    advertisements beside a story's text are cut out by
                    their column (a headline or a priced first line, a
                    price or postal address, no dialogue or narrative);
                    illustration pages and text boxes inside picture boxes
                    are furniture "text inside an illustration".
                    v2.1.2 (2026-09-04): the roles above
                    a story head follow the annotators (a second setting
                    of the title is title, the blurb teaser, a kicker
                    subtitle, a synopsis heading heading); the credit
                    line under the by-line ("Author of …") carries the
                    teaser role and is kept as author_credit (r00 exports
                    it); inside a story a display line that is not a
                    chapter head has no role, heading is for departments;
                    "PART ONE" and "I.—THE MURDER CLUB" are chapter heads;
                    the page range takes in a trailing leaf the scan has
                    out of order (Galaxy 1952-03) and flags the record.
    s09_assembly_eval    the assembly harness: every candidate (live —
                    whatever data/articles holds, the rules' records
                    since the switch —, rules, rules-on-model) against
                    the human-verified records, the contents page, and
                    structural checks → data/assembly_v2/eval.json; the
                    /assembly page of the site shows it. --yardstick
                    <archive> replays the annotations over an archived
                    assembly after a switch.
    s10_assembly_audit   the audit (2026-09-03): every record a person
                    corrected against the live assembly, region by
                    region, each disagreement sorted by cause
                    (furniture, advertisement, split, other piece …) with
                    the machine's and the person's reasons →
                    data/assembly_v2/audit.json and a report.
                    --yardstick <archive> as for s09; --show keeps every
                    disputed region. docs/assembly-accuracy.md is the
                    reading of it.
    scripts/switch_assembly.py   makes a v2 candidate the live assembly,
                    moving the old assembly and the annotation logs to
                    data/assembly_archive/<stamp>/ (nothing deleted;
                    README.txt says how to undo). Run r00 again after.
                    --refresh (2026-09-03): a new run of the same rules
                    replaces the live records and KEEPS the annotation
                    logs — every candidate record takes the id of the
                    live record with the same regions (or the best
                    overlap ≥ 0.5), new records get fresh ids; an issue
                    whose annotated records would change regions is
                    refused unless --force (the replay re-places moved
                    regions by key). The old live file is kept under
                    data/assembly_archive/<stamp>_refresh/. Always
                    --dry-run first; run r00 and rebuild the explorer
                    after. docs/assembly-v2.md has the rules and results.
                    Since 2026-09-04 a VERIFIED record (verify with no
                    unverify after it) is never changed by a refresh:
                    its live machine record is carried over as it is, its
                    regions are taken away from the candidate records
                    (leftovers that the machine would have added to it go
                    to the file's `unsorted` list for a person to pull
                    in), and no flag overrides this — unverify on the
                    site first. Every other annotated record is reported
                    one line each (regions unchanged / REGIONS CHANGE
                    +n −m / kept as verified). --verified-from
                    data/assembly_archive/<stamp>_refresh takes, for a
                    record verified BEFORE that stamp, the copy kept
                    there (what the person saw) instead of the live one —
                    used once, to undo the forced refresh of 4 September
                    (see the journal entry of that day).
    scripts/compare_effective.py   did a machine run change what the
                    annotators see? Compares the effective records
                    (machine record + replayed log) of two states —
                    --before data/assembly_archive/<stamp>_refresh/articles
                    --after data/articles — for every record a person
                    touched, region by region with roles; the check to
                    run after any refresh (verified records must come
                    out "identical"). --early <kept copy> takes, for a
                    record verified before that copy's stamp, the
                    before-state from there (the pair of --verified-from).
                    Pass the copy's articles/ folder; the stamp is read
                    from anywhere in the path (until 4 September 17:30 it
                    was read from the last path part only, so ".../articles"
                    gave no stamp and the two restored records were
                    reported as DIFFERS — the check's mistake, not the
                    records'; a path without a stamp now stops the script,
                    and the first line says which stamp it uses).

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
                        Since 2026-09-04: background.by_stratum in the
                        summary (the survival curve of every full stratum
                        later decade × years band × topic quartile, for
                        exact k6/k7/k8 and para k10) and
                        background/placement_<set>.json — every matched
                        cross-issue pair placed among comparable pairs at
                        two levels (full stratum; topic quartile × band),
                        the pair itself left out; the pair page and the
                        story page read it (explore_pages.placement()).
                        → data/reuse/background/pairs_<set>.csv.gz,
                          summary_<set>.json, placement_<set>.json
    r06_validation      protocol 3.2 "parameter selection and validation"
                        (2026-09-04): --build draws the hand-review set
                        from r04's own candidates (K=20 retrieval + exact
                        seeds, cross-issue only): strata = source
                        (embedding-only / exact-seeded) × alignment score
                        band of the candidate region (below any rule <9,
                        loose rule only 9–15, default rule 16–29, strong
                        30+) × in the two lowest bands the cosine band
                        (closest 5%, next 20%, the rest); 15 per stratum,
                        seed 20260904, weight = pool / drawn; sampled
                        items traced (r04._trace) for cols/identity;
                        → data/reuse/validation/review_set.jsonl (items:
                        windows with 25 words of context, the aligned span,
                        the machine's numbers, stratum, weight; ids vNNN,
                        set_id = stamp) and review_set_stats.json. The site
                        writes judgments.jsonl (ts, user, set_id, item,
                        judgment paraphrase|not|unsure, note; a reader's
                        later judgment of an item replaces the earlier).
                        --calibrate (also live on the site): consensus
                        label per item (every deciding reader agrees;
                        unsure does not count against; disagreement =
                        disputed, excluded), the grid K {5,10,20} × rules
                        {(15,.50),(20,.55),(20,.60),(25,.60),(25,.65),
                        (30,.70)} → precision / recall raw and weighted,
                        pool; chosen = max weighted recall at weighted
                        precision ≥ 0.90, ties to the smaller pool; Cohen's
                        kappa between the two most active readers. Needs
                        the r04 embedding cache (same set) or recomputes
                        it. Pilot: 115 items from 393,630 candidates in 9
                        strata (4 September).
                        → data/reuse/validation/{review_set.jsonl,
                          review_set_stats.json, judgments.jsonl,
                          calibration.json}

Story sets: `machine` (every assembled story, 50+ words), `verified`
(human-verified only) and `corrected` (verified or modified). The
2026-08-31 run used machine and corrected because only one story was
verified; the runs of 2–4 September were machine only (the assembly
switch had archived the corrections and the workbench started clean);
from 4 September evening a corrected set of 40 stories exists again
(12 verified, 28 modified) and the reruns cover both sets.

Each rerun of r04 writes a new embedding cache (data/reuse/para/
emb_<set>_w50s25_<hash>.npz, 38 MB, one per story-set content) and
never deletes the old ones — four lay on the main server on 4
September; the ones whose hash no stats file names any more can be
deleted. Results travel to the repository through the read-only data
door (the section above): a walk over `ls` and `get` fetches every
file under data/reuse but the *.npz caches (119 files, 6 MB on 4
September; `get` returns the bytes as they are, and the `bytes` field
of `ls` checks each one); bundle, extract into the Dropbox repository,
commit from the Mac, then pull on the server with the stash step above
(STASH-SAME-DROPPED confirms the server's files and the commit are the
same bytes).

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

The explorer (webapp/explore_pages.py, v0.11.0) is the service side
of the site: `/overview` (sliceable by decade, genre or magazine —
`?decade=1930&genre=weird&mag=weird-tales` — with stories per year by
genre, the share of story pairs sharing text by decade and genre by
genre, the author reuse ring, the magazine grid, the time axis, the
census, the background curve, and what the explorer covers), lists
(`/authors`, `/magazines`, `/issues`, `/stories`, `/pairs`, one hundred
rows a page with `?page=N`), entity pages (`/author/<key>`,
`/magazine/<slug>`, `/issue/<id>` with provenance from the Internet
Archive item record and the state of every process step, `/story/<id>`
with the teaser and the date with its source, `/pair/<a>/<b>`), and the
raw layer (`/raw/story/<id>`, `/raw/pair/<a>/<b>`, `/raw/author/<slug>`,
`/raw/issue/<id>`, `/raw/magazine/<slug>`, `/raw/index`,
`/raw/file?path=<under data/>`; add `.json` for the file itself; the
raw lists take `?page=N&limit=M`, up to 5000 a page, and carry a `next`
field).

Built for the whole corpus: the pages read from one SQLite file,
`data/explorer.sqlite` (tables issues, records, authors, author_links,
magazines, mag_links, issue_links, matches, aligns, events, pairs,
meta — the data dictionary on `/method` describes them), which the
module rebuilds from the export, the reuse outputs, the pair table, the
annotation logs, the archive metadata and the stage folders whenever
one of them changes (checked every 20 s; the first build blocks the
request, later rebuilds happen in whichever request notices while
others keep reading the old file). By hand: `python3
webapp/explore_pages.py --build`; at corpus scale set
`PULP_EXPLORER_STATIC=1` in the site's environment so the file is only
ever built by hand. The database is derived data: delete it and it is
rebuilt; it is not in git. Author names are shown in title case
(`display_author`; the printed forms are in fine print on the author's
page and in the raw record). Only COMPLETE issues — assembled into
records by the machine (an export proves an assembly) — appear on the
explorer side; the workroom's `/reuse/progress` shows every selected
issue at every step (archive record, page images, layout OCR, text
stages, assembly, export, annotation, verification) against the
archive's 27,973 items. Drawings of individual entities are made only
for slices small enough to read (80 authors, 40 magazines, 120 issues);
above that a table of the top entries stands in. The data door serves
the same records (`/api/<token>/story/<id>` and the other entity paths,
plus `/api/<token>/index`), so an automated reader can read everything
the pages show. `/method` quotes the protocol verbatim step by step
(docs/method-reuse.md) with the implementation after each step, and
generates the parameter table, data dictionary and file list from the
result files.

The workroom's `/assembly` page (v0.11.1) shows the assembly harness's
output — the comparison table and, per issue and candidate, every
contents-page piece and every human-corrected record with its score —
from data/assembly_v2/eval.json. The site reads the machine's own
region roles from a v2 assembly (title, subtitle, author, teaser), so a
v2 record opens on the workbench with those sections filled; human
role actions still override them. Raw files under data/assembly_v2 are
reachable through /raw/file.

Feedback (v0.11.0): the box at the foot of every page is prefilled with
the member's name, and sending keeps the reader on the page. On
`/feedback` an admin sees every entry with edit and done/reopen
controls; a member sees and can edit only their own entries; every edit
keeps the earlier text in the entry's `history`. Marking a by-line
segment as author strips a leading "By"; the teaser role keeps a
story's printed blurb as metadata (never story text) and r00 exports
it with the record, together with `date` and `date_source` ("issue"
until other evidence is recorded).

The reuse pages (webapp/reuse_pages.py, v0.9.0): `/reuse` overview
with server-drawn SVG charts (no libraries), `/reuse/clusters` with
filters, `/reuse/cluster/<set>/<kind>/<k>/<n>` with witnesses grouped
by story and located live in the current article text ("open on
workbench" is the article page with `?sel=<region key>`, which
highlights that region), `/reuse/progress` with annotation progress,
the pipeline status board and the process board (every issue at every
step, from explore_pages.process_board_html). Member login required,
like the rest of the site.

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
