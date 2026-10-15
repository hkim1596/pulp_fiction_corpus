# The text-reuse stages at corpus scale — the plan of 14 October 2026

Written by Claude for Heejin Kim after his choice of 14 October ("Build and dry-run now, label it as such"). The
protocol's analysis (sections 3 and 4 of pulp-mag-text-reuse-CHR-named.pdf) has run only on the pilot's 585
stories (the r-series, docs/handbook.md). The corpus now has about 67,000 stories and half a billion words; the
stages have to be rebuilt to that scale. This note says what changes, what stays, what it needs, and how long it
takes. The method is the protocol's; only the engineering changes.

## 1. What this run is, and is not

- It is an engineering dry run: the stages rebuilt for the corpus, run on the corpus as it stands today (before the
  freeze), so that the code is proven and the website shows reuse across the whole collection within weeks.
- It is not the study's analysis. The journal's rule for a Registered Report ("no analysis was carried out prior to
  [acceptance]") and the plan's section 3.3 say the study's numbers come from the frozen corpus, version 1.0, after
  the protocol's acceptance (manuscript CHR-2026-0073, under review). Every output of the dry run is written under
  data/reuse/corpus_dryrun/, every page that shows it carries the line "engineering dry run of 14 October 2026
  onward; not the study's numbers", and the build log records it. The study's run repeats the same code on v1.0.

## 2. The three stages, pilot design and corpus design

### r02 — exact reuse (protocol 3.1)

Pilot: a Python dictionary from every k-word shingle (k = 6, 7, 8) to its places; a shingle in 2 to 50 stories is a
seed; each seed extended left and right while the tokens stay identical; same-issue matches set apart; clusters.
At corpus scale the dictionary alone (500 million shingles) does not fit; everything else stays.

Corpus: the same steps with arrays instead of a dictionary.
1. Tokens (r01's rules, unchanged) become integer ids in one memory-mapped array (half a billion × 4 bytes ≈ 2 GB)
   with the story boundaries; tokenization runs in 32 processes (minutes).
2. For each k, every shingle gets a 64-bit hash computed in numpy; the hashes are sorted with their (story,
   position) pairs (≈ 8 GB per k; the server has 503 GB of memory). Equal hashes side by side are the seeds; a
   hash in more than 50 stories is skipped and written out, as in the pilot (MAX_DF).
3. Candidate pairs are reduced to one per diagonal (same two stories, same offset), extended in vectorized batches
   (compare token arrays stepwise, not one token at a time in Python), verified by the tokens themselves (so a hash
   collision cannot make a match), and de-duplicated by their maximal span, as in the pilot.
4. Same-issue matches, the region families, the clusters (union-find), the pair statistics, the story shares and
   the length histogram: as in the pilot, written in the same file shapes, so that the site's reuse pages read them.
Expected: hours, not days; the number of matches is unknown until it runs (the commonplace cap decides it).

### r04 — near-verbatim and rewritten reuse (protocol 3.2)

Pilot: passages of 50 words, step 25; bge-small-en-v1.5 embeddings; the K nearest passages in other stories (K =
10; 5 and 20 as sensitivity) plus every exact match as a candidate; word-by-word local alignment of each candidate;
keep rule 20 columns and identity 0.60; same-issue alignments set apart; the planted-reuse copy (r03) scores recall.

Corpus: about 20 million passages.
1. Embeddings on a GPU (GPU 2, free since the model check finished; GPU 0 once the last issues are read): about an
   hour with the same model; on the processor alone (fastembed, 64 cores) about a day. Cached as before.
2. Nearest neighbours with an approximate index (faiss, inverted lists) instead of the pilot's exact search; the
   index is built once (tens of minutes) and queried for all passages (hours on the processor).
3. Alignment only for candidates above a similarity floor (set from the pilot's own validation set, r06), so that
   the alignments run in the tens of millions at most, in 60 processes (hours). The alignment code is the pilot's.
4. The validation set (r06) is drawn again from the corpus candidates for Sujin's review, so that the keep rule is
   re-set on corpus material before the study's run, as the protocol says.
Expected: one to two days of running.

### r05 — the background (protocol 4.1)

Pilot: one row for every pair of stories (41,041 pairs), with reuse extent, dates, years apart, topic similarity
on reuse-masked text, shared author / magazine / publisher / genre; survival curves by stratum; the stratified
sampler run as a check; a first two-part hierarchical model.

Corpus: 67,000 stories make 2.2 billion pairs; the table cannot be written. The protocol's own answer is the
stratified sampler: every matched pair (from r02 and r04) gets its row; the background is a stratified random
sample of unmatched pairs (about two million, drawn with a fixed seed within strata of later decade × years apart
× topic quartile), each row weighted by its stratum's size, and the survival curves, the placements and the model
are computed on the weighted sample. The pilot already ran the sampler against the full table to check it; that
check is the warrant. Topic similarity (TF-IDF cosine) is computed for the rows only; story-level embeddings (the
mean of the passage vectors) give the second column as before.
Expected: hours.

### The website

The reuse overview, the cluster list and the cluster pages read a "set"; the corpus set is corpus_dryrun, chosen on
the pages beside the pilot's machine and verified sets, with the dry-run line at the top. The cluster list is paged
(the pilot's was one page). Nothing of the pilot's pages is removed.

## 3. What it needs

- The server's processor and memory (64 cores, 503 GB) for r02 and r05; GPU 2 (and GPU 0 once the reading of the
  last issues is done) for the embeddings. The language-model lane on GPU 2 is stopped, not removed, while the
  embeddings run. Disk: tens of GB (the embeddings cache about 30 GB).
- Packages on the server: faiss-cpu (or faiss-gpu), sentence-transformers or fastembed, numpy, pandas,
  scikit-learn, statsmodels; the first paste checks what is there.
- Sujin's time for the corpus validation set (about 200 candidate passages, as in the pilot's r06).

## 4. Order and time

1. r02 at scale: two to three days of building with tests on a synthetic corpus and on the pilot's stories (the
   pilot's numbers must come out the same from the new code — that is the test), then the run (hours).
2. r04 at scale: three to four days, then the run (one to two days).
3. r05 at scale: two to three days, then the run (hours).
4. The site's pages for the corpus set: two days.
About two weeks of building in all, each stage deployed and run as soon as it is ready, in that order, so that the
site shows exact reuse across the corpus first.

## 5. What is decided and what is open

Decided (Heejin, 14 October): build now and dry-run on the corpus; label it. Open, to be put to Heejin with the
numbers when each stage runs: the similarity floor for r04's candidates (from the pilot's validation set), the size
of the pair sample in r05, and whether the lane on GPU 2 may be stopped for the embedding hours.
