"""The people page (site v0.29.1, 4 November 2026): the co-principal investigators, from config/people.json, side by side.

  /people    public (readable before the passcode); in the Explore row for members

The file holds groups in order, each with a heading, an optional note and its people in the order they are to be
listed in one row; a person has a name, lines (title and institution, as they ask to be listed) and optional links. Asked for by Dennis Yi Tenen on 4 November 2026 ("a brief 'people' page listing us as co-PIs
along with other team members participating"); the order Tenen, Kim, Kang set by Sujin Kang the same day. Bound to
the site's helpers with bind(globals()) from app.py.
"""
import json
import os

_G = {}


def bind(g):
    _G.update(g)


def _esc(s):
    return _G["esc"](s)


def people(root=None):
    """The groups as the file lists them; an empty list when the file is missing or unreadable."""
    p = os.path.join(root or _G["ROOT"], "config", "people.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("groups", []) if isinstance(d, dict) else []
    except Exception:
        return []


def _person(p):
    lines = "".join(f"<div>{_esc(l)}</div>" for l in p.get("lines", []))
    mail = p.get("email") or ""
    mail = f"<div><a href='mailto:{_esc(mail)}'>{_esc(mail)}</a></div>" if mail else ""
    links = " · ".join(f"<a href='{_esc(l['url'])}'>{_esc(l.get('label') or l['url'])}</a>"
                       for l in p.get("links", []) if l.get("url"))
    links = f"<p class='fine' style='margin:4px 0 0'>{links}</p>" if links else ""
    return (f"<div class='card pad'><h3 style='margin:0 0 6px'>{_esc(p.get('name', ''))}</h3>"
            f"<div class='muted'>{lines}{mail}</div>{links}</div>")


PEOPLE_CSS = ("<style>.people{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}"
              "@media(max-width:820px){.people{grid-template-columns:1fr}}</style>")


def cards_html(root=None):
    """The first group's people side by side, for the landing page (4 November 2026: the landing page's Contact card
    became the People block)."""
    groups = people(root)
    if not groups:
        return ""
    g = groups[0]
    cards = [_person(p) for p in g.get("people", [])]
    return PEOPLE_CSS + f"<h2>{_esc(g.get('heading', 'People'))}</h2><div class='people'>" + "".join(cards) + "</div>"


def people_html(root=None):
    """The page body: one block per group, its people side by side in one row, in the file's order."""
    groups = people(root)
    out = [PEOPLE_CSS, "<h1>People</h1>"]
    if not groups:
        out.append("<p class='muted'>(config/people.json is missing or empty.)</p>")
    for g in groups:
        out.append(f"<h2>{_esc(g.get('heading', ''))}</h2>")
        if g.get("note"):
            out.append(f"<p class='muted'>{_esc(g['note'])}</p>")
        cards = [_person(p) for p in g.get("people", [])]
        out.append(("<div class='people'>" + "".join(cards) + "</div>") if cards else "<p class='muted'>—</p>")
    return "".join(out)
