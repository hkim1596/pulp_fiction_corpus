"""Conservative geometric reading order without renumbering OCR regions.

Region indices are persistent annotation identities.  Only fragment traversal is
changed here.  Recursive whitespace cuts read complete columns before moving
right, including pages with a spanning title/footnote.  Overlapping/ambiguous
layouts retain the OCR order rather than inventing a new sequence.
"""
import math
import re


def order_region_keys(pages, keys):
    """Return (page, region_index) keys in layout order; preserve page order.

    Callers supply pages in their desired scan/printed order. No IDs, page
    numbers, ownership, roles or source layout objects are changed.
    """
    groups = {}
    for pn, idx in keys:
        groups.setdefault(pn, []).append(idx)
    result = []
    for pn, indices in groups.items():
        page = pages.get(pn)
        if not page:
            result.extend((pn, i) for i in indices)
            continue
        boxes = {}
        for i in indices:
            try:
                b = tuple(float(v) for v in page["regions"][i]["bbox"])
                if len(b) != 4 or not all(math.isfinite(v) for v in b) or b[0] >= b[2] or b[1] >= b[3]:
                    raise ValueError("invalid box")
                boxes[i] = b
            except (KeyError, IndexError, TypeError, ValueError):
                break
        # Unknown geometry must not cause a body box to disappear.
        if len(boxes) != len(indices):
            result.extend((pn, i) for i in indices)
            continue
        width = float(page.get("width") or max(b[2] for b in boxes.values()))
        height = float(page.get("height") or max(b[3] for b in boxes.values()))
        # Numbered picture-story panels carry a stronger explicit sequence than
        # columns. Do not turn a correct 1,2 / 3,4 panel grid into 1,3,2,4.
        numbered = [int(m.group(1)) for i in indices
                    if (m := re.match(r"^\s*(\d{1,3})[.)]\s+\S", page["regions"][i].get("text") or ""))]
        if len(numbered) >= 3 and all(b == a + 1 for a, b in zip(numbered, numbered[1:])):
            result.extend((pn, i) for i in indices)
            continue
        headers, footers = [], []
        for i in indices:
            r = page["regions"][i]
            t = (r.get("text") or "").strip()
            if len(t.split()) <= 12 and (r.get("label") in ("PageHeader", "PageFooter") or t.isdecimal()):
                if boxes[i][3] < .12 * height:
                    headers.append(i)
                elif boxes[i][1] > .88 * height:
                    footers.append(i)
            elif r.get("label") == "Footnote" and boxes[i][1] > .85 * height:
                footers.append(i)

        def cuts(ids, axis, minimum, tolerance=0):
            ordered = sorted(ids, key=lambda i: boxes[i][axis])
            edge = boxes[ordered[0]][axis + 2] - tolerance
            found = []
            for j in range(1, len(ordered)):
                gap = boxes[ordered[j]][axis] + tolerance - edge
                if gap >= minimum:
                    left = set(ordered[:j])
                    found.append((gap, [i for i in ids if i in left], [i for i in ids if i not in left]))
                edge = max(edge, boxes[ordered[j]][axis + 2] - tolerance)
            return found

        def column_cut(ids):
            choices = []
            for gap, left, right in cuts(ids, 0, max(1, width * .003), width * .006):
                # A diagonal layout is not two side-by-side columns.
                top = max(min(boxes[i][1] for i in left), min(boxes[i][1] for i in right))
                bottom = min(max(boxes[i][3] for i in left), max(boxes[i][3] for i in right))
                if bottom <= top:
                    continue
                # Tiny title letters/artist signatures do not establish columns.
                def substantial(side):
                    lengths = [len((page["regions"][i].get("text") or "").split()) for i in side]
                    return max(lengths) >= 8 or (len(side) >= 2 and sum(lengths) >= 4)
                if not all(substantial(side) for side in (left, right)):
                    continue
                choices.append((gap, left, right))
            return max(choices, key=lambda c: c[0]) if choices else None

        def walk(ids, depth=0):
            if len(ids) < 2 or depth > 64:
                return ids
            # A publication statement below a story spans a separate horizontal
            # band even when OCR split its heading and body into narrow boxes.
            for i in ids:
                if re.match(r"(?i)^STATEMENT OF (?:THE )?OWNERSHIP", (page["regions"][i].get("text") or "").strip()):
                    y = boxes[i][1]
                    top = [j for j in ids if boxes[j][3] < y]
                    bottom = [j for j in ids if boxes[j][1] >= y]
                    if top and bottom and len(top) + len(bottom) == len(ids):
                        return walk(top, depth + 1) + walk(bottom, depth + 1)
            cut = column_cut(ids)
            if cut:
                return walk(cut[1], depth + 1) + walk(cut[2], depth + 1)
            prose_boxes = [i for i in ids if len((page["regions"][i].get("text") or "").split()) >= 8]
            if len(prose_boxes) >= 4 and all(boxes[i][2] - boxes[i][0] < .6 * width for i in ids):
                centers = [(boxes[i][0] + boxes[i][2]) / 2 for i in prose_boxes]
                if max(centers) - min(centers) > .3 * width:
                    return ids   # suspected columns without a reliable gutter: preserve OCR order
            horizontal = cuts(ids, 1, max(1, height * .002))
            if not horizontal:
                return ids
            # Peel off spanning headings/footers before splitting paragraph
            # rows; otherwise aligned paragraph gaps interleave two columns.
            def score(c):
                return (sum(len(side) for side in c[1:] if len(side) > 1 and column_cut(side)), c[0])
            cut = max(horizontal, key=score)
            return walk(cut[1], depth + 1) + walk(cut[2], depth + 1)

        edge = set(headers + footers)
        result.extend((pn, i) for i in headers + walk([i for i in indices if i not in edge]) + footers)
    return result
