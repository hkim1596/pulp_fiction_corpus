# Running the corpus build (Phase 0 and Phase 1)

Written 4 October 2026 for corpus-build-plan-2026-10-04 (Dropbox root; Team project `claude/corpus-build-plan-2026-10-04.md`).
Everything here runs on the main server (`tailab@155.230.137.46`, port 52345; the repository at
`~/shared/khj/pulp_fiction_corpus`, which is `/mnt/sda/pulp/pulp_fiction_corpus`). Heejin logs in; the commands are
pasted from `pilot_export/p50_pastes.txt` in the Dropbox folder.

## What the stages are

| stage | file | what it does | writes |
|---|---|---|---|
| select | `pipeline/s00b_select.py` | the archive's item list (from `s00_survey.py --run`, `--enrich`) → the corpus issue list, clause by clause; `--approve "Name"` writes the approval | `config/corpus_issues.json` (untracked), `config/corpus_approval.json` (tracked), `data/survey/selection.jsonl`, `selection_counts.json`, `duplicates.json` |
| download + image | `pipeline/s01c_fetch.py` | 4 parallel items; metadata, `_djvu.txt`, `_djvu.xml`, `scandata.xml`, the `_jp2.zip` master (PDF only when there is no JP2); then 2,200 px JPEG working pages and 300 px thumbnails | `data/raw/<id>/`, `data/masters/<id>/`, `data/pages/<id>/`, `data/thumbs/<id>/` |
| read | `pipeline/s02_layout_ocr.py` | Surya 0.22, one worker per GPU | `data/layout/<id>/`, `data/text/<id>/routeA/` |
| clean | `pipeline/s04_rules.py --src routeA` | the pilot's rule cleanup | `data/text/<id>/rules_routeA/` |
| lemma | `pipeline/s04b_lemma.py` | spaCy `en_core_web_sm` 3.8.0, tags and lemmas aligned to the printed tokens | `data/text/<id>/lemma_routeA.jsonl.gz` |
| assemble | `pipeline/s08_assemble_rules.py` | the pilot's assembly rules v2.3, then the cross-issue pass per magazine (serials, excerpts) | `data/assembly_v2/rules/<id>/` |
| orchestrate | `pipeline/run_corpus.py` | runs all of the above as one resumable process; deletes each master once its issue is assembled; frees working images when the disk runs short; writes `data/corpus/progress.json` | `data/corpus/state/<id>.json`, `events.jsonl` (everything, in time order), `run_info.jsonl` (code and tool versions per start), `progress.json`, `fetch_manifest.jsonl` |
| reap by hand | `scripts/corpus_reaper.py` | what is on disk, what can be freed, free it | — |

Settings: `config/corpus_settings.json` (every number the stages use). The issue list a pilot stage reads is still
`config/pilot_issues.json`; the orchestrator sets `PULP_ISSUES=config/corpus_issues.json` for the processes it
starts, so the site and the pilot records do not change. The web app's own switch is `PULP_CONFIG`.

## Why it is faster than the pilot

The pilot's s01 took about 170 s per issue: one item at a time, a 2 s pause after every file, the PDF and the hOCR
as well as the JP2 archive, and a PNG conversion of every page. The corpus downloader takes four items at once
(the archive's own guidance for bots: "limit to 4 concurrent downloads with 1 second delay"), takes the JP2 archive
only (a third of an item's bytes; measured 4 October: the JP2 zip is 0.71 MB per page at the median), writes JPEG
working pages in separate processes while the next items download, and keeps the master zipped (one file per
issue, so no flood of small files). Measured from the sandbox on 4 October: 12–15 MB/s on one connection, about
26 MB/s on three. The whole corpus is about 0.7 TB of JP2 (7,467 issues, roughly 980,000 pages), so the transfer
alone is one to three days; the imaging keeps up on six processes (about one page per second per process).
Reading on the GPUs is the long pole: about a week on two cards at the pilot's speed.

## Space (the 4 TB)

/mnt/sda holds the repository and `data/` (3.9 TB, about 3.3 TB free on 4 October); /mnt/sdb (3.6 TB) is empty.
The peak is far below 4 TB because nothing is kept that can be re-fetched:

| what | size for the whole corpus | kept until |
|---|---|---|
| JP2 masters (zipped) | ~0.7 TB if all were on disk at once | the issue is assembled, then deleted (`images.keep_master_until`) |
| working JPEG pages 2,200 px | ~0.45 TB | the corpus is frozen (`images.keep_working_until`); earlier only if free space falls under `images.min_free_gb` (400 GB), oldest assembled issues first |
| thumbnails 300 px | ~25 GB | always |
| archive text + positional OCR + scandata | ~25 GB | always |
| layout JSON, Surya text, cleaned text, lemmas, assembly | ~40 GB | always |

Because the download runs days ahead of the reading, most masters will sit on disk at once (~0.7 TB) next to the
working pages (~0.45 TB): about 1.2 TB at the peak. /mnt/sdb turned out not to be writable by our account
(PASTE 2, 4 October), so everything stays on /mnt/sda, which had 2.7 TB free that day — enough with room to
spare. (If /mnt/sdb is ever opened to us: `mkdir -p /mnt/sdb/pulp_masters && ln -s /mnt/sdb/pulp_masters
data/masters` before a download; the stages follow the link.) The reaper measures free space on `data/`
(/mnt/sda).

## The run, step by step

0. Look at the machine and write the answers into the journal: `nproc`, `free -g`, `df -h /mnt/sda /mnt/sdb /`,
   `nvidia-smi`. Set `images.processes` and `lemma.processes` in the settings to about a fifth of the cores
   (4 October: 64 cores, 503 GB of memory → 12 and 12; the reading and the downloads need cores too).
1. Code and dependencies: `git pull`; `pip install --user spacy==3.8.16` and the `en_core_web_sm-3.8.0` wheel from
   spaCy's release page (the pin is checked at start); `pip install --user internetarchive` and `ia configure`
   (Heejin types the archive.org login; optional — it makes the downloader an authenticated, better-treated client).
   `python3 pipeline/s04b_lemma.py --selftest`, `python3 pipeline/s00b_select.py --selftest`.
2. Fresh survey: `python3 pipeline/s00_survey.py --run` (about a minute), then `--enrich` (resumable; only items
   not yet enriched are fetched — the September records are kept), then `python3 pipeline/s00b_select.py`.
   Read `data/survey/selection_counts.json`; compare with the sandbox counts of 4 October (below).
3. Heejin approves. The list `config/corpus_issues.json` is generated on the server and is not tracked by git
   (a generated tracked file would block every later `git pull` on the server); the approval is the small tracked
   file `config/corpus_approval.json`, which carries the list's fingerprint, the counts and the settings. On the Mac:
   `scp rtx6000:shared/khj/pulp_fiction_corpus/config/corpus_issues.json config/` into the Dropbox clone, then
   `python3 pipeline/s00b_select.py --approve "Heejin Kim"`, commit `config/corpus_approval.json` and push, and
   `rtx update pulp_fiction_corpus`. The stages refuse any list whose fingerprint differs from the approved one, so
   a regenerated list has to be approved again. A copy of the approved list goes to the Dropbox folder
   (`pilot_export/p50_corpus_issues.json`) for the record.
4. A trial: `python3 pipeline/s01c_fetch.py --run --limit 20` (twenty issues, no GPU), look at `data/pages/<id>/`
   on the site, and at `data/corpus/state/<id>.json`.
5. The reading servers. Surya 0.22 reads through a vLLM server in a docker container; without a URL it
   spawns one itself on the GPU named by the environment variable VLLM_GPUS (default "0") with batch sizes from
   VLLM_GPU_TYPE (default "4090", a 24 GB card; "h100" sizes them for an 80–97 GB card: 104 sequences, 16,384
   batched tokens — surya's table has no entry for the RTX PRO 6000), reserving 85% of the card's memory. Surya
   keeps one spawned server per machine (a sentinel in ~/.cache/datalab/surya/), so a second server is started
   by hand with the same `docker run` command surya uses (PASTE 4b and 6 in pilot_export/p50_pastes.txt have it:
   image vllm/vllm-openai:v0.20.1, model datalab-to/surya-ocr-2, port 8021 on GPU 2, 8022 on GPU 3), and each
   reading worker is pointed at its server with SURYA_INFERENCE_URL. The orchestrator takes the URLs from
   `settings.reading.servers`, or from the environment variable PULP_SURYA_SERVERS (comma-separated), one worker
   per URL; `settings.reading.spawn_env` is what a worker passes to surya when there is no URL. On 4 October
   GPUs 0, 1 and 3 held about 89–90 GB each (other models; GPU 3 is Heejin's own) and GPU 2 was empty; the
   same evening the other project gave up GPU 0 and Heejin decided "Use only GPU 0. No GPU 2.", so the run
   reads on one card: port 8020 on GPU 0 (PASTE 4b refuses to start a server on a card holding 10 GB or more).
   A second server on GPU 3 (port 8022) comes if Heejin frees it (PASTE 6: start the server, STOP the run,
   start it again; PASTE 5 and 6 build the server list from the ports that answer). A worker whose server
   stops answering waits for it, puts the issue in hand back and marks nothing failed (events
   "reading_server_down", "reading_server_error" in events.jsonl). GPUs 1 and 3 hold two vLLM lanes of other
   work (vllm-qwen3-14b on port 8004, vllm-9b-gpu3 on port 8006); a small process of Heejin's stylometry
   project holds about 0.5 GB on every card and 1.6 GB on GPU 0 — harmless.
6. The run, in tmux so it survives the login: `tmux new -s corpus` then
   `python3 pipeline/run_corpus.py --run 2>&1 | tee -a data/corpus/run.log`. Detach with Ctrl-B D.
7. Watching: `python3 pipeline/run_corpus.py --status` (counts per stage, pages read, rates since the process
   started, hours left at those rates, free space, failures), `tail -f data/corpus/run.log`, and the site's
   `/api/<token>/health` for the disks.
8. Stopping and resuming: `touch data/corpus/STOP` (every worker finishes its current file and the process exits),
   later `python3 pipeline/run_corpus.py --run` again; nothing is done twice. Ctrl-C does the same, less gently.
9. Failures: an issue that fails a stage is retried on the next resume scan, up to the limits in
   `run_corpus.MAX_FAILS` (6 downloads, 2 of everything else), then set aside with its error in its state file
   (`--status` counts them under `given_up`). `python3 pipeline/run_corpus.py --retry-failed` clears the marks.
10. At the end: `python3 pipeline/run_corpus.py --link` once more (the cross-issue pass over every magazine), then
    the Phase 2 steps of the plan (the quality score, the paratext parallel corpus, the sample for verification).

## The counts of 4 October

On the server (PASTE 2, 17:18 KST; survey of the day, enrich records for every item) — the list that was approved:

    0 items in the collection                      28,410
    1 English or no language record                22,447   (set aside: not English 5,963; archive text not English 123)
    2 dated 21,214; undated (kept)                  1,110
    3 in the window 1890–1955                      11,501   (set aside: outside 9,713)
    4 fiction magazine                              8,511   (comic 263, dime novel 2,387, general-interest 277, non-fiction 1,173)
    5 issues in the corpus                          7,440   (duplicate scans kept as alternates 1,071; undated among the issues 589; magazines 1,582)
    md5 of config/corpus_issues.json               10bc79121c3405d6b1257d405d8e0f6a

In the sandbox earlier that day (no enrich records): 28,411 → 22,448 → 11,513 → 8,538 → 7,467 issues (1,071
alternates, 604 undated, 1,605 magazines).

The duplicate rule (same_issue in s00b): two items of one magazine are one issue when both carry a volume and
number and they agree; or both carry a whole number (#24) and it and the year agree; or the cover month agrees
and is a real month (year-only records never merge) and, where either carries a day, both do and agree; a month
alone merges only magazines that are not more than monthly (fewer than 15 distinct issues in every year). The
archive's date field is read after the title, and a date of January 1st counts as "year only" (measured: 258 of
the window's 01-01 dates stood under a title naming another month or season). The meeting deck of 23 September
counted 914 duplicates under the first, cruder rule (same magazine and cover month); the better rule finds 1,071.

Known limits, recorded: the British and the American edition of a magazine with the same whole numbers would
merge (Zane Grey's Western, British Edition — both editions are in the list under one magazine key); an undated
item is never merged; `format` is "unknown" for every issue because pulp or digest cannot be read from the
archive's records — the imaging step records the master's pixel size and the nominal dpi, and a later pass can set
the format from the measured trim size (the dpi values are unreliable: one 1904 weekly claims 96 dpi).

## Decisions taken on 4 October (Heejin)

- The green light: "I got the green light to go. Don't worry about the protocol anymore. Just keep log of
  everything, so we can publish our database and write in https://openhumanitiesdata.metajnl.com". The run
  starts now. The log is docs/corpus-build-log.md plus the machine-written records it lists (`data/corpus/
  events.jsonl`, `run_info.jsonl`, the state files, the fetch manifest, the timings, the approval file).
- The 604 undated items stay in the list (the protocol keeps them out of the dated analyses only) but download last,
  so a decision to drop the obvious modern fan magazines among them can wait.
- Pastes are given in the chat as well as in the pastes file ("Always give me paste here as well").
