#!/usr/bin/env python3
"""Pulp Fiction Corpus — pilot website. One dependency-free Python file.

Runs on 127.0.0.1:8092 behind the Cloudflare tunnel (pulp.digihumeng.org).
Reads the pipeline's data/ tree in place — nothing is exported (same doctrine
as the causal-inference sites). Landing page is open and capability-only;
every other page requires the shared passcode.

Access control:
  password file: $PULP_SITE_PASSWORD_FILE (default ~/shared/khj/.pulp_site_password)
                 read on EVERY request; absent = site fully open
  cookie: pfauth = <expiry_ts>.<hmac_sha256(secret, expiry_ts)>, HttpOnly, 45 days
  secret: $PULP_SECRET_FILE (default ~/shared/khj/.pulp_webapp_secret), auto-created

House rules honored here: no <b>/<strong> anywhere; a HOW TO READ THIS PAGE box
on every members page; plain English; honest empty states with live numbers;
Cache-Control: no-store on every page.
"""
import difflib
import glob
import hashlib
import hmac
import html
import json
import os
import re
import secrets as pysecrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_VERSION = "0.2.0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CONFIG = os.environ.get("PULP_CONFIG",
                        os.path.join(ROOT, "config", "pilot_issues.json"))
PASS_FILE = os.environ.get("PULP_SITE_PASSWORD_FILE",
                           os.path.expanduser("~/shared/khj/.pulp_site_password"))
SECRET_FILE = os.environ.get("PULP_SECRET_FILE",
                             os.path.expanduser("~/shared/khj/.pulp_webapp_secret"))
FEEDBACK = os.path.join(DATA, "feedback.jsonl")
COOKIE_DAYS = 45
CONTACT = "hkim1596@knu.ac.kr"

# canonical display order for text stages (only those present are shown)
STAGE_ORDER = ["ia", "routeA", "routeB", "rules_ia", "rules_routeA",
               "rules_routeB", "llm_qwen_routeA", "llm_claude_routeA",
               "llm_qwen_routeB", "llm_claude_routeB", "llm_qwen_ia",
               "llm_claude_ia"]
STAGE_LABEL = {
    "ia": "IA baseline OCR", "routeA": "our OCR (layout route)",
    "routeB": "our OCR (vision-LLM route)", "rules_ia": "rules on IA text",
    "rules_routeA": "rules on layout route", "rules_routeB": "rules on VLM route",
    "llm_qwen_routeA": "LLM cleanup - Qwen (layout route)",
    "llm_claude_routeA": "LLM cleanup - Claude (layout route)",
    "llm_qwen_routeB": "LLM cleanup - Qwen (VLM route)",
    "llm_claude_routeB": "LLM cleanup - Claude (VLM route)",
    "llm_qwen_ia": "LLM cleanup - Qwen (IA text)",
    "llm_claude_ia": "LLM cleanup - Claude (IA text)",
}

# ---------------- data access ----------------

def cfg():
    try:
        return json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        return {"approved": False, "issues": []}


def issue_by_id(iid):
    for i in cfg()["issues"]:
        if i["id"] == iid:
            return i
    return None


def pages_of(iid):
    d = os.path.join(DATA, "pages", iid)
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.endswith(".png"))


def stages_of(iid):
    out = []
    if os.path.exists(os.path.join(DATA, "raw", iid, "ia_text.txt")):
        out.append("ia")
    d = os.path.join(DATA, "text", iid)
    if os.path.isdir(d):
        out += [x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
    return [s for s in STAGE_ORDER if s in out] + sorted(set(out) - set(STAGE_ORDER))


_ia_cache = {}
def page_text(iid, stage, n):
    """Text of page n (1-based) at a stage; '' if missing."""
    if stage == "ia":
        if iid not in _ia_cache:
            p = os.path.join(DATA, "raw", iid, "ia_text.txt")
            _ia_cache[iid] = (open(p, encoding="utf-8", errors="replace")
                              .read().split("\x0c") if os.path.exists(p) else [])
        pages = _ia_cache[iid]
        return pages[n - 1] if 0 < n <= len(pages) else ""
    p = os.path.join(DATA, "text", iid, stage, f"page_{n:04d}.txt")
    return open(p, encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""


def layout_of(iid, n):
    p = os.path.join(DATA, "layout", iid, f"page_{n:04d}.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def stage_input(stage):
    """Which stage a stage was computed FROM (for change highlighting)."""
    if stage.startswith("rules_"):
        return stage[len("rules_"):]
    if stage.startswith("llm_"):
        parts = stage.split("_", 2)          # llm, backend, src
        return "rules_" + parts[2] if len(parts) == 3 else None
    return None


STAGE_ENGINE = {"ia": "ABBYY OCR (Internet Archive)",
                "routeA": "Surya layout + recognition",
                "routeB": "vision LLM"}


def stage_page_meta(iid, stage):
    """Per-page meta (latency, model, cost) written by s03/s05: page name -> record."""
    p = os.path.join(DATA, "text", iid, stage, "meta.jsonl")
    out = {}
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
                out[r.get("page", "")] = r
            except Exception:
                pass
    return out


def issue_rates(iid):
    """Seconds per page for each stage of this issue, from timings.jsonl."""
    rates = {}
    for r in timings():
        if r.get("issue") != iid or not r.get("pages"):
            continue
        st, spp = r.get("stage"), r["seconds"] / r["pages"]
        key = None
        if st == "s02_layout_ocr":
            key = "routeA"
        elif st == "s03_vlm_ocr":
            key = "routeB"
        elif st == "s04_rules":
            key = f"rules_{r.get('src', 'ia')}"
        elif st == "s05_llm_clean":
            key = f"llm_{r.get('backend', '?')}_{r.get('src', '?')}"
        if key:
            rates[key] = spp
    return rates


def timings():
    p = os.path.join(DATA, "timings.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


# ---------------- auth ----------------

def secret():
    if not os.path.exists(SECRET_FILE):
        os.makedirs(os.path.dirname(SECRET_FILE), exist_ok=True)
        with open(SECRET_FILE, "w") as f:
            f.write(pysecrets.token_hex(32))
        os.chmod(SECRET_FILE, 0o600)
    return open(SECRET_FILE).read().strip()


def site_password():
    try:
        return open(PASS_FILE).read().strip()
    except Exception:
        return None  # no file -> site open


def make_token():
    exp = str(int(time.time()) + COOKIE_DAYS * 86400)
    sig = hmac.new(secret().encode(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def token_ok(tok):
    try:
        exp, sig = tok.split(".", 1)
        good = hmac.new(secret().encode(), exp.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:
        return False


# ---------------- html helpers ----------------

CSS = """
body{font-family:Georgia,'Times New Roman',serif;margin:0;color:#1c1a17;background:#faf7f2}
a{color:#7a3020}
.wrap{max-width:1200px;margin:0 auto;padding:16px 20px 60px}
.top{border-bottom:2px solid #1c1a17;padding:14px 0;margin-bottom:18px;display:flex;justify-content:space-between;align-items:baseline}
.brand{font-size:22px;letter-spacing:.4px}
.brand a{text-decoration:none;color:#1c1a17}
.nav a{margin-left:14px;font-size:14px}
h1{font-size:26px;font-weight:normal;margin:8px 0 14px}
h2{font-size:19px;font-weight:normal;margin:20px 0 8px;border-bottom:1px solid #d8cfc0;padding-bottom:3px}
.howto{background:#f3ead9;border:1px solid #d8cfc0;padding:10px 14px;font-size:14px;margin:0 0 18px}
.howto .t{letter-spacing:1.2px;font-size:12px;color:#7a3020;margin-bottom:4px}
table{border-collapse:collapse;font-size:14px;width:100%}
td,th{border:1px solid #d8cfc0;padding:6px 9px;text-align:left;font-weight:normal;vertical-align:top}
th{background:#f3ead9}
.num{text-align:right;font-variant-numeric:tabular-nums}
.muted{color:#75695a}
.empty{background:#fff;border:1px dashed #b8a88e;padding:14px;font-size:14px;color:#5a4f40}
.viewer{display:flex;gap:18px;align-items:flex-start}
.scan{position:relative;flex:0 0 36%}
.scan img{width:100%;display:block;border:1px solid #b8a88e}
.scan svg{position:absolute;left:0;top:0;width:100%;height:100%}
.textpane{flex:1;min-width:0}
.panelgrid{flex:1;min-width:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px;align-items:start}
.panel{border:1px solid #b8a88e;background:#fff}
.panel .ph{background:#f3ead9;border-bottom:1px solid #d8cfc0;padding:6px 10px;font-size:12.5px;line-height:1.5}
.panel .ph .nm{color:#7a3020;letter-spacing:.4px}
.panel .ph .facts{color:#5a4f40}
.panel pre{border:0}
pre{white-space:pre-wrap;font-family:Georgia,serif;font-size:14.5px;line-height:1.5;background:#fff;border:1px solid #d8cfc0;padding:12px;margin:0;max-height:640px;overflow-y:auto}
.stages a{display:inline-block;margin:0 8px 6px 0;font-size:13px;padding:2px 7px;border:1px solid #b8a88e;text-decoration:none}
.stages a.on{background:#1c1a17;color:#faf7f2;border-color:#1c1a17}
.d_ins{background:#dcefdc}
.d_del{background:#f6d7d3;text-decoration:line-through}
.pgnav a{margin-right:10px}
.fb{margin-top:34px;border-top:1px solid #d8cfc0;padding-top:12px;font-size:14px}
.fb input[type=text]{width:180px} .fb textarea{width:100%;height:60px}
.fb input,.fb textarea,.fb button{font-family:inherit;font-size:14px;border:1px solid #b8a88e;background:#fff;padding:5px}
.footer{margin-top:44px;font-size:12px;color:#75695a;border-top:2px solid #1c1a17;padding-top:8px}
.land{max-width:640px;margin:8vh auto 0;padding:0 20px}
.land h1{font-size:34px}
.land p{font-size:17px;line-height:1.65}
input.pw{font-size:16px;padding:6px;border:1px solid #b8a88e}
button.go{font-size:15px;padding:6px 16px;border:1px solid #1c1a17;background:#1c1a17;color:#faf7f2}
.boxlabel{font-size:11px;fill:#7a3020}
"""


def esc(s):
    return html.escape(str(s), quote=True)


def page(title, body, member=True, path="/"):
    nav = ("<span class='nav'>"
           "<a href='/issues'>issues</a><a href='/method'>method</a>"
           "<a href='/timing'>timing</a><a href='/feedback'>feedback</a>"
           "<a href='/logout'>log out</a></span>") if member else ""
    fb = ("<div class='fb'><form method='POST' action='/feedback'>"
          f"<input type='hidden' name='path' value='{esc(path)}'>"
          "<div class='muted'>Leave feedback on this page — it goes to the "
          "project log with a link back here.</div>"
          "<p>Name <input type='text' name='name'></p>"
          "<p><textarea name='comment' placeholder='What should change?'></textarea></p>"
          "<p><button>Send feedback</button></p></form></div>") if member else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{esc(title)} · Pulp Fiction Corpus</title><style>{CSS}</style></head>
<body><div class='wrap'>
<div class='top'><span class='brand'><a href='/'>PULP FICTION CORPUS</a>
<span class='muted' style='font-size:13px'> · pilot</span></span>{nav}</div>
{body}{fb}
<div class='footer'>Pulp Fiction Corpus pilot · Digital Humanities Engineering
Center, Kyungpook National University, with Columbia University ·
v{APP_VERSION} · texts shown verbatim with their OCR errors, anchored to the
scans · contact {CONTACT}</div>
</div></body></html>"""


def howto(text):
    return (f"<div class='howto'><div class='t'>HOW TO READ THIS PAGE</div>"
            f"{text}</div>")


def diff_html(old, new):
    """Word-level diff of new against old, no bold tags."""
    a, b = old.split(), new.split()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            out.append(esc(" ".join(b[j1:j2])))
        elif tag == "insert":
            out.append(f"<span class='d_ins'>{esc(' '.join(b[j1:j2]))}</span>")
        elif tag == "delete":
            out.append(f"<span class='d_del'>{esc(' '.join(a[i1:i2]))}</span>")
        else:
            out.append(f"<span class='d_del'>{esc(' '.join(a[i1:i2]))}</span> "
                       f"<span class='d_ins'>{esc(' '.join(b[j1:j2]))}</span>")
    return " ".join(out)


def md_to_html(md):
    """Small markdown renderer for METHOD.md: headers, lists, code, paragraphs."""
    out, in_code, in_list = [], False, False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(esc(line))
            continue
        if re.match(r"\s*[-*] ", line):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{esc(line.strip()[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        m = re.match(r"(#{1,3}) (.*)", line)
        if m:
            lvl = min(len(m.group(1)) + 0, 3)
            out.append(f"<h{lvl}>{esc(m.group(2))}</h{lvl}>")
        elif line.strip():
            out.append(f"<p>{esc(line)}</p>")
    if in_list:
        out.append("</ul>")
    if in_code:
        out.append("</pre>")
    return "\n".join(out)


# ---------------- request handler ----------------

class H(BaseHTTPRequestHandler):
    server_version = "pulpsite"

    # --- plumbing ---
    def _send(self, code, body, ctype="text/html; charset=utf-8", cookie=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to, cookie=None):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _member(self):
        if site_password() is None:
            return True
        c = self.headers.get("Cookie", "")
        m = re.search(r"pfauth=([^;]+)", c)
        return bool(m and token_ok(m.group(1)))

    def log_message(self, fmt, *args):
        pass  # quiet; serve script logs restarts

    # --- routes ---
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if path == "/":
            return self._send(200, self.landing())
        if path == "/login":
            return self._send(200, self.login_page(""))
        if path == "/logout":
            return self._redirect("/", cookie="pfauth=; Max-Age=0; Path=/")
        if not self._member():
            return self._redirect("/login")
        if path == "/issues":
            return self._send(200, self.issues_page())
        m = re.fullmatch(r"/issue/([\w\-]+)", path)
        if m:
            return self._send(200, self.issue_page(m.group(1)))
        m = re.fullmatch(r"/issue/([\w\-]+)/p/(\d+)", path)
        if m:
            return self._send(200, self.viewer(m.group(1), int(m.group(2)), qs))
        m = re.fullmatch(r"/img/([\w\-]+)/page_(\d{4})\.png", path)
        if m:
            p = os.path.join(DATA, "pages", m.group(1), f"page_{m.group(2)}.png")
            if os.path.exists(p):
                return self._send(200, open(p, "rb").read(), "image/png")
            return self._send(404, "no image", "text/plain")
        m = re.fullmatch(r"/dl/([\w\-]+)/([\w\-]+)", path)
        if m:
            iid, stage = m.groups()
            n = len(pages_of(iid)) or 500
            text = "\n\n".join(page_text(iid, stage, i) for i in range(1, n + 1)).strip()
            return self._send(200, text, "text/plain; charset=utf-8")
        if path == "/method":
            return self._send(200, self.method_page())
        if path == "/timing":
            return self._send(200, self.timing_page())
        if path == "/feedback":
            return self._send(200, self.feedback_page())
        return self._send(404, page("Not found", "<h1>Not found</h1>"), )

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        ln = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(ln).decode("utf-8"))
        get = lambda k: form.get(k, [""])[0].strip()
        if path == "/login":
            pw = site_password()
            if pw is not None and hmac.compare_digest(get("passcode"), pw):
                tok = make_token()
                return self._redirect("/issues",
                    cookie=f"pfauth={tok}; Max-Age={COOKIE_DAYS*86400}; Path=/; HttpOnly")
            time.sleep(1.0)
            return self._send(200, self.login_page("That passcode is not right."))
        if path == "/feedback":
            if not self._member():
                return self._redirect("/login")
            os.makedirs(DATA, exist_ok=True)
            with open(FEEDBACK, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "path": get("path")[:300], "name": get("name")[:80],
                    "comment": get("comment")[:4000]}, ensure_ascii=False) + "\n")
            return self._redirect("/feedback")
        return self._send(404, "no", "text/plain")

    # --- pages ---
    def landing(self):
        c = cfg()
        n = len(c.get("issues", []))
        gated = site_password() is not None
        note = "" if gated else ("<p class='muted'>(No passcode is set right now, "
                                 "so member pages are open.)</p>")
        body = f"""
<div class='land'>
<h1>PULP FICTION CORPUS</h1>
<p>A research archive in preparation: American pulp fiction magazines
(1896–1959), rebuilt from library scans into clean, page-anchored text for
computational study. A pilot of {n} issue{'s' if n != 1 else ''} is being
processed end to end.</p>
<p>Digital Humanities Engineering Center, Kyungpook National University,
with the Department of English and Comparative Literature, Columbia University.</p>
<p>Access for collaborators: <a href='/login'>enter the passcode</a>.
To request access, write to {CONTACT}.</p>{note}
<div class='footer'>v{APP_VERSION}</div>
</div>"""
        return f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Pulp Fiction Corpus</title><style>{CSS}</style></head>
<body>{body}</body></html>"""

    def login_page(self, msg):
        m = f"<p class='muted'>{esc(msg)}</p>" if msg else ""
        body = f"""<div class='land'><h1>Collaborator access</h1>{m}
<form method='POST' action='/login'>
<p><input class='pw' type='password' name='passcode' autofocus>
<button class='go'>Enter</button></p></form>
<p class='muted'>One shared passcode for the project team. Request it at
{CONTACT}.</p></div>"""
        return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Access · Pulp Fiction Corpus</title><style>{CSS}</style></head>
<body>{body}</body></html>"""

    def issues_page(self):
        c = cfg()
        rows = []
        n_dl = 0
        for i in c["issues"]:
            iid = i["id"]
            np = len(pages_of(iid))
            n_dl += 1 if np else 0
            sts = stages_of(iid)
            stage_cells = ", ".join(STAGE_LABEL.get(s, s) for s in sts) or "—"
            link = f"<a href='/issue/{iid}'>{esc(i['magazine'])} · {esc(i['cover_date'])}</a>"
            gold = "✓" if i.get("gold") else ""
            rows.append(f"<tr><td>{link}</td><td>{esc(i['genre'])}</td>"
                        f"<td class='num'>{np or '—'}</td><td>{gold}</td>"
                        f"<td class='muted'>{esc(stage_cells)}</td></tr>")
        approved = c.get("approved")
        state = ("" if approved else
                 "<div class='empty'>The 10-issue list is still marked "
                 "proposed (approved=false in config/pilot_issues.json). "
                 "Nothing has been downloaded from the Internet Archive yet — "
                 "the table below shows the plan, with zero pages "
                 "everywhere.</div>")
        body = (howto(
            "One row per pilot issue. Pages = working page images downloaded. "
            "The gold column marks issues with human-proofread text for "
            "accuracy measurement. Click an issue to see its pages, every "
            "processing stage, and downloads.")
            + f"<h1>Pilot issues ({n_dl} of {len(c['issues'])} downloaded)</h1>"
            + state
            + "<table><tr><th>issue</th><th>genre</th><th>pages</th>"
              "<th>gold</th><th>stages present</th></tr>"
            + "".join(rows) + "</table>")
        return page("Issues", body, path="/issues")

    def issue_page(self, iid):
        info = issue_by_id(iid)
        if not info:
            return page("Unknown", "<h1>Unknown issue</h1>")
        pngs = pages_of(iid)
        sts = stages_of(iid)
        t = [r for r in timings() if r.get("issue") == iid]
        trows = "".join(
            f"<tr><td>{esc(r['stage'])}</td><td class='num'>{r.get('pages') or ''}</td>"
            f"<td class='num'>{r['seconds']}</td>"
            f"<td class='num'>{round(r['seconds']/r['pages'],2) if r.get('pages') else ''}</td>"
            f"<td class='muted'>{esc(r.get('error',''))}</td></tr>"
            for r in t)
        dls = " · ".join(f"<a href='/dl/{iid}/{s}'>{esc(STAGE_LABEL.get(s,s))}</a>"
                         for s in sts)
        grid = " ".join(f"<a href='/issue/{iid}/p/{n}'>{n}</a>"
                        for n in range(1, len(pngs) + 1))
        empty = ("" if pngs else
                 "<div class='empty'>0 pages on disk for this issue — stage 1 "
                 "(download) has not run for it yet.</div>")
        body = (howto(
            "This is one pilot issue. The page grid opens the side-by-side "
            "viewer (scan next to text, stage by stage). The timing table is "
            "the measured wall-clock of every pipeline stage on this issue — "
            "these numbers, times the size of the full archive, are how we "
            "decide the full-corpus method.")
            + f"<h1>{esc(info['magazine'])} — {esc(info['cover_date'])}</h1>"
            + f"<p class='muted'>Internet Archive item {esc(info['ia_identifier'])} · "
              f"genre {esc(info['genre'])} · format {esc(info['format'])}"
            + (f" · gold: {esc(info['gold'].get('note',''))}" if info.get("gold") else "")
            + "</p>" + empty
            + (f"<h2>Pages ({len(pngs)})</h2><p class='pgnav'>{grid}</p>" if pngs else "")
            + (f"<h2>Downloads (full text per stage)</h2><p>{dls}</p>" if sts else "")
            + ("<h2>Timing so far</h2><table><tr><th>stage</th><th>pages</th>"
               "<th>seconds</th><th>sec/page</th><th>note</th></tr>"
               + trows + "</table>" if t else
               "<div class='empty'>No timing rows for this issue yet.</div>"))
        return page(info["magazine"], body, path=f"/issue/{iid}")

    def viewer(self, iid, n, qs):
        info = issue_by_id(iid)
        if not info:
            return page("Unknown", "<h1>Unknown issue</h1>")
        pngs = pages_of(iid)
        sts = stages_of(iid)
        pagefile = f"page_{n:04d}.txt"
        rates = issue_rates(iid)

        # which method panels are shown: ?show=a,b,c (default: all available)
        show_param = qs.get("show", [""])[0]
        shown = [s for s in show_param.split(",") if s in sts] or list(sts)
        diff_stage = qs.get("diff", [""])[0]   # one panel may show its changes

        def url(show_list, dstage):
            q = []
            if show_list != sts:
                q.append("show=" + ",".join(show_list))
            if dstage:
                q.append("diff=" + dstage)
            return f"/issue/{iid}/p/{n}" + ("?" + "&".join(q) if q else "")

        # toggle row: click a method to show/hide its panel
        toggles = []
        for s in sts:
            if s in shown:
                new = [x for x in shown if x != s] or [s]
                cls = "on"
            else:
                new = [x for x in sts if x in shown or x == s]
                cls = ""
            toggles.append(f"<a class='{cls}' href='{url(new, diff_stage)}'>"
                           f"{esc(STAGE_LABEL.get(s, s))}</a>")
        toggle_row = ("".join(toggles)
                      or "<span class='muted'>no text stages yet — only the "
                         "scan is on disk</span>")

        # method panels, side by side
        panels = []
        for s in shown:
            text = page_text(iid, s, n)
            meta = stage_page_meta(iid, s).get(pagefile, {})
            model = meta.get("model") or STAGE_ENGINE.get(s) or (
                "deterministic rules" if s.startswith("rules_") else s)
            secs = meta.get("latency_s") or rates.get(s)
            facts = [esc(str(model))]
            if secs:
                facts.append(f"{round(secs, 2)} s/page")
            if meta.get("usd") is not None:
                facts.append(f"${meta['usd']:.4f}/page")
            if meta.get("accepted") is False:
                facts.append("guard: correction rejected, rules text kept")
            src = stage_input(s)
            dlink = ""
            if src and src in sts:
                if diff_stage == s:
                    dlink = (f" · <a href='{url(shown, '')}'>plain</a>")
                else:
                    dlink = (f" · <a href='{url(shown, s)}'>changes vs "
                             f"{esc(STAGE_LABEL.get(src, src))}</a>")
            if diff_stage == s and src:
                body_html = f"<pre>{diff_html(page_text(iid, src, n), text)}</pre>"
            elif text:
                body_html = f"<pre>{esc(text)}</pre>"
            else:
                body_html = ("<div class='empty' style='border:0'>This stage "
                             "has not run for this page yet.</div>")
            panels.append(
                f"<div class='panel'><div class='ph'>"
                f"<span class='nm'>{esc(STAGE_LABEL.get(s, s))}</span><br>"
                f"<span class='facts'>{' · '.join(facts)}{dlink}</span></div>"
                f"{body_html}</div>")
        grid = ("<div class='panelgrid'>" + "".join(panels) + "</div>"
                if panels else
                "<div class='panelgrid'><div class='empty'>No text stages have "
                "run yet for this issue. After stage s02 the first method "
                "panel appears here; each later stage adds its own panel with "
                "its time and model.</div></div>")

        # scan + layout overlay (route A coordinates)
        lay = layout_of(iid, n)
        svg = ""
        laynote = ""
        if lay and lay.get("width"):
            boxes = []
            for rg in lay["regions"]:
                x0, y0, x1, y1 = rg["bbox"]
                boxes.append(
                    f"<rect x='{x0}' y='{y0}' width='{x1-x0}' height='{y1-y0}' "
                    f"fill='none' stroke='#7a3020' stroke-opacity='0.55' "
                    f"stroke-width='3'/>"
                    f"<text x='{x0+4}' y='{max(y0-6,12)}' class='boxlabel'>"
                    f"{esc(rg['label'])}</text>")
            svg = (f"<svg viewBox='0 0 {lay['width']} {lay['height']}' "
                   f"preserveAspectRatio='none'>{''.join(boxes)}</svg>")
        else:
            laynote = ("<p class='muted' style='font-size:12.5px'>No layout "
                       "regions for this page yet — the boxes appear after "
                       "stage s02 (layout detection) has run.</p>")
        img = (f"<img src='/img/{iid}/page_{n:04d}.png' alt='page scan'>"
               if 0 < n <= len(pngs) else
               "<div class='empty'>No scan image for this page.</div>")

        nav = (f"<span class='pgnav'>"
               + (f"<a href='/issue/{iid}/p/{n-1}'>&larr; page {n-1}</a>" if n > 1 else "")
               + f" page {n} of {len(pngs) or '?'} "
               + (f"<a href='/issue/{iid}/p/{n+1}'>page {n+1} &rarr;</a>" if n < len(pngs) else "")
               + "</span>")
        body = (howto(
            "Left: the scan, with the layout regions our detector found drawn "
            "on it once stage s02 has run. Right: one panel per cleaning "
            "method, side by side, each labeled with the model used, its "
            "measured seconds per page, and its cost per page where money is "
            "involved — so methods can be compared directly for quality "
            "against computing power. Click a method name above to show or "
            "hide its panel; inside a panel, 'changes' marks what that method "
            "changed against its input: <span class='d_ins'>added</span>, "
            "<span class='d_del'>removed</span>. Text is verbatim — errors "
            "are shown, not hidden.")
            + f"<h1><a href='/issue/{iid}'>{esc(info['magazine'])} "
              f"{esc(info['cover_date'])}</a> · page {n}</h1>"
            + f"<p class='stages'>{toggle_row}</p>"
            + f"<p>{nav}</p>"
            + f"<div class='viewer'><div class='scan'>{img}{svg}{laynote}</div>"
            + grid + "</div>")
        return page(f"{info['magazine']} p{n}", body,
                    path=f"/issue/{iid}/p/{n}")

    def method_page(self):
        p = os.path.join(ROOT, "METHOD.md")
        md = open(p, encoding="utf-8").read() if os.path.exists(p) else "(METHOD.md missing)"
        body = (howto(
            "The full processing method, exactly as implemented in the "
            "repository. This page is rendered from METHOD.md in the repo, so "
            "the description and the code move together. Comment via the "
            "feedback box below; the method is revised from feedback before "
            "the protocol is frozen.")
            + md_to_html(md))
        return page("Method", body, path="/method")

    def timing_page(self):
        t = timings()
        agg = {}
        for r in t:
            a = agg.setdefault(r["stage"], {"sec": 0.0, "pages": 0, "runs": 0})
            a["sec"] += r.get("seconds", 0) or 0
            a["pages"] += r.get("pages") or 0
            a["runs"] += 1
        FULL_PAGES = 1_700_000  # ~15k unique issues x ~112 pages, stated assumption
        rows = ""
        for s in sorted(agg):
            a = agg[s]
            spp = a["sec"] / a["pages"] if a["pages"] else None
            proj = (spp * FULL_PAGES / 3600) if spp else None
            rows += (f"<tr><td>{esc(s)}</td><td class='num'>{a['runs']}</td>"
                     f"<td class='num'>{a['pages']}</td>"
                     f"<td class='num'>{round(a['sec'],1)}</td>"
                     f"<td class='num'>{round(spp,2) if spp else '—'}</td>"
                     f"<td class='num'>{round(proj) if proj else '—'}</td></tr>")
        body = (howto(
            "Every pipeline run writes one timing row per issue; this table "
            "sums them by stage. The last column projects to a full corpus of "
            "1.7 million pages (about 15,000 unique issues at 112 pages each — "
            "an assumption, stated here so it can be argued with) on the same "
            "hardware, single process. It answers the pilot's second "
            "question: is the method affordable at full scale?")
            + "<h1>Timing</h1>"
            + ("<table><tr><th>stage</th><th>runs</th><th>pages</th>"
               "<th>seconds</th><th>sec/page</th>"
               "<th>full corpus, hours (projected)</th></tr>" + rows + "</table>"
               if rows else
               "<div class='empty'>0 timing rows — no stage has run yet.</div>"))
        return page("Timing", body, path="/timing")

    def feedback_page(self):
        items = []
        if os.path.exists(FEEDBACK):
            for line in open(FEEDBACK, encoding="utf-8"):
                try:
                    items.append(json.loads(line))
                except Exception:
                    pass
        rows = "".join(
            f"<tr><td class='muted'>{esc(i['ts'])}</td>"
            f"<td>{esc(i.get('name') or 'anonymous')}</td>"
            f"<td><a href='{esc(i.get('path','/'))}'>{esc(i.get('path','/'))}</a></td>"
            f"<td>{esc(i.get('comment',''))}</td></tr>"
            for i in reversed(items))
        body = (howto(
            "Everything collaborators have sent through the feedback boxes, "
            "newest first, each linked to the page it was written on. This "
            "list drives revisions during development.")
            + f"<h1>Feedback ({len(items)})</h1>"
            + ("<table><tr><th>when</th><th>who</th><th>page</th>"
               "<th>comment</th></tr>" + rows + "</table>" if items else
               "<div class='empty'>No feedback yet. Every members page has a "
               "feedback box at the bottom.</div>"))
        return page("Feedback", body, path="/feedback")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), H)
    print(f"pulp site v{APP_VERSION} on http://{args.host}:{args.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
