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

### 2026-10-06, 10:15 KST — Sujin's 206 judgments; Heejin's choices, kind by kind (p50q, site v0.20.0); the reading stopped

PASTE 9a (10:01). The site: 1,102 issues assembled; 49,172 records, 10,685 stories; the model had checked 33,672
records (774 issues, 112,484 pages: 782 asked again, 758 boxes left open, 77 pages flagged; 202 s an issue) and
disagreed with 7,584 (22.5%); 7,658 needed a look; 74 verified. The review's pool: 19,170 cases from 763 issues of 90
magazines — a box added 5,654, a box out as advertising 4,149, a box moved to the piece before 3,947, a split 2,006, a
box out as page furniture 1,611, a join 990, a record taken apart 733, a new piece inside a record 80.

Sujin's judgments on /review/model (one reader; about 25 of each kind):

    kind of change                         rules right   model right   neither   can't tell   model right of the decided
    box moved out as advertising                 1            15           9          1             60%
    join                                         5            15           2          4             68%
    split                                        5            15           3          2             65%
    box added to a record                        6            11           4          5             52%
    box moved out as page furniture             11            10           4          1             40%
    record taken apart                           8             4           3         10             27%
    new piece begun inside a record             17             6           3          0             23%
    box moved to the piece before                9             5           8          4             23%

Her notes: a box the model calls advertising is advertising, but so are boxes around it ("121M is advertising, yes, but
not only that box, right column is lists of ads"; "the box before · 37U · Is also part of ad"); a contents list is not
advertising; a box the model takes for the start of a new piece is often a continuation from another page ("149p is not
a begin, but continuation from other piece").

The questions put to Heejin at 10:05, with these numbers, and his answers (10:06), all as recommended:
- A box moved out of a story as advertising — "Apply automatically (Recommended)": the box leaves the story and goes to
  an advertisement record; the record no longer needs a look for it.
- Joins and splits — "Apply, keep flagged (Recommended)": the site shows the model's version, and the record stays on
  the needs-a-look list so a person can undo it.
- A box added to a record, a box moved out as page furniture — "Keep as a flag (Recommended)".
- A new piece begun inside a record, a box moved to the piece before, a record taken apart — "Stop flagging them
  (Recommended)": these no longer make a record need a look; what the model said stays saved with the record.

p50q and site v0.20.0:
- config/corpus_settings.json, publish.kinds: out_ad apply; join, split apply_flag; box_in, out_furniture flag; out_new,
  out_continues, apart ignore (with the verdicts in the comment). A change there reaches every issue at the next refresh.
- s12 build_records takes the set of kinds to apply (decision_kind names the change a decision makes: out_furniture,
  out_ad, new_ad — a box of no record made advertising —, split, out_new, join, out_continues, box_in, orphan); a
  decision of another kind leaves the box as the rules have it. s12 records_with(iid, kinds) makes an issue's records so
  from the stored decisions (no model is asked). The follower is not changed: data/assembly_v2/llm keeps the model's
  full view.
- s13 publishes the rules' records with the applied kinds made (records_with), and marks every record: "rules, checked by
  the model: agrees"; "rules, with the model's changes applied" (each change in the flags, in the past tense: "applied:
  the model moved box 37U out of this record as advertising (sure 0.98)"); "rules, checked by the model: disagrees (see
  the flags)" for the flagged kinds; "… disagreement set aside" for the ignored kinds; "made by the model (from the rules'
  records)" for the advertising records and the split-off parts it made. needs_look: a flagged kind, an applied-and-flagged
  kind (a join, a split), an open decision, or a box under 0.9. model_check keeps what the model said: applied, pending,
  set aside. A box the model would add to a record is told apart by where it comes from: from no record (box_in) or from
  an advertisement (the same decision as the advertisement's own "box moved to the piece before", and set aside with it);
  in the review's pool the two notes of that decision are one case. The note published.json is version 3 and carries the
  policy; every issue is made again in the first refresh.
- The review page shows, beside the counts, what the site now does with each kind; the Corpus run page counts the records
  the model's changes were applied to; the workbench labels say "applied", "set aside" or "model".

The reading stopped (10:01): no issue assembled in the last hour; GPU 0 held 1.6 GB at 0% (the reading server holds about
85 GB). The model (GPU 2) and the site kept going. PASTE 9c (sent at once) shows why and starts the reading server again
if GPU 0 is free, this time with Docker's own restart (--restart unless-stopped, without --rm), so that a stop no longer
halts the run; nothing is removed. The download went on (6,650 of 7,440 issues, 181 an hour; the rest in about four
and a half hours), and its files — the archive masters, kept until an issue is assembled, and the page images — took 786
GB since yesterday noon: 1,635 GB free at 10:01, about 1,490 GB when the download ends. At 31 issues read an hour the
reading needs about eight and a half more days.

### 2026-10-06, 11:25 KST — the reading server started again; p50q live

PASTE 9c (about 11:17): the last issue read was at 06:53 (argosy_all_story_weekly_1924_11_ly19241101), so about four and
a half hours of reading were lost (some 140 issues). The run itself was alive (its 25 processes); no reading worker was
running, because the run waits for the server before it starts one; no reading container was there, and Docker kept no
record of its end (the container was started with --rm and removed itself when it stopped), so the cause is not known.
On the cards: a process of another project (/home/tailab/shared/khj/interpretable_deep_learning_stylometry, pid 554847)
holds a small share of all four, GPU 0 included (1.6 GB there); if it took more of GPU 0 for a while, the reading server
(which takes 85% of the card) would have run out of memory and stopped. The lab's lanes on GPUs 1 and 3 and ours on GPU 2
were as before; 459 GB of memory free. The reading server was started again at about 11:17, now with Docker's own restart
(--restart unless-stopped, without --rm), so a stop restarts it by itself and its log is kept. The download went on (one
item refused by the archive with HTTP 500, retried by the run).

PASTE 9d committed p50q (7e816f2); 9e restarted the refresh and the site (v0.20.0) at about 11:21, and within a minute the
refresh had made 354 checked issues again kind by kind. PASTE 9a shows the reading's return and the new counts.

### 2026-10-06, 11:35 KST — p50q's first counts; a split was flagged twice (p50r: one flag per change)

PASTE 9f committed the entry above (9667400). PASTE 9a (about 11:25):
- The reading is back: GPU 0 holds 87 GB at 77% (the reading server); one issue assembled in the last hour. The run:
  6,979 of 7,440 issues downloaded, 1,107 assembled, 29.3 read an hour since the start, 1,591 GB free.
- The refresh's first cycle on p50q published 1,102 issues again, 820 of them checked by the model. The site: 54,624
  records, 13,686 stories; 41,717 records checked by the model; on 13,581 of them the model differs from the rules (a
  change applied, flagged or set aside); 10,713 need a look; 74 verified.
- The review's pool: 17,684 cases — a box moved to the piece before 4,839, a box out as advertising 4,448, a box added
  2,402, a split 2,170, a box out as page furniture 1,779, a join 1,174, a record taken apart 788, a new piece inside a
  record 84. The pool is smaller than at 10:01 (19,170) with more issues in it, mostly because a box the model would add
  to a record from an advertisement now counts as the advertisement's own "box moved to the piece before": one case for
  one decision (box added 5,654 → 2,402; box moved to the piece before 3,947 → 4,839).

What did not go as planned: the share of checked records that need a look went up, from 7,658 of 33,672 (22.7%) at
10:01 to 10,713 of 41,717 (25.7%), when setting three kinds of change aside should have brought it down. One cause, in
s13: when the model splits a record, the site makes the part from the split box on into a new record ("made by the
model"), and s13 put both parts on the needs-a-look list for the same split — the first part, which carries the change
("applied: the model split this record …"), and the new part. With some 2,170 splits applied (the pool's count), that is
up to about 2,000 records too many on the list (fewer where the new part needs a look for a reason of its own).

p50r (pipeline/s13_publish.py; the site's code is unchanged, v0.20.0): a change is flagged once, on the record it came
from (a split: the first part; a join: the record the other was joined to); a part the model made needs a look only for
a change of its own of a flagged kind, a decision the model left open on one of its boxes, or a box it was under 0.9
sure of. published.json is version 4, so the first refresh after the update makes every issue again. The selftest: the
split's new part no longer needs a look; three records of the made-up issue do (were four). The settings' note on
apply_flag says the same. PASTE 9g commits it, 9h restarts the refresh (the site and the model's follower keep
running), and 9i is the check from now on: 9a's counts and, for the records that need a look, why — an applied split or
join, a box the model would add or move out as page furniture, a decision left open, a box under 0.9 — and, for the
records on the list only because of a box under 0.9, how sure the model was, to show what a lower threshold would take
off the list.

### 2026-10-06, 12:00 KST — p50r deployed; the first count of why records need a look; most of the list is the model's cuts

PASTE 9g committed p50r (e513cba) and the server took it. PASTE 9h (about 11:51): the selftest passed, the refresh was
started again, and within 45 seconds 448 of the 1,113 published issues had been made again with one flag per change.
PASTE 9i ran a minute later, before that first cycle had ended (536 of 1,113), so its counts are still those of p50q
(the cycle of 11:46):
- The site: 55,171 records, 13,868 stories; 42,569 records checked by the model; on 13,893 the model differs from the
  rules; on 10,374 its changes were applied (or the record was made by it); 10,958 need a look; 74 verified. The
  review's pool: 18,117 cases from 825 issues of 90 magazines. The model had checked 837 issues (123,604 pages; 202 s an
  issue); in the last hour 14 issues were assembled and 36 checked by the model.
- The run: 7,107 issues downloaded, 1,120 read and assembled, 29.2 an hour since the start; 1,577.5 GB free. GPU 0 held
  87 GB at 71% (the reading), GPU 2 was at 100% (the model); GPUs 1 and 3 (the lab's) idle.

Why the 10,958 records needed a look (a record can have more than one reason; in brackets, the records with that
reason alone):

    an applied split (kept on the list)                    5,496   (4,629)
    an applied join (kept on the list)                     1,150     (366)
    a box the model would add                              2,028   (1,442)
    a box the model would move out as page furniture       1,075     (762)
    a decision the model left open                           147       (2)
    a box the model was under 0.9 sure of                    419     (153: 124 at 0.8-0.9, 18 at 0.7-0.8, 10 at 0.5-0.7, 1 under 0.5)
    more than one reason                                             (1,357)
    no reason in the flags                                           (2,247)

By how the record was made: made by the model 5,523; rules with the model's changes applied 3,230; rules, the model
disagrees 2,113; rules, the model agrees 82; disagreement set aside 10.

What it shows:
1. The 2,247 records with no reason in their flags are parts the model made, on the list only because they were made:
   the double flag that p50r removes. After the first cycle on p50r the list should be about 8,700.
2. The 0.9 threshold hardly matters: 153 records are on the list for that alone, and 0.8 would take off 124 of them.
3. Most of the list is the model's cuts: 5,496 records carry an applied split, 4,629 for no other reason, while the
   review's pool has 2,210 split cases. The difference is in how a record cut more than once is kept. When the model
   cuts a rules record, the part after the cut is a new record (s12 build_records), and a further cut lands in that new
   part and is noted there; the review's cases are taken from the rules' records only (s13 cases_of). So the review
   shows only the first cut in each record. Sujin's split verdicts (model right on 15 of the 23 she could decide) are
   about first cuts; the later cuts, about 3,300 of the 5,500, have not been looked at by anyone. The count of last
   pieces (the 2,247 of point 1) is close to the count of records cut (about 2,200), which fits.
4. PASTE 9j counts the cuts on the server: pieces per record cut, the rules' type of the records cut, first and later
   cuts compared (a title from the model, a chapter or number heading at the cut, the length of the new piece, the
   model's confidence), and twelve later cuts at random with the words on either side, so that the next choice — how
   the later cuts are reviewed and flagged — rests on what they are.

### 2026-10-06, 12:30 KST — p50r live: 8,998 records need a look; what the model's cuts are

PASTE 9i (12:24), after the first cycle on p50r: all 1,132 published issues made again with one flag per change. The
site: 56,096 records, 14,249 stories; 43,824 records checked by the model; on 14,311 the model differs from the rules;
on 10,669 its changes were applied (or the record was made by it); 8,998 need a look (20.5% of the checked records;
25.7% under p50q at 11:25); 74 verified. Why (in brackets, the records with that reason alone): an applied split 5,647
(4,757); an applied join 1,225 (409); a box the model would add 2,084 (1,474); a box it would move out as page
furniture 1,126 (788); a decision left open 154 (2); a box under 0.9 sure 432 (158: 127 at 0.8-0.9, 19 at 0.7-0.8, 11 at
0.5-0.7, 1 under 0.5); more than one reason 1,410; no record left without a reason. By how the record was made: made by
the model 3,375; rules with the model's changes applied 3,349; rules, the model disagrees 2,177; rules, the model
agrees 86; disagreement set aside 11. The review's pool: 18,600 cases from 843 issues of 90 magazines. The model had
checked 853 issues; in the last hour 30 issues were assembled (the reading at its usual pace again) and 32 checked by
the model. The run: 7,197 issues downloaded, 1,136 assembled, 1,569 GB free.

PASTE 9j (12:25), the model's cuts in the 853 checked issues: 5,660 cuts in 2,425 of the rules' records. Pieces per
record cut: 2 pieces 1,488; 3 pieces 373; 4 pieces 167; 5 to 9 pieces 302; 10 or more 95. The records cut, by the
rules' type: cut once — story 1,070, feature 315, contents page (toc) 65, poem 21; cut more than once — story 715,
feature 178, contents page 35.

    the model's cuts                       first cuts (in a rules record)   later cuts (in a piece the model cut off)
    cuts                                              2,425                            3,235
    the new piece has a title from the model         2,392                            3,138
    it begins with a chapter, part or number            68                               48
    words in the new piece, median                      471                               33
    new pieces under 150 words                          847                            2,087
    the model sure 0.95 or more / 0.9-0.95 / <0.9   2,337 / 26 / 62                  3,088 / 32 / 115

Twelve later cuts at random: in seven the new piece carries the same title as the record it was cut from — "By W.
Alexander" (a by-line taken as a title; 12 pieces), "Lord Johnnie" (a record the rules typed as a story; the text at
this cut is a Dollar Book Club advertisement; 19 pieces), "The Pursuit of Antar" (12 pieces, the cut between an empty title piece and the story's first
paragraph), "A Long Evening", "By David M. Speaker", "Adventures Buffalo Bill", "Antarctica—AND Our Future" — and five
new pieces have no text of their own (only a title or a by-line). Four look like real new pieces inside a record the
rules had made too long: "The Dirty Guy" (10 Story Book, 1921), "The Drought" (Adventure, 1922, in a record titled with
the masthead), "Mastermind" (Amazing Stories, 1949), "The Closing Months of 1959" (Amazing Stories, 1939); one is a
next-issue notice ("Beyond Courage").

What it shows: the first cuts are mostly cuts between pieces of normal length; the later cuts are mostly the model
cutting one piece into fragments — the same title given again and again, a title or by-line made a piece of its own —
and the model is as sure of them as of the first cuts, so its confidence does not tell them apart. The review page has
not shown them (it takes its cases from the rules' records). PASTE 9k counts how many cuts give the new piece the same
title as the piece it is cut from (or no text of its own, or fall in a contents page) and how many records would leave
the list if such cuts were not made, with samples of what would remain; the choice of a rule for the cuts is put to
Heejin after it.

### 2026-10-06, 15:15 KST — PASTE 9k; Heejin: three kinds of split set aside, the further splits reviewed (p50s, site v0.21.0)

PASTE 9k, on the 963 issues the model had checked by then:

    the model's splits                                  first (in a rules record)   later (in a part it split off)
    splits                                                       2,638                        3,487
    the new part repeats the title of the part before            848                         1,394
    the new part has no text of its own                          570                         1,197
    either of the two                                           1,190                        1,959
    in a contents page                                            100                           232
    the rest: splits / words in the new part, median     1,448 / 997                   1,528 / 300

Of the 6,108 records that carried a split, 5,110 needed a look for splits alone; 2,067 of them would leave the list if
the splits repeating a title were not made, 2,761 if those leaving a part without text were not made either, and 298
if contents pages were not split. The samples: the five first splits repeating the title were all wrong — a teaser
("Sheathe Your Claws, Hellcat!", "Old Marshals Never Die"), a subtitle ("Undersea Prey"), the title and by-line cut
off from the story ("The Educated Harpoon", 2,448 words; "For the Flag", 21,319 words). The parts without text were a
title in two boxes ("The HOODED" | "Liner"), a heading ("A Forgotten Hero of the Air War", "Odes"), a teaser line ("Kay
Mercedes was beautiful but deadly"). The splits in contents pages cut the entries apart ("The Non-Gravitational
Vortex", "The Bulldog Breed", "The 500th Shot", "Our Cover", a "Contents" continued). Of ten later splits that would
remain, three looked right ("All Trains Stop", a filler; "Calvert of Allobar", 24,109 words, cut from "Vigilante
Justice"; "Future Sky Fighter"), five wrong (an advertisement inside a Buffalo Bill story taken for a new story, which
then carried the rest of the story; an article on Steinmetz cut from its by-line; a contents entry; a page of book
advertisements cut book by book; the letters of a "Discussions" column cut letter by letter) and two unclear.

The two questions put to Heejin and his answers, both as recommended:
- Should the site stop making the splits the samples show to be almost always wrong? — "Set aside all three
  (Recommended)": no split where the new part repeats the title of the part before it, where it has no text of its own,
  or inside a contents page unless the part cut off is a long text (over 300 words); the boxes stay in the record, what
  the model said is kept, not flagged.
- The splits that remain inside parts the model had split off (about 1,400 expected): — "Review them, keep flagged
  (Recommended)": they stay made and flagged; the review page shows them as their own kind, with a sample of the splits
  set aside, so that both can be judged; then Heejin decides as for the other kinds.

p50s does this:
- s12 tidy_cuts, after the decisions are applied (settings llm_link.cut_rules): a split is set aside and its part goes
  back where the new part repeats the title of the part it was cut from or of an earlier part of the same record (it goes
  back to that part, so a story resumed after an advertisement is one record again); where the split leaves a part with
  no text of its own on either side — the new part (it goes back to the part before), or the part before (a heading cut
  off from its text, which goes back to the heading: the samples show this side as often, e.g. "The Educated Harpoon",
  and it is the same rule seen from the other side of the split); and in a contents page where the part cut off has no
  more than 300 words. The parts are taken last made first, so that a part's own later parts are settled first. The
  record keeps a note, "cut at <box> set aside: <why> (<confidence>)"; a rules' record whose splits were all set aside is
  the rules' record again, with the rules' own text.
- s12 is_title_box: a box that merely begins with the words the model gave as the new part's title, but is much longer
  than a title, is body text, not a title box (before, such a paragraph was kept out of the record's text, and its part
  would have counted as having no text).
- s12 decision_kind: a split in a part the model had split off is of the kind split_later.
- s13: two more kinds in settings publish.kinds — split_later (apply_flag, as Heejin chose) and cut_set_aside (ignore:
  not flagged, kept in the record's model_check). A record with set-aside splits only is "rules, checked by the model:
  disagreement set aside"; a further split is flagged as "applied: the model split this part again …". The review's
  cases for both kinds come from the published records (later_cases), so that the parts the model made are named as the
  site names them; publish_all goes on past an issue that fails (it is logged and counted); published.json version 5,
  so every issue is made again at the next refresh.
- The review page: the two kinds ("split again", "split set aside"), with their own words under the scan (blue: the
  part split off before, purple: the part split off again; or the record with the split set aside); site v0.21.0.
- Tested in the sandbox: the s12 and s13 selftests (a title repeated twice, a by-line made a part, a contents page cut
  at its entries with a long story kept, a real second story kept and its further split of kind split_later, a heading
  cut from its text, the rules' title and by-line cut from the story); and the fixture end to end (the follower with a
  fake lane, s13, the review pool, the review page for both new kinds, the site refresh).

The model's stored records must be made again with the rules: PASTE 9m stops the follower after the issues in hand,
rebuilds the model's records of every checked issue from its stored decisions (no model is asked), starts the follower
and the refresh again (the refresh makes every issue again) and restarts the site; it runs in tmux p50s (log
data/corpus/p50s_deploy.log). PASTE 9l commits p50s; 9n is the check afterwards.

### 2026-10-07, 21:25 KST — Astra project access verified (Codex)

At Heejin's request, activated the prepared dedicated SSH key, a repository deploy key with write permission,
and the named active website administrator astra. Existing keys and accounts were preserved. Verified file
read/write access, a database write transaction rolled back without changes, repository SSH authentication and
write permission, and website login and admin-page access. No corpus data or application code changed, no services
restarted, and the pending p50s changes were not deployed. This entry remains a local addition pending the next
reviewed code deployment; credentials and detailed access records remain outside the repository.

### 2026-10-07, 21:48 KST — Read-only examination found source identity and publication defects (Codex)

The approved list has 7,440 archive identifiers but only 7,417 internal IDs (21 colliding IDs across 44 entries).
The December 1923 Argosy group contains three distinct issues under one ID; its model article and page-decision
files are malformed. Publication repeatedly fails on that cache, leaving the website database at 6 October
17:04:28 KST while OCR and assembly continue. At 21:42 KST there were 7,212 downloaded and 2,499 assembled unique
state IDs. Raw progress counters reuse the same state for duplicate entries. There are 205 failed downloads
with no queued retry; the survey confirms 80 collection records in the approved selection. The examination
made no production changes. Preserve affected data and correct source identity before rebuilding those records
or relying on a simple restart. Full findings and a bounded repair proposal are in the Dropbox project report
project-examination-2026-10-07.md, with a machine-readable snapshot and read-only collector under pilot_export/.

### 2026-10-07–08 — Automatic story continuity, rules 2.6 (Codex)

Heejin requested critical continuity fixes and fully automatic layout/OCR assembly, prioritizing whole stories.
Rules 2.6 now uses geometric reading order, stronger printed continuation and identity evidence, protection against
false headings and model cuts, exact source-page completion, and atomic publication/retry recovery. All 7,440
approved archive entries have unique IDs; the 44 affected collision entries' original artifacts were archived and
rehashed. CPU subprocess isolation also prevents inherited OCR pipes from hanging completed jobs.

The full 2.6 pass installed 2,536 of 2,572 selected assemblies. The next bounded pass completed at 04:41:53 KST
on 8 October: 879 of 902 installed and 23 retained unchanged under metadata guards. Those holds include real
continuity and title/ownership defects; their prior output is not certified correct. A strict live reading-order
check exposed six preexisting Hupmobile catalogue boxes interrupting one recovered sentence. The final general
catalogue-separation rule preserves those boxes in a separate ad after positive page and continuation evidence.
A reconciled 33-case replay changed only that issue; every other ownership group remained unchanged.

Final source fa7235a deployed at 05:08:27 KST; the one-issue correction completed at 05:09:57 with no failures.
Fresh consumers restarted at 05:11:02. Linux and local checks pass 231 unit tests, 21 continuity regressions and
five self-checks. The 42-story development benchmark retains 13,700 additional annotated body words, improving
96.026% to 99.944% recall and reducing false splits from five to zero; this is not a held-out corpus-wide estimate.
Live checks against the 05:11:15 database confirmed all 66 recovered narrative boxes in consecutive reading order,
with the six catalogue boxes (145 words), adjacent ads and the next-month note excluded from reading text. Four
earlier repairs also pass; 42 forms carry current assembly digests and invalid/stale submissions return 409.

All eight annotation logs and eight live assemblies remain byte-identical; all 23 held receipts are unchanged;
no meaningful cross-issue relationships were lost. Final provenance checks validate 160 OCR manifests / 22,596
pages; database integrity and all 47 deployed source hashes pass. The website snapshot has 111,117 records and
2,703 assembled issues; processing continued to 2,706 by the audit, with all stop/upgrade markers clear.
Only the minimal annotation-safety overlay was applied to the app; preexisting p50s app/settings changes remain
separate. See docs/continuity-improvements-2026-10-07.md and pilot_export/continuity-2026-10-07/ for evidence,
benchmark caveats and known remaining defects. Fully automatic execution does not guarantee 100% accuracy.

### 2026-10-14, 23:25 KST — the corpus run finished on 12 October; the code that built it committed; the server in line with git

The run (started 4 October, 22:05 KST; code p50g at the start, rules 2.6 from 8 October) ended by itself at 13:58 on
12 October ("nothing left to do"): 7,306 of the 7,440 approved issues downloaded, imaged, read, cleaned, lemmatized
and assembled; 974,720 pages read; the archive masters deleted for 7,304 after assembly. 134 issues failed to
download and were not retried (all marked failed between 05:11 and 09:46 on 8 October): 120 archive items have
neither a JP2 zip nor a PDF, 13 answered HTTP 500, one HTTP 401. The model's check (s12, GPU 2) finished at 17:44 on
12 October with every assembled issue linked. The website at 22:54 on 14 October: 7,273 issues current and 33 held
(corrected by a person, or under source conflict); 322,644 records, 67,087 stories, 18,180 authors, 1,584 magazines;
321,865 records checked by the model, 54,920 disagreements, 19,078 changes applied, 57,926 records needing a look;
125 verified; the newest annotation 9 October 20:14; the model-check review still at Sujin's 206 judgments of 5
October. Disk: 1,996 GB free on the data disk, 378 GB on the root disk.

The record between 8 and 14 October is the journal's (development-journal.md in the Dropbox folder; this log had no
entry). The Stage 1 protocol was submitted by Prof. Tenen (manuscript ID CHR-2026-0073, from his email, shown to
Claude on 7 October; the date of submission is not in the email).

Git (14 October, Heejin, PASTE 9q–9s): until today nothing after e513cba (p50r, 6 October) was committed; the
corpus was built with code that existed only as working copies on the Mac and the server. Two commits were made from
the Mac clone: 9f5fb62, p50s exactly as delivered on 6 October (its pipeline part had gone live inside Codex's
release, its site part had not); 040e598, Codex's rules 2.6 and source-ID repair of 7–8 October with its 25 tests,
the migration and continuity scripts and its notes — the code that built the corpus (45 of the 47 files in the
server's release manifest, data/corpus/releases/continuity-2026-10-07/manifest.json, are byte-identical to it; the
two others are webapp/app.py, the server having run the annotation guard on the v0.20.0 app, and Codex's notes).
The server was then brought in line with git by a checked reset (pilot_export/commit-2026-10-14/server_git_sync.sh:
every pipeline file and the approval file were already identical; the files overwritten — p50s's site part, the
settings file, the docs, .gitignore — are kept in data/corpus/releases/git-sync-2026-10-14/before/); the website
restarted on v0.21.0 (the review page's two new kinds, "split again" and "split set aside", are live). Three stray
logs at the repository's root on the server (s06_final.log, s06_final2.log, thumbs.log) are untracked and left alone.

Open, in order of weight: the 134 missing issues (retry the 13 HTTP 500 cases; inspect the 120 and the one 401 item;
state the result in this log and in the data paper's coverage note); the accuracy of the assembly on corpus issues
(the ~400-record human sample not started; no model-check judgments since 5 October; Codex's 99.9% recall is on its
42-story development set, not a held-out sample); the 57,926 records needing a look; the 33 held issues; Prof.
Jae-Yon Lee's offer of computing power (ADHO; a decision for Heejin, now less pressing since the run is finished).

### 2026-10-15, 08:40 KST — three decisions of 14 October turned into code (p50t, p50u); the work on the server prepared

Heejin's three choices on the evening of 14 October, each put to him with a recommended option: on the 134 issues the
run could not download, "Fetch the real scans, set the rest aside"; on the accuracy of the assembly, "150 issues,
stratified, two readers"; on the text-reuse analysis, which had run only on the pilot's stories, "Build and dry-run now,
label it as such". The code (commits of 15 October, p50t and p50u) is as follows; the results come in the next entry,
after the server has run PASTE 9w (pilot_export/p50_pastes.txt).

The missing issues. The 134 were classified from the archive's item records on 14 October (the queries ran from the
Mac): 13 answered HTTP 500 and hold real scans (a Comic Book RAR and a text PDF each: retried); one is a lending book
(HTTP 401, "I Cheated Russia's White Death": set aside); 120 have neither a JP2 zip nor a PDF — 21 are Comic Book ZIP
or RAR scans (about 11.5 GB: fetched); 7 are loose page images (two of them bound volumes of 906 and 1,621 images,
ArgosyAll-StoryWeekly and Astounding_Stories_of_Super_Science_1931, set aside; two Love Story items of 350 images each,
phantom_detective_4104_orig with 116, and two items of 92 and 138 images that looked like illustration sets on 14
October — the script's rule decides these by the file names: 20 or more images whose names carry page numbers in
sequence are a scan and are fetched, images named by title are illustrations and are set aside); 83 hold
a cover or illustrations only (set aside: not a scan); 3 are zips of unknown content (one "Generic Raw Book Zip" and
captain_hazzard_3805, fetched; New_Detective…rtf.r, text, set aside); 2 are DjVu only (Punch 1920: set aside); 2 have
no images (Amazing_Stories_Annual, Amazing_Stories_Checklist.mht: set aside); 2 had no metadata when asked (queried
again by the script). The downloader (pipeline/s01c_fetch.py) now takes a Comic Book ZIP or RAR, a raw zip of page
images, or loose page images (20 or more, numbered in sequence) when an item has no JP2 zip and no PDF, in that order
of preference, and
images them with the same thumbnails and page files as before; a selftest covers the file choice and the imaging of
the new kinds. scripts/missing_issues.py does the classification from the run's events and the archive's records and
writes config/corpus_set_aside.json (the issue, its archive identifier, the class, the reason; "decided_by": Heejin,
14 October); corpus_lib.set_aside() keeps every stage off those issues, the run page and --status count them, and
--retry-failed --issues clears the failure marks of the named issues only. The approved list and its fingerprint are
untouched: the set-aside issues stay in the list and are reported as such in the datasheet.

The verification sample (plan section 6.1). scripts/draw_verification_sample.py draws 150 assembled, non-set-aside
issues stratified by decade of the cover date and genre in the plan's eight classes (science fiction, detective,
western, adventure, weird, general, romance, other; the archive's rarer filings fall in "other", the filing as given is
kept on every issue; the format, pulp or digest, is "unknown" for every item in the archive's metadata and so cannot
be a stratum): at least two issues from every non-empty cell, the rest in proportion to the cells' sizes, chosen with
seed 20261014; the reading order mixes the genres; every fifth issue of that order is read by a second reader too,
for a figure of reader agreement. On the approved list this gives 58 cells and 30 second-reader issues; the sample is
written to config/verification_sample.json and committed (it is part of the study's record). The website's
Verification page (/verify/sample, v0.22.0) lists the issues in reading order with what is done on each (records
verified of the records the machine made, by whom), a checklist for the readers (every piece on the contents page has
a record starting on the right page with the right title and author; every record holds its own boxes and nothing
else; the roles are right; serial parts carry their part; mark VERIFIED when right or fixed; OCR misreadings inside a
box are not this sample's concern), progress by decade and genre, and the assembly's accuracy by stratum once
pipeline/s11_corrections_audit.py --sample has run (a record is "exact" when the person left it as the machine made
it, "same boxes" when only a role or a field was corrected; the strata are weighted by their share of the corpus).

Text reuse at corpus scale (docs/reuse-at-scale-plan-2026-10-14.md). The three stages have run only on the pilot's
stories; the corpus has about 67,000 stories and half a billion words. The plan: an engineering dry run of the stages
rebuilt for the corpus, on the corpus as it stands, with everything written under data/reuse/corpus_dryrun/ and every
page that shows it carrying the line "engineering dry run of 14 October 2026 onward; not the study's numbers" — the
Registered Report rule and plan section 3.3 keep the study's numbers for the frozen corpus, version 1.0, after the
protocol's acceptance (manuscript CHR-2026-0073), when the same code runs again. The exact-reuse stage is rebuilt first
(pipeline/r02_verbatim_corpus.py): the method is the pilot's step for step (k = 6, 7, 8; the commonplace cap at 50
stories; extension; de-duplication; same-issue matches set apart; the pilot's own cluster, pair and share functions),
the engine is arrays — token ids in one array, 64-bit shingle hashes sorted in numpy, shingles grouped by their actual
tokens, seeds reduced to runs on their diagonals and extended in vectorized batches, the candidate seeds bucketed on
disk by story pair when there are more than 50 million. Its selftest runs both engines on fabricated corpora (planted
passages shared by two to six stories, repeats inside a story, same-issue pairs with shared regions, commonplace
shingles, a weak hash with many collisions, the on-disk bucket path) and requires identical matches, clusters, pair
statistics and story shares; --pilot-check does the same on the pilot's stories on the server. Two limits of size,
recorded in the stats: every match is counted and kept as arrays (the journal's estimate of 22 September is 200
million story pairs with a six-word match at the pilot's rates), but the files with text and the clusters hold the
matches of ten tokens or more (the pilot's own length bins start a new bin at ten); cluster members carry 300
characters of text instead of 1,500. The next stages (r04 near-verbatim with GPU embeddings and an approximate index;
r05 the background on a stratified pair sample; the site's pages for the corpus set) follow in that order.

Prepared for the server (PASTE 9w, in tmux p50t): the classification run with the archive's records, the set-aside
file written, the failure marks of the fetchable issues cleared and the corpus run started again (the reading server
on GPU 0 is up; the follower on GPU 2 links the issues as they are assembled); the verification sample drawn; the site
restarted on v0.22.0; the pilot check of the new engine, then the snapshot of the story set and the passes at k = 6,
7, 8. Open after that: whether a RAR tool exists on the server (bsdtar, 7z or unrar; without one the Comic Book RAR
items fail at the imaging step and stay set aside — a sudo install is Heejin's call); the reader guide for Sujin and
the start of the reading; the floor for the cluster files (ten tokens) and the pages for the corpus set, to be put to
Heejin with the first numbers.

### 2026-10-15, 10:45 KST — p50t and p50u ran on the server: the missing issues classified, the sample drawn, exact reuse over the whole corpus in nine minutes

Heejin ran PASTE 9u–9x between 09:45 and 09:55 KST (commits 16e6bf0, 31bd57e, a894cbd; the server fast-forwarded, no
tracked file differing; the r02 selftest passed on the server; numpy 2.2.6, Python 3.10.12). The work script (tmux
p50t, data/corpus/p50t-2026-10-15/work.log) ran from 09:49:01 to 10:09:46.

The missing issues. The script classified the 134 from the archive's item records (fetched once, 0.4 s apart, cached in
data/corpus/p50t-2026-10-15/archive_metadata.json): 94 set aside (89 not a scan, 3 without images, 1 bound volume, 1
restricted), 40 to fetch or retry (25 comic-book archives, 2 zips, 13 passing failures); the failure marks of the 40
were cleared and the run started again at 09:52 (by 10:13 it had downloaded 35 and imaged 25 of them). A look at the
records behind the classes showed three things the first rules got wrong, and the rules were refined the same morning
(p50v, PASTE 9y–9aa): (1) five items hold several issues each — Astounding Stories 1932 (eight monthly comic-book
archives and 1,169 page images), Astounding 1933 (two archives, "January and March 1933"), Astounding Science Fiction
1940–1942 (18 archives), Fantastic Adventures 1948–1950 (30 archives), and the 1931 volume already set aside (1,621
images) — fetching one archive of each would have put one month's issue under a year's record; two or more comic-book
archives with different names, 600 or more page images, or a title with a year range or two months now mean "several
issues in one item", set aside; (2) the two Love Story items of 15 May 1926 (the same upload twice) are flip-book
exports whose 100 pages sit beside 100 thumbnails and 60 icons, numbered 1–148 with 48 pages missing (6–9, 56–99): the
page rule now takes the largest group of numbered files in one folder (the downloader's own rule, so what is fetched is
what was classified), and a sequence with a fifth to a half of its numbers missing is an incomplete scan, set aside;
(3) two illustration sets (Walter M. Baumhofer, 138 images; Peter Driben, 92) whose file names carry years are not
scans (numbers that are all years do not count as page numbers). With the refined rules on the cached records: 99 set
aside (87 not a scan, 6 several issues in one item, 2 incomplete scans, 3 without images, 1 restricted), 35 to fetch
or retry (20 single-issue comic-book archives — 11 ZIP, 9 RAR, 1.5 GB — 2 zips, 13 passing failures). The server has
no RAR tool (bsdtar, 7z, unrar all absent): PASTE 9z puts the free unrar in ~/bin without sudo; until it is there the
nine RAR scans fail at the imaging step. The reading of the fetched issues waits: the reading server on GPU 0 is down
and the card is held by another project's job until about 16 October, 10:00 (Heejin, 15 October: "GPU 0 is used by
another project. The work will end in 24 hours. Let's start using it after that."); PASTE 7i starts the server again
then, and the run's workers go on by themselves.

The verification sample: 150 issues drawn from the 7,306 assembled (seed 20261014), 58 cells, 30 read by a second
reader; by decade 1890s 14, 1900s 12, 1910s 14, 1920s 22, 1930s 24, 1940s 23, 1950s 25, undated 16; by genre science
fiction 27, other 22, adventure 19, detective 19, western 19, general 18, weird 15, romance 11. The site is v0.22.0
(/verify/sample is a workroom page, so it asks for the login). The population is the assembled issues of 15 October;
the few issues fetched afterwards are not in it, and that is recorded in the sample file.

Exact reuse over the whole corpus (the dry run; data/reuse/corpus_dryrun/). The pilot check on the server's pilot
stories (78 stories, 625,352 tokens) gave the pilot's own numbers at k = 6, 7, 8 (423, 36 and 3 cross-issue matches;
211, 30 and 3 clusters). The snapshot: 63,041 stories (type story, 50 tokens or more) of 6,828 issues from the 7,306
export files, 423,431,756 tokens, 668,471 distinct, 2.3 GB of text, 38 s in 32 processes. The passes: k = 6 in 529 s
(the engine 294 s): 89.5 million shared shingle positions, 36.6 million shingle groups, 104 million seeds on 43.9
million diagonal runs; 43,714,611 cross-issue matches and 137,731 same-issue; 11,942 commonplace shingles skipped
(in more than 50 stories); 38,343,399 story pairs with a match; 62,138 of the 63,041 stories with some match — the
six-word stock phrases reach almost every story; by length 43,132,531 matches of 6–9 tokens, 288,859 of 10–19, 142,923
of 20–49, 66,852 of 50–99, 64,420 of 100–499 and 19,026 of 500 or more; the longest 39,517 tokens; 103,143 clusters
over the 582,080 matches of ten tokens or more (9,243 with three or more witnesses). k = 7 in 226 s: 9,369,996
cross-issue matches, 3,375 commonplace, 8,352,330 pairs; k = 8 in 209 s: 2,324,357 matches, 2,084 commonplace,
1,748,638 pairs; the matches of ten tokens or more are nearly the same set at every k (585,428 and 589,307 written).
Region families: every story its own (no two records of one issue share a region key in the corpus assembly), so
the same-issue matches are repeats, not shared-region duplicates.

What the numbers say, for the record and not as findings: (a) the 19,026 matches of 500 tokens or more and the
longest of 39,517 are whole stories present twice or more — reprint magazines and British editions, but also the
archive's duplicate items of one issue under several names (the journal's open item "a duplicate check by text for
issues the archive holds under several names"): the pair table (machine_k6_pairs.npz: 38 million pairs with their
cover) now gives that check its data — pairs with cover near 1.0 on both sides and the same magazine and date are
duplicate items, to be resolved before any analysis counts them as reuse; (b) the commonplace cap at 50 stories
removed 11,942 shingles at k = 6 and left 43 million short matches: the journal's estimate of 22 September (200
million pairs with a six-word match at the pilot's rates) was high because the cap bites harder in a corpus of 63,000
stories; (c) the ten-token floor for the text files and the clusters holds 1.3 percent of the k = 6 matches and every
match of 20 tokens or more. Open for Heejin: that floor (ten tokens, the pilot's own bin boundary) for the cluster
files and the pages; the duplicate-item check before the study's run.

### 2026-10-16 to 17 — the project taken over by Sujin Kang; p50w and rules 2.7 installed; the near-verbatim stage over the corpus (p52)

Heejin Kim handed the project to Sujin Kang on 16 October; her decisions since then (TAKEOVER-TODO-2026-10-16.md in
the folder). Every command was run by Sujin from pilot_export/p52_takeover/p52_pastes.txt.

The server rebooted on 15 October at 18:01. The site came back on 16 October at 15:33 (an @reboot line now starts
it); the site refresh, the model-check follower, the embeddings and the containers pulp-llm-8023 and
surya-vllm-8020 (run with --rm) did not. After the reboot the lab's citl containers took GPU 0 and GPU 2; GPU 1
holds the lab's vllm-qwen3-14b. Sujin's decision: this project uses GPU 3, and the embeddings run on a card.

Code: p50w (written on 15 October by Heejin's session and left uncommitted) and p51 (rules 2.7, toc_folio) committed
as 790d79b and 4260f49 (the git name on these two is "XZ", an old setting on Sujin's Mac); the set-aside list as
370d9da. The missing issues classified again with the p50w rules: 121 reviewed, 99 never downloaded and set aside
(87 not a scan, 6 bound volumes, 3 without images, 2 incomplete scans, 1 restricted), 22 to fetch or retry. The
duplicate check: 3,549 same-text story pairs and 519 issue pairs (408 another magazine: an edition or a reprint; 46
the same issue under two identifiers; the rest the same magazine under another date). Site v0.23.0.

The near-verbatim stage over the corpus (engineering dry run of 14 October 2026 onward; not the study's numbers).
The processor's partial embedding run of 15 October (334 of 846 chunks, fastembed) was moved aside so that all
vectors come from one run; bge-small-en-v1.5 through transformers on GPU 3 inside vllm/vllm-openai:latest (the
container needs USER and LOGNAME: the image has no name for uid 1001) embedded 16,904,488 passages in 2,990 s.
Neighbours (20 each, exact, torch on GPU 3) in 3,130 s; 381,942,102 candidate regions in 96 buckets; alignment in 60
processes: 362,699,077 candidates, 9,244,241 alignments at the keep score. Report: K=5 196,283, K=10 201,565, K=20
212,200 cross-issue alignments, about 11,200 clusters of 40 or more columns at each K. Review set (r06) 20261017_005345:
275 items in 11 strata. Background (r05): 36,286,370 matched pairs (exact k=6 or near-verbatim K=20, both dated) and
two samples of about 480,000 unmatched pairs, standing for 1,855,043,955 cross-issue pairs; 6,597 s.

Rules 2.7 on the corpus: the read-only report (10b) stopped at 3,500 of 7,316 issues. Three issues kept toc_folio
busy for hours: mammoth_detective_1946_07_4194607beb and naked_screaming_terror_undated_sue3gorbag have OCR lines of
thousands of characters (15,702 on one page), and new_worlds_1949_49akv10jpg a line of spaced leader dots that does
not end in a number; the row patterns' nested repetition took exponential time on them. toc_folio 1.1: lines longer
than 300 characters are not read as rows, and the leader groups of the two-entries pattern are a single character
class. On the 217 layout issues of the development and test sets the readings are identical except one, which gains
a true entry (wonder_stories_1936_01: "THE MAD WORLD by A. L. Burkholder..... 800"); the three issues now take 0.1 to
0.3 s. A regression test was added (242 tests).

### 2026-10-17, 11:20 KST — rules 2.7 over the corpus (10b) and on the verification sample (10c)

10b, the read-only comparison of the contents-page reading of rules 2.6 and toc_folio 1.1 over every assembled issue
(data/corpus/toc_folio_report.json; 117 s on 16 processes): 7,316 issues, 3 errors (circled digits read as page
numbers; toc_folio 1.2 fixes them). A contents page found: 5,726 issues with 2.6, 6,171 with 2.7; a contents page with
entries: 5,009 and 6,099. Entries: 60,880 and 78,385; of the 2.7 entries 62,698 are confirmed on the page they point to,
911 were found by title and by-line, 10,524 rest on the page number alone. 26 contents pages are a book's chapter list.
The folio map changed the page offset of 52 issues; 633 issues have inserted or missing leaves and 1,172 leaves are out
of order.

10c, the 150 issues of the verification sample re-assembled with rules 2.7 by Codex's protected upgrade
(scripts/upgrade_continuity.py; none held, none skipped): 138 complete, 12 refused with "cannot safely retain
cross-issue metadata for <record>". The 12 keep their 2.6 assembly. In the one looked at (weird_tales_1949_11, record
a019) the 2.6 record carries the contents entry "64. Final Blackout" (a serial part) over pages 23–25, which hold a
Jules de Grandin story: 2.7 no longer makes that record, and the upgrade will not drop its serial metadata without a
rule for it. The twelve: 10_short_novels_magazine_1938_10_1n01193810, amazing_stories_1926_10_ctober1926,
all_story_weekly_1914_05_0119140502, argosy_1919_09_90913ibcbc, blue_book_1921_11_4n01192111,
death_at_sea_by_richard_sale_1940_athsea1940, silken_threads_a_detective_s_1890_ardner1890,
strange_stories_of_coinciden_1891_redway1891, swift_nick_of_the_york_road__1913_3backcover,
the_complete_sherlock_holmes_1891_holmesvol2, weird_tales_1949_11_rgon776sas, hammer_s_house_of_horror_undated_sproutscan.
Sujin's reading of the sample starts with the 138; the 12 wait until each is looked at.

### 2026-10-17, 12:45 KST — the corpus run on GPU 3; archives read by their first bytes

The reading server ran on GPU 3 (surya-gpu3-8020, the recipe of PASTE 7i) and the corpus run started again at 11:23
with the set-aside list of 16 October (99 issues). It ended at 12:21 with "nothing left to do": 7,329 issues read and
assembled (23 more than on 15 October), 978,178 pages read. Twelve issues failed: four downloads of comic-book
archives stopped with "File is not a zip file" (wonder_stories_1935_04_6n11193504, h65r4ewsx2_1931_07_8019310718,
crack_detective_stories_1949_05_inationdpp, ten_detective_aces_1945_11_inationdpp), and eight .cbr archives could not
be unpacked with unrar (exit 10: argosy_1897_03_oundvolume, astounding_1934_11_4n03193411,
astounding_1942_04_04dtsg0318, wild_west_weekly_1907_12_1227tousey, griffit_m_300_years_hence_undated_yearshence;
exit 3: astounding_1934_03_3n01193403, spaceway_british_ed_1954_02_1n02195402, other_worlds_undated_5gorgon776).
The archive's file names do not say what the files are: s01c chose zip or RAR by the extension, so a .cbz that is a
RAR failed at the download check and a .cbr that is a zip failed at unrar ("no files to extract", exit 10). s01c now
reads the first bytes (archive_kind: zip, RAR or 7z) at both steps; the self-test has a zip named .cbr. Exit 3 is
unrar's checksum error: those archives may be damaged; the retry shows which.

### 2026-10-17, 17:35 KST — rules 2.7 over the whole corpus (10d)

10d, the protected upgrade over every assembled issue not yet on 2.7 (7,168 selected, 8 workers, 16:00–17:35): 6,533
installed, 635 refused with "cannot safely retain cross-issue metadata for <record>", all kept on 2.6. With the 169
already on 2.7, 6,702 of the 7,337 assembled issues are on rules 2.7 (91.3%). D5 sorted the 635 refused records by
what the refused record carries (data/corpus/p52-2026-10-16/refused_2.7.json): 347 a house or advertisement record
flagged as quoting a story (A), 159 a story whose serial information came from the contents line only (B), 27 a
serial marker read from the page and unlinked (B2), 96 a serial part linked to another issue (C), 5 a work identity
or title only (D, E).

### 2026-10-18, 14:30 KST — what the refusals were made of; one guard rule; six contents layouts (rules 2.8)

All 634 refused issues were fetched through the data door into Sujin's session and re-assembled there with 2.7, and
every record the guard refuses was traced to the new records holding its boxes (988 records; an issue stops at its
first refusal). Kind A (639 records) is 2.6's error almost throughout: a block cut out of a story or a book as "a
house announcement quoting a story" (its title a sentence of the story), which 2.7 puts back. Kind B (170) is the
guard protecting a better 2.6 reading: in those issues 2.7 read the contents line worse than 2.6 — the title line
dropped and the author's name taken as the title, so the story record had no title to match ("B. Wallis", no author,
typed poem, 187 boxes; Weird Tales 1937-12, 12 of 14 entries). The fault is in toc_folio 1.0 (p51), not in 1.1 or
1.2: the three versions were run on the same issue.

One rule was added to the guard (scripts/upgrade_continuity.py, absorbed_excerpt_inference): an old advertisement or
house record flagged as quoting a story, with no work identity and no serial, whose every box is retained in the new
assembly and which no advertisement/house record holds whole, has its excerpt flag retired into the audit when the
flag was local only (no story in the corpus matched it) or when every box is still advertising, house or contents
matter (the cross-issue pass re-matches those). A text-matched block absorbed into a story, feature, poem or letters
record stays refused. Over the 634 refused issues, whole-issue with validation: 175 pass (300 records retired), 459
still refuse. Codex's tests pass unchanged; six tests were added from the refused records' shapes.

Six contents layouts were fixed in toc_folio (1.3; the assembly stamps rules 2.8; the grouping of boxes is unchanged):
a title line ending in its leader with the author and the number on the next printed line (Weird Tales 1937-12);
"TITLE.....AUTHOR..... 689" in a cell list, which deglue broke after the leader (Argosy 1909-11); two-line heading
boxes "Title ....." over "AUTHOR 102", and a department's description carrying the number (Adventure 1947-09); a name
with its own leader under the title (Western Story 1935-07); a year in a title taken for a page number (Adventure
1928-06). On 150 of the refused issues the fixes change 33 entries in 6 issues, every one to the printed reading, and
leave the other 1,999 entries as they were; against 2.6, entries read with the author as the title fall from 29 to 3.
Three tests from the real pages (253 tests). Next: 10e, scripts/toc_entries_report.py over the corpus (read-only: how
many installed issues carry the fault, which entries change); 10f, the upgrade to 2.8 over the whole corpus; then
run_corpus.py --link, the cross-issue pass the upgrade does not do. The rules are written out in
pilot_export/p52_takeover/rules-p52-2026-10-18.md.

### 2026-10-18, 14:40 KST — 10e: the installed contents readings against toc_folio 1.3

scripts/toc_entries_report.py over the 7,347 issues with an assembly (176 s on 16 processes; data/corpus/p52-2026-10-16/
toc_entries_report.json): 76,559 installed contents entries against 78,870 read with 1.3; 73,288 the same. Of the 6,702
issues on rules 2.7, **137 change their reading**: 143 entries that 2.7 read with the author as the title now have their
title, 146 more are split into title and author ("by Isaac Asimov" / none → "THE DEEP" / Isaac Asimov), and no entry is
read with the author as the title any more. Of the 614 issues on 2.6 (the refused), 561 change, as 2.6 and 2.8 read
differently; in 39 of them 1.3 still reads an author as a title (section heads above a by-line: "SERIALS" / R. G. EMERY;
"(Two Part Serial—Conclusion)" / Skidmore) — left for a later round. One regression found and fixed before the 2.8 run:
in three issues of Avon Fantasy Reader (six scans) 1.3 took the publisher's list of titles at the back of the book,
laid out and labelled like a contents page, for the contents; at the back of an issue only a page that says CONTENTS
in words is read as one (patch 0008; a test from that book).

### 2026-10-18, 22:22 KST — rules 2.8 over the whole corpus (10f); the cross-issue pass (P52r)

10f, the protected upgrade to rules 2.8 over every assembled issue (7,337 selected, 8 workers, 14:52–22:22): 6,896
installed (94.0%), 441 refused, 0 skipped. 439 of the refused are issues the 2.7 upgrade had refused (they keep 2.6);
two were on 2.7 and stay there (weird_tales_1929_12_6192912sas, weird_tales_1938_04_04atlpmurf: their records changed
shape between the two readings). 440 refusals are "cannot safely retain cross-issue metadata for <record>", one
"conflicting excerpt_of in merged nonstory metadata". Of the 634 issues refused on 17 October, 195 passed: 175 through
the guard's new case, 20 through the contents fixes alone.

The 441 by the refused record's kind, after re-assembling all of them with 2.8 in Sujin's session: 153 B (a story
whose serial information came from the contents line; in 137 of 183 such records the story is still not found under
its author — the author's name read as the title where a section head or a descriptor line stands above the by-line,
the body in a feature or poem record, or the 2.6 record itself titled with a by-line; in 36 the same story is found
smaller, the old record having swallowed what follows); 162 A (an advertisement or house block whose text matched a
story in another issue, now inside a story, feature, poem or letters record: refused on purpose, 100 ad and 62
house); 93 C (a serial part linked to another issue); 29 B2 (a printed serial marker, unlinked); 4 other. No guard
rule for B: the refusals protect 2.6 assemblies that are better than the 2.8 ones in those issues; the release of B
is another round on the contents parser (the "section head or descriptor line above the by-line" layouts, 39 issues
listed by 10e) and on the assembler's start detection. Recommendation for corpus 1.0: the refused issues keep their
current assembly, recorded by backend in the manifest and the data paper.

P52r, the cross-issue pass (run_corpus.py --link) over every magazine, 22:45–22:50: 1,506 magazines linked — work
identities, serial links between issues and excerpt matches for the records the upgrade rebuilt. The upgrade carries
old links over by identity and computes no new ones; this pass had not been run after 10d either. The site refresh
then republished the records (23:38: 7,298 issues current, 39 held for human corrections; 326,165 records, 66,444
stories, 18,300 authors; 295 records verified by Sujin's reading so far). Weird Tales 1937-12, refused at 2.7 for its
author-as-title reading, is on 2.8.

### 2026-10-19, 11:30 KST — decisions 1 and 2: the corpus is pulp magazines; the sample is read at two levels

Sujin's morning examination of the project (project-status-2026-10-19-EN.md in the share) found the verification
sample on the critical path — six issues worked through in two days at about an hour each, 150 to read — and standalone
books in the corpus and in the sample (the first slot of the reading order is H. G. Wells's *The Plattner Story and
Others*, Methuen 1897; also *Silken Threads* 1890 and *The Shadow* 1931). Sujin decided both recommendations as proposed
(docs/decisions.md, 2026-10-19).

**Decision 1, the candidate rule (scripts/nonmagazine_candidates.py, rule 1.0).** Every selected issue is scored on
signals from the archive's item record (a Title - Author (Publisher, Year) title; a book's description with page count
and binding; no volume or number; subjects; a modern date; an index, comic or dime-novel name), the selection (a
"magazine" of one issue; a book's or a paperback series' name) and the assembly's reading (a chapter list for a
contents page; no by-lines on the pages; chapter heads; entries without authors; a dime novel's shape of 40 pages or
fewer before 1925), with strong magazine evidence counting against (a volume-and-number title, by-lines on several
pages and authors on the contents entries). Each signal carries a class; the item's class is the one with most points,
and the proposal is EXCLUDE at 5 points, LOOK at 3. Run in Sujin's session on a mirror of the server's meta.json and
analysis.json for all 7,440 selected issues (every file fetched through the data door; 8 s to score): **proposed
EXCLUDE 526** — 278 books, 195 dime-novel and nickel-weekly numbers (Wild West Weekly, New Nick Carter Weekly, Pluck and
Luck, Tip Top Weekly, Frank Reade Library, Blue and Gray Weekly, Chums ...), 42 single stories extracted from magazines,
9 indexes and portfolios, 2 comics — **LOOK 374** (96 dime novel, 71 not a fiction magazine — Physical Culture, True
Detective, the newspaper supplements —, 69 book, 59 outside the period, 41 comic, 38 single story), KEEP 6,540. In the verification
sample: 32 proposed EXCLUDE, 11 LOOK, 107 KEEP. The review table (nonmagazine_candidates_2026-10-19.md and .csv in the
share's p52_takeover folder) lists every EXCLUDE and LOOK row with its signals and evidence and every sample issue;
Sujin confirms item by item (or class by class, marking exceptions), scripts/set_aside_nonmagazine.py applies the
filled table to config/corpus_set_aside.json (class "not a magazine issue", the kind and signals kept with each entry)
and scripts/draw_verification_sample.py --replace redraws the sample slots the excluded issues held, within their
decade-and-genre cells, keeping each slot's order and second-reader mark; a cell the exclusion empties (every 1910s
western in the corpus was a Wild West Weekly number) gives its slot to its decade, so the sample keeps its 150 — on the
proposal as it stands, 32 slots would be redrawn, 13 of them from outside an emptied cell. Set-aside issues now leave the publication
(s13), the export (r00: their export files are removed) and the explorer's issue list; their files stay on disk. The
same script is to be run on the server for the record (P52t).

**Decision 2, the contents check (site v0.24.0).** /verify/contents/<issue>: the contents page's scan beside the entries
the assembly read from it, each with the record the machine made for it (found by the record's own contents entry,
else a reading record starting on the entry's scan page or one off, else by title), and a verdict per entry — right as
made, fixed, missing, not a piece — with the entries the parser missed counted below; saved as one line per check in
data/review/contents_check.jsonl under the reader's name. The Verification page gains the contents column (CHECK /
checked, with the issue's right-over-listed count and the readers), the figure of the study (listed pieces assembled
right, over all checked issues, against Supplement A's 95 percent; by decade and genre; the first reader's check
counts, a second reader's agreement is reported), a "contents check to do" filter, and the checklist in two levels.
docs/verification-reader-guide.md has the level-1 section. 272 tests.

### 2026-10-19, 14:30 KST — the B refusals looked at again: two more guard cases (Weird Tales 1949-11)

Sujin opened sample issue #7, Weird Tales 1949-11, for verification and found the stories cut to a page or two each.
The issue is one of the 441 kept on rules 2.6 by the upgrade's guard: 2.6 had taken a book club's numbered list on
page 5 ("*41. Minions of the Moon—Beyer", "74. Fox Woman", "32. Works of M. P. Shiel") for the contents page and
placed story starts from it, so "32. Works of M. P. Shiel" owned pp. 8–18 of Allison V. Harding's *The Underbody* and
"66. Legion of Space" pp. 25–31 of Seabury Quinn's *Conscience Maketh Cowards*. The 2.8 candidate reads the real
contents page and the eleven stories whole; the guard refused it because the seven fake records carry a serial read
from the contents and a work identity made from the list item, which no candidate record can take over by identity.
Yesterday's survey of the B refusals (one record per issue; "found under its author") undercounted this: many of the
2.6 records in B have no author at all, their title being the by-line.

Every refused metadata record of the 441 issues surveyed again against the cached 2.8 candidates (560 records: 231 A,
183 B, 109 C, 35 B2, 2 other). Of the 183 B records, the candidate's main holder covers all the old boxes in 121 and
sixty percent or more in 168; the holder is a story in 139, a feature in 40. By the relation between the old title
and the holder's: the old title is the holder's by-line ("By David M. Speaker") 21, a contents line read whole with
the holder's title inside it ("THE LUNAR CONSUL by Sidney Patszer", "DAWN TO DUSK a new novel by Eando Binder") 36,
the holder's author alone 5, a list item or a bare number 9; the same title with a different cut 36 (the old record
had swallowed the pieces after it, or was a two-box contents stub the candidate grew into the whole piece); the same
author under another title 13; the reverse fault — the candidate titled the piece with the author's name, where a
section head stands above the by-line — 14; no relation (the candidate lost the start, or titled the holder "May",
"Contents for July") 45.

Two named cases added to scripts/upgrade_continuity.py, in the style of Codex's: **retired_contents_misreading** — the
old record is a reading record whose serial came from the contents, with no link to another issue and no external
provenance; every box is retained; and either the old title is junk (a numbered list item whatever follows it; a bare
or roman numeral, chapter number or section word with no author read) or one candidate record holding sixty percent
of the boxes reads the piece with its own printed title and author and the old title is its by-line, its author, or a
contents line containing its title or author. The inference is retired into the audit; the candidate's own contents
reading supplies serial and work identity, and the cross-issue pass recomputes the links. The reverse fault, links,
provenance, a lost box and a holder under sixty percent all still refuse. **transferred_same_title_trimmed / grown** —
the same title (a trailing part marker apart, then with both authors read and equal), same author or one side unread,
and either the candidate record holds a subset of at least a quarter of the old boxes with every other old box in
furniture or in a reading record with its own printed title and author (a box cut off into an advertisement or an
untitled record refuses), or the old boxes all lie inside the candidate record; the old metadata is transferred as on
any match. Codex's tests unchanged; 10 new tests (test_continuity_contents_misreading.py); 282 pass.

Measured on the 441 refused issues in the session (the cached candidates): **90 pass** — 65 B with work_id, 3 B, 16 C
(an issue's other records), 6 B2 — 56 misreadings retired, 30 trimmed and 30 grown records transferred, 12 absorbed
excerpt inferences retired; 351 stay refused (163 A text-matched, 85 B, 77 C, 23 B2, 3 other). Of the sample's six
2.6 issues, Weird Tales 1949-11 (#7) and Argosy 1919-09 (#14) pass; Amazing Stories 1926-10 (#103: the candidate
merged a serial's conclusion into the editorial before it) and All-Story Weekly 1914-05 (#145: the candidate titled
the pieces of a 100-page novel "May", "The Cavalier (Weekly)") stay on 2.6, rightly. P52v runs the upgrade again on
the server (a plain run selects the issues below 2.8) and then the cross-issue pass.

Sujin's proposal in the feedback — bound each piece by the next piece's start page from the contents, with other rules
for advertisements and features mixed in — is what the assembler does when the contents page is read right (pieces
are placed from the contents entries and run to the next start; running heads and continuation notices are checked);
the fault here was the choice of the contents page, fixed in 2.7/2.8 (toc_folio's find_contents; cf. the Avon catalogue
of 18 October), and the guard's caution kept the old reading. Her two further rules of 17–18 October stand.

### 2026-10-19, 15:10 KST — the non-magazine review by clicks (site v0.25.0); P52v and P52w done

P52v (13:03–13:39): the upgrade over the 441 issues below rules 2.8 with the two new guard cases — **91 installed, 350
refused** (as measured in the session: 90/351); the cross-issue pass after it, 1,506 magazines. Weird Tales 1949-11 is
on 2.8 (eleven stories whole); its republication was held by the publisher because Sujin had made 358 actions on the
old, misread records that morning — the annotation log was moved to data/assembly_archive/2026-10-19_wt_1949_11/ with
the old live records, and the issue republished (P52w, 14:34; the site now 7,297 current, 40 held).

Sujin asked for the non-magazine review by clicks rather than in a CSV: **/review/nonmagazine** (WORKROOM → "Not a
magazine?") lists the candidate table's rows — proposed EXCLUDE or LOOK, and the sample's issues — with the cover
thumbnail, the selection name and the archive's title, date, pages, class and score, the signals (their meaning on
hover) and the evidence, a link to the issue and to archive.org; EXCLUDE / KEEP / ? per row with a note, and one
button to mark every undecided row of the current view (filters: proposal, class, decided or not, in the sample).
Every click is a line in data/review/nonmagazine_review.jsonl under the reader's name; the latest per item counts.
scripts/set_aside_nonmagazine.py --decisions reads the log joined to the candidate table (the CSV route stays).

### 2026-10-21, 12:30 KST — kinds, not exclusion: the book mode of the assembler (site v0.25.0, book mode 1.0)

Sujin, reviewing the candidates: books are not to be thrown out but told apart from magazines, and the assembler
cuts a novel's chapters into stories. Decided (docs/decisions.md, 2026-10-21): two kinds, magazine and book (the dime
novels with the books); what leaves the corpus leaves for a reason (excerpt of a magazine, not English, not a text,
comic, outside the period); the book mode now.

**The book mode** (pipeline/s08_book.py; s08 reads the kind from config/corpus_kinds.json or the issue dict): a second
pass over the engine's records for an item of kind book. A novel — the contents page a chapter list, most entries
chapter-numbered, or no contents — becomes one story record titled with the book's title and author, chapter heads
kept as chapter roles; what stands before the first chapter is one `other` record, the front matter. A collection —
the contents page lists pieces — becomes one record per listed piece, its boxes every reading record from the piece's
first page to the next piece's, the author the entry's or the book's. A reading record that reads like a publisher's
catalogue (prices and formats several times per thousand words, or a catalogue's title; never the holder of a listed
piece) becomes house matter. The book's title and author come from the archive's record ("Title - Author (Publisher,
Year)", "Surname, Initials - Title", "Title, by Author"; the creator field) or the selection's name. Measured in the
session on the server's layout pages: *Silken Threads* (1890) — one story, 333 pages, 29 chapters, 62,178 words (was
31 "features" and 59 chapter heads); *The Plattner Story and Others* (Methuen 1897) — the dedication as front matter,
17 stories by H. G. Wells placed from the contents (7,709 words for the title story … 9,714 for *A Slip Under the
Microscope*), Methuen's catalogue (two records, 10,500 words) as house matter. No box unassigned or owned twice.

**The review page** (/review/nonmagazine) asks for the kind or the reason by a click — MAGAZINE, BOOK, EXCERPT, NOT
ENGLISH, NOT A TEXT, COMIC, OUTSIDE, ? — with the rule's suggestion shown (book and dime novel → book; index → not a
text; comic; outside the period; single story → undecided: an excerpt is a duplicate only when its issue is in the
corpus). The 19 October exclude/keep clicks show as "earlier" and are migrated once (scripts/apply_kinds.py --migrate:
x on a Serials compilation or with "excerpt" in the note → excerpt; x or k on a book or dime novel → book).
**scripts/apply_kinds.py** writes config/corpus_kinds.json (tracked; empty until then) and the set-aside list, redraws
the sample's slots (books leave the sample too: scripts/draw_verification_sample.py --replace reads the kinds) and
runs **scripts/reassemble_books.py** (the book mode over every book, the annotation logs made on the chapter-split
records archived under data/assembly_archive/<stamp>_books/, each book published). The export carries `kind` on every
record (r00). 287 tests.

### 2026-10-21, 10:45 KST — the kinds applied; 629 books re-assembled; the contents check page fixed (v0.25.1)

P52y–P52u: Sujin reviewed the candidate table by cover and title on /review/nonmagazine and found the rule's
suggestions right; a bulk click on the dime-novel view marked 192 items "excerpt" by mistake and was corrected by
192 appended "book" lines (the log is append-only; the latest decision counts). apply_kinds.py wrote
config/corpus_kinds.json and the set-aside reasons, redrew the sample's slots, and reassemble_books.py re-read
**629 books in the book mode** (published 629, held 0; e.g. Work and Win no. 1030, 1918: one story of 17 records, 6
joined). The contents check page (/verify/contents) answered 502 on Weird Tales 1949-11: the analysis's `toc_pages`
is the list of the contents spread's scans there, not a count; read both ways now, and the route answers a message
instead of taking the site down. Site v0.25.1.
