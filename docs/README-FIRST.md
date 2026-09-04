# Read this first

This folder is the complete, self-contained record of the Pulp Fiction
Corpus project. If Heejin Kim has shared his Dropbox folder with you,
everything you need to understand, run, and continue the work is in
your hands: this repository (code and operational docs), the
development journal one level above it (the project's full history),
and the `secrets/` folder next to the journal (the actual passwords,
tokens, and keys, each explained in its README). Nothing is held back.

## What this project is

We are building a clean, research-grade text corpus of American pulp
fiction magazines (roughly 1896–1959) from the Internet Archive's scans,
and publishing the method as a Registered Report in Computational
Humanities Research (Cambridge). Dennis Yi Tenen (Columbia) writes the
Stage 1 protocol; the corpus and tools are built at Kyungpook National
University under Heejin Kim (Digital Humanities Engineering Center,
https://www.digihumeng.org). A later second stage will look for reuse of
pulp fiction text in contemporary fiction.

The corpus unit is the ARTICLE: every separately printed unit of a
magazine issue — story, serial installment, poem, feature, letters page,
even advertisement — becomes its own addressable record with its title
and author exactly as printed, its page range, and a link back to the
exact regions of the scanned page it came from. Page furniture (running
heads, page numbers) is recorded, not thrown away; anything the machine
cannot place is kept in an unsorted list. Nothing is silently dropped.

A ten-issue pilot has been fully processed (the "development set" under
Registered Report rules — see the constraints section of the handbook).
The pilot's measured results are in `docs/pilot-results.md`. The main
finding: our layout-based reading route cuts word errors to a third to a
half of the Archive's own text; the current weak point is the automatic
ASSEMBLY of segments into articles, which is why human verification on
the website is the present focus.

## The website

The working surface of the project is a website: https://pulp.digihumeng.org
It shows every pilot issue page by page (scan next to text at every
cleaning stage), the method description, timing tables, and — most
importantly — the annotation workbench, where a signed-in annotator
repairs and verifies the machine-assembled articles. Guest passcode
access is read-only; annotator accounts are approved by the admin
(Heejin). Every human action is recorded, append-only, under the
annotator's name; the machine's output is never overwritten.

## Map of this folder

    webapp/app.py            the website (no frameworks; routes, workbench, viewer)
    webapp/explore_pages.py  the explorer: overview, lists, entity pages, raw layer
    webapp/reuse_pages.py    the text-reuse pages of the site (/reuse…)
    pipeline/s00_survey.py   the survey of the archive's collection
                             (metadata only; data/survey/)
    pipeline/s01…s07         the corpus-building stages, in order
    pipeline/s08, s09        assembly v2 (the rules engine) and the
                             assembly harness; docs/assembly-v2.md
    pipeline/r00…r05         the text-reuse analysis stages (protocol
                             sections 3–4), rehearsed on the pilot
    scripts/                 setup, deploy helpers, backup server scripts
    config/pilot_issues.json the approved 10-issue development set
    config/departments.json  each magazine's standing departments (name, type, conductor) — assembly v2.2
    data/reuse/              the reuse pipeline's result files (the one
                             data folder kept in git; small JSON)
    docs/                    you are here — handbook, technical notes,
                             pilot results, implementation plan; also
                             method-reuse.md (the protocol quoted step by
                             step with the implementation) and decisions.md,
                             both rendered live at /method; assembly-v2.md
                             (the rules and how the methods compare)
    METHOD.md                the method text, rendered live at /method

The processed data itself (scans, text stages, articles, annotations)
lives on the servers under `data/`, not in this folder — it is large and
reproducible from the pipeline. The pulled result files
(`pulp_metrics.json`, `pulp_timings.jsonl`, `pulp_articles_index.json`)
and the story export the reuse pipeline ran on
(`pilot_export/pilot_stories.jsonl.gz`) sit one level above this
repository folder in the Dropbox share.

## How to continue development

1. Read `docs/handbook.md` — architecture, servers, deploy routine,
   and the operating rules that were learned the hard way.
2. Read `development-journal.md`, ONE LEVEL ABOVE this repository in
   the Dropbox share — the full story of what was asked, built,
   broken, and fixed, in order, including the requests in Heejin's
   own words. This is the "prompt thread" of the project. It stays
   out of git on purpose, as does the `secrets/` folder next to it,
   which holds the project's actual passwords and tokens with its
   own README — full continuity for anyone who has the folder.
3. Check `docs/pilot-results.md` before touching the pipeline, so you
   know what is already measured and decided.

If you are continuing the work with Claude (Cowork or Claude Code):
share this folder with the session and ask it to read `docs/` first.
The journal gives any new session the context an old one had. The
original chat threads live in Heejin's Claude account and can be
continued there; this folder carries everything the chats produced.

## Three rules that protect the project

The ten pilot issues are the only Internet Archive data we download or
analyze until the Registered Report protocol is accepted — this is a
publication-ethics commitment, enforced by an approval gate in the
downloader. Secrets never enter git or GitHub — their values live only
in the Dropbox `secrets/` folder outside this repository, so the
repository can someday be published. And on the shared GPU server, never
touch docker images, containers, or files belonging to other lab
members without coordinating first.
