# Development journal — the prompt thread

This is the working history of the project: what Heejin asked for, in
his words, what was built or decided in response, what broke, and how
it was fixed — in order. It was written so that anyone who receives
this folder (a collaborator, a new developer, or a fresh Claude
session) inherits the full context of the conversations that produced
the code. It is a faithful reconstruction from the session records, not
a verbatim chat export; the live chat threads remain in Heejin's Claude
account and can be continued there.

## 2026-08-17 — framing

Heejin: clean up pulp fiction texts from the Internet Archive (stage
one) and publish the corpus-building work in Computational Humanities
Research as a Registered Report; later, detect reuse of pulp fiction in
contemporary fiction (stage two). Dennis Yi Tenen (Columbia) will write
the Stage 1 protocol. Heejin pasted CHR's Registered Report
instructions with the instruction to "read carefully and memorize."
The rule that shapes everything after: no study data may be collected
or analyzed before the protocol is accepted, except a declared
development set. Decision: a ten-issue pilot is that development set,
named in the config with an explicit approval gate in the downloader.
Survey of IA holdings: ~28,075 pulp items, roughly 1896–1959. Design
documents written (`pulp-corpus-design-v1.md`, `pulp-pilot-spec-v1.md`,
outline for Dennis).

## 2026-08-19 — pilot plan and the website

Heejin: process ten issues end to end; layout detection first, then
OCR; mixture of rule-based and LLM cleanup; measure the time; look at
the American Stories method; share everything through a website on the
lab server, passcode-protected, like his other project (GitHub → server
clone, Cloudflare tunnel, landing attached to the center's site);
display the cleaning method on the site for collaborator feedback;
"Before creating website ask me many details." Decisions from his
answers: both OCR routes compared (route A layout+regions via Surya,
route B vision model, deferred); both cleanup models compared (local
qwen lane, Claude API); the ten issues mix magazines and eras and
include Project Gutenberg gold overlap; the site is the project's main
sharing surface, passcode 3333 for guests.

## 2026-08-20 — repo, server, tunnel, and the paste lessons

Repository created (after a rename: `pulp-fiction` deleted,
`pulp_fiction_corpus` created). Site v0.1 deployed: dependency-free
stdlib server, landing + issues + method + timing + feedback pages.
Tunnel hostname `pulp.digihumeng.org` added to the existing `cihd-site`
tunnel. Heejin approved the ten pilot issues in chat; the approval
(name and date) is recorded in `config/pilot_issues.json`; s01 download
ran: 1,434 pages, ten issues, plus gold texts.

Four operating lessons were learned from real incidents this day, now
permanent rules (handbook, "writing terminal pastes"): a bare `#`
comment line executed and its `>` redirect created junk files that got
committed; an unquoted instruction line aborted a whole paste with a
parse error; the literal placeholder "PUT-YOUR-PASSCODE-HERE" became
the site's actual passcode; a nano instruction put the passcode into a
file NAME. Also: Ctrl-C sent into the tunnel tmux session killed both
public sites (error 1033) — the tunnel session is command-form and must
be killed and recreated, never interrupted.

The server disk filled to 100% for the first time (docker holds
~920 GB; other lab members' containers dominate). Cleanup guidance
written; a lab-level conversation about the fastest-growing container
was flagged for Heejin.

## 2026-08-21 — pipeline buildout and the qwen incident

s02 adapted to Surya 0.22 (it now launches its own vLLM docker on GPU 0
and returns a new "blocks" schema; the adapter handles both schemas; a
completeness guard stops it starting on a half-downloaded issue). s01b
written when the Archive's plain text turned out to have no page
breaks (pages come from its positional OCR instead). s04 rules built
and hardened against three synthetic-test failures (running-head key,
weak garbage filter, detached punctuation). s05 dual-backend cleanup
written with a similarity guard and per-page cost/time logging.

The qwen lane crashed every stage that used it: the model runs
"thinking" mode by default and returns empty message content. Fix,
now standard for every call to that lane: send
`chat_template_kwargs {"enable_thinking": false}`, strip any
`<think>` block, treat empty replies as retryable, and degrade per
page (keep the input text, flag it) instead of dying — abort only
after ~20 consecutive failures. Heejin asked whether stages could run
simultaneously instead of waiting for each other; they were split into
parallel tmux sessions, which also meant the qwen lane's timing was
measured under contention (noted in the results). The disk filled a
second time mid-run; recovery scripts rerun every remaining stage
idempotently.

## 2026-08-22 (day) — the final form, and the workbench in seven steps

Heejin, on the corpus's final form: every story or article accessible
by its title or author; everything segmented, "even advertisement as a
separate article if possible"; ornamental titles recorded exactly as
printed; page numbers and running heads kept as metadata; nothing
dropped. s07 assembly was built to that specification (units with
verbatim titles/authors, furniture, unsorted, provenance to scan
regions), and the site gained the article database.

Heejin, on assembly quality: "assembling process is not generally
accurate… sections within a story are regarded as separate stories and
the order of stories got wrong, instructions like 'continued from xxx
page' included as part of story text… let the website show how each
separate ocr'd text is assembled for each story and a user can reorder
them… website flags them as verified and modified, also a login system
… database records who verifies and modifies." The annotation layer
was built as append-only event sourcing (machine output never edited;
every action one logged line with username; replayed on view). Then,
over seven feedback rounds the same day, the workbench became: public
signup with admin approval; a visual two-column workbench (scan with
labeled boxes left, segment cards right); a unique id for EVERY box
including page numbers ("Every box including tiny ones with just page
numbers should have a unique id"); both columns independently
scrollable; title/subtitle/author role buttons and manual text
segments; position dropdowns as well as dragging; three color-coded
sections with bars (title burgundy, author green, body black); and
finally the whole issue's pages in the left column, auto-scrolled to
the story's first page, with a click-to-include panel for any box on
any page ("to 'include' missing segment, we need whole issue with
their unique id segments… live inclusion function").

## 2026-08-22 (evening) — three infrastructure finds

First: the site had been silently dead for some time — the deploy paste
discovered no site process at all and rebuilt it with a self-restarting
wrapper (that wrapper is now the standard, handbook "site process").
Second: the new whole-issue workbench showed broken images for every
page. Diagnosis chain proved serving was perfect end to end; the real
cause was scale — 148 full scans of 2–5 MB each through the tunnel,
with caching disabled site-wide. Fix: small cached page previews
(`/thumb/…`, built on demand, pre-buildable), browser caching enabled
for images only, "full size" link per page. Third: the measurement
stage hung silently for over ten minutes — the stdlib text comparer is
quadratic and a full issue is ~350,000 characters (hours to days), and
the output was block-buffered through the pipe on top. Rewritten with
rapidfuzz (exact edit distance, ~5 s per full-issue comparison) with
progress lines; then a second fix taught the story locator to find a
story from probes inside it (the Gutenberg files open with added
material, so first-line anchors failed), and added error composition
(misread characters versus missing/extra content).

## 2026-08-22 (night) — the pilot read

Full results in `docs/pilot-results.md`. Headline: the layout route
cuts word errors to a third to a half of the Archive's text
(16.7→6.6%, 13.7→5.3%, 14.8→4.2% on the three gold stories); the
apparent 18% full-issue error floor is ~99% coverage mismatch, not
misreading (true misread rate ~0.2–0.3%); LLM cleanup gains almost
nothing and each backend hurt once (qwen worsened one issue; Claude
silently shortened a novella by ~2,300 characters). Cost figures are
logged per page and not yet extracted; the command exists in the chat
record and can be rerun any time:
sum the numeric fields of `data/text/*/llm_*_routeA/meta.jsonl`.

Heejin's direction after reading: "the main issue here seems not
cleaning up characters but assembly of stories." Plan set: annotators
manually correct stories (adding and removing segments) on the
workbench; the human-corrected stories then become the reference for
diagnosing and fixing the automatic assembly. Before that: make the
website self-explanatory for a first-time visitor (read the site's
collected feedback, then a guidance overhaul); stand up a backup web
server on a Mac Studio for a planned week-long main-server outage
(`docs/backup-server.md`); and make this Dropbox folder carry the
complete project — code, documentation, results, and this journal —
so anyone it is shared with can continue the work without missing
anything.

## 2026-08-22 (late) — the feedback round and site v0.8.0

The site's feedback box did its job on day one. Sujin Kang hand-repaired
stories in Astounding 1930-01 and reported, with page-level precision,
that all three long stories split at every chapter heading (one had
mid-story boxes assigned to nothing at all), that the machine prefers
ornamental garble over the printed title and by-line, and that the
contents page — the issue's own ground truth — was being treated as just
another article. Heejin's own notes named the interaction pains: losing
your place after every action, no way to select several boxes at once,
no color feedback on inclusion. Response in two parts. Site v0.8.0: the
page now remembers scroll position through every action; boxes
multi-select with one confirm; a range claim takes a box and everything
after it through page N; pickers are labeled and readable (id, page
range, title); whole records merge in both directions; chapter no. /
chapter title one-click roles (convention: chapter apparatus stays in
the text, tagged — Sujin's option c, adopted); a rewritten workbench
help box, a friendlier landing, and a new guide page with a
first-repair walkthrough. And `docs/assembly-notes.md`: Sujin's six
assembly rules, recorded as the specification for the next s07 —
verified human-repaired articles become its test set.

## How to keep this journal alive

Add an entry whenever a work session changes the code, the data, or a
decision: date, what was asked (quote the request), what was done,
what broke. One paragraph is enough. If a Claude session did the work,
ask it to append its own entry before it finishes.
