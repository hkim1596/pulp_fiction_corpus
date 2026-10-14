"""Source evidence for retaining a serial link after correcting merged stories.

This module does not change ownership. It only validates an independently built
candidate so the migration can keep the original story's relationship metadata.
"""
import re


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def keys(record):
    return {(int(f["page"]), int(i)) for f in record.get("fragments", []) for i in f.get("region_ids", [])}


def printed_identity(record, pages):
    """Require the record's own heading and byline, including a combined box."""
    from s08_assemble_rules import byline_author, chapter_head, split_part

    def title_form(text):
        # Some contents OCR prepends chapter ranges and appends the serial's
        # length. Remove only those literal forms; no fuzzy title comparison.
        text = re.sub(r"(?i)^\s*chapters?\s+[ivxlcdm\d]+(?:\s*[-–]\s*[ivxlcdm\d]+)?\s*", "", text or "")
        text = split_part(text)[0]
        text = re.sub(r"(?i)[.\s,;:]*\bin\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+parts?[.\s,;:]*$", "", text)
        return norm(text)

    title, author = title_form(record.get("title")), norm(record.get("author"))
    if not title or not author:
        return False
    title_seen = author_seen = False
    owned = keys(record)
    # OCR sometimes joins the title directly to "by" ("Blue Flamesby Ethel
    # and James Dorrance"). Require exact title+by+author text in the first
    # owned header; absence of spaces alone must not erase printed identity.
    if owned:
        pn, idx = min(owned)
        if pn in pages and idx < len(pages[pn]["regions"]):
            first = pages[pn]["regions"][idx]
            combined = norm(first.get("text")).replace(" ", "")
            if first.get("label") == "SectionHeader" and combined == (title + " by " + author).replace(" ", ""):
                title_seen = author_seen = True
    for key, role in (record.get("roles") or {}).items():
        if role not in ("title", "author"):
            continue
        pn, idx = map(int, key.split(":"))
        if (pn, idx) not in owned or pn not in pages or idx >= len(pages[pn]["regions"]):
            continue
        region = pages[pn]["regions"][idx]
        text = region.get("text") or ""
        embedded = re.match(r"^(.+?)(?:\n+|,\s+)(by\s+.+)$", text, re.I | re.S)
        heading = embedded.group(1) if embedded else text
        if role == "title" and region.get("label") == "SectionHeader" and not chapter_head(heading):
            title_seen |= title_form(heading) == title
        byline = embedded.group(2) if embedded else text
        read_author = byline_author(byline)
        if embedded or role == "author":
            author_seen |= norm(read_author or re.sub(r"(?i)^\s*by\s+", "", byline)) == author
    return title_seen and author_seen


def corrected_story_prefix(before, after, candidate, pages):
    """A true serial keeps its link when later independently headed works split.

    A remaining story tail cannot qualify through prose similarity, a chapter
    heading, or a guessed contents title. Every removed reading box must belong
    to a later story with its own physically printed title and author.
    """
    if not pages or before.get("type") != "story" or after.get("type") != "story":
        return False
    if any(norm(before.get(k)) != norm(after.get(k)) for k in ("title", "author")):
        return False
    old, new = keys(before), keys(after)
    if not new or not old - new or not printed_identity(after, pages):
        return False
    # A corrected heading may recover its adjacent chapter apparatus that the
    # old record omitted. These additions cannot include reading prose.
    added = new - old
    for pn, idx in added:
        if (pn != min(new)[0] or pn not in pages or idx >= len(pages[pn]["regions"])):
            return False
        text = pages[pn]["regions"][idx].get("text") or ""
        if (after.get("roles", {}).get(f"{pn}:{idx}") not in ("title", "author", "chapter", "note", "subtitle")
                or len(text.split()) > 12):
            return False
    furniture = {(int(f["page"]), int(f["idx"])) for f in candidate.get("furniture", [])}
    removed = old - new - furniture
    if not removed or min(removed) <= max(new):
        return False
    other = [r for r in candidate.get("articles", []) if r is not after and keys(r) & removed]
    if not other or set().union(*(keys(r) & removed for r in other)) != removed:
        return False
    for record in other:
        owned = keys(record)
        if (record.get("type") != "story" or not owned or min(owned) <= max(new)
                or norm(record.get("title")) == norm(after.get("title"))
                or not printed_identity(record, pages)):
            return False
    return True
