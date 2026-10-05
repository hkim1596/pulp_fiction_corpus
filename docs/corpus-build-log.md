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

(Next entries: the calibration; the pilot accuracy; the second trial.)
