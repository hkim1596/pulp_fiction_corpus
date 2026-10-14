"""Prove a serial's endpoint when OCR omitted the following story's heading."""
import re

from continuity_metadata_splits import keys, norm, printed_identity


def corrected_serial_end_prefix(before, after, candidate, pages):
    """Keep links on a printed serial prefix, never on the following story.

    The alternative to an independently OCR'd following title requires all of:
    an explicit next-issue ending, an exact contents/folio boundary, a different
    named author, and that next title repeated in at least two running heads.
    This proves metadata ownership only; it does not change source regions.
    """
    from s08_assemble_rules import folio_of

    if not pages or before.get("type") != "story" or after.get("type") != "story":
        return False
    if any(not norm(before.get(k)) or norm(before.get(k)) != norm(after.get(k))
           for k in ("title", "author")) or not printed_identity(after, pages):
        return False
    old_serial, new_serial = before.get("serial") or {}, after.get("serial") or {}
    if not any(old_serial.get(k) for k in ("part_n", "part_label")):
        return False
    if any(old_serial.get(k) != new_serial.get(k)
           for k in ("part_n", "part_label", "part_total")):
        return False
    old, new = keys(before), keys(after)
    if not new or not new < old:
        return False
    furniture = {(int(r["page"]), int(r["idx"])) for r in candidate.get("furniture", [])}
    removed = old - new - furniture
    if not removed or min(removed) <= max(new):
        return False
    endpoint = min(removed)
    pn, idx = endpoint
    if pn != max(new)[0] or pn not in pages or idx >= len(pages[pn]["regions"]):
        return False
    notice = (pages[pn]["regions"][idx].get("text") or "").strip()
    if len(notice.split()) > 60 or not re.fullmatch(
            r"\(?TO BE CONTINUED NEXT (?:WEEK|MONTH|ISSUE)[.!)]*"
            r"(?:\s+(?:Don['’]t forget|This magazine|The continuation)\b[^\n]*)?",
            notice, re.I):
        return False
    remaining = removed - {endpoint}
    others = [r for r in candidate.get("articles", []) if r is not after and keys(r) & remaining]
    if len(others) != 1 or not remaining:
        return False
    following = others[0]
    owned = keys(following)
    toc = following.get("toc") or {}
    if (following.get("type") != "story" or not owned or min(owned) < endpoint
            or not remaining <= owned or not owned <= removed | furniture
            or norm(following.get("title")) == norm(after.get("title"))
            or norm(following.get("author")) == norm(after.get("author"))):
        return False
    if any(not norm(following.get(k)) or norm(toc.get(k)) != norm(following.get(k))
           for k in ("title", "author")) or toc.get("type") != "story":
        return False
    if not toc.get("page") or folio_of(pages[pn]) != toc["page"]:
        return False
    title = norm(following["title"])
    named_pages = {p for p, _ in owned if p > pn and any(
        r.get("label") == "PageHeader" and norm(r.get("text")) == title
        for r in pages.get(p, {}).get("regions", []))}
    return len(named_pages) >= 2
