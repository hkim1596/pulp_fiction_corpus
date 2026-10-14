#!/usr/bin/env python3
"""CPU-only, resumable backfill of approved assembled issues to current rules.

Defaults to a read-only plan. Pass --run to build and validate in isolated staging,
then install atomic files. Original backups are immutable; human annotations are
never written and an annotated live assembly is always held. No OCR, API or GPU
inference is called. Existing cross-issue links are transferred by identity instead
of rewriting other issues with a corpus-wide linking pass.
"""
import argparse
import copy
from difflib import SequenceMatcher
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "scripts"))
from corpus_lib import (all_states, corpus_config, partition_issue_ids,
                        require_approved, settings, write_json_atomic)
from s08_assemble_rules import VERSION as RULES_VERSION
from continuity_metadata_splits import corrected_story_prefix, printed_identity
from article_guard import article_write_lock
from continuity_serial_boundary import corrected_serial_end_prefix

TARGET = tuple(map(int, RULES_VERSION.split(".")))
TARGET_BACKEND = f"rules_v{RULES_VERSION}"
BACKUP_NAME = "continuity-2026-10-07"
SCRIPT = "scripts/upgrade_continuity.py"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def digest(path):
    path = Path(path)
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def version(doc):
    m = re.match(r"^rules_v(\d+)\.(\d+)(?:\D|$)", doc.get("backend") or "")
    return tuple(map(int, m.groups())) if m else None


def safe_id(iid):
    if not isinstance(iid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", iid):
        raise ValueError(f"unsafe issue id: {iid!r}")
    return iid


def output_paths(iid):
    safe_id(iid)
    return [f"data/assembly_v2/rules/{iid}/articles.json",
            f"data/assembly_v2/rules/{iid}/analysis.json",
            f"data/assembly_v2/rules_on_model/{iid}/articles.json",
            f"data/assembly_v2/llm/{iid}/articles.json",
            f"data/assembly_v2/llm/{iid}/compare.json",
            f"data/articles/{iid}/articles.json",
            f"data/articles/{iid}/published.json"]


def atomic_copy(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".continuity-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as out, open(source, "rb") as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def backup_once(root, iid, issue_dir):
    """Create the original manifest once; no existing backup byte is replaced."""
    root, issue_dir = Path(root), Path(issue_dir)
    manifest_path = issue_dir / "original.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        for rel, sha in manifest["files"].items():
            if sha is not None and digest(issue_dir / "original" / rel) != sha:
                raise ValueError(f"damaged original backup: {rel}")
        return manifest
    issue_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for rel in output_paths(iid):
        src, dst = root / rel, issue_dir / "original" / rel
        sha = digest(src)
        files[rel] = sha
        if sha is None:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            if digest(dst) != sha:
                raise ValueError(f"partial backup differs from current source: {rel}")
        else:
            # Exclusive creation makes even a interrupted backup non-overwriting.
            with src.open("rb") as inp, dst.open("xb") as out:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
        if digest(src) != sha or digest(dst) != sha:
            raise ValueError(f"source changed during backup: {rel}")
    manifest = {"issue": iid, "created": now(), "files": files}
    write_json_atomic(str(manifest_path), manifest)
    return manifest


def record_keys(rec):
    return {f"{f['page']}:{i}" for f in rec.get("fragments", []) for i in f.get("region_ids", [])}


def unsorted_keys(doc):
    return {x["key"] if isinstance(x, dict) else str(x) for x in doc.get("unsorted", [])}


def validate_candidate(old, candidate):
    counts = Counter(f"{f['page']}:{i}" for r in candidate.get("articles", [])
                     for f in r.get("fragments", []) for i in f.get("region_ids", []))
    duplicate = [k for k, n in counts.items() if n > 1]
    ids = [r["article_id"] for r in candidate.get("articles", [])]
    if duplicate or len(ids) != len(set(ids)) or candidate.get("checks", {}).get("double_owned_regions", 0):
        raise ValueError(f"candidate has duplicate ownership or article IDs ({duplicate[:8]})")
    added = unsorted_keys(candidate) - unsorted_keys(old)
    if added:
        raise ValueError(f"candidate adds unsorted regions: {sorted(added)[:8]}")
    def universe(doc):
        return (set().union(*(record_keys(r) for r in doc.get("articles", [])))
                | {f"{f['page']}:{f['idx']}" for f in doc.get("furniture", [])}
                | unsorted_keys(doc))
    missing = universe(old) - universe(candidate)
    if missing:
        raise ValueError(f"candidate loses region identities: {sorted(missing)[:8]}")
    return {"regions": len(counts), "duplicate_owned": 0, "added_unsorted": 0,
            "records": len(ids), "old_records": len(old.get("articles", []))}


def normalized(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def retired_serial_reason(rec):
    """Two evidence-based parser errors; never retire an external work link."""
    serial, toc = rec.get("serial") or {}, rec.get("toc") or {}
    if (rec.get("work_id") or rec.get("excerpt_of") or rec.get("contains_excerpt")
            or serial.get("prev") or serial.get("next") or serial.get("part_n_source")
            or serial.get("part_total_source") or serial.get("source") != "contents"):
        return None
    printed = rec.get("title_as_printed") or toc.get("title") or ""
    ending = re.search(r"(?i)(?<=\w)ending\b", printed)
    if (ending and (serial.get("part_label") or "").lower() == "ending"
            and toc.get("type") == "feature"
            and normalized(rec.get("work_title")) == normalized(printed[:ending.start()])):
        return "serial ending marker matched inside a word in a contents feature title"
    blurb = toc.get("blurb") or ""
    cover = re.search(r'(?is)\b(?:our\s+)?cover\b.{0,120}?story\s+entitled\s*[, :]*["“]([^"”]+)["”]\s*,?\s*by\s+([^,\n]+)', blurb)
    if (cover and serial.get("part_label") and serial["part_label"].lower() in blurb.lower()
            and rec.get("author") and normalized(cover.group(2)) != normalized(rec["author"])
            and normalized(cover.group(1)) != normalized(rec.get("work_title") or rec.get("title"))):
        return "contents blurb explicitly describes the cover illustration of a different titled work and author"
    return None


def retired_other_work_blurb_reason(rec, candidate, pages):
    """A contents blurb can accidentally include the next titled serial entry."""
    serial, toc = rec.get("serial") or {}, rec.get("toc") or {}
    if (not pages or serial.get("source") != "contents" or not serial.get("part_label")
            or rec.get("work_id") or rec.get("excerpt_of") or rec.get("contains_excerpt")
            or any(serial.get(k) for k in ("prev", "next", "part_n_source", "part_total_source"))):
        return None
    own = [r for r in candidate.get("articles", []) if normalized(r.get("title")) == normalized(rec.get("title"))
           and normalized(r.get("author")) == normalized(rec.get("author")) and printed_identity(r, pages)]
    if len(own) != 1:
        return None
    source_text = " ".join(pages[int(k.split(":")[0])]["regions"][int(k.split(":")[1])].get("text") or ""
                           for k in record_keys(own[0]))
    label = normalized(serial["part_label"])
    if re.search(r"\b" + re.escape(label) + r"\b", normalized(source_text)):
        return None  # the original piece has its own independently printed ordinal
    blurb = normalized(toc.get("blurb"))
    for after in candidate.get("articles", []):
        title, author = normalized(after.get("title")), normalized(after.get("author"))
        if (not title or not author or after is own[0] or not printed_identity(after, pages)
                or not blurb.startswith(title + " ")):
            continue
        following = blurb[len(title):].strip()
        if not following.startswith(label + " "):
            continue
        byline = " ".join(following[len(label):].strip().split()[:len(author.split())])
        if SequenceMatcher(None, byline, author).ratio() >= .95:
            return "contents blurb begins with another independently printed story title, the ordinal and its matching author; original piece has no printed ordinal"
    return None


def corrected_suffix(before, after, candidate, pages):
    """Prove a small heading returned the preceding story's ending to its owner."""
    if not pages or normalized(before.get("title")) != normalized(after.get("title")) \
            or normalized(before.get("author")) != normalized(after.get("author")):
        return False
    bt, at = before.get("toc") or {}, after.get("toc") or {}
    if not bt or any(bt.get(k) != at.get(k) for k in ("title", "author", "page")):
        return False
    old_keys, new_keys = record_keys(before), record_keys(after)
    old_pages = {int(k.split(":")[0]) for k in old_keys}
    if len(old_pages) != 1 or not new_keys:
        return False
    pn = next(iter(old_pages))
    if pn not in pages or {int(k.split(":")[0]) for k in new_keys} != {pn}:
        return False
    regs = pages[pn]["regions"]
    disjoint_poem = (not (new_keys & old_keys) and after.get("type") == "poem" and at.get("type") == "poem"
                    and not any((before.get("serial") or {}).get(k) for k in ("prev", "next", "part_label", "part_n", "part_total", "part_n_source", "part_total_source"))
                    and any(len((regs[int(k.split(":")[1])].get("text") or "").splitlines()) >= 3
                            and len((regs[int(k.split(":")[1])].get("text") or "").split()) >= 8 for k in new_keys))
    if not new_keys < old_keys and not disjoint_poem:
        return False
    headings = [int(k.split(":")[1]) for k in new_keys
                if after.get("roles", {}).get(k) == "title"
                and regs[int(k.split(":")[1])].get("label") == "SectionHeader"
                and normalized(regs[int(k.split(":")[1])].get("text")) == normalized(after.get("title"))]
    if not headings:
        return False
    first = min(headings)
    furniture = {f"{f['page']}:{f['idx']}" for f in candidate.get("furniture", [])}
    removed = old_keys - new_keys - furniture
    if not removed or any(int(k.split(":")[1]) >= first for k in removed):
        return False
    owners = [r for r in candidate.get("articles", []) if record_keys(r) & removed]
    return (len(owners) == 1 and owners[0].get("type") == "story"
            and removed <= record_keys(owners[0])
            and min((f["page"], min(f["region_ids"])) for f in owners[0]["fragments"] if f["region_ids"]) < (pn, first))


def recovered_contents_identity(before, after, pages):
    """An unlinked one/two-box contents stub replaced by its printed story head."""
    serial, bt, at = before.get("serial") or {}, before.get("toc") or {}, after.get("toc") or {}
    if (not pages or len(record_keys(before)) > 2 or serial.get("source") != "contents"
            or before.get("work_id") or before.get("excerpt_of") or before.get("contains_excerpt")
            or any(serial.get(k) for k in ("prev", "next", "part_n_source", "part_total_source"))
            or any(serial.get(k) and serial[k] != (after.get("serial") or {}).get(k)
                   for k in ("part_label", "part_n", "part_total"))
            or after.get("type") != "story" or len(record_keys(after)) < 3 or bt.get("page") != at.get("page")):
        return False
    raw = bt.get("title") or ""
    inline = re.match(r"(?is)^(.*?)\s*,?\s+by\s+(.+)$", raw)
    title = inline.group(1).rstrip(", ") if inline else raw
    author = bt.get("author") or before.get("author") or (inline.group(2) if inline else "")
    if (normalized(title) != normalized(at.get("title")) or not author
            or normalized(author) != normalized(after.get("author"))):
        return False
    title_text, author_text = [], []
    for k, role in after.get("roles", {}).items():
        if role not in ("title", "author"):
            continue
        pn, idx = map(int, k.split(":"))
        if pn not in pages:
            return False
        text = pages[pn]["regions"][idx].get("text") or ""
        (title_text if role == "title" else author_text).append(text)
    return (normalized(" ".join(title_text)) == normalized(after.get("title"))
            and normalized(re.sub(r"(?i)^\s*by\s+", "", " ".join(author_text))) == normalized(author)
            and bool(after.get("serial")))


def recovered_author_only_contents(before, after, pages):
    """A byline misread as a serial title carries no external work identity."""
    serial, toc = before.get("serial") or {}, before.get("toc") or {}
    byline = re.fullmatch(r"(?is)\s*by\s+(.+)", toc.get("title") or "")
    if (not pages or not byline or serial.get("source") != "contents"
            or before.get("work_id") or before.get("excerpt_of") or before.get("contains_excerpt")
            or any(serial.get(k) for k in ("prev", "next", "part_label", "part_n", "part_total", "part_n_source", "part_total_source"))
            or normalized(before.get("title")) != normalized(toc.get("title"))
            or after.get("type") != "story" or after.get("title_source") != "page"
            or after.get("author_source") != "page"
            or normalized(byline.group(1)) != normalized(after.get("author"))):
        return False
    old_keys, new_keys = record_keys(before), record_keys(after)
    if not old_keys or not new_keys or len(old_keys & new_keys) / len(old_keys) < .8 \
            or len(old_keys & new_keys) / len(new_keys) < .8:
        return False
    wanted = normalized(f"{after.get('title')} by {after.get('author')}")
    for k, role in after.get("roles", {}).items():
        if role != "title":
            continue
        pn, idx = map(int, k.split(":"))
        region = pages[pn]["regions"][idx]
        if region.get("label") == "SectionHeader" and normalized(region.get("text")) == wanted:
            return True
    return False


def author_only_contents_ordinal(before, after, candidate, pages):
    """Read an author-only alias against its actual adjacent contents lines.

    Complete region containment and printed identity are required. An ordinal
    may stay only inside this title's physical entry; a later title/byline must
    independently prove a borrowed ordinal before it can be retired.
    """
    serial, toc = before.get("serial") or {}, before.get("toc") or {}
    byline = re.fullmatch(r"(?is)\s*by\s+(.+)", toc.get("title") or "")
    if (not pages or not byline or before.get("type") != "story" or after.get("type") != "story"
            or serial.get("source") != "contents" or not serial.get("part_label")
            or before.get("work_id") or before.get("excerpt_of") or before.get("contains_excerpt")
            or any(serial.get(k) for k in ("prev", "next", "part_n_source", "part_total_source"))
            or normalized(before.get("title")) != normalized(toc.get("title"))
            or normalized(byline.group(1)) != normalized(after.get("author"))
            or not record_keys(before) or not record_keys(before) <= record_keys(after)
            or not printed_identity(after, pages)):
        return None
    label = normalized(serial["part_label"])
    def descriptor(text):
        return bool(re.match(r"(?i)^\W*(?:(?:a\s+)?(?:serial|story)\s+in\s+|part\s+|first\s+part|conclusion\b)", text))
    def contains_label(text):
        return bool(re.search(r"\b" + re.escape(label) + r"\b", normalized(text)))
    def byline_page(text):
        m = re.fullmatch(r"(?is)\s*by\s+(.+?)\s*[.·…\s]+\s*(\d+)\s*", text)
        return (normalized(m.group(1)), int(m.group(2))) if m else None
    title, author = normalized(after.get("title")), normalized(after.get("author"))
    for pn in sorted(pages)[:12]:
        lines = [line.strip() for r in pages[pn]["regions"] for line in (r.get("text") or "").splitlines() if line.strip()]
        for i, line in enumerate(lines):
            if byline_page(line) != (author, toc.get("page")):
                continue
            begin = i - 1
            while begin >= 0 and i - begin <= 3 and descriptor(lines[begin]):
                begin -= 1
            if begin < 0 or normalized(lines[begin]) != title:
                continue
            own = list(lines[begin + 1:i])
            end = i + 1
            while end < len(lines) and end - i <= 3 and descriptor(lines[end]):
                own.append(lines[end]); end += 1
            if any(contains_label(t) for t in own):
                fresh = after.get("serial") or {}
                if any(fresh.get(k) not in (None, serial.get(k)) for k in ("part_label", "part_n", "part_total")):
                    return None
                return {"action": "preserve", "contents_scan": pn, "title_line": lines[begin],
                        "byline_line": line, "ordinal_lines": own}
            while end < len(lines) and re.match(r"(?i)^illustrat(?:ed|ion)\s+by\s+", lines[end]):
                end += 1
            if end >= len(lines):
                continue
            next_title = normalized(lines[end])
            others = [r for r in candidate.get("articles", []) if r is not after
                      and normalized(r.get("title")) == next_title and printed_identity(r, pages)]
            if len(others) != 1:
                continue
            other = others[0]
            j = end + 1
            following = []
            while j < len(lines) and j - end <= 3 and descriptor(lines[j]):
                following.append(lines[j]); j += 1
            read_byline = byline_page(lines[j]) if j < len(lines) else None
            if (any(contains_label(t) for t in following) and read_byline
                    and read_byline[0] == normalized(other.get("author"))
                    and next_title in normalized(toc.get("blurb"))
                    and contains_label(toc.get("blurb") or "")):
                return {"action": "retire", "contents_scan": pn, "title_line": lines[begin],
                        "byline_line": line, "next_title_line": lines[end], "ordinal_lines": following,
                        "next_byline_line": lines[j], "ordinal_owner": other["article_id"]}
    return None


def printed_ordinal_label(text, label):
    """A short installment descriptor may precede the literal part label."""
    text = re.sub(r"(?i)^\W*(?:a\s+)?serial\s+in\s+(?:\d+|one|two|three|four|five|six)\s+parts?\W*", "", text or "")
    return normalized(text) == normalized(label)


def printed_byline_alias(before, after, pages):
    """An old byline title keeps its real work ID on the identical story body."""
    serial = before.get("serial") or {}
    byline = re.fullmatch(r"(?is)\s*by\s+(.+)", before.get("title") or "")
    if (not pages or not byline or before.get("type") != "story" or after.get("type") != "story"
            or before.get("excerpt_of") or before.get("contains_excerpt")
            or any(serial.get(k) for k in ("part_label", "part_n", "part_total", "part_n_source", "part_total_source"))
            or normalized(byline.group(1)) != normalized(after.get("author"))
            or not printed_identity(after, pages)):
        return False
    old, new = record_keys(before), record_keys(after)
    if not old or not old <= new or len(new - old) > 3:
        return False
    first = min(int(k.split(":")[0]) for k in old)
    if not any(before.get("roles", {}).get(k) == "title" and after.get("roles", {}).get(k) == "author"
               and normalized(pages[int(k.split(":")[0])]["regions"][int(k.split(":")[1])].get("text"))
               == normalized(before["title"]) for k in old):
        return False
    return all(int(k.split(":")[0]) == first and after.get("roles", {}).get(k) in ("title", "author")
               and len((pages[first]["regions"][int(k.split(":")[1])].get("text") or "").split()) <= 20
               for k in new - old)


def retired_mixed_column_excerpt(before, candidate, pages):
    """Retire only a local excerpt flag on a source-proved mixed-column group."""
    if (not pages or before.get("type") != "house" or not before.get("contains_excerpt")
            or any(before.get(k) for k in ("work_id", "work_title", "serial", "excerpt_of"))):
        return None
    old = record_keys(before)
    if not old or len({k.split(":")[0] for k in old}) != 1:
        return None
    pn = int(next(iter(old)).split(":")[0])
    if pn not in pages:
        return None
    owners = [r for r in candidate.get("articles", []) if old & record_keys(r)]
    if not owners or set().union(*(old & record_keys(r) for r in owners)) != old:
        return None
    reading = [r for r in owners if r.get("type") in ("story", "feature", "letters", "poem")]
    if len(reading) != 1 or any(r.get("type") not in ("ad", "house") for r in owners if r is not reading[0]):
        return None
    story = reading[0]
    if (f"preceding story columns retained above another continuation on p.{pn}" not in story.get("flags", [])
            or not any(int(k.split(":")[0]) == pn - 1 for k in record_keys(story))):
        return None
    from s08_assemble_rules import prose_like, FILLER_RE, PRICE_RE, POSTAL_RE, ADDRESS_RE
    regs = pages[pn]["regions"]
    body = [regs[int(k.split(":")[1])] for k in old & record_keys(story)]
    narrative = [r for r in body if r.get("label") == "Text" and not FILLER_RE.search(r.get("text") or "")]
    if len(narrative) < 2 or sum(prose_like(r) for r in narrative) < 2 or any(not r.get("bbox") for r in narrative):
        return None
    first = min(narrative, key=lambda r: (r["bbox"][1], r["bbox"][0]))
    if not (first.get("text") or "")[:1].islower():
        return None
    boundary = min(r["bbox"][0] for r in narrative)
    commercial = []
    for r in owners:
        if r is story:
            continue
        for k in old & record_keys(r):
            box = regs[int(k.split(":")[1])]
            text = box.get("text") or ""
            if len(text.split()) <= 40 and FILLER_RE.search(text):
                continue
            if not box.get("bbox") or box["bbox"][2] > boundary:
                return None
            commercial.append(text)
    text = " ".join(commercial)
    if (not commercial or not (PRICE_RE.search(text) or re.search(r"(?i)\b(?:mail|send)\s+(?:the\s+)?coupon\b", text))
            or not (POSTAL_RE.search(text) or ADDRESS_RE.search(text))):
        return None
    return {"page": pn, "story_article_id": story["article_id"], "retained_story_regions": len(body),
            "retained_commercial_records": [r["article_id"] for r in owners if r is not story]}


def nonstory_containment(before, after):
    """An ad/house merge may retain its old excerpt only with complete ownership."""
    if before.get("type") not in ("ad", "house") or after.get("type") not in ("ad", "house"):
        return False
    old_keys, new_keys = record_keys(before), record_keys(after)
    return (bool(old_keys) and old_keys < new_keys
            and len({k.split(":")[0] for k in new_keys}) == 1)


def corrected_ad_subset(before, after, candidate, pages):
    """Keep a printed offer's link after its neighbour's continuation is removed."""
    if (not pages or before.get("type") != "ad" or after.get("type") != "ad"
            or not before.get("excerpt_of")
            or not normalized(before.get("title"))
            or normalized(before.get("title")) != normalized(after.get("title"))):
        return False
    old_keys, new_keys = record_keys(before), record_keys(after)
    if not new_keys < old_keys or len({k.split(":")[0] for k in old_keys}) != 1:
        return False
    pn = int(next(iter(old_keys)).split(":")[0])
    regs = pages[pn]["regions"]
    if not any(after.get("roles", {}).get(k) == "title"
               and regs[int(k.split(":")[1])].get("label") == "SectionHeader"
               and normalized(regs[int(k.split(":")[1])].get("text")) == normalized(after["title"])
               for k in new_keys):
        return False
    from s08_assemble_rules import AD_WORDS, POSTAL_RE, ADDRESS_RE, COMPANY_RE, CONT_FROM_RE
    text = " ".join(regs[int(k.split(":")[1])].get("text") or "" for k in new_keys)
    if len(AD_WORDS.findall(text)) < 2 or not (POSTAL_RE.search(text) or ADDRESS_RE.search(text) or COMPANY_RE.search(text)):
        return False
    furniture = {f"{f['page']}:{f['idx']}" for f in candidate.get("furniture", [])}
    removed = old_keys - new_keys - furniture
    owners = [r for r in candidate.get("articles", []) if record_keys(r) & removed]
    if not removed or len(owners) != 1 or owners[0].get("type") != "story" or not removed <= record_keys(owners[0]):
        return False
    # The newly restored owner has both a printed story title and an explicit
    # continuation notice among the boxes mistakenly included in the old ad.
    return (any(role == "title" for role in (owners[0].get("roles") or {}).values())
            and any(CONT_FROM_RE.search(regs[int(k.split(":")[1])].get("text") or "") for k in removed))


def relocated_printed_serial(before, after, candidate, pages):
    """Recover a named linked installment from a demonstrably wrong old placement."""
    if (not pages or before.get("type") != "story" or after.get("type") != "story"
            or not before.get("work_id") or before.get("excerpt_of") or before.get("contains_excerpt")
            or any(normalized(before.get(k)) != normalized(after.get(k)) for k in ("title", "author"))
            or not printed_identity(after, pages)):
        return False
    bt, at, serial = before.get("toc") or {}, after.get("toc") or {}, before.get("serial") or {}
    if (not bt or bt.get("page") is None or any(bt.get(k) != at.get(k) for k in ("title", "author", "page"))
            or not any(serial.get(k) for k in ("part_label", "part_n", "part_total"))
            or any(serial.get(k) != (after.get("serial") or {}).get(k) for k in ("part_label", "part_n", "part_total"))):
        return False
    old_keys, new_keys = record_keys(before), record_keys(after)
    if not old_keys or not new_keys or old_keys & new_keys:
        return False
    first = min(int(k.split(":")[0]) for k in new_keys)
    if max(int(k.split(":")[0]) for k in old_keys) >= first:
        return False
    from s08_assemble_rules import folio_of
    printed = bt["page"]
    page_matches = folio_of(pages[first]) == printed
    # Missing folios on facing illustrated pages may be bounded by nearby
    # independently printed folios on both sides, at the exact scan offset.
    neighbours_match = (any(first - d in pages and folio_of(pages[first - d]) == printed - d for d in (1, 2))
                        and any(first + d in pages and folio_of(pages[first + d]) == printed + d for d in (1, 2)))
    owners = [r for r in candidate.get("articles", []) if record_keys(r) & old_keys]
    # A tiny misplaced contents stub may consist solely of packaging slogans,
    # rather than a preceding story. Require explicit commercial source text,
    # no narrative-sized box, complete preservation and independently printed
    # matching installment number on the real story's first page.
    commercial_stub = False
    if (page_matches or neighbours_match) and all(int(k.split(":")[0]) in pages for k in old_keys):
        boxes = [pages[int(k.split(":")[0])]["regions"][int(k.split(":")[1])] for k in old_keys]
        text = " ".join(b.get("text") or "" for b in boxes)
        furniture = {f"{f['page']}:{f['idx']}" for f in candidate.get("furniture", [])}
        preserved = furniture | set().union(*(record_keys(r) for r in candidate.get("articles", [])))
        from s08_assemble_rules import roman_int
        numbered = [re.fullmatch(r"(?i)\s*(?:part\s+)?([ivxlcdm]+|[0-9]+)\s*[.]?\s*", r.get("text") or "")
                    for r in pages[first]["regions"]]
        matching_part = any(m and roman_int(m.group(1)) == serial.get("part_n") for m in numbered)
        commercial_stub = (len(old_keys) <= 3 and old_keys <= preserved and matching_part
                           and all(len((b.get("text") or "").split()) <= 20 for b in boxes)
                           and bool(re.search(r"(?i)\b(?:pancakes|waffles|buckwheats)\b", text))
                           and bool(re.search(r"(?i)\b(?:red|yellow|blue) box\b", text)))
    return ((page_matches or neighbours_match) and (commercial_stub or (len(owners) == 1
            and owners[0].get("type") == "story" and old_keys <= record_keys(owners[0])
            and (owners[0].get("toc") or {}).get("page") is not None
            and (owners[0].get("toc") or {})["page"] < printed
            and normalized(owners[0].get("title")) != normalized(before.get("title")))))


def unlinked_contents_alias(before, after, pages):
    """Re-read an inline contents identity while retaining every box and ordinal."""
    serial, fresh = before.get("serial") or {}, after.get("serial") or {}
    bt, at = before.get("toc") or {}, after.get("toc") or {}
    if (not pages or before.get("type") != "story" or after.get("type") != "story"
            or before.get("work_id") or before.get("excerpt_of") or before.get("contains_excerpt")
            or any(serial.get(k) for k in ("prev", "next", "part_n_source", "part_total_source"))
            or not any(serial.get(k) for k in ("part_label", "part_n", "part_total"))
            or any(serial.get(k) != fresh.get(k) for k in ("part_label", "part_n", "part_total"))
            or not bt or bt.get("page") != at.get("page")
            or not record_keys(before) or not record_keys(before) <= record_keys(after)):
        return False
    inline = re.fullmatch(r"(?is)(.*?)\s*,?\s+by\s+(.+)", bt.get("title") or "")
    same_toc = (normalized(bt.get("title")) == normalized(at.get("title"))
                and normalized(bt.get("author")) == normalized(at.get("author")))
    same_inline = (inline and normalized(inline.group(1)) == normalized(at.get("title"))
                   and normalized(inline.group(2)) == normalized(at.get("author")))
    if not (same_toc or same_inline):
        return False
    # A recorded as-printed spelling is accepted only when the actual owned
    # heading/byline confirms it. There is no fuzzy author/title comparison.
    physical = copy.deepcopy(after)
    physical["title"] = after.get("title_as_printed") or after.get("title")
    physical["author"] = after.get("author_as_printed") or after.get("author")
    if printed_identity(physical, pages):
        return True
    # A missing OCR byline can still preserve an unlinked contents ordinal if
    # the literal owned title and explicit installment descriptor both agree.
    from s08_assemble_rules import roman_int
    first = min(int(k.split(":")[0]) for k in record_keys(after))
    regs = pages[first]["regions"]
    title_seen = any(f"{first}:{i}" in record_keys(after)
                     and after.get("roles", {}).get(f"{first}:{i}") == "title"
                     and normalized(r.get("text")) == normalized(physical["title"])
                     for i, r in enumerate(regs))
    numbers = {"first": 1, "second": 2, "third": 3, "fourth": 4}
    # Some display titles are missing from OCR altogether. Use the same strict
    # serial-start evidence as assembly: actual owned byline, exact contents
    # author, matching printed ordinal, modal scan offset and an exact named
    # running head in the following pages. This path still has no external
    # relationship and retains every original box.
    from s08_assemble_rules import byline_author, folio_of, nearby_serial_contents
    offsets = Counter(pn - folio_of(page) for pn, page in pages.items() if page.get("height") and folio_of(page))
    offset = offsets.most_common(1)[0][0] if offsets else None
    for i, r in enumerate(regs):
        if (f"{first}:{i}" not in record_keys(after)
                or after.get("roles", {}).get(f"{first}:{i}") != "author"
                or normalized(byline_author(r.get("text") or "")) != normalized(at.get("author"))):
            continue
        descriptor = " ".join(x.get("text") or "" for x in regs[i + 1:i + 3])
        m = re.match(r"(?i)\s*(first|second|third|fourth) of (two|three|four) parts\b", descriptor)
        if not m:
            continue
        n, total = numbers[m.group(1).lower()], {"two": 2, "three": 3, "four": 4}[m.group(2).lower()]
        agrees = serial.get("part_n") == n or (normalized(serial.get("part_label")) == "conclusion" and n == total)
        if agrees and nearby_serial_contents(pages, first, {"idx": i, "author": at["author"]}, [at], offset):
            return True
    for i, r in enumerate(regs):
        if f"{first}:{i}" not in record_keys(after):
            continue
        m = re.match(r"(?i)\s*(first|second|third|fourth) of (two|three|four) parts\b", r.get("text") or "")
        if not m or not title_seen:
            continue
        n, total = numbers[m.group(1).lower()], {"two": 2, "three": 3, "four": 4}[m.group(2).lower()]
        if serial.get("part_n") == n or (normalized(serial.get("part_label")) == "conclusion" and n == total):
            return True
    return False


def retired_chapter_excerpt(before, after, candidate, pages):
    """A chapter heading is not an independent advertised story excerpt."""
    if (not pages or before.get("type") != "house" or not before.get("contains_excerpt")
            or before.get("excerpt_of") or before.get("work_id") or before.get("serial")
            or after.get("type") != "story" or not printed_identity(after, pages)):
        return False
    old_keys, new_keys = record_keys(before), record_keys(after)
    if not old_keys or len({k.split(":")[0] for k in old_keys}) != 1 or not old_keys & new_keys:
        return False
    from s08_assemble_rules import chapter_head
    pn = int(next(iter(old_keys)).split(":")[0])
    regs = pages[pn]["regions"]
    title_idxs = [i for i,r in enumerate(regs) if f"{pn}:{i}" in old_keys
                  and normalized(r.get("text")) == normalized(before.get("title"))]
    if not any(i > 0 and chapter_head(regs[i-1].get("text") or "") for i in title_idxs):
        return False
    remainder = old_keys - new_keys
    owned = set().union(*(record_keys(r) for r in candidate.get("articles", [])))
    return (len(remainder) <= 2 and remainder <= owned
            and all(regs[int(k.split(":")[1])].get("label") == "SectionHeader"
                    and len((regs[int(k.split(":")[1])].get("text") or "").split()) <= 8 for k in remainder))


def printed_conclusion_identity(before, after, pages):
    """Keep a real conclusion ordinal when contents OCR lost the work's title."""
    serial, toc = before.get("serial") or {}, before.get("toc") or {}
    if (not pages or before.get("work_id") or before.get("excerpt_of") or before.get("contains_excerpt")
            or any(serial.get(k) for k in ("prev", "next", "part_n_source", "part_total_source"))
            or normalized(serial.get("part_label")) != "conclusion"
            or any(serial.get(k) not in (None, 2) for k in ("part_n", "part_total"))
            or after.get("serial") or after.get("type") != "story" or not printed_identity(after, pages)
            or not record_keys(before) or not record_keys(before) <= record_keys(after)
            or any(not normalized(before.get(k + "_as_printed"))
                   or normalized(before.get(k + "_as_printed")) != normalized(after.get(k)) for k in ("title", "author"))
            or not re.search(r"(?i)\bserial in 2 parts\b", toc.get("title") or "")):
        return False
    first = min(int(k.split(":")[0]) for k in record_keys(after))
    return any(re.fullmatch(r"(?i)\s*part\s+II\s*", r.get("text") or "")
               and f"{first}:{i}" in record_keys(after) for i, r in enumerate(pages[first]["regions"]))


def recomputed_unlinked_identity_reason(before, after, pages, candidate=None):
    """Correct source-proved titles without discarding an external relationship."""
    if unlinked_contents_alias(before, after, pages):
        return "all original regions and meaningful ordinal retained; identical contents identity/page corroborated by owned printed identity or explicit installment descriptor with owned title or exact named running-head evidence"
    if candidate and retired_chapter_excerpt(before, after, candidate, pages):
        return "unlinked local house-excerpt inference was a printed chapter heading; all reading boxes retained in the complete story and remaining boxes are short chapter apparatus"
    serial = before.get("serial") or {}
    if (not pages or before.get("work_id")
            or any(serial.get(k) for k in ("prev", "next", "part_n_source", "part_total_source"))
            or (after.get("type") != "story" and not (after.get("type") == "feature"
                and before.get("type") == "house" and before.get("contains_excerpt")
                and not before.get("excerpt_of") and not serial)) or not printed_identity(after, pages)):
        return None
    old_keys, new_keys = record_keys(before), record_keys(after)
    if not old_keys or not new_keys:
        return None
    # A serial whose old contents title swallowed its byline can also have
    # swallowed an editorial continuation after its explicit installment end.
    # The editorial must independently point to its own earlier printed page.
    if candidate and new_keys < old_keys and not before.get("excerpt_of") and not before.get("contains_excerpt"):
        bt, at = before.get("toc") or {}, after.get("toc") or {}
        inline = re.fullmatch(r"(?is)(.*?)\s*,?\s+by\s+(.+)", bt.get("title") or "")
        removed = old_keys - new_keys
        first, last = min(int(k.split(":")[0]) for k in new_keys), max(int(k.split(":")[0]) for k in new_keys)
        owners = [r for r in candidate.get("articles", []) if record_keys(r) & removed]
        ended = any(re.fullmatch(r"(?i)\W*to be continued\W*", reg.get("text") or "")
                    for pn in (last - 1, last) if pn in pages for reg in pages[pn]["regions"])
        same_part = any(serial.get(k) for k in ("part_label", "part_n", "part_total")) and all(
            serial.get(k) == (after.get("serial") or {}).get(k) for k in ("part_label", "part_n", "part_total"))
        if (inline and normalized(inline.group(1)) == normalized(after.get("title"))
                and normalized(inline.group(2)) == normalized(after.get("author"))
                and bt.get("page") == at.get("page") and same_part and ended
                and min(int(k.split(":")[0]) for k in removed) > last
                and len(owners) == 1 and owners[0].get("type") in ("feature", "letters")
                and removed <= record_keys(owners[0])):
            from s08_assemble_rules import CONT_FROM_RE, folio_of
            other_keys = record_keys(owners[0])
            earlier_folios = {folio_of(pages[pn]) for pn in {int(k.split(":")[0]) for k in other_keys} if pn < first}
            first_removed = min(int(k.split(":")[0]) for k in removed)
            if any((m := CONT_FROM_RE.search(reg.get("text") or "")) and int(m.group(1)) in earlier_folios
                   for reg in pages[first_removed]["regions"]):
                return "corrected inline contents title/byline and unchanged ordinal; explicit installment end precedes an editorial continuation pointing to its own earlier printed page"
    if not old_keys <= new_keys:
        return None
    bt, at = before.get("toc") or {}, after.get("toc") or {}
    printed_ordinal_stub = (serial.get("source") == "head" and bool(serial.get("part_label"))
                            and min(int(k.split(":")[0]) for k in old_keys) == min(int(k.split(":")[0]) for k in new_keys)
                            and any(before.get("roles", {}).get(k) == "subtitle"
                                    and printed_ordinal_label(pages[int(k.split(":")[0])]["regions"][int(k.split(":")[1])].get("text"),
                                                              serial["part_label"]) for k in old_keys))
    paratext_stub = (len(old_keys) <= 8 and (bool(bt) or printed_ordinal_stub)
                     and normalized(before.get("title")) == normalized(after.get("title"))
                     and normalized(before.get("author")) == normalized(after.get("author"))
                     and all(bt.get(k) == at.get(k) for k in ("title", "author", "page"))
                     and all(before.get("roles", {}).get(k) in ("title", "subtitle", "author", "note", "chapter", "teaser", "caption", "synopsis")
                             and len((pages[int(k.split(":")[0])]["regions"][int(k.split(":")[1])].get("text") or "").split()) <= 40
                             for k in old_keys))
    if (not before.get("excerpt_of") and not before.get("contains_excerpt")
            and any(serial.get(k) for k in ("part_label", "part_n", "part_total"))
            and all(serial.get(k) == (after.get("serial") or {}).get(k)
                    for k in ("part_label", "part_n", "part_total"))
            and (len(old_keys) / len(new_keys) >= .95 or paratext_stub)):
        return "all original regions retained under independently printed story title/byline; meaningful serial ordinal unchanged; no external relationship"
    excerpt = before.get("excerpt_of") or {}
    if (before.get("type") in ("ad", "house") and set(excerpt) == {"title", "author"}
            and not serial and before.get("contains_excerpt")
            and all(normalized(excerpt.get(k)) and normalized(excerpt.get(k)) == normalized(after.get(k))
                    for k in ("title", "author"))
            and min(int(k.split(":")[0]) for k in old_keys) == min(int(k.split(":")[0]) for k in new_keys)):
        return "local title/author-only house-excerpt inference replaced by the complete independently printed matching story; no external relationship"
    if (before.get("type") == "house" and before.get("contains_excerpt") and not excerpt and not serial
            and old_keys < new_keys
            and len({k.split(":")[0] for k in old_keys}) == 1
            and not any(after.get("roles", {}).get(k) in ("title", "author", "subtitle", "teaser", "note") for k in old_keys)):
        return "unlinked house-excerpt inference replaced by its unchanged reading boxes inside one independently printed complete story; no external relationship"
    return None


def removed_transactional_tail(before, after, pages):
    """Recognize a later advertising column, never an unverified narrative tail."""
    if not pages or before.get("type") != "story" or after.get("type") != "story":
        return False
    if any(normalized(before.get(k)) != normalized(after.get(k)) for k in ("title", "author")):
        return False
    bt, at = before.get("toc") or {}, after.get("toc") or {}
    if not bt or any(bt.get(k) != at.get(k) for k in ("title", "author", "page")):
        return False
    old_keys, new_keys = record_keys(before), record_keys(after)
    if not new_keys or not new_keys < old_keys:
        return False
    removed = old_keys - new_keys
    tail_pages = {int(k.split(":")[0]) for k in removed}
    if len(tail_pages) != 1 or min(tail_pages) <= max(int(k.split(":")[0]) for k in new_keys):
        return False
    first = min(int(k.split(":")[0]) for k in new_keys)
    headings = [k for k in new_keys if after.get("roles", {}).get(k) == "title"
                and before.get("roles", {}).get(k) == "title" and int(k.split(":")[0]) == first]
    if not any(pages[first]["regions"][int(k.split(":")[1])].get("label") == "SectionHeader"
               and normalized(pages[first]["regions"][int(k.split(":")[1])].get("text"))
               and normalized(pages[first]["regions"][int(k.split(":")[1])].get("text"))
               in normalized(after.get("title")) for k in headings):
        return False
    pn = next(iter(tail_pages))
    regs = pages[pn]["regions"]
    boxes = [regs[int(k.split(":")[1])] for k in removed]
    if any(not b.get("bbox") for b in boxes):
        return False
    # The removed boxes themselves must establish a commercial offer. Never use
    # an unrelated checkout box elsewhere in the same column as evidence.
    left, right = min(b["bbox"][0] for b in boxes), max(b["bbox"][2] for b in boxes)
    page_width = max(b.get("bbox", [0, 0, 0, 0])[2] for b in regs)
    if right - left > .4 * page_width:
        return False
    ordered = sorted(boxes, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    first_prose = next((b.get("text") or "" for b in ordered if len((b.get("text") or "").split()) >= 25), "")
    if first_prose and not re.search(r"(?i)\b(?:book|manual|charts|plates)\b", first_prose):
        return False
    text = " ".join(b.get("text") or "" for b in boxes)
    return (bool(re.search(r"\$\s*\d", text))
            and bool(re.search(r"(?i)\b(?:mail|send)\s+(?:the\s+)?coupon\b", text))
            and bool(re.search(r"(?i)\bmoney\s+(?:refunded|back)\b", text))
            and bool(re.search(r"(?i)\b(?:book|manual|charts)\b", text)))


def preserve_links(old, candidate, pages=None, audit=None):
    """Keep a link only when one old record uniquely matches one new record."""
    fields = ("work_id", "work_title", "serial", "excerpt_of", "contains_excerpt")
    old_records, new_records = old.get("articles", []), candidate.get("articles", [])
    taken, transfers = set(), 0
    norm = normalized
    audit = audit if audit is not None else []
    for before in old_records:
        if not any(before.get(k) for k in fields):
            continue
        mixed = retired_mixed_column_excerpt(before, candidate, pages)
        if mixed:
            audit.append({"kind": "retired_unlinked_mixed_column_excerpt", "article_id": before.get("article_id"),
                          "proof": mixed, "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before},
                          "reason": "local excerpt flag had no named or external relationship; every original box retained in the adjacent story's run-on columns, separate commercial column, or short next-issue notice"})
            continue
        retired = retired_serial_reason(before) or retired_other_work_blurb_reason(before, candidate, pages)
        if retired:
            entry = {"kind": "retired_unlinked_serial", "article_id": before.get("article_id"),
                     "reason": retired, "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before},
                     "evidence": copy.deepcopy(before.get("toc")), "candidate_records_cleaned": []}
            for after in new_records:
                if (retired_serial_reason(after) == retired or retired_other_work_blurb_reason(after, candidate, pages) == retired) \
                        and record_keys(after) & record_keys(before):
                    after.pop("serial", None)
                    after.pop("work_title", None)
                    entry["candidate_records_cleaned"].append(after["article_id"])
            audit.append(entry)
            continue
        aliases = [(after, author_only_contents_ordinal(before, after, candidate, pages)) for after in new_records]
        aliases = [(after, proof) for after, proof in aliases if proof]
        if len(aliases) == 1:
            after, proof = aliases[0]
            if proof["action"] == "preserve":
                after["serial"] = copy.deepcopy(before["serial"])
                after["work_title"] = after["title"]
            audit.append({"kind": "resolved_author_only_contents_ordinal", "article_id": before.get("article_id"),
                          "candidate_article_id": after["article_id"], "proof": proof,
                          "reason": "all original regions retained under exact printed title/byline; physical contents line order proves whether the ordinal belongs to this title or the next independently printed work",
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before}})
            continue
        conclusions = [after for after in new_records if printed_conclusion_identity(before, after, pages)]
        if len(conclusions) == 1:
            after = conclusions[0]
            after["serial"] = copy.deepcopy(before["serial"])
            after["work_title"] = after["title"]
            audit.append({"kind": "preserved_printed_conclusion_identity", "article_id": before.get("article_id"),
                          "candidate_article_id": after["article_id"],
                          "reason": "all original boxes retained under exact as-printed title/byline; two-part contents declaration and printed Part II prove unchanged conclusion ordinal",
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before}})
            continue
        identities = [(after, recomputed_unlinked_identity_reason(before, after, pages, candidate)) for after in new_records]
        identities = [(after, reason) for after, reason in identities if reason]
        if len(identities) == 1:
            after, reason = identities[0]
            audit.append({"kind": "recomputed_unlinked_printed_identity", "article_id": before.get("article_id"),
                          "candidate_article_id": after["article_id"], "reason": reason,
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before},
                          "candidate_metadata": {k: copy.deepcopy(after[k]) for k in fields if k in after}})
            continue
        recovered = [after for after in new_records if recovered_contents_identity(before, after, pages)
                     or recovered_author_only_contents(before, after, pages)]
        if len(recovered) == 1:
            audit.append({"kind": "recomputed_unlinked_contents_identity", "article_id": before.get("article_id"),
                          "candidate_article_id": recovered[0]["article_id"],
                          "reason": ("unlinked author-only contents serial replaced by actual printed title and byline with at least 80% shared body"
                                     if recovered_author_only_contents(before, recovered[0], pages) else
                                     "old one/two-box unlinked contents stub replaced by same contents title/author/page with actual printed title and byline"),
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before},
                          "candidate_metadata": {k: copy.deepcopy(recovered[0][k]) for k in fields if k in recovered[0]}})
            continue
        serial = before.get("serial") or {}
        if (serial.get("source") == "contents" and not before.get("work_id")
                and not before.get("excerpt_of") and not before.get("contains_excerpt")
                and not any(serial.get(k) for k in ("prev", "next", "part_label", "part_n", "part_total",
                                                    "part_n_source", "part_total_source"))):
            audit.append({"kind": "recomputed_empty_unlinked_contents_metadata", "article_id": before.get("article_id"),
                          "reason": "old contents-only serial has no work identity, relationship, ordinal or external provenance; fresh printed-source assembly supplies metadata",
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before},
                          "candidate_records": [{"article_id": r["article_id"], "title": r.get("title"),
                                                 "shared_regions": len(record_keys(before) & record_keys(r))}
                                                for r in new_records if record_keys(before) & record_keys(r)]})
            continue
        ks = record_keys(before)
        choices = []
        for j, after in enumerate(new_records):
            nk = record_keys(after)
            overlap = len(ks & nk)
            corrected = corrected_suffix(before, after, candidate, pages)
            contained = nonstory_containment(before, after)
            advertising_tail = removed_transactional_tail(before, after, pages)
            cleaned_ad = corrected_ad_subset(before, after, candidate, pages)
            split_prefix = corrected_story_prefix(before, after, candidate, pages)
            relocated = relocated_printed_serial(before, after, candidate, pages)
            serial_end = corrected_serial_end_prefix(before, after, candidate, pages)
            byline_alias = printed_byline_alias(before, after, pages)
            if not ks or not nk or (not overlap and not corrected and not relocated):
                continue
            exact = ks == nk
            same_title = norm(before.get("title")) == norm(after.get("title")) and bool(norm(before.get("title")))
            if exact or (same_title and overlap / len(ks) >= .8 and overlap / len(nk) >= .8) \
                    or corrected or contained or advertising_tail or cleaned_ad or split_prefix or relocated or serial_end or byline_alias:
                # A verified printed heading can recover a misplaced contents
                # record whose old body actually belongs to the preceding story.
                choices.append((2.0 if corrected or split_prefix or relocated or serial_end else overlap / len(ks | nk), j))
        choices.sort(reverse=True)
        if not choices or (len(choices) > 1 and choices[0][0] == choices[1][0]) or choices[0][1] in taken:
            raise ValueError(f"cannot safely retain cross-issue metadata for {before.get('article_id')}")
        _, j = choices[0]
        taken.add(j)
        after = new_records[j]
        contained = nonstory_containment(before, after)
        advertising_tail = removed_transactional_tail(before, after, pages)
        cleaned_ad = corrected_ad_subset(before, after, candidate, pages)
        split_prefix = corrected_story_prefix(before, after, candidate, pages)
        relocated = relocated_printed_serial(before, after, candidate, pages)
        serial_end = corrected_serial_end_prefix(before, after, candidate, pages)
        byline_alias = printed_byline_alias(before, after, pages)
        if byline_alias:
            audit.append({"kind": "preserved_link_on_printed_byline_alias", "article_id": before.get("article_id"),
                          "candidate_article_id": after.get("article_id"),
                          "reason": "all old story boxes retained; only the independently printed opening title/byline added; old title box is exactly the new author box; existing work identity and relationships preserved",
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before}})
        if contained:
            for field in ("work_id", "work_title", "serial", "excerpt_of"):
                if before.get(field) and after.get(field) and before[field] != after[field]:
                    raise ValueError(f"conflicting {field} in merged nonstory metadata for {before.get('article_id')}")
            audit.append({"kind": "nonstory_excerpt_containment", "article_id": before.get("article_id"),
                          "candidate_article_id": after.get("article_id"),
                          "reason": "all original ad/house regions retained in one unique same-page ad/house record",
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before},
                          "original_regions": len(ks), "candidate_regions": len(record_keys(after))})
        elif relocated:
            audit.append({"kind": "relocated_named_serial_identity", "article_id": before.get("article_id"),
                          "candidate_article_id": after.get("article_id"),
                          "reason": "exact title/author/contents/ordinal and printed heading; printed folio or both neighbouring folios confirm contents page; all old misplaced regions preserved in a preceding distinct contents story or a source-verified tiny commercial packaging stub",
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before}})
        elif serial_end:
            audit.append({"kind": "corrected_explicit_serial_end_prefix", "article_id": before.get("article_id"),
                          "candidate_article_id": after.get("article_id"),
                          "reason": "same printed serial identity and ordinal; explicit installment-end notice precedes a distinct contents story at its printed page with corroborating running headings",
                          "removed_regions": sorted(ks - record_keys(after)),
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before}})
        elif split_prefix:
            audit.append({"kind": "corrected_merged_story_prefix", "article_id": before.get("article_id"),
                          "candidate_article_id": after.get("article_id"),
                          "reason": "same printed title and author retained; every removed reading box belongs to a later independently printed story title and byline",
                          "removed_regions": sorted(ks - record_keys(after)),
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before}})
        elif cleaned_ad:
            audit.append({"kind": "corrected_advertisement_boundary", "article_id": before.get("article_id"),
                          "candidate_article_id": after.get("article_id"),
                          "reason": "same printed advertisement heading and commercial source text retained; removed boxes restored to a titled story with explicit continuation notice",
                          "removed_regions": sorted(ks - record_keys(after)),
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before}})
        elif advertising_tail:
            audit.append({"kind": "removed_transactional_advertising_tail", "article_id": before.get("article_id"),
                          "candidate_article_id": after.get("article_id"),
                          "reason": "same title/author/contents and printed start; removed later-page column advertises a priced product with refund and mail coupon",
                          "removed_regions": sorted(ks - record_keys(after)),
                          "original_metadata": {k: copy.deepcopy(before[k]) for k in fields if k in before}})
        elif len(ks & record_keys(after)) / len(ks) < .8:
            audit.append({"kind": "corrected_title_boundary", "article_id": before.get("article_id"),
                          "candidate_article_id": after.get("article_id"),
                          "reason": "same title/author/contents entry; actual heading retained; removed prefix belongs to the preceding story",
                          "original_regions": len(ks), "retained_regions": len(record_keys(after))})
        for k in fields:
            if not before.get(k):
                continue
            if k == "serial":
                if (after.get("type") == "poem" and (after.get("toc") or {}).get("type") == "poem"
                        and before[k].get("source") == "contents"
                        and not any(before[k].get(part) for part in ("prev", "next", "part_label", "part_n", "part_total", "part_n_source", "part_total_source"))):
                    audit.append({"kind": "retired_empty_serial_on_printed_poem", "article_id": before.get("article_id"),
                                  "original_metadata": copy.deepcopy(before[k]),
                                  "reason": "explicit poetry contents section and printed verse identify a poem; work_id retained on that poem"})
                    continue
                merged = copy.deepcopy(before[k] or {})
                merged.update(after.get(k) or {})
                for link in ("prev", "next", "part_n_source", "part_total_source"):
                    if link in (before[k] or {}):
                        merged[link] = copy.deepcopy(before[k][link])
                for part in ("part_n", "part_total"):
                    if merged.get(part) is None and (before[k] or {}).get(part) is not None:
                        merged[part] = before[k][part]
                after[k] = merged
            else:
                after[k] = copy.deepcopy(before[k])
        if byline_alias and before.get("work_title"):
            after["work_title"] = after["title"]
        for flag in before.get("flags", []):
            if ("matched by text" in flag or "by the order of the issues" in flag) and flag not in after.setdefault("flags", []):
                after["flags"].append(flag)
        transfers += 1
    return transfers


@contextmanager
def stage_modules(stage):
    import corpus_lib
    import s08_assemble_rules as rules
    import s12_llm_link as model
    import s13_publish as publish
    changes = [(rules, "ROOT", str(stage)), (model, "ROOT", str(stage)),
               (corpus_lib, "SETTINGS_PATH", str(stage / "config/corpus_settings.json")),
               (corpus_lib, "CORPUS_CONFIG", str(stage / "config/corpus_issues.json")),
               (publish, "ROOT", str(stage)), (publish, "LIVE", str(stage / "data/articles")),
               (publish, "ANN", str(stage / "data/annotations"))]
    old = [(m, k, getattr(m, k)) for m, k, _ in changes]
    for m, k, v in changes:
        setattr(m, k, v)
    try:
        yield rules, model, publish
    finally:
        for m, k, v in old:
            setattr(m, k, v)


def stage_issue(root, stage, meta, mode, look_below):
    iid = meta["id"]
    (stage / "config").mkdir()
    for name in ("departments.json", "illustrators.json", "corpus_settings.json"):
        source = root / "config" / name
        if source.exists():
            shutil.copy2(source, stage / "config" / name)
    write_json_atomic(str(stage / "config/corpus_issues.json"), {"issues": [meta]})
    layout = stage / "data/layout" / iid
    layout.parent.mkdir(parents=True)
    layout.symlink_to(root / "data/layout" / iid, target_is_directory=True)
    ann_rel = f"data/annotations/{iid}.jsonl"
    inputs = output_paths(iid) + [ann_rel, f"data/assembly_v2/llm/{iid}/pages.jsonl",
                                f"data/assembly_v2/llm/{iid}/flags.jsonl"]
    for rel in inputs:
        src, dst = root / rel, stage / rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    rule_rel = f"data/assembly_v2/rules/{iid}/articles.json"
    old = read_json(root / rule_rel)
    with stage_modules(stage) as (rules, model, publish):
        if not rules.run_issue(iid, meta, log=lambda _s: None):
            raise ValueError("no layout pages available")
        candidate = read_json(stage / rule_rel)
        if version(candidate) != TARGET:
            raise ValueError(f"unexpected candidate backend {candidate.get('backend')}")
        metadata_audit = []
        layout_pages = rules.load_pages(iid)
        transfers = preserve_links(old, candidate, pages=layout_pages, audit=metadata_audit)
        validation = validate_candidate(old, candidate)
        write_json_atomic(str(stage / rule_rel), candidate)
        # The experimental assembly is not a publication input. Keep its entire
        # previous artifact directory if its own identity/coverage proof fails;
        # a validated main assembly must not inherit its stale metadata.
        rom = f"data/assembly_v2/rules_on_model/{iid}/articles.json"
        experimental_hold = None
        if (root / rom).exists() and (stage / rom).exists():
            optional_audit = []
            try:
                previous_experimental = read_json(root / rom)
                doc = read_json(stage / rom)
                preserve_links(previous_experimental, doc, pages=layout_pages, audit=optional_audit)
                validate_candidate(previous_experimental, doc)
            except (ValueError, KeyError, TypeError) as exc:
                directory = Path(rom).parent
                experimental_hold = {"variant": "rules_on_model", "error": str(exc),
                                     "preserved_sha256": {str(p.relative_to(root)): digest(p)
                                         for p in sorted((root / directory).rglob("*")) if p.is_file()}}
                shutil.rmtree(stage / directory)
                shutil.copytree(root / directory, stage / directory)
                metadata_audit.append({"kind": "held_experimental_output", **experimental_hold,
                                       "reason": "optional experimental metadata/coverage proof failed; all original experimental files and links retained byte-identically; main rules/publication validated independently"})
            else:
                metadata_audit.extend(optional_audit)
                write_json_atomic(str(stage / rom), doc)
        # Rebuild from cached decisions even for human-held issues; no inference.
        cached = stage / f"data/assembly_v2/llm/{iid}/pages.jsonl"
        if cached.exists():
            if model.rebuild_issue(iid, "llm", meta={iid: meta}) is None:
                raise ValueError("cached model rebuild produced no result")
        what = publish.publish_issue(iid, mode, look_below, force=True)
        if (stage / ann_rel).exists():
            for rel in output_paths(iid)[-2:]:
                if digest(root / rel) != digest(stage / rel):
                    raise ValueError("human-corrected live file changed in staging")
        live = stage / f"data/articles/{iid}/articles.json"
        if live.exists() and not (stage / ann_rel).exists():
            # Model publication must meet the same ownership/coverage guards.
            validate_candidate(old, read_json(live))
    return {"validation": validation, "cross_issue_records_retained": transfers,
            "metadata_audit": metadata_audit, "experimental_metadata_hold": experimental_hold,
            "cached_model_rebuilt": cached.exists(), "publication": list(what)}


def rollback(root, issue_dir, receipt):
    """Recover only files still equal to this attempt's old/new bytes."""
    iid = receipt["issue"]
    # Older, uncoordinated writers may have logged against either old or new
    # IDs. An event carries no baseline digest, so neither version is provably
    # safe to restore. Keep the durable receipt for explicit recovery.
    if (any(rel.startswith("data/articles/") for rel in receipt.get("candidate_sha256", {}))
            and digest(root / f"data/annotations/{iid}.jsonl") != receipt.get("annotation_sha256")):
        raise ValueError("annotation baseline changed during commit; automatic rollback is unsafe")
    for rel, after in receipt.get("candidate_sha256", {}).items():
        current, before = digest(root / rel), receipt["before_sha256"].get(rel)
        if current == before:
            continue
        if current != after:
            raise ValueError(f"concurrent change prevents rollback: {rel}")
        prior = issue_dir / "attempt_before" / rel
        if before is None:
            (root / rel).unlink(missing_ok=True)
        elif digest(prior) == before:
            atomic_copy(prior, root / rel)
        else:
            raise ValueError(f"missing attempt backup: {rel}")


def upgrade_one(root_string, meta, mode="rules_flagged", look_below=.9, force=False):
    # Also protects direct callers and interrupted-commit recovery, not only
    # the bulk CLI. Staging publishes into a different root and lock namespace.
    with article_write_lock(root_string, safe_id(meta["id"])):
        return _upgrade_one_locked(root_string, meta, mode, look_below, force)


def _upgrade_one_locked(root_string, meta, mode="rules_flagged", look_below=.9, force=False):
    root, iid = Path(root_string), safe_id(meta["id"])
    issue_dir = root / "data/corpus" / BACKUP_NAME / iid
    receipt_path = issue_dir / "receipt.json"
    receipt = {"issue": iid, "started": now(), "status": "building", "target": TARGET_BACKEND}
    try:
        if receipt_path.exists():
            previous = read_json(receipt_path)
            if previous.get("status") == "committing":
                receipt = previous
                rollback(root, issue_dir, previous)
                receipt = {"issue": iid, "started": now(), "status": "building", "target": TARGET_BACKEND}
            if not force and previous.get("status") == "complete" and version(read_json(root / output_paths(iid)[0])) >= TARGET:
                return previous
        issue_dir.mkdir(parents=True, exist_ok=True)
        backup_once(root, iid, issue_dir)
        current = read_json(root / output_paths(iid)[0])
        if version(current) is None or version(current) > TARGET or (version(current) == TARGET and not force):
            return dict(receipt, status="skipped", reason=f"backend is not older than {TARGET_BACKEND}")
        paths = output_paths(iid)
        before = {rel: digest(root / rel) for rel in paths}
        ann = root / f"data/annotations/{iid}.jsonl"
        ann_before = digest(ann)
        write_json_atomic(str(receipt_path), receipt)
        # The attempt copy supports rollback to exactly the pre-attempt state;
        # original/ and original.json are never touched on a retry.
        attempt = issue_dir / "attempt_before"
        if attempt.exists():
            shutil.rmtree(attempt)
        for rel, sha in before.items():
            if sha is not None:
                dst = attempt / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root / rel, dst)
        with tempfile.TemporaryDirectory(prefix="stage-", dir=issue_dir) as temp:
            stage = Path(temp)
            receipt.update(stage_issue(root, stage, meta, mode, look_below))
            if digest(ann) != ann_before:
                raise ValueError("annotations changed during staging; retry against the new human baseline")
            if any(digest(root / rel) != sha for rel, sha in before.items()):
                raise ValueError("assembly/publication changed concurrently; no staged file installed")
            to_install = [rel for rel in paths if (stage / rel).exists()]
            if receipt.get("experimental_metadata_hold"):
                to_install = [rel for rel in to_install if not rel.startswith(f"data/assembly_v2/rules_on_model/{iid}/")]
            if ann.exists():
                to_install = [rel for rel in to_install if not rel.startswith("data/articles/")]
            receipt.update(status="committing", before_sha256=before,
                           candidate_sha256={rel: digest(stage / rel) for rel in to_install},
                           annotation_sha256=ann_before)
            write_json_atomic(str(receipt_path), receipt)
            # Rules become visible after their analysis/cache and before the live
            # records. The running sentinel asks independent publishers to wait.
            order = sorted(to_install, key=lambda r: (2 if r.startswith("data/articles/") else
                                                      1 if r == paths[0] else 0, r))
            try:
                for rel in order:
                    if rel.startswith("data/articles/") and ann.exists():
                        raise ValueError("new human annotation appeared before live installation")
                    atomic_copy(stage / rel, root / rel)
                if digest(ann) != ann_before:
                    raise ValueError("human annotations changed during installation")
            except BaseException:
                rollback(root, issue_dir, receipt)
                receipt["status"] = "rolled_back"
                raise
        receipt.update(status="complete", completed=now())
        write_json_atomic(str(receipt_path), receipt)
        return receipt
    except Exception as exc:
        # Failed builds never touch live files. Interrupted commits are recoverable
        # from the durable committing receipt, including a process termination.
        unresolved = receipt.get("status") == "committing"
        receipt.update(status="committing" if unresolved else "failed", failed=now(), error=str(exc))
        issue_dir.mkdir(parents=True, exist_ok=True)
        (issue_dir / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        write_json_atomic(str(receipt_path), receipt)
        return dict(receipt, status="failed", recovery_required=unresolved)


def recovery_pending(root, issues):
    """Check durable receipts, including a worker that died before returning."""
    for meta in issues:
        path = Path(root) / "data/corpus" / BACKUP_NAME / safe_id(meta["id"]) / "receipt.json"
        if path.exists():
            try:
                if read_json(path).get("status") == "committing":
                    return True
            except (OSError, ValueError):
                return True
    return False


def plan(root, issues, states, only=None, limit=None, force=False):
    requested = None if only is None else ([only] if isinstance(only, str) else only)
    if requested is not None:
        if not isinstance(requested, list) or not requested or len(requested) > 10000:
            raise ValueError("explicit issue selection must be a nonempty JSON list of at most 10000 IDs")
        for iid in requested:
            safe_id(iid)
        if len(set(requested)) != len(requested):
            raise ValueError("explicit issue selection contains duplicate IDs")
        unknown = set(requested) - {m["id"] for m in issues}
        if unknown:
            raise ValueError(f"unknown issue IDs: {sorted(unknown)[:8]}")
    if force and requested is None:
        raise ValueError("--force requires explicit --issue or --issues-file")
    safe, conflicts = partition_issue_ids(issues)
    if requested is not None and set(requested) & set(conflicts):
        raise ValueError("explicit issue selection contains IDs naming conflicting archive sources")
    selected, skipped = [], Counter()
    for meta in safe:
        iid = safe_id(meta["id"])
        if requested is not None and iid not in requested:
            continue
        if "assembled" not in states.get(iid, {}).get("stages", {}):
            skipped["not_assembled"] += 1
            continue
        try:
            backend = version(read_json(root / output_paths(iid)[0]))
        except (OSError, ValueError):
            skipped["missing_or_invalid_rules"] += 1
            continue
        receipt_path = root / "data/corpus" / BACKUP_NAME / iid / "receipt.json"
        recovery = receipt_path.exists() and read_json(receipt_path).get("status") == "committing"
        if not recovery and (backend is None or backend > TARGET or (backend == TARGET and not force)):
            skipped["current_or_unknown_backend"] += 1
            continue
        selected.append(meta)
    return selected[:limit] if limit else selected, {"conflicting_ids_held": conflicts, "skipped": dict(skipped)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", action="store_true", help="install upgrades; default only prints a plan")
    ap.add_argument("--workers", type=int, default=2, choices=range(1, 9), metavar="1..8")
    ap.add_argument("--limit", type=int)
    selection = ap.add_mutually_exclusive_group()
    selection.add_argument("--issue")
    selection.add_argument("--issues-file", help="JSON list of explicit, unique approved issue IDs")
    ap.add_argument("--force", action="store_true", help="recheck current assemblies; requires --issue or --issues-file")
    args = ap.parse_args()
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be positive")
    if args.force and not (args.issue or args.issues_file):
        ap.error("--force requires explicit --issue or --issues-file")
    cfg = corpus_config()
    require_approved(cfg, "upgrade continuity")
    try:
        explicit = read_json(args.issues_file) if args.issues_file else args.issue
        selected, details = plan(ROOT, cfg["issues"], all_states(), explicit, args.limit, args.force)
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    report = {"script": SCRIPT, "created": now(), "run": args.run, "target": TARGET_BACKEND,
              "selected": len(selected), "issues": [i["id"] for i in selected], "workers": args.workers,
              "force": args.force, **details}
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not args.run or not selected:
        return 0
    base = ROOT / "data/corpus" / BACKUP_NAME
    base.mkdir(parents=True, exist_ok=True)
    sentinel = ROOT / "data/corpus/CONTINUITY_UPGRADE"
    with (base / "upgrade.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another continuity upgrade holds the lock")
        if sentinel.exists():
            try:
                own = read_json(sentinel).get("script") == SCRIPT
            except (ValueError, OSError):
                own = False
            if not own:
                raise SystemExit("CONTINUITY_UPGRADE is owned by another operation")
        write_json_atomic(str(sentinel), {"script": SCRIPT, "pid": os.getpid(), "started": now()})
        results = []
        pub = settings().get("publish", {})
        mode, threshold = pub.get("prefer", "rules_flagged"), float(pub.get("look_below", .9))
        def save(status):
            progress = dict(report, status=status, updated=now(), finished=len(results),
                            completed=sum(r["status"] == "complete" for r in results),
                            failed=sum(r["status"] == "failed" for r in results))
            write_json_atomic(str(base / "results.json"), {"run": report, "results": results})
            write_json_atomic(str(base / "progress.json"), progress)
        save("running")
        try:
            with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
                jobs = {pool.submit(upgrade_one, str(ROOT), meta, mode, threshold, args.force): meta["id"] for meta in selected}
                for future in as_completed(jobs):
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {"issue": jobs[future], "status": "failed", "error": str(exc)}
                    results.append(result)
                    save("running")
                    print(f"{result['issue']}: {result['status']}" + (f" — {result['error']}" if result.get("error") else ""), flush=True)
            save("complete" if not any(r["status"] == "failed" for r in results) else "complete_with_failures")
        except BaseException:
            save("interrupted")
            raise
        finally:
            # A failed rollback leaves a partly installed baseline. Keep edits
            # and independent publishers held until a recovery resolves it.
            if not any(r.get("recovery_required") for r in results) and not recovery_pending(ROOT, selected):
                sentinel.unlink(missing_ok=True)
    return 1 if any(r["status"] == "failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
