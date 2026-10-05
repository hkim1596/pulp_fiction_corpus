# The corpus build log

A dated record of how the corpus was built — every run, every count, every version, every decision — kept so
that the database can be published (Zenodo) and described in a data paper for the Journal of Open Humanities Data
(openhumanitiesdata.metajnl.com: 1,000–1,500 words; sections Overview — repository location, context; Method —
steps, sampling strategy, quality control, constraints; Dataset description — object name, format names and
versions, creation dates, dataset creators, language, license, repository name, publication date; Reuse
potential). Heejin, 4 October 2026: "Just keep log of everything, so we can publish our database."

Where the machine-written record is (all on the server under `data/corpus/` unless said otherwise):

| file | what it holds |
|---|---|
| `data/survey/items.jsonl`, `enrich.jsonl` | every item of the archive's collection (28,411 on 4 October 2026) with the derived fields; the per-item metadata record (page count, OCR engine, detected language) |
| `data/survey/selection.jsonl`, `selection_counts.json`, `duplicates.json` | every item's decision and reason under the selection rule; the counts per clause; the duplicate groups |
| `config/corpus_issues.json` (archived as `pilot_export/p50_corpus_issues.json` in the Dropbox folder) | the issue list: id, archive identifier, magazine, cover date, year, genre, undated flag, page count in the record, alternates |
| `config/corpus_approval.json` (git) | the approval: who, when, the list's SHA-256 fingerprint, the counts, the settings |
| `config/corpus_settings.json` (git) | every setting the stages used, versioned |
| `run_info.jsonl` | one record per start of the orchestrator: host, git commit, Python, the versions of surya-ocr, spaCy, the model, Pillow, torch; the settings; the approval |
| `events.jsonl` | the chronological log: every stage finished or failed for every issue (with sizes, pages, seconds, errors), every deletion and why, every run start |
| `state/<id>.json` | the same facts per issue: for the download the archive's file names, sizes and md5s, the archive's upload and change dates and OCR engine; for the imaging the page count, the master's pixel size, the nominal dpi; for the reading the pages and seconds; for the lemmas the token count; for the assembly the record counts |
| `fetch_manifest.jsonl` | every file fetched from the archive (role, name, bytes, md5) and every issue event of the downloader |
| `data/timings.jsonl` | one line per stage per issue: seconds and pages (the site's /timing page reads it) |
| `data/raw/<id>/meta.json` | the archive's full item record as fetched (files, md5s, dates, uploader's metadata) |
| `progress.json` | the running counts (rewritten every minute) |
| `run.log` | the console output of the orchestrator (tee) |

Code: github.com/hkim1596/pulp_fiction_corpus (public); the build runs from the commit recorded in `run_info.jsonl`.

## Entries

### 2026-10-04 — the selection rule and its counts (sandbox, survey of 4 October, no enrich records)

Source: the Internet Archive collection `pulpmagazinearchive`, surveyed with `pipeline/s00_survey.py --run`
(the collection's item list through the archive's search API; derived fields: language class, kind, genre,
year, magazine name). Items: 28,411.

The rule (`pipeline/s00b_select.py`, clauses in order; an item is set aside at the first clause it fails):
1. language: marked English, or no language record — 22,448 (set aside 5,963);
   1b. the archive's text detected as not English (from the enrich record; none in the sandbox) — 0;
2. dated: a cover year from the date field, else the year field, else the title — 21,249 dated; 1,199 undated
   are KEPT and flagged (out of the dated analyses);
3. window 1890–1955 — 11,513 (set aside 9,736);
4. kind: fiction magazine — 8,538 (set aside: comic magazine 337, dime novel 2,387, general-interest magazine
   277 [subcollections libertymagazine, colliersmagazine, mccallsmagazine, mccluresmagazine], non-fiction
   magazine 1,173);
5. duplicates: one record per issue — 7,467 issues; 1,071 items are duplicate scans, kept as "alternates" of
   the item with more pages (then the better archive text, then the earlier upload) and never downloaded.
   Two items of one magazine are one issue when both carry a volume and number and they agree; or both carry a
   whole number (#24) and it and the year agree; or the cover month agrees and is a real month (year-only records
   never merge) and, where either carries a day, both do and agree; a month alone merges only magazines that are
   not more than monthly (fewer than 15 distinct issues in every year). The cover month is read from the title
   first, then from the archive's date field; a date field of January 1st counts as "year only" (258 of the
   window's 01-01 dates stood under a title naming another month or season; 4,885 of 6,200 dated items carry
   day 01). Undated among the 7,467: 604. Magazines (one key for spellings that differ only in "The" or
   punctuation): 1,605. Page counts known for 2,701 issues (median 133); estimated pages 980,000.

Known limits of the rule, recorded: the British and the American edition of a magazine with the same whole
numbers merge under one magazine key (Zane Grey's Western Magazine, British Edition); an undated item is never
merged; `format` (pulp or digest) is "unknown" for every issue because it cannot be read from the archive's
records — the imaging step records the master's pixel size and nominal dpi for a later pass. The meeting deck of
23 September 2026 counted 7,897 issues and 914 duplicates under the first rule (same magazine and cover month).

Decided 4 October: the 604 undated items stay in the list (the protocol keeps them out of the dated analyses
only) but are placed last in the download order; many are fan magazines of unknown date (collection
pulp_misc_horror, uploads of 2017), to be judged later.

### 2026-10-04 — the downloader and the imaging (sandbox tests)

`pipeline/s01c_fetch.py`: four items at once (the archive's guidance for bots), per item the metadata record,
`_djvu.txt`, `_djvu.xml`, `scandata.xml` and the `_jp2.zip` master (a PDF only when the item has no JP2 archive);
resumable (HTTP Range); size and md5 checked against the item record; 429/503 honoured with Retry-After; six
retries with growing waits; 1 s between files, 2 s between items per worker; User-Agent
"pulp_fiction_corpus/1.0 (text-reuse study, KNU Digital Humanities Engineering Center; contact
hkim1596@knu.ac.kr)". Masters kept zipped. Working images: JPEG, 2,200 px high (never upscaled), quality 90;
thumbnails 300 px, quality 80. Measured: one item of 32 leaves, 16.6 MB, 21 s with the pauses; the transfer alone
15 MB/s on a 37 MB file; imaging 0.36 s a page for 1,744 px masters, 1.2 s a page for 3,269 px masters.

`pipeline/s04b_lemma.py`: spaCy 3.8.16, en_core_web_sm 3.8.0 (pinned and checked at start), parser and NER off;
output per issue: text, lemma, part of speech, character offset for every token of the cleaned pages. 44,700
tokens in 4 s. The rule lemmatizer is consistent, not always right ("riding" → "rid"); both sides of every
comparison get the same treatment.

`pipeline/run_corpus.py`: download → image → read (Surya 0.22, one worker per GPU) → clean (s04 rules) →
lemma → assemble (s08 rules v2.3) → cross-issue links per magazine; the JP2 master deleted once its issue is
assembled; working images of the oldest assembled issues deleted when free space falls under 400 GB. A
two-issue trial of download and imaging through the orchestrator ran in 36 s; a restart found nothing to do.

### 2026-10-04 — the green light

Heejin: "I got the green light to go. Don't worry about the protocol anymore. Just keep log of everything, so
we can publish our database and write in https://openhumanitiesdata.metajnl.com". The run starts with
`pilot_export/p50_pastes.txt` (Dropbox). Every paste's output is appended here, with the date.

### 2026-10-04 (evening, KST) — the code on the server; the machine; the selection on the server

PASTE 1 (Mac): commit 2724719 "p50: the corpus stages …" pushed to github.com/hkim1596/pulp_fiction_corpus and
pulled on the server; the site restarted as v0.16.2.

PASTE 2 (server): 64 cores; 503 GB of memory (469 available); disks: root 1.8 TB with 445 GB free, /mnt/sda
3.6 TB with 2.7 TB free (the project's data), /mnt/sdb 3.6 TB with 3.3 TB free but not writable by our
account (so the masters stay on /mnt/sda). GPUs: four NVIDIA RTX PRO 6000 Blackwell Max-Q (97,887 MiB each);
memory in use: GPU 0 90,137 MiB, GPU 1 89,423 MiB, GPU 2 575 MiB, GPU 3 89,061 MiB — GPU 2 free for the
reading. Installed: spaCy 3.8.16, en_core_web_sm 3.8.0 (self-test ok: pipes tok2vec, tagger, attribute_ruler,
lemmatizer), internetarchive; Pillow 10.4.0 with JPEG 2000; pdftoppm present. Survey of the day: 28,410 items
(19,486 marked English, 2,961 unmarked, 5,963 other languages; 14,499 fiction magazines of all years with
822,692 page images in their records, 2,430 magazine names); enrich: 28,286 records from September kept, 164
fetched. Selection (17:18 KST): 28,410 → 22,447 English or unmarked (123 more set aside because the archive's
own text was detected as not English) → 21,214 dated + 1,110 undated kept → 11,501 in 1890–1955 → 8,511
fiction magazines (comic 263, dime novel 2,387, general-interest 277, non-fiction 1,173 set aside) → 7,440
issues; 1,071 duplicate scans as alternates; 589 undated; 1,582 magazines. md5 of the list:
10bc79121c3405d6b1257d405d8e0f6a. The differences from the sandbox (7,467) come from the enrich records
(detected language, page counts) and one item fewer in the collection.

Settings for the run (p50b, p50c): 12 imaging and 12 lemma processes; the reading server a vLLM container
started with surya's own command (VLLM_GPU_TYPE h100: 104 sequences, 16,384 batched tokens, 85% of the card's
memory) on port 8020 on GPU 0 — the other project gave up GPU 0 later the same evening, and Heejin decided
"Use only GPU 0. No GPU 2."; a second server on GPU 3 (port 8022) only if he frees it. The CHECK of 17:55 KST:
GPU 0 1,651 MiB used (a small process of the stylometry project), GPU 1 89,423 MiB (vllm-qwen3-14b, port 8004),
GPU 2 575 MiB, GPU 3 89,061 MiB at 100% (vllm-9b-gpu3, port 8006). Expected at the pilot's speed of 0.92 s a
page on one card (1,286 pages in 1,183 s on 20 August 2026, 24 GB batch settings): about 980,000 pages → ten
days on one card; the larger batch settings should shorten that. The download (about 0.7 TB) takes about a
day; imaging keeps pace. A reading worker whose server stops answering waits and marks nothing failed.

### 2026-10-04, 17:56 KST — the list approved

PASTE 3 (Mac): config/corpus_issues.json and selection_counts.json copied from the server;
`s00b_select.py --approve "Heejin Kim"` wrote config/corpus_approval.json: 7,440 issues, fingerprint
a8a60adbbe26…; commit af5562a pushed and pulled on the server. The approved list is archived as
pilot_export/p50_corpus_issues.json (Dropbox) with p50_selection_counts.json.

### 2026-10-04, 18:02–18:05 KST — the first twenty issues downloaded and imaged (PASTE 4)

Commit fe29714 (p50c: the reading on GPU 0 only) on the server. `s01c_fetch.py --run --limit 20`: twenty
issues downloaded (0 failed) and imaged in about two minutes end to end, four downloads at a time at 15–60 s
per issue (28–86 MB each; the archive's datanodes differ in speed), the imaging in parallel at 0.4–0.6 s a page
per process (68 pages in 27–57 s, 148 pages in 78 s, 158 pages in 67 s). Masters on disk: 1.3 GB for the
twenty — about 65 MB per issue, 0.7 MB per page, which puts the whole corpus's masters near 0.5 TB. The state
record of the first issue (10 Short Novels Magazine v01 n01 [1938-10], archive item
10-short-novels-magazine-v-01-n-01-1938-10, uploaded 2021-10-07, archive OCR tesseract 5.0.0-beta-20210815)
holds the four files' names, sizes and md5s, 148 leaves, master 1920 × 2980 px, scandata ppi 600 → a nominal
3.2 × 4.97 in, which is not the magazine's size: the dpi values in the archive's records are unreliable (another
issue claims 20 × 27.6 in), as expected; the format question (pulp or digest) will need a measurement of the
page images against the printed text size, later. /mnt/sda: 751 GB used, 2.7 TB free.

### 2026-10-04, 18:11–18:21 KST — the reading server on GPU 0; the first issue read (PASTE 4b)

The first try stopped itself: GPU 0 held 11,057 MiB (1,628 MiB of the stylometry process and 9,406 MiB of
another python process, PID 448434), over the paste's 10 GB limit. The paste was changed to allow a neighbour of
up to 20 GB and to give the server what is free less a 6 GB margin: a share of 0.83 of the card. The pilot's
stale server record (port 56169, container gone) was removed. surya-ocr on the server is 0.22.1; its vLLM image
vllm/vllm-openai:v0.20.1 was not on the machine and was pulled (the pilot's run of August used an earlier
image); the server (container surya-vllm-8020, model datalab-to/surya-ocr-2, 104 sequences, 16,384 batched
tokens, max model length 18,000) answered after 230 s including the pull; GPU 0 then at 94,282 MiB.

The first issue of the list, '47 — The Magazine of the Year, March 1947 (158 pages, archive item
47themagazineoftheyearv01n01194703bones): 158 pages read in 164.9 s — 1.04 s a page, the pilot's speed
(0.92) despite the larger batches, with the client process itself busy for 118.6 s of CPU (image loading and
encoding, result parsing): the client, not the card, looks like the limit, so the run starts with two reading
workers on the one server. Page 3 reads cleanly ("A Statement of Intention … IN THE first issue of a new
magazine it is customary to proclaim one's special identity. '47 is the only national magazine owned and
controlled by people who write, paint, and photograph professionally…"). Working images measured: 100 MB for
100 pages, 123 MB for 148, 59 MB for 68 — about 0.9 MB a page at 2,200 px and JPEG quality 90, twice the
estimate; the corpus's working images will come to about 0.9 TB, the masters about 0.5–0.7 TB: a peak near
1.5 TB on the 2.7 TB free, and the reaper's floor of 400 GB stands.

### 2026-10-04, 18:38 KST — the run started (PASTE 5)

`run_corpus.py --run` in tmux session `corpus` with PULP_SURYA_SERVERS naming the GPU 0 server twice (two reading
workers on one server), code fe29714, 7,440 issues. The first two minutes of the pane: downloads of 10-Story
Detective issues (114–116 leaves, 69–88 MB) at 16–53 s each, four at a time; one HTTP 500 from the archive on a
JP2 zip, answered by the downloader with a 30 s wait and a retry, as designed. The console goes to
data/corpus/run.log; the per-minute counts to data/corpus/progress.json; every stage event to events.jsonl.

### 2026-10-04, 18:50 KST — the first STATUS; the run stopped by itself at about 18:52

STATUS at 18:50:20 (twelve minutes in): downloaded 81, imaged 76, read 9, cleaned 9, lemmatized 9,
assembled 9, masters removed 9; pages imaged 8,617, read 782; rates since the start: downloads 304 an hour
(24 hours left), imaging 279 an hour, reading 44.8 an hour with two workers on the one server — against the 26
an hour one worker would give, so the second worker nearly doubles the rate; 166 hours (seven days) of reading
left at that rate; GPU 0 at 86% utilisation (so a third worker would gain little); free space 2,919 GB; no
issue given up. The nine assembled issues had their masters deleted as designed.

Then the pane showed the last download line at 18:52:20 followed by CORPUS-DONE: the orchestrator process had
ended without a word — no "nothing left to do", no traceback in the pane — with 7,355 issues still in the
download queue, 65 in the reading queue, and progress.json last written at 18:50:20 (the main loop had not
ticked for two minutes before the end). The reading server stayed up. Cause under investigation (PASTE 7b reads
the console log, the pane's scrollback, the kernel log and the last events, then restarts the run; nothing is
done twice). Change made at once (p50d): the orchestrator now writes its own log (data/corpus/orchestrator.log)
and a crash note (run_crash.log; run_faults.log for a crash of the interpreter itself) that do not depend on the
console pipe, and logs its own end.

### 2026-10-04, 20:05 KST — the cause of the stop; the restart (PASTE 7a, 7b)

The console log held the traceback the pane had scrolled past: `run_corpus.py` line 445, `log("run", f"reading
failed {iid}: {err[:300]}")`, TypeError on None — a reading result had arrived for an issue the resume scan had
already queued for post-processing (the worker marks the state file before it reports), the code fell into the
failure branch with no error text, and the orchestrator died at 18:51:10. Fixed (p50e): a result for an
already-queued issue is simply skipped. Also in the log: two issues whose imaging failed with "broken data
stream when reading image file" (a_fighting_man_of_mars_1931, a_town_is_drowning_1955) — a JP2 leaf Pillow
cannot decode; from p50e such a leaf becomes a blank page recorded in the state (bad_leaves) and
`s01c_fetch.py --repair` re-renders it from the archive's PDF (the PDF holds the leaves marked
addToAccessFormats in scandata.xml, in order). Python on the server is 3.10.12; no memory pressure (45 GB of
503 used); no kernel kill. Restarted at 20:05 with the p50d code (crash notes to a file) — the race could
recur until p50e is on the server (PASTE 7c, 7d). Progress at the restart: downloaded 89, imaged 83, read 12,
assembled 12.

### 2026-10-04, evening — the box-linking stage designed and built (s12_llm_link)

Heejin: a language model is to decide, box by box after the layout detection, what continues what; the local
model first, the Claude API (Fable or Opus) when it is unsure, a person when it is still unsure; GPU 2 for the
local lane; a trial first. Built in the sandbox with a self-test (prompt, answer parsing, record building,
comparison with the rules): docs/corpus-run.md, "The box-linking stage". Model ids and prices from
platform.claude.com on 4 October 2026: claude-opus-5-5 $4/$20, claude-fable-5-1 $10/$50, claude-sonnet-5-5
$2/$10 per million tokens. PASTE 8a reads the lab's own lane configuration (image, model, mounts) so the GPU 2
lane uses a model already on the machine.

### 2026-10-04, 20:30 KST — the fix on the server (p50e, commit dd8c5bb); a STOP that was not noticed

PASTE 7c put p50e on the server. PASTE 7d touched the STOP file and waited 25 minutes: the orchestrator (p50d
code) did not end. The cause is in the same main loop: progress() ran seven `du -sb` calls a minute over the
growing data tree, each allowed 600 s; on this disk they took minutes (the 0.0 directory sizes in the first
STATUS were their timeouts), so the loop stalled between ticks and the STOP file went unread. Fixed (p50f):
the sizes are measured by a background thread every half hour at the lowest disk priority (`nice`, `ionice`),
never in the loop. PASTE 7g ends the stuck process (tmux session, then the orchestrator and its reading
subprocesses by name) and restarts on p50f; an issue mid-reading is read again from the start.

The Anthropic API key: saved since 20 August 2026 in the Dropbox folder, secrets/pulp_env.txt (a mirror of the
server's ~/shared/khj/.pulp_env; the key begins sk-ant-a…), with PULP_CLAUDE_MODEL=claude-h… (Haiku, the pilot's
choice for s05). The box-linking stage reads ANTHROPIC_API_KEY from the same file; its model is set in
config/corpus_settings.json (llm_link.escalate.model), not by PULP_CLAUDE_MODEL.

### 2026-10-04, 21:03 KST — the restart on p50f; the reading had not moved for an hour

PASTE 7f (commit 260c781) and 7g: the stuck orchestrator ended by name, two failure marks cleared, the run
restarted at 21:03 on p50f; the first status ticks every minute again (downloaded 96, imaged 95, read 12,
assembled 12 at 21:04). Seen in the log: between the restart of 20:06 and the end at 21:03 no issue was read
(read stayed at 12, pages read at 982) although the two reading workers were alive and waiting for their
subprocesses — the reading server or the surya client had stalled; the subprocesses were ended with the
orchestrator. PASTE 7h checks whether the reading moves now (read events by time, the subprocesses' age, the
server's throughput lines and errors, GPU 0); PASTE 7i restarts the reading server if not — the workers put
their issue back and wait for the server, so the orchestrator itself is not touched.

### 2026-10-04, 21:22 KST — the reading check (PASTE 7h): the server idle, no reading subprocess, downloads held by refused files

The reading server was healthy and idle (its last requests at 20:09, a burst answered at 2,903 tokens a second,
then "Running: 0 reqs"); no s02 subprocess existed at 21:22, nineteen minutes after the restart; no
"reading_server_down" event; GPU 0 at 94 GB with the vLLM engine (83 GB), the stylometry process (1.6 GB) and the
other python process (9.4 GB). So between 20:06 and 21:03 the two s02 clients sent one batch of requests, got
their answers, and then hung; after 21:03 the workers started no reading at all — the stacks of the threads
(PASTE 7k) will say where they wait. Meanwhile the downloads: three of the four download workers were sitting in
long waits (up to 1,200 s, try 6) on files the archive answers with HTTP 500 every time (a scandata.xml, a
djvu.xml, a jp2.zip), so "downloaded" stood at 96 for forty minutes. Changes (p50g): the archive's text files get
three tries and are then recorded as missing (the issue goes on with its master); waits are capped at 300 s; a
request limit of 32 per reading worker (two workers, 64 requests, under the server's 104 sequences — the
default, 96 each, is the suspect for the hang); `kill -USR1 <pid>` writes every thread's stack to
run_faults.log.

### 2026-10-04, 21:56 KST — the reading moved again after a restart; the stuck state recorded (PASTE 7k)

Before the restart the 21:03 process had 123 issues in its reading queue, ten threads alive, no thread error in
the console log, no reading finished — the two reading workers sat idle with a healthy server; why is not yet
known. The restart at 21:56 (still p50f: the p50g files had not reached the clone, the device link being down)
read three issues in four minutes (21:56, 21:58, 22:00; 200 requests to the server in four minutes; two s02
processes alive). The paste then sent USR1 to the new process, which on the p50f code has no handler and ends
the process — my mistake: the paste should have checked the code version first. The run therefore needs PASTE
7l after PASTE 7j. p50g adds, besides the download policy and the 32-request limit: a reading that takes longer
than settings.reading.issue_timeout_s (3,600 s) is killed with its surya child (its own process group) and put
back; a stall watch in the main loop — no reading finished for 20 minutes while readings wait and the server
answers — writes every thread's stack to run_faults.log and an event "reading_stalled", so the next stuck
state explains itself.

### 2026-10-04, 22:05 KST — the run on p50g (commit a18d083); the lab's lanes read (PASTE 8a)

PASTE 7j and 7l: p50g on the server, the run restarted at 22:05 with two reading workers at 32 requests each;
a reading finished at 22:05 and two s02 processes were three minutes old at the check. PASTE 8a: the lab's two
lanes are vllm/vllm-openai:latest serving Qwen3-14B (GPU 1, port 8004, from /home/tailab/shared/models/qwen3-14b,
32,768 context, thinking parser on) and qwen3.5-9b (GPU 3, port 8006); the Hugging Face cache holds smaller
models only (Qwen2.5-7B-Instruct, gpt-oss-20b, Llama-2-7b, BERT variants, surya-ocr-2); GPU 2 held 4,583 MiB
(the stylometry job's share grows there too); the Anthropic key is in the server's environment file. Decision:
the box-linking lane on GPU 2 serves the lab's own copy of Qwen3-14B (container pulp-llm-8023, port 8023, 80% of
the card, 64 sequences, thinking off per request, JSON answers enforced); qwen3.5-9b stays available for a
comparison through the PULP_LLM_MODEL/PULP_LLM_BASE_URL overrides.

### 2026-10-04, 22:45 KST — the lane on GPU 2 up (PASTE 8c); the first prompt seen; the ssh session dropped (8d)

pulp-llm-8023 (vllm/vllm-openai:latest, Qwen3-14B, 80% of GPU 2, 64 sequences) was up after 120 s; GPU 2 at
81,344 MiB. The first test question — does "and the wolves came down" continue "The night was cold and the
moon rose red over the hills," — was answered {"continues": false, "confidence": 0.3}, wrong: a one-shot
JSON answer with thinking off and no context is a weak test, but it is a warning that the local tier may be
unsure often (a high escalation share) or wrong with confidence; the trial measures both against the rules,
and p50i adds a thinking switch (llm_link.local.thinking; PULP_LLM_THINKING=1) and --redo/--tag so the same
issues can be run again with thinking on, or with qwen3.5-9b, and the reports compared. The dry run of page 5
of '47 showed the prompt as intended (17 boxes, labels, positions, the rules' proposals — including a rules
weakness: the story "F. D. R." is said to begin mid-sentence on page 5, "and materials used, and all sorts of
details about the construction"). The ssh session then broke ("Can't assign requested address"), so the
single-issue run and the trial did not start; PASTE 8f runs the trial in tmux where a dropped session cannot
end it.

### 2026-10-04, 23:40 KST — the trial's first issue (PASTE 8f); three faults in the stage, fixed in p50j

The trial (tmux llmtrial, --trial 100 --tag qwen14b_nothink, one issue at a time) started at 23:27 on p50i. Its
first issue ('47, 1,562 boxes) agreed with the rules engine on 81.4% of the boxes (0.8143). The two large
disagreement classes were the ones that matter for assembly: 132 boxes the rules joined to the previous piece
that the model called the start of a new one (rules=previous / model=new), and 78 the other way round
(rules=new / model=previous); the rest were furniture, advertisement and caption labels. Which side is right in
each class is not known from this run and is the next thing to look at (PASTE 8h prints samples of both classes
with the box text). Three faults showed in the same output. (1) Every escalation to the Claude API failed: the
request carried temperature 0, and claude-opus-5-5 answers "temperature is deprecated for this model" (HTTP
400); 23 pages of the first issue were affected, so the API tier had not been tested at all. (2) Some local
answers were unusable: the model pretty-printed the JSON over many lines and the 1,500-token answer limit cut
it off, so the parser saw an incomplete answer and the page went up a tier for the wrong reason. (3) The lane
was almost idle: six requests in flight, 254 output tokens a second, 4.5% of the key-value cache in use — the
stage asked one page at a time within an issue, because each page's prompt was built from the previous page's
answer (the open piece). p50j fixes the three: no temperature is sent to the API (max_tokens 2,500); the system
prompt asks for compact one-line JSON and the local answer limit is 4,000 tokens; and the pages of an issue are
asked in parallel (48 at a time) with the open piece taken from the rules engine's view of the previous page
rather than from the model's own previous answer — the pages become independent, the question to the model is
the same, and the chains are still built page by page afterwards. At most six API calls are in flight at a
time; the API spend file now also counts input and output tokens. The trial is restarted on p50j (same tag,
--redo); the first run's outputs are overwritten, its log lines stay in data/corpus/llm_link_trial.log.

### 2026-10-05, 00:00–09:20 KST — p50j deployed (f5d6133); the first trial stopped after five issues; a second ssh drop; pastes no longer wait

PASTE 8g committed p50j as f5d6133 and the server pulled it. PASTE 8h then stopped the first trial, which had
finished five issues in about half an hour, six issues at a time: '47 (agreement 0.8143, above) and four others,
of which the last three printed — 10 Story Book 1922-02 (68 pages, 1,280 boxes, agreement with the rules 0.698,
22 pages flagged, 1,831 s), 1921-11 (1,304 boxes, 0.815, 27 flagged, 1,874 s) and 1922-03 (1,355 boxes, 0.721,
31 flagged, 1,903 s). No page reached the API (every call failed on the temperature parameter), so every page
the local tier was unsure of, or whose answer it could not parse, was flagged: 22 to 31 of 68 pages (32–46%).
That is the most the API tier would have had to take on these issues with the p50i prompt. 8h then printed
"s12 selftest ok" and LANE-OK, and the ssh session broke again ("Read from remote host 155.230.137.46: Can't
assign requested address"), this time during the paste's five-minute wait. The two commands that start the
trial in tmux come straight after LANE-OK and take a fraction of a second, so the trial almost certainly
started at about 00:00. The error is raised on the Mac when its own network address goes away (a Wi-Fi or VPN
reconnection), not by the server. Both drops (4 Oct 22:45, during a run in the foreground; 5 Oct 00:00, during
a `sleep 300`) came while a paste was waiting for minutes. From now on a server paste returns within about a
minute: long work starts in tmux and the paste ends; the check is a separate paste that can be run any number
of times. PASTE 8i is that check for the trial: it starts the trial only if no trial process is running, the
tmux pane does not show TRIAL-DONE and no trial report newer than the deployed s12 exists; it finds the process
with an anchored `pgrep -f "^python3 pipeline/s12_llm_link.py --trial"`, because the tmux shell's own command
line contains the same text and stays alive in its `sleep 86400`; and it prints the trial's numbers over every
issue finished since the tmux session was created, with the corpus run's progress. The handbook's paste section
records the rule.

### 2026-10-05, 09:20 KST — the first box-linking trial's results (PASTE 8i); what they show

The trial on p50j (tag qwen14b_nothink) ran from 00:10 to 04:07: 100 issues (the first hundred assembled: 10
Story Book, 10 Story Detective, 10 Story Western, '47, Action Stories, A. Merritt's Fantasy and others), 12,099
pages, 229,867 boxes, 14,213 s — 1.17 s a page with one issue at a time and 48 pages in flight. Local answers:
11,629, 47.2 s each, 827 output tokens each. 2,827 of them (24%) could not be read: the model broke the list
partway and wrote a box as a quoted string with escaped quotes (`{"boxes":[{"k":1,…},"k\":2,\"joins\":…`);
under the lane's plain JSON mode a string inside the list is valid JSON, so the server let it through. Of the
8,802 readable answers, 7,124 pages had every box at confidence 0.85 or more, 1,634 had a box between 0.5 and
0.85, 44 a box under 0.5. So 4,505 pages (37%) wanted the API: 2,827 unreadable and 1,678 unsure. The API
answered 322 of them (Opus 5.5 with the page image: 10.2 s, 4,337 tokens in and 1,097 out, $0.0393 a page; 5
answers unreadable), $12.65 in all, and from 00:36 refused every call: "Your credit balance is too low to access
the Anthropic API" (4,183 calls). Flagged pages: 3,261 (27%). The 2,719 pages no model answered were given
"previous" for every box, i.e. chained into whatever piece was open — a fault: from p50k they keep the rules'
decisions and are flagged.

Agreement with the rules, box by box: 83.6% (by issue from 0.389, 310 All-Story Weekly covers, to 0.945; median
0.839; by magazine from 0.784, 10 Story Book, to 0.880, 5 Western Novels). The figure mixes two questions and the
comparison had a fault. (a) A box was compared with the box just before it, so after a running head or an
advertisement the rules were counted as beginning a piece even where their records continue it. (b) The rules'
"advert" names a kind of box, the model's "previous" a link: 17,500 boxes that the rules call advertising and the
model chained to the box before them (mostly the later boxes of one advertisement) counted as disagreements; the
largest pairs were rules=advert/model=previous 17,500, rules=new/model=previous 7,378, rules=furniture/model=
previous 5,582, rules=previous/model=new 2,360, rules=advert/model=new 1,813. The model's hints had fault (a)
too: the first box of every page, and the box after a running head, were described to it as beginning a piece;
it mostly overrode them (the "F. D. R." case of 4 October, "begins mid-sentence on page 5", was this fault, not
the rules). The samples of the two boundary classes (16, with their texts) favour the model: continuations in
mid-sentence that the rules are said to split ("were foregathered from the best families of France and Spain…",
"road grew to a river…", "people. Someone remarked that…" — some of these are fault (a)), and new titles the
rules ran on ("The Dirty Guy A Story of the Track BY EDWIN HEIMBACH", "A BARNYARD TRAGEDY", a verse filler after
a rule in 10 Story Book); a few are unclear (a by-line first on its page; genre labels such as "• Baseball"; an
anecdote heading inside a department of '47). One more fault, in the record builder: a "previous" box after an
advertisement went into the open story, while the model, following the prompt's words ("continues the piece of
the box before it"), sometimes used "previous" for the second box of an advertisement, so advertising text could
enter a story record.

Projection: the trial's own escalation share (2.7%, the pages the API answered) gives about $1,000 for the
corpus's 980,000 pages; with every unsure page answered — 14–18% once all answers are readable — about
$5,400–6,900 on Opus 5.5, half on Sonnet 5.5. The local tier at 1.17 s a page would need about 13 days on GPU 2,
longer than the reading. The API credit is Heejin's to add (the Claude Console's billing page); the stage runs
without it, flagging instead.

The corpus run at 09:19: 1,921 issues downloaded, 1,914 imaged, 352 read, cleaned and assembled (351
lemmatized), 349 masters removed; no failures, no stall-watch events, no crash notes; 2,529 GB free on /mnt/sda.
Rates since 22:05: 158.5 downloads an hour (about 35 hours left), 30 readings an hour — 58,306 pages, 1.45 pages
a second, GPU 0 at 71% at the check. Before p50g it read 1.9 pages a second at 86–89%: the 32-request limit set
in p50g costs about a quarter of the speed.

### 2026-10-05, 10:00 KST — p50k: the faults of the first trial corrected; the accuracy run on the pilot issues

s12: the local answer is held to a JSON schema (a list of box objects with fixed fields and allowed values; the
server lets the model write nothing else), with plain JSON, then nothing, as fallbacks if the lane refuses it;
the answer limit grows with the page (about 60 tokens a box, 4,000 to 12,000); a cut-off answer is asked again
with twice the room, an unreadable one once more at temperature 0.3; how each answer ended is kept. The rules'
decisions are computed once per issue in reading order across the issue (a piece runs on across furniture and
advertising) and serve three uses: the hints, the comparison, and the answer for a page no model answered. The
open piece shown for a page is looked for up to four pages back, past pages of advertising. The prompt now says
that "previous" continues the editorial piece that is open, also across advertising, that every box of an
advertisement is "advert", and that "new" is never used for advertising; the builder takes "new" with kind
"ad" as the start of an advertisement. The comparison asks two questions apart: the kind of every box
(editorial, advertising, furniture) and, where both call a box editorial, whether a piece begins there. The API
is not asked again in a run after a refusal retrying cannot cure (no credit, a refused key, an unknown model);
budgets are per run ($50) and in total ($100 across all runs, $12.65 spent); those pages are flagged, each flag
with its reason. Trials write to their own folders (data/assembly_v2/llm_trial_<tag>), resumable, with a line
per issue in summary.jsonl; --same-as runs a trial on an earlier trial's issues; --pilot runs the ten pilot
issues into data/assembly_v2/llm. Two issues at a time keep the lane busy. s09: the llm variant is scored with
the others; --issues-dir/--variant/--out score a trial's corpus issues on the contents pages and the structural
checks (issues nobody corrected skip loading the site). The reading: 48 requests per worker (96 in all, under
the server's 104 sequences), read again by the orchestrator for each issue.

The paste (PASTE 8k) moves the first trial's outputs to data/assembly_v2/llm_trial_qwen14b_nothink; prints a
calibration from its 317 pages answered by both tiers (how often Opus agrees with the local model at each level
of the local model's confidence) and a diagnosis of its unreadable answers; and starts tmux `llmjob`: s12 over
the pilot issues, s09 on the pilot issues (rules and llm against the human-verified records and the contents
pages: the first accuracy figure for the box links against people's corrections), the second trial on the same
100 issues (tag qwen14b_schema), and s09 over those issues for the rules and both trials.

### 2026-10-05, 10:05 KST — the calibration (PASTE 8k); Heejin: no API; p50l; the site's live corpus board (v0.17.0)

PASTE 8j committed p50k (92b116f); the server pulled it. PASTE 8k moved the first trial's outputs to
data/assembly_v2/llm_trial_qwen14b_nothink and read them again. The 2,827 unreadable answers: 78 showed a box written
as a quoted string within their first 600 characters (the raw answer was kept only that far), 3 had been cut off at
the 4,000-token limit; the rest broke later in the list or left boxes out (they were 925 output tokens long at the
median, about the length of a readable answer, and their pages had 21.5 boxes on average against 19.2). The
calibration, on the 209 pages both the local model and the API (Opus 5.5, with the page image) answered: the two
gave the same answer on 95.1% of the 1,804 boxes the local model was at least 0.95 sure of (98.8% of the 1,471
begins-or-continues decisions among them), 70.9% at 0.85–0.95 (671 boxes; 85.7%), 44.0% at 0.70–0.85 (448; 57.3%),
61.4% at 0.50–0.70 (44), 84.0% below 0.50 (25). Where they differed: local new / API previous 193, local previous /
API advert 116 (mostly the second and later boxes of an advertisement, which the old prompt let the local model call
"previous"), local previous / API new 56, local new / API advert 41, local new / API caption 33, local previous /
API furniture 31, local previous / API caption 27. Read together: on the boxes it was very sure of, the local model
and Opus agreed almost always; below 0.95 they parted often. The API changed about 560 box decisions on those 209
pages (one in five), about 0.25% of the trial's boxes; whether its changes were corrections is not known without
people's corrections (the pilot run now in progress measures the local model against them). The pilot run on p50k
started at 09:49 (tmux llmjob; the API was refused again on its first call, as the credit is spent, so it measures
the first reading alone).

Heejin, 10:00: "Using api costs too much. Was there any gain by using it? Let the local model do the job as much as
possible and if unavoidable let it flag them for a person." — and "The website download count hasn't changed I
think. No live update on the website??"

p50l: the API path is off (settings.llm_link.escalate.enabled false; the code is kept). The doubtful boxes get a
second reading by the same local model with thinking on, asked about those boxes only, told what the first reading
said, with Qwen's sampling for its thinking mode (temperature 0.6, top_p 0.95, top_k 20). Thresholds from the
calibration: a box under 0.95 in the first reading is doubtful; it is settled when the two readings agree or the
second is at least 0.85 sure; a page is flagged only when an unsettled box's decision moves a piece (previous, new,
advert), each flag carrying both readings and the box's text; a page no reading could read keeps the rules'
decisions and is flagged. The first reading gives a reason only when it is less than 0.95 sure (shorter answers).
The local model can be asked on several lanes in turn (local.extra_lanes; empty: the lab's own Qwen3-14B lane on
GPU 1 was idle at both checks this morning, but it is the lab's and needs Heejin's leave). Pilot runs take a tag
(data/assembly_v2/llm_pilot_<tag>) so that runs can be compared on the human-verified records.

The website (v0.17.0): every count on it came from the explorer database, which is built from the pilot list, so the
corpus run never showed. webapp/corpus_run_pages.py reads data/corpus/progress.json (rewritten by the run every
minute) and data/corpus/events.jsonl (incrementally) at request time; /run (Workroom, "Corpus run") shows the
totals, rates, hours left, failures, a year strip of the selection against what has been downloaded, read and
assembled, the latest events and the box-linking runs, and reloads itself every minute; the same board heads the
Progress page; the explorer's downloaded and assembled counts (year strip, collection bar) add the corpus run's
issues.

The corpus run at 09:50: 2,030 downloaded and imaged, 370 read and assembled, 368 masters removed, no failures, no
stall-watch events, 2,499 GB free; 160.8 downloads and 30.2 readings an hour since 22:05; the readings then running
still carried the 32-request limit (they had started before the pull), the next ones carry 48. One download was
waiting out the archive's errors (Blue Book 1939-07, try 6 of 7).

### 2026-10-05, 10:45 KST — the first pilot score (PASTE 8n, 8o): why the model's records lost; p50m; GPU 1 not to be used

PASTE 8m committed p50l and site v0.17.0 (b02bcfa). PASTE 8n: the site answered at v0.17.0, the readings carried 48
requests each, the p50k job was stopped after its pilot had been scored, and the p50l pilot started at 10:10. The p50k
pilot (the first reading alone; the API refused at once, the credit being spent): 10 issues, 1,434 pages, not one
unreadable answer (the JSON schema works), 7 pages with a box under 0.85; agreement with the rules 99.5% on the kind of
box and 99.75% on piece starts. Its score (s09) against the 74 human-verified records and the 108 pieces of the
contents pages:

    variant  pieces found title author clean cover xstart over | verified exact Jaccard | records chap noauth
    rules       108   108   108  88/88   106  0.95      2    0 |       74    65    0.99 |     588    0      4
    llm         108   104   100  87/87    95  0.75     16    0 |       74    36    0.84 |     264    7     20

The model agreed with the rules on 99.75% of the piece starts, so the loss came from how its records were built, not
from its judgment: a link from one box to the box before it cannot say that a story resumes after a filler, an
advertisement or a jump ("Continued on page 98"), so every resumption became a record of its own — stories cut short
(coverage 0.75), the fragments counted as chapter splits and as records without an author; and the hints had told the
model that such boxes "begin" a piece. The pilot issues are also where the rules are at their best: the rules engine
was written against them; the corpus issues will tell more.

p50m: the model's decisions are applied to the rules' records instead of replacing them. Where the model agrees, the
rules' record is kept as the rules made it, resumptions and all; where it disagrees, the record changes at that box:
it splits (the model begins a piece where the rules continue one), it joins the piece before it (the model continues
where the rules begin a record), or a box moves out as advertising or furniture, or into the open piece. Each change is
listed in the record with the model's confidence (llm.changes; llm.kept marks the records left as the rules made
them). The rules' decisions mark a piece's beginning only at its record's first box; a later box after another piece
is hinted as "continues the story … (resumed after another piece)". The prompt says that a story resumes after
advertising, a filler or a jump, and that a chapter heading inside a story continues it. A box takes the title role
only when it carries the title. --rebuild <variant> remakes a run's records and comparison from its stored decisions,
without asking the model, so the p50k and p50l pilots can be scored again with the new builder at once. The lane is
restarted with room for 128 requests (at 10:11 it ran 64 with 31 waiting, 862 tokens a second, 38% of its request
memory in use).

Heejin, 10:40: "Don't use GPU 1." — settings.llm_link.local.extra_lanes stays empty.

The corpus run at 10:10: 2,085 downloaded, 2,078 imaged, 381 read, cleaned, lemmatized and assembled, 379 masters
removed, no failures, no stall-watch events, 2,482 GB free.

PASTE 8p (once the p50l pilot is scored): the p50l pilot's numbers and score; that job stopped; tmux llmjob: the lane
restarted with 128; --rebuild of the p50k and p50l pilots and their score (the new builder alone, on the old
decisions); the p50m pilot and its score; the p50m trial on the same 100 issues and its score.

### 2026-10-05, 11:10 KST — the whole corpus on the website, live; the box linking follows the corpus run (p50n, site v0.18.0)

PASTE 8p committed p50m (a4b1071). PASTE 8q: the p50l pilot (two local readings, old builder) asked 6 of its 1,434 pages
again at the 0.95 threshold (0.42%); of the 14 doubtful boxes the two readings agreed on 9 and the second was at least
0.85 sure of 5, none stayed open, no page was flagged; 148.7 s an issue, the second readings 580 s in all. Its score:
40 of 74 verified records exactly right (overlap 0.83), 103 of 108 contents pieces found, 93 clean, 16 chapter splits —
the old builder's loss again. The p50m job rebuilt the p50k and p50l pilot runs on the rules' records (10:29),
restarted our lane with room for 128 requests (up at 10:30) and began the p50m pilot. The first reading is almost never
unsure (under 0.5% of pages below 0.95), so its confidence hardly separates its right answers from its wrong ones; what
marks a record for people is mostly a disagreement — a change the model makes to the rules' records — and an open
decision, not the confidence.

Heejin, 10:33: "Why the website still shows 585 stories assembled only for pilot. What I expect is whole corpus
now run on the same way. Show them by authors, Magazines, issues, and stories. And Workbench shows how they are
assembled automatically, and show confidence scores and flags when automation is not so sure. Human annotator will fix
some of them and the algorithm can improve accordingly. Let's the website shows everything we are doing here. It must
lively update eveything."

Why the site showed the pilot alone: its workbench and explorer read an issue's records from data/articles/<id> (the
live assembly, on which people's corrections are replayed), and the explorer's database was built from the pilot list
and the pilot's export; the corpus run's records stayed in data/assembly_v2/rules/<id>, which the site never read.

p50n and site v0.18.0:
- pipeline/s13_publish.py puts every assembled corpus issue on the site: data/articles/<id>/articles.json from the
  model-checked records (s12) when the issue has them, else the rules'; every record carries how it was assembled
  ("rules (not yet checked by the model)", "rules, checked by the model", "… (changed)"), the model's lowest confidence
  on its boxes, its flags (the rules' notes, each change the model made, each decision it left open) and needs_look (a
  change, an open decision, or a confidence under settings.publish.look_below, 0.9). An issue someone has corrected is
  not written again (the corrections are replayed on the records they were made on).
- pipeline/r00_export_stories.py --corpus exports the corpus issues one file each (data/export/corpus/<id>.jsonl),
  again only when an issue's live records or its corrections changed.
- The explorer database now holds every selected corpus issue (its stages from the run's state file, its archive record
  once downloaded) and every exported corpus record, with three more columns (confidence, assembly, needs_look);
  authors, magazines, issues and stories therefore cover the corpus as far as it is assembled.
- scripts/site_refresh.py (tmux siterefresh) runs publish, export and rebuild every settings.site.refresh_minutes (5);
  the database is built beside the old one and moved into place; the site never builds at request time (the file
  data/explorer.static); data/corpus/site_refresh.json holds the last cycle's numbers, shown on /run.
- The workbench list (/articles) reads the database, paged, with columns for the assembly, the confidence and the flags
  (⚑ = needs a look) and a filter "needs a look"; the issue and record pages of the workbench show the same, and every
  flag in full. Corpus issues open on the workbench like the pilot's (issue_by_id reads the corpus list too).
- /log shows this build log; /run adds the issues checked by the model and those on the site, the last refresh, and the
  accuracy tables (s09); the Progress page's per-issue table lists the issues begun (at most 300), not all 7,440.
- s12 --follow checks every assembled corpus issue in the list's order, two at a time, and waits for more; it keeps its
  bookkeeping out of the run's state files (an issue is done when its compare.json exists; the fact goes to
  events.jsonl; failures to data/corpus/llm_follow_failures.jsonl), because two writers on one state file could lose a
  mark. New records the model makes are numbered <issue>_a9001 on, which the site's record lookup recognises.

The corrections loop: a person's corrections on the workbench are kept per issue (data/annotations/<id>.jsonl) and
replayed on the records; the issue is held from republication; the refresh exports it within minutes, so the explorer
shows the corrected records; s09 scores every way of assembling against the records people verify; s11 lists what
people changed, box by box, for the next change to the rules (s08) or to the model's prompt and hints (s12).

Five more points found while testing:
- The ten pilot issues are in the corpus list too, under the archive item's own name (wt_1925_11 is
  weird_tales_1925_11_5192511sas; all ten match by archive identifier). The explorer shows each of them once, with the
  pilot's checked records; the corpus run's own records of them stay on disk and on the workbench, where they can be
  scored against the pilot's verified records (a measure of the corpus pipeline on the same pages).
- The corpus list keeps some issues the archive holds under two or three names when the magazine's name differs between
  the records (Galaxy, March 1952: three items, one as "Galaxy Magazine (March"); the explorer shows every archive item,
  so such an issue's stories can appear twice until a later duplicate check by text removes them. Not changed now.
- The magazines list holds all 1,604 selected magazines; it is now sorted by stories (the ones with assembled records
  first) and says how many have records so far. A corpus issue's page explains itself as a corpus issue (the pilot's
  timing table does not apply). s12 --issue, like --follow, leaves the run's state files alone.
- The public front page spoke of "a development set of 10 issues" (Heejin, earlier: "Forget about development set."); it
  now says that the whole corpus is being built, with the number of selected issues from the run's progress file, and
  its counters (issues, records, stories, words) include the corpus records.
- The follower must not turn a stop of the lane into flags: while the lane on GPU 2 does not answer it waits (it looks
  again every five minutes), and an issue on which the lane failed on many pages (3 or more, and at least a tenth) is not
  written at all and is asked again later; otherwise every such page would fall back to the rules' decisions and be
  flagged for a person. An issue that fails for another reason twice is left for a person
  (data/corpus/llm_follow_failures.jsonl).

The 100-issue p50m trial is dropped: the follower checks every assembled corpus issue the same way, so its numbers come
from the whole corpus, and the lane is not shared between two jobs. The folder data/assembly_v2/llm held the p50k pilot
run (the pilot runs before p50l had no name of their own); it becomes llm_pilot_p50k, so that data/assembly_v2/llm holds
the corpus alone and the numbers on /run are the corpus's.

PASTE 8s commits p50n. PASTE 8t (server): the p50m pilot's state; the old job stopped (the p50m pilot resumes: issues
already done are not asked again, the one or two in progress start again); llm moved to llm_pilot_p50k; tmux llmjob:
the p50m pilot finished, all pilot runs scored side by side (data/assembly_v2/eval_pilot_all.txt), then s12 --follow
over the corpus; tmux siterefresh started (its first cycle publishes the rules' records of every assembled issue at
once); the site restarted on v0.18.0. PASTE 8u: the refresh's numbers, the follower's pace against the reading's, the
pilot score.

(Next entries: the first refresh cycles; the follower's pace; the p50m pilot score.)

### 2026-10-05, 11:50 KST — the p50m pilot score; Heejin: the rules' records, with the model's view as flags (p50o, site v0.18.1)

PASTE 8s committed p50n (88431b3). PASTE 8t (11:36): the job of 10:29 had finished the p50m pilot at 10:45, scored it,
and begun the 100-issue p50m trial, which 8t stopped (its folder llm_trial_qwen14b_p50m stays, unfinished);
data/assembly_v2/llm became llm_pilot_p50k; the pilot runs were scored side by side; the follower (s12 --follow) began
at 11:36; tmux siterefresh began at 11:36; the site restarted on v0.18.0. PASTE 8u (11:37): the first refresh cycle was
still running (it had published the rules' records of 432 issues; the explorer database was still the old one). By 11:39
the public front page showed 443 issues read, 17,162 records, 4,121 stories and 35,849,273 words of story text —
and 22 records verified: the explorer still read the pilot's export of 31 August, made before the 74 verifications of 8
September (fixed in p50o). The corpus run at 11:37: 2,409 issues downloaded, 433 read and assembled, 167.7 downloads
and 30.9 readings an hour, 2,431 GB free, nothing given up.

The pilot score (data/assembly_v2/eval_pilot_all.txt; every model run rebuilt with the p50m builder: the model's
decisions applied to the rules' records):

    run              exact of 74   overlap   clean of 108   cover   chapter splits   records   stories without author
    rules                 65         0.99         106        0.95          2            588              4
    llm_pilot_p50k        48         0.96          99        0.80         16            643             16
    llm_pilot_p50l        51         0.95          99        0.91         16            613             12
    llm_pilot_p50m        50         0.97         103        0.88          5            617             11

(live — the assembly people corrected — 62 exact; rules_on_model as rules.) The p50m prompt and hints (a piece begins
only at its record's first box; a resumption after another piece is named as such) cut the chapter splits from 16 to 5,
but the model's changes still lose against the rules alone: 15 fewer verified records exactly right, 29 more records,
7 more stories without an author. The pilot is the rules' home ground (they were written on these issues), so this does
not say what the model does on corpus issues; it says the model's changes cannot be taken as they come.

The question put to Heejin at 11:40, with these numbers: which version should the website show as each corpus record —
"Rules + model flags (Recommended): Show the rules' records. Where the model disagrees, mark the record 'needs a look'
and say what the model would change. A person decides; those decisions later tell us whether the model or the rules is
right more often on corpus issues." or "Model's changes (as now)". Heejin: "Rules + model flags (Recommended)".

p50o and site v0.18.1:
- s13 publishes in the mode settings.publish.prefer = rules_flagged: the rules' records, each compared with the model's
  reading of its boxes. A record the model would keep as it is: "rules, checked by the model: agrees". A record the
  model would change: "rules, checked by the model: disagrees (see the flags)", the record unchanged, and in its flags
  what the model would do, in plain words and with the workbench's names of the boxes ("the model would split this
  record: a new piece begins at 12F (sure 0.98)"; "… move box 14C out of this record as advertising …"; "… join this
  record to “The Red Moon” at box 15A …"). The confidence is the model's lowest on the record's boxes. A record needs a
  look when the model disagrees, left a decision on it open, or was under 0.9 sure of one of its boxes. What the model
  said stays with the record (model_check: agrees, its own change notes, open decisions), for the comparison with
  people's decisions. The modes llm (the model's records, v0.18.0) and rules (the rules' alone) remain. A small note,
  data/articles/<id>/published.json, says how each live file was made; the files v0.18.0 made from the model's records
  are made again in the new mode in the first cycle.
- r00 --pilot-live, run by the refresh: the pilot's file for the explorer (data/pilot_stories.jsonl) is written again
  when a pilot issue's records or corrections change, so the explorer counts the pilot's verified records as they are
  (the reuse stages' inputs, data/export/stories.jsonl and paratext.jsonl, are left to a reuse run).
- The explorer counts the records the model disagrees with and the records people verified; /run shows them with the
  last refresh. The workbench's wording says that a record the model disagrees with keeps the rules' form.
- The follower (s12 --follow) is not changed and keeps running.

PASTE 8v (Mac) commits p50o; 8w (server) restarts the refresh and the site; 8x shows the refresh's numbers and, on the
pilot, which kinds of the model's changes broke records the rules had right (the first list for improving the model).

(Next entries: the refresh's numbers; the model's pace against the reading's; the kinds of change that broke records.)

### 2026-10-05, 11:58 KST — p50o live; the model's first numbers on corpus issues; what broke the pilot's records

PASTE 8v committed p50o (fa02c5a). PASTE 8w restarted the refresh and the site (v0.18.1); the follower kept running.
PASTE 8x (11:55):

- The refresh made every live file again in the new mode in one cycle of 47 s: 445 issues, 18 of them checked by the
  model, 427 not yet. The explorer: 17,803 records, 4,291 stories, 1,568 authors, 1,581 magazines; 685 records checked
  by the model, which disagrees with 182 (27%); 185 records need a look; 74 verified (the pilot's verifications, now
  counted; the front page had said 22). A database build takes 2.2 s; an ordinary cycle 2.8 s.
- The follower (from 11:36): 20 issues by 11:55, 110 s an issue, two at a time (about 65 issues an hour against the
  reading's 31): it is working through the 445 issues assembled before it started and should reach the reading in
  about half a day. 1,777 pages: none unreadable, 20 asked again (1.1%), 3 flagged for an open decision (0.17%).
- On the corpus so far the model would change 172 of the 757 rules' records it checked (23%): a box added to a record
  61 times, a record split 58, a box moved out as page furniture 37, out as advertising 35, a box moved to the piece
  before 15, a record joined to the one before 13 (a record can have several).
- The pilot's lesson (llm_pilot_p50m against the rules, on the 74 verified records): the model's changes broke 16
  records the rules had right — a box added 7 times (as a caption, a notice or a continuation), a split 4, a box out
  as furniture 4, a join 1, a change on a neighbouring record 3 — and put 1 right (a box added). The broken examples
  include the serial "The Demolished Man" split at 134:16 (0.95; 531 boxes lost to the new piece), "The Return of the
  Undead" split at 138:16 (0.98), and boxes added as captions or notices at 0.99. The model's confidence on these wrong
  changes is 0.95 to 0.99: its confidence does not tell its right changes from its wrong ones.
- Four records that need a look, picked at random, show both sides: the model would join a by-line the rules had made a
  record of its own ("By Mary England") to its story, and would take apart a "feature" the rules made of what reads
  like a pipe advertisement — both look right at first sight; it would split "Hot Dogies for the Lone Star" at box 6C,
  where the rules had followed the story's "continued from page 6" notices — a case for a person; and one record ("By
  Edouarde": a by-line taken as a title) needs a look only because the model was 0.8 sure of one of its boxes.
- The corpus run at 11:55: 2,447 downloaded, 445 read and assembled, 166.8 downloads and 31.1 readings an hour, 2,426 GB
  free, nothing given up.

What it means: at about one record in four, "needs a look" is too long a list for people to go through for the whole
corpus (about 75,000 records at this rate). On the pilot nearly all the model's disagreements were wrong; on corpus
issues, which the rules were not written on, some are clearly right. Which kinds of disagreement can be trusted is a
question for a small human check of the corpus disagreements, by kind (put to Heejin).

### 2026-10-05, 12:10 KST — Heejin: a quick review page for the model's disagreements (p50p, site v0.19.0)

The question put to Heejin at 11:57, with the numbers above: "On corpus issues the model disagrees with about 1 record
in 4. That is about 75,000 'needs a look' records for the whole corpus, too many to check. … To learn which kinds of
disagreement to trust, how should people check them?" — a quick review page, the workbench as it is, or waiting for
more issues. Heejin (11:58): "Quick review page (Recommended)": one disagreement at a time on the scan, with three
buttons — rules right, model right, neither; about 150 random cases spread over the kinds of change; then, for each
kind, the model's change is applied, ignored, or kept as a flag.

p50p and site v0.19.0:
- /review/model (Workroom, "Model check"; webapp/model_review_pages.py). A case is one change the model would make to
  one of the rules' records (a join, seen from both records, is one case). The page shows the issue and the record,
  what the model would change in plain words, the scan of the page with the box of the question in red, the record as
  the rules made it in blue (the other record of a join or a move in purple, other records and page furniture in grey
  dashes), and the text of the box and of the boxes before and after it. A person chooses THE RULES ARE RIGHT, THE
  MODEL IS RIGHT, NEITHER (with a note on the right fix) or CAN'T TELL (keys 1 to 4). Each judgment is one line of
  data/review/model_check.jsonl (time, reader, case, issue, record, kind, box, the change, the model's confidence,
  verdict, note); a named account is needed, as for corrections.
- The next case: the kind of change judged least so far (settings.review.per_kind_target, 25 each), in it the magazine
  judged least so far, a case nobody has judged yet, at random. The kinds: a split, a join, a box added, a box moved
  out as advertising, out as page furniture, to the piece before, a new piece begun inside a record, a record taken
  apart. The counts by kind (rules right, model right, neither, can't tell, and the model's share of the decided ones)
  are on the page and on /run.
- s13 lists each issue's cases in its note (data/articles/<id>/published.json, version 2; every issue is made again
  once to add them) and the refresh gathers them into data/review/model_disagreements.jsonl, the page's pool, whenever
  something was published. The boxes are named as on the workbench (12D: page 12, fourth box in reading order).
- What the counts will decide, kind by kind, once each kind has about 25 (Heejin's decision then): apply the model's
  change (it is right most of the time), ignore it (it is wrong most of the time; the record no longer needs a look for
  it), or keep it as a flag. Applying only some kinds needs s12's record builder to take a list of kinds; that is built
  when the counts call for it.

The cases come from the issues the model has checked so far, which follow the corpus list's order (magazines in
alphabetical order, then dates), so the first cases come from a few magazines; the choice of the magazine judged least
spreads them as the model goes on. PASTE 8y (Mac) commits p50p; 8z (server) restarts the refresh and the site; 9a is
the check, now with the review's counts.

### 2026-10-05, 12:15 KST — p50p live: 1,363 cases waiting for the model check review

PASTE 8y committed p50p (1b15139); 8z restarted the refresh and the site (v0.19.0) at 12:12. The refresh made all 457
issues again with their cases (47.7 s) and gathered 1,363 cases for /review/model from the 32 issues the model had
checked, which belong to 8 magazines (the first in the list's alphabetical order). By kind: a box moved out of a record
as advertising 565, a box added to a record 297, a box moved to the piece before 252, a box moved out as page furniture
117, a split 92, a join 20, a record taken apart 18, a new piece begun inside a record 2. That is about 43 cases an
issue, two in five of them "a box out as advertising", so the verdict on that kind matters most. The explorer: 18,553
records, 4,412 stories; the model had checked 1,476 records and disagreed with 341 (23%); 350 need a look; 74 verified.

The model's pace: 32 issues from 11:36 to 12:13 (about 52 an hour; 128 s an issue, two at a time) against 31 issues
read an hour, so it should clear the 425 issues waiting in about a day and then keep pace with the reading. Its 3,233
pages: 42 asked again (1.3%), 7 boxes left open, 4 pages flagged. At 12:13 the reading's GPU (0) showed 0% for a moment;
the run had read 12 issues since 11:55, so this was a gap between issues, not a stall. The corpus run at 12:12: 2,477
downloaded, 457 read and assembled, 165.5 downloads and 31.3 readings an hour, 2,422 GB free, nothing given up.

Next: people with a named account judge about 25 cases of each kind on /review/model; PASTE 9a shows the counts; then
Heejin decides, kind by kind, whether the model's change is applied, ignored, or kept as a flag.
