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
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_VERSION = "0.6.0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CONFIG = os.environ.get("PULP_CONFIG",
                        os.path.join(ROOT, "config", "pilot_issues.json"))
PASS_FILE = os.environ.get("PULP_SITE_PASSWORD_FILE",
                           os.path.expanduser("~/shared/khj/.pulp_site_password"))
SECRET_FILE = os.environ.get("PULP_SECRET_FILE",
                             os.path.expanduser("~/shared/khj/.pulp_webapp_secret"))
USERS_FILE = os.environ.get("PULP_USERS_FILE",
                            os.path.expanduser("~/shared/khj/.pulp_users.json"))
ANNDIR = os.path.join(DATA, "annotations")
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
    p = os.path.join(DATA, "text", iid, stage, f"page_{n:04d}.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8", errors="replace").read()
    if stage == "ia":
        # fallback when s01b has not written per-page IA text yet
        if iid not in _ia_cache:
            rp = os.path.join(DATA, "raw", iid, "ia_text.txt")
            _ia_cache[iid] = (open(rp, encoding="utf-8", errors="replace")
                              .read().split("\x0c") if os.path.exists(rp) else [])
        pages = _ia_cache[iid]
        if len(pages) == 1:
            return ("[The Internet Archive text for this issue has no page "
                    "boundaries; run pipeline/s01b_ia_pages.py to split it "
                    "by pages.]" if n > 1 else pages[0])
        return pages[n - 1] if 0 < n <= len(pages) else ""
    return ""


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


def articles_of(iid):
    p = os.path.join(DATA, "articles", iid, "articles.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def articles_index():
    p = os.path.join(DATA, "articles", "index.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def article_by_id(aid):
    m = re.match(r"(.+)_(?:a|u)\d+$", aid)
    if not m:
        return None, None
    d = effective_doc(m.group(1))
    if d:
        for a in d["articles"]:
            if a["article_id"] == aid:
                return a, d
    return None, None


STATUS_CHIP = {
    "auto": "<span class='chip stA'>automatic</span>",
    "modified": "<span class='chip stM'>modified</span>",
    "verified": "<span class='chip stV'>verified</span>",
}

# workbench javascript (plain string; __TOKENS__ substituted per page)
WB_JS = r"""
var ISSUE="__ISSUE__", AID="__AID__", CAN=__CAN__;
function post(params){params.issue=ISSUE;params.article_id=AID;
  fetch('/annotate',{method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams(params)}).then(function(){location.reload();});}
function selKey(k){
  document.querySelectorAll('.hl').forEach(function(e){e.classList.remove('hl');});
  document.querySelectorAll('[data-key="'+k+'"]').forEach(function(e){
    e.classList.add('hl');
    if(e.classList.contains('card')||e.classList.contains('othercard'))
      e.scrollIntoView({behavior:'smooth',block:'center'});
  });
  var g=document.querySelector('g[data-key="'+k+'"]');
  if(g) g.closest('.scanwrap').scrollIntoView({behavior:'smooth',block:'nearest'});
}
document.addEventListener('click',function(ev){
  var t=ev.target.closest('[data-selkey]');
  if(t) selKey(t.getAttribute('data-selkey'));
});
if(CAN){
  var cards=document.getElementById('cards');
  if(cards){
    var dragging=null;
    cards.querySelectorAll('.card').forEach(function(c){
      c.addEventListener('dragstart',function(){dragging=c;c.classList.add('dragging');});
      c.addEventListener('dragend',function(){c.classList.remove('dragging');dragging=null;});
      c.addEventListener('dragover',function(e){
        e.preventDefault();
        if(!dragging||dragging===c)return;
        var r=c.getBoundingClientRect();
        if(e.clientY < r.top + r.height/2) cards.insertBefore(dragging,c);
        else cards.insertBefore(dragging,c.nextSibling);
      });
    });
    cards.addEventListener('drop',function(e){
      e.preventDefault();
      var order=[].map.call(cards.querySelectorAll('.card'),function(c){
        return c.getAttribute('data-key');}).join(',');
      post({act:'order_js',order:order});
    });
    cards.addEventListener('dragover',function(e){e.preventDefault();});
  }
  document.querySelectorAll('.cardtext').forEach(function(el){
    el.addEventListener('dblclick',function(){
      if(el.querySelector('textarea'))return;
      var orig=el.textContent;
      el.innerHTML='';
      var ta=document.createElement('textarea');ta.className='editbox';ta.value=orig;
      var sv=document.createElement('button');sv.textContent='save correction';
      var ca=document.createElement('button');ca.textContent='cancel';
      ca.style.marginLeft='8px';
      sv.onclick=function(){post({act:'fragtext',frag:el.getAttribute('data-key'),text:ta.value});};
      ca.onclick=function(){el.textContent=orig;};
      el.appendChild(ta);el.appendChild(sv);el.appendChild(ca);
    });
  });
}
"""


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


USERS_LOCK = threading.Lock()


def users():
    """Named accounts, or None when no users file exists (guest-only mode)."""
    try:
        return json.load(open(USERS_FILE, encoding="utf-8"))
    except Exception:
        return None


def save_users(u):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(u, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(USERS_FILE, 0o600)
    except Exception:
        pass


def account_status(rec):
    return rec.get("status", "active")   # older accounts lack the field


def is_admin(username):
    u = (users() or {}).get(username or "")
    return bool(u) and u.get("role") == "admin" \
        and account_status(u) == "active"


def check_user(username, password):
    """True only for an ACTIVE account with the right password."""
    u = (users() or {}).get(username)
    if not u or account_status(u) != "active":
        return False
    h = hashlib.sha256((u["salt"] + password).encode()).hexdigest()
    return hmac.compare_digest(h, u["pw"])


def is_pending(username):
    u = (users() or {}).get(username)
    return bool(u) and account_status(u) == "pending"


def display_name(username):
    u = (users() or {}).get(username)
    return (u or {}).get("name") or username


def make_token(user):
    exp = str(int(time.time()) + COOKIE_DAYS * 86400)
    payload = f"{user}|{exp}"
    sig = hmac.new(secret().encode(), payload.encode(),
                   hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def token_user(tok):
    """Return the username ('guest' included) for a valid cookie, else None."""
    try:
        user, exp, sig = tok.split("|")
        good = hmac.new(secret().encode(), f"{user}|{exp}".encode(),
                        hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, good) and int(exp) > time.time():
            return user
    except Exception:
        pass
    return None


# ---------------- annotations: append-only event log ----------------

def ann_events(iid):
    p = os.path.join(ANNDIR, f"{iid}.jsonl")
    out = []
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def ann_append(iid, user, action, payload):
    os.makedirs(ANNDIR, exist_ok=True)
    with open(os.path.join(ANNDIR, f"{iid}.jsonl"), "a",
              encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "user": user, "issue": iid, "action": action,
                            **payload}, ensure_ascii=False) + "\n")


_regions_cache = {}
def page_regions(iid, pno):
    key = (iid, pno)
    if key not in _regions_cache:
        p = os.path.join(DATA, "layout", iid, f"page_{pno:04d}.json")
        try:
            regs = json.load(open(p, encoding="utf-8"))["regions"]
        except Exception:
            regs = []
        _regions_cache[key] = sorted(regs, key=lambda r: r.get("order", 0))
    return _regions_cache[key]


def fragkey(fr):
    return f"{fr['page']}:{'-'.join(str(x) for x in fr['region_ids'])}"


def frag_text(iid, fr, overrides=None):
    """A fragment's OCR text; a human per-segment correction wins."""
    if overrides:
        o = overrides.get(fragkey(fr))
        if o is not None:
            return o
    regs = page_regions(iid, fr["page"])
    parts = []
    for ridx in fr["region_ids"]:
        if 0 <= ridx < len(regs):
            parts.append(regs[ridx].get("text") or "")
    return "\n".join(parts)


def assemble_text(iid, fragments, overrides=None):
    raw = "\n".join(frag_text(iid, fr, overrides) for fr in fragments)
    try:
        import sys as _sys
        _p = os.path.join(ROOT, "pipeline")
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
        from s07_articles import clean_text
        return clean_text(raw)
    except Exception:
        return raw


def effective_doc(iid):
    """Machine articles + replayed human annotations -> what the site shows.

    The machine output is never modified; every human action is one JSONL
    event with its user and time, replayed here in order.
    """
    doc = articles_of(iid)
    if not doc:
        return None
    arts = []
    for a in doc["articles"]:
        arts.append({**a, "fragments": [dict(f) for f in a["fragments"]],
                     "status": "auto", "verified_by": None,
                     "verified_at": None, "modified_by": [],
                     "text_override": None, "last_mod": ""})
    byid = {a["article_id"]: a for a in arts}
    user_furniture = []
    overrides = {}
    uns = []
    for u in doc.get("unsorted", []):
        segs = u.get("segments") or u.get("region_ids") or []
        if u.get("page") is not None and segs:
            uns.append({"page": u["page"], "region_ids": list(segs)})
    n_user = 0

    def findfrag(k):
        for a in arts:
            for fr in a["fragments"]:
                if fragkey(fr) == k:
                    return a, fr
        for fr in uns:                      # claiming an unsorted segment
            if fragkey(fr) == k:
                return "unsorted", fr
        return None, None

    def touch(a, ev):
        if ev["user"] not in a["modified_by"]:
            a["modified_by"].append(ev["user"])
        a["last_mod"] = ev["ts"]

    for ev in ann_events(iid):
        act = ev.get("action")
        aid = ev.get("article_id")
        a = byid.get(aid)
        if act == "set_meta" and a:
            for k in ("title", "author", "type"):
                if k in ev:
                    a[k] = ev[k] or None
            touch(a, ev)
        elif act == "set_text" and a:
            a["text_override"] = ev.get("text", "")
            touch(a, ev)
        elif act == "set_frag_order" and a:
            want = ev.get("order", [])
            cur = {fragkey(fr): fr for fr in a["fragments"]}
            newlist = [cur[k] for k in want if k in cur]
            newlist += [fr for fr in a["fragments"]
                        if fragkey(fr) not in want]
            a["fragments"] = newlist
            touch(a, ev)
        elif act == "move_frag":
            src, fr = findfrag(ev.get("frag", ""))
            if not fr:
                continue
            if src == "unsorted":
                uns.remove(fr)
            else:
                src["fragments"].remove(fr)
                touch(src, ev)
            tgt = byid.get(ev.get("to_id", ""))
            if ev.get("to_id") == "new" or not tgt:
                n_user += 1
                na = {"article_id": f"{iid}_u{n_user:03d}", "type": "story",
                      "title": None, "author": None, "pages": [],
                      "fragments": [fr], "text": "", "status": "auto",
                      "verified_by": None, "verified_at": None,
                      "modified_by": [], "text_override": None,
                      "last_mod": ""}
                arts.append(na)
                byid[na["article_id"]] = na
                touch(na, ev)
            else:
                tgt["fragments"].append(fr)
                touch(tgt, ev)
        elif act == "frag_furniture":
            src, fr = findfrag(ev.get("frag", ""))
            if fr:
                if src == "unsorted":
                    uns.remove(fr)
                else:
                    src["fragments"].remove(fr)
                    touch(src, ev)
                user_furniture.append({"frag": ev.get("frag"),
                                       "by": ev["user"], "ts": ev["ts"]})
        elif act == "edit_frag_text":
            k = ev.get("frag", "")
            overrides[k] = ev.get("text", "")
            a2, _fr = findfrag(k)
            if a2 and a2 != "unsorted":
                touch(a2, ev)
        elif act == "merge" and a:
            tgt = byid.get(ev.get("into_id", ""))
            if tgt and tgt is not a:
                tgt["fragments"].extend(a["fragments"])
                a["fragments"] = []
                touch(a, ev)
                touch(tgt, ev)
        elif act == "verify" and a:
            a["verified_by"] = ev["user"]
            a["verified_at"] = ev["ts"]
        elif act == "unverify" and a:
            a["verified_by"] = None
            a["verified_at"] = None

    out = []
    for a in arts:
        if not a["fragments"] and not a["text_override"]:
            continue  # emptied by moves/merges
        a["pages"] = sorted({fr["page"] for fr in a["fragments"]}) or a["pages"]
        a["text"] = (a["text_override"] if a["text_override"] is not None
                     else assemble_text(iid, a["fragments"], overrides))
        if a["verified_at"] and a["verified_at"] >= a["last_mod"]:
            a["status"] = "verified"
        elif a["modified_by"]:
            a["status"] = "modified"
        out.append(a)
    return {**doc, "articles": out, "unsorted": uns,
            "frag_overrides": overrides,
            "user_furniture": user_furniture}


def issue_frag_map(iid, doc):
    """Per page: every segment (from any article, or unsorted) with a short
    unique id like 12A (page 12, first segment in reading order)."""
    per_page = {}
    def omin(pno, region_ids):
        regs = page_regions(iid, pno)
        vals = [regs[r].get("order", r) for r in region_ids if r < len(regs)]
        return min(vals) if vals else 999
    for a in doc["articles"]:
        for fr in a["fragments"]:
            per_page.setdefault(fr["page"], []).append(
                {"key": fragkey(fr), "owner": a["article_id"],
                 "title": a.get("title"), "region_ids": fr["region_ids"],
                 "page": fr["page"], "o": omin(fr["page"], fr["region_ids"])})
    for fr in doc.get("unsorted", []):
        per_page.setdefault(fr["page"], []).append(
            {"key": fragkey(fr), "owner": None, "title": "(unsorted)",
             "region_ids": fr["region_ids"], "page": fr["page"],
             "o": omin(fr["page"], fr["region_ids"])})
    ids = {}
    for pno, lst in per_page.items():
        lst.sort(key=lambda e: e["o"])
        for i, e in enumerate(lst):
            e["id"] = f"{pno}{chr(65 + i) if i < 26 else 'Z' + str(i)}"
            ids[e["key"]] = e["id"]
    return per_page, ids


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
.chip{font-size:11.5px;padding:1px 8px;border:1px solid;border-radius:9px;letter-spacing:.4px}
.stA{color:#75695a;border-color:#b8a88e;background:#f3ead9}
.stM{color:#7a5220;border-color:#c99b4e;background:#f7ecd4}
.stV{color:#2c5e2e;border-color:#7fae81;background:#e3efe3}
.mini{display:inline}
.mini button{font-size:11.5px;padding:1px 6px;border:1px solid #b8a88e;background:#fff;cursor:pointer;font-family:inherit}
.mini select{font-size:11.5px;padding:1px;border:1px solid #b8a88e;font-family:inherit}
.annform input[type=text]{font-size:14px;padding:3px;border:1px solid #b8a88e;font-family:inherit}
.annform select,.annform button{font-size:13px;padding:3px 8px;border:1px solid #b8a88e;background:#fff;font-family:inherit}
.annform textarea{width:100%;height:280px;font-size:13.5px;font-family:inherit;border:1px solid #b8a88e;padding:8px}
.fragsnip{color:#5a4f40;font-size:12.5px}
.wb{display:flex;gap:16px;align-items:flex-start}
.wbleft{flex:0 0 42%;max-height:86vh;overflow-y:auto;padding-right:4px}
.wbright{flex:1;min-width:0}
.scanwrap{position:relative;margin:0 0 14px}
.scanwrap img{width:100%;display:block;border:1px solid #b8a88e}
.scanwrap svg{position:absolute;left:0;top:0;width:100%;height:100%}
.scanwrap .pgcap{font-size:12px;color:#75695a;margin:2px 0 0}
.card{border:1px solid #b8a88e;background:#fff;margin:0 0 10px}
.card[draggable=true]{cursor:grab}
.card.dragging{opacity:.45}
.card.hl,.othercard.hl{outline:3px solid #7a3020}
.card .ch{background:#f3ead9;border-bottom:1px solid #d8cfc0;padding:4px 8px;font-size:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.idchip{display:inline-block;background:#1c1a17;color:#faf7f2;font-size:11.5px;padding:0 7px;letter-spacing:.6px;cursor:pointer}
.idchip.other{background:#75695a}
.cardtext{padding:8px 10px;font-size:13.5px;line-height:1.5;white-space:pre-wrap;max-height:170px;overflow-y:auto}
.cardtext.edited{background:#f7f3e8}
.othercard{opacity:.8;border-style:dashed;margin:0 0 10px}
.fbox rect{cursor:pointer}
.editbox{width:100%;height:150px;font-size:13px;font-family:inherit;border:1px solid #7a3020;padding:6px}
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


def page(title, body, member=True, path="/", admin=False):
    userslink = "<a href='/users'>users</a>" if admin else ""
    nav = ("<span class='nav'>"
           "<a href='/issues'>issues</a><a href='/articles'>articles</a>"
           "<a href='/method'>method</a>"
           "<a href='/timing'>timing</a><a href='/activity'>activity</a>"
           f"<a href='/feedback'>feedback</a>{userslink}"
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

    def _user(self):
        """Username for a named account, 'guest' for passcode access,
        None when not logged in. Site fully open (guest) with no password
        file and no users file."""
        c = self.headers.get("Cookie", "")
        m = re.search(r"pfauth=([^;]+)", c)
        if m:
            u = token_user(urllib.parse.unquote(m.group(1)))
            if u:
                return u
        if users() is None and site_password() is None:
            return "guest"
        return None

    def _member(self):
        self.user = self._user()
        return self.user is not None

    def _page(self, title, body, path="/"):
        return page(title, body, member=True, path=path,
                    admin=is_admin(getattr(self, "user", None)))

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
        if path == "/signup":
            return self._send(200, self.signup_page("", {}))
        if path == "/logout":
            return self._redirect("/", cookie="pfauth=; Max-Age=0; Path=/")
        if not self._member():
            return self._redirect("/login")
        if path == "/users":
            if not is_admin(self.user):
                return self._send(403, self._page(
                    "Admins only", "<h1>Admins only</h1><p class='muted'>"
                    "The user-management page is for administrator "
                    "accounts.</p>"))
            return self._send(200, self.users_page(""))
        if path == "/activity":
            return self._send(200, self.activity_page())
        if path == "/issues":
            return self._send(200, self.issues_page())
        if path == "/articles":
            return self._send(200, self.articles_page(qs))
        m = re.fullmatch(r"/article/([\w\-]+)", path)
        if m:
            return self._send(200, self.article_page(m.group(1)))
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
            uname, pword = get("username"), get("password")
            if uname and check_user(uname, pword):
                tok = make_token(uname)
                return self._redirect("/issues",
                    cookie=f"pfauth={urllib.parse.quote(tok)}; "
                           f"Max-Age={COOKIE_DAYS*86400}; Path=/; HttpOnly")
            pw = site_password()
            if (not uname and pw is not None
                    and hmac.compare_digest(get("passcode"), pw)):
                tok = make_token("guest")
                return self._redirect("/issues",
                    cookie=f"pfauth={urllib.parse.quote(tok)}; "
                           f"Max-Age={COOKIE_DAYS*86400}; Path=/; HttpOnly")
            if uname and is_pending(uname):
                return self._send(200, self.login_page(
                    "That account is still waiting for approval."))
            time.sleep(1.0)
            return self._send(200, self.login_page(
                "That login is not right."))
        if path == "/signup":
            return self.do_signup(get)
        if not self._member():
            return self._redirect("/login")
        if path == "/users":
            if not is_admin(self.user):
                return self._send(403, "admins only", "text/plain")
            return self.do_users_action(get)
        if path == "/feedback":
            os.makedirs(DATA, exist_ok=True)
            with open(FEEDBACK, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "path": get("path")[:300],
                    "name": get("name")[:80] or display_name(self.user),
                    "user": self.user,
                    "comment": get("comment")[:4000]}, ensure_ascii=False) + "\n")
            return self._redirect("/feedback")
        if path == "/annotate":
            if self.user == "guest":
                return self._send(403, self._page("No", howto(
                    "Annotation needs a named account, so the record shows "
                    "who verified or changed each article. Ask Heejin for "
                    "an account, then log in with it.") +
                    "<h1>Guests cannot annotate</h1>"))
            return self.do_annotate(get)
        return self._send(404, "no", "text/plain")

    def do_signup(self, get):
        uname = get("username").lower()
        name = get("name")
        note = get("note")[:300]
        pw, pw2 = get("password"), get("password2")
        vals = {"username": uname, "name": name, "note": note}
        if not re.fullmatch(r"[a-z0-9_]{2,24}", uname) or uname in (
                "guest", "admin", "root"):
            return self._send(200, self.signup_page(
                "Username: 2-24 characters, letters/digits/underscore "
                "only.", vals))
        if not name:
            return self._send(200, self.signup_page(
                "Please give your name.", vals))
        if len(pw) < 6:
            return self._send(200, self.signup_page(
                "Password: at least 6 characters.", vals))
        if pw != pw2:
            return self._send(200, self.signup_page(
                "The two passwords differ.", vals))
        with USERS_LOCK:
            u = users() or {}
            if uname in u:
                return self._send(200, self.signup_page(
                    "That username is taken.", vals))
            if sum(1 for r in u.values()
                   if account_status(r) == "pending") >= 100:
                return self._send(200, self.signup_page(
                    "Too many open requests right now — write to "
                    + CONTACT + " instead.", vals))
            salt = pysecrets.token_hex(8)
            u[uname] = {"name": name[:80], "salt": salt,
                        "pw": hashlib.sha256((salt + pw).encode()).hexdigest(),
                        "status": "pending", "note": note,
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            save_users(u)
        return self._send(200, self.signup_page(
            "Request received. The administrator will approve your "
            "account; you can log in once that has happened.", {}, ok=True))

    def do_users_action(self, get):
        uname = get("username")
        act = get("act")
        with USERS_LOCK:
            u = users() or {}
            rec = u.get(uname)
            if not rec:
                return self._redirect("/users")
            if act == "approve" and account_status(rec) == "pending":
                rec["status"] = "active"
                rec["approved_by"] = self.user
                rec["approved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            elif act == "remove" and uname != self.user:
                del u[uname]
            elif act == "make_admin" and account_status(rec) == "active":
                rec["role"] = "admin"
            save_users(u)
        return self._redirect("/users")

    def signup_page(self, msg, vals, ok=False):
        m = (f"<p class='{'muted' if ok else ''}' "
             f"style='{'color:#2c5e2e' if ok else 'color:#7a3020'}'>"
             f"{esc(msg)}</p>" if msg else "")
        form = "" if ok else f"""
<form method='POST' action='/signup'>
<p><input class='pw' type='text' name='username' placeholder='username'
value='{esc(vals.get('username', ''))}'></p>
<p><input class='pw' type='text' name='name' placeholder='your name'
value='{esc(vals.get('name', ''))}' size='30'></p>
<p><input class='pw' type='text' name='note' size='30'
placeholder='who you are / affiliation (shown to the admin)'
value='{esc(vals.get('note', ''))}'></p>
<p><input class='pw' type='password' name='password'
placeholder='password (min 6)'>
<input class='pw' type='password' name='password2'
placeholder='password again'></p>
<p><button class='go'>Request account</button></p></form>"""
        body = f"""<div class='land'><h1>Request an annotator account</h1>
<p>Accounts let you correct and verify articles; every action is recorded
under your name. New accounts start as requests and work after the
administrator approves them.</p>
{m}{form}
<p class='muted'><a href='/login'>back to login</a> · questions:
{CONTACT}</p></div>"""
        return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Sign up · Pulp Fiction Corpus</title><style>{CSS}</style></head>
<body>{body}</body></html>"""

    def users_page(self, msg):
        u = users() or {}
        rows = ""
        for uname in sorted(u, key=lambda x: (account_status(u[x]) != "pending",
                                              x)):
            rec = u[uname]
            st = account_status(rec)
            chip = ("<span class='chip stM'>pending</span>" if st == "pending"
                    else ("<span class='chip stV'>admin</span>"
                          if rec.get("role") == "admin"
                          else "<span class='chip stA'>annotator</span>"))
            acts = ""
            if st == "pending":
                acts += (f"<form class='mini' method='POST' action='/users'>"
                         f"<input type='hidden' name='username' "
                         f"value='{esc(uname)}'>"
                         f"<input type='hidden' name='act' value='approve'>"
                         f"<button>approve</button></form> ")
            elif rec.get("role") != "admin":
                acts += (f"<form class='mini' method='POST' action='/users'>"
                         f"<input type='hidden' name='username' "
                         f"value='{esc(uname)}'>"
                         f"<input type='hidden' name='act' value='make_admin'>"
                         f"<button>make admin</button></form> ")
            if uname != self.user:
                acts += (f"<form class='mini' method='POST' action='/users'>"
                         f"<input type='hidden' name='username' "
                         f"value='{esc(uname)}'>"
                         f"<input type='hidden' name='act' value='remove'>"
                         f"<button>remove</button></form>")
            approved = (f"approved by {esc(display_name(rec['approved_by']))}"
                        if rec.get("approved_by") else "")
            rows += (f"<tr><td>{esc(uname)}</td><td>{esc(rec.get('name', ''))}"
                     f"</td><td>{chip}</td>"
                     f"<td class='muted'>{esc(rec.get('note') or '')}</td>"
                     f"<td class='muted'>{esc(rec.get('created_at') or '')} "
                     f"{approved}</td><td>{acts}</td></tr>")
        npend = sum(1 for r in u.values() if account_status(r) == "pending")
        body = (howto(
            "Every account on the site. Requests from the sign-up page "
            "appear here as pending and cannot log in until approved. "
            "Approving records your name and the time. Removing an account "
            "does not remove the annotations it already made — those stay "
            "in the log under its name.")
            + f"<h1>Users ({len(u)} · {npend} pending)</h1>"
            + ("<table><tr><th>username</th><th>name</th><th>role</th>"
               "<th>note</th><th>history</th><th>actions</th></tr>"
               + rows + "</table>" if rows else
               "<div class='empty'>No accounts yet.</div>"))
        return self._page("Users", body, path="/users")

    def do_annotate(self, get):
        iid = get("issue")
        aid = get("article_id")
        act = get("act")
        back = get("back") or f"/article/{aid}"
        doc = effective_doc(iid)
        if not doc:
            return self._send(404, "no issue", "text/plain")
        art = next((a for a in doc["articles"]
                    if a["article_id"] == aid), None)
        if act == "meta" and art:
            ann_append(iid, self.user, "set_meta",
                       {"article_id": aid, "title": get("title"),
                        "author": get("author"), "type": get("type")})
        elif act == "text" and art:
            ann_append(iid, self.user, "set_text",
                       {"article_id": aid, "text": get("text")})
        elif act in ("up", "down") and art:
            order = [fragkey(fr) for fr in art["fragments"]]
            k = get("frag")
            if k in order:
                i = order.index(k)
                j = i - 1 if act == "up" else i + 1
                if 0 <= j < len(order):
                    order[i], order[j] = order[j], order[i]
                    ann_append(iid, self.user, "set_frag_order",
                               {"article_id": aid, "order": order})
        elif act == "detach" and art:
            ann_append(iid, self.user, "move_frag",
                       {"article_id": aid, "frag": get("frag"),
                        "to_id": "new"})
        elif act == "moveto" and get("to_id") and get("frag"):
            # source is wherever the fragment currently lives (an article
            # or the unsorted list); replay finds it by key
            ann_append(iid, self.user, "move_frag",
                       {"article_id": aid, "frag": get("frag"),
                        "to_id": get("to_id")})
        elif act == "order_js" and art and get("order"):
            order = [k for k in get("order").split(",") if k]
            ann_append(iid, self.user, "set_frag_order",
                       {"article_id": aid, "order": order})
        elif act == "fragtext" and get("frag"):
            ann_append(iid, self.user, "edit_frag_text",
                       {"article_id": aid, "frag": get("frag"),
                        "text": get("text")})
        elif act == "furniture" and art:
            ann_append(iid, self.user, "frag_furniture",
                       {"article_id": aid, "frag": get("frag")})
        elif act == "merge" and art and get("into_id"):
            ann_append(iid, self.user, "merge",
                       {"article_id": aid, "into_id": get("into_id")})
            back = f"/article/{get('into_id')}"
        elif act == "verify" and art:
            ann_append(iid, self.user, "verify", {"article_id": aid})
        elif act == "unverify" and art:
            ann_append(iid, self.user, "unverify", {"article_id": aid})
        return self._redirect(back)

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
        acct = ""
        if users() is not None:
            acct = """<h2 style='border:0'>Annotator account</h2>
<form method='POST' action='/login'>
<p><input class='pw' type='text' name='username' placeholder='username'
autofocus> <input class='pw' type='password' name='password'
placeholder='password'> <button class='go'>Log in</button></p></form>
<p class='muted'>Named accounts can verify and correct articles; every
action is recorded under your name. No account yet?
<a href='/signup'>Request one</a>.</p>"""
        body = f"""<div class='land'><h1>Collaborator access</h1>{m}
{acct}
<h2 style='border:0'>Guest (read only)</h2>
<form method='POST' action='/login'>
<p><input class='pw' type='password' name='passcode'
placeholder='shared passcode'> <button class='go'>Enter</button></p></form>
<p class='muted'>Accounts and the passcode: {CONTACT}.</p></div>"""
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
        return self._page("Issues", body, path="/issues")

    def issue_page(self, iid):
        info = issue_by_id(iid)
        if not info:
            return self._page("Unknown", "<h1>Unknown issue</h1>")
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
            + self.issue_articles_html(iid)
            + (f"<h2>Downloads (full text per stage)</h2><p>{dls}</p>" if sts else "")
            + ("<h2>Timing so far</h2><table><tr><th>stage</th><th>pages</th>"
               "<th>seconds</th><th>sec/page</th><th>note</th></tr>"
               + trows + "</table>" if t else
               "<div class='empty'>No timing rows for this issue yet.</div>"))
        return self._page(info["magazine"], body, path=f"/issue/{iid}")

    def issue_articles_html(self, iid):
        doc = effective_doc(iid)
        if not doc:
            return ("<h2>Articles</h2><div class='empty'>Not yet assembled "
                    "— the article stage (s07) has not run for this issue."
                    "</div>")
        rows = "".join(
            f"<tr><td><a href='/article/{a['article_id']}'>"
            f"{esc(a.get('title') or '(untitled)')}</a></td>"
            f"<td>{esc(a.get('author') or '')}</td>"
            f"<td>{esc(a.get('type') or '')}</td>"
            f"<td>{STATUS_CHIP.get(a.get('status'), '')}</td>"
            f"<td class='num'>{a['pages'][0] if a['pages'] else '?'}–"
            f"{a['pages'][-1] if a['pages'] else '?'}</td></tr>"
            for a in doc["articles"])
        nv = sum(1 for a in doc["articles"] if a.get("status") == "verified")
        extra = (f"<p class='muted'>{nv} of {len(doc['articles'])} verified · "
                 f"{len(doc.get('furniture', []))} page-furniture units from "
                 f"the machine + {len(doc.get('user_furniture', []))} marked "
                 f"by annotators · {len(doc.get('unsorted', []))} segments "
                 f"unsorted, kept for review</p>")
        return (f"<h2>Articles in this issue ({len(doc['articles'])})</h2>"
                "<table><tr><th>title as printed</th><th>author</th>"
                "<th>type</th><th>status</th><th>pages</th></tr>"
                + rows + "</table>" + extra)

    def viewer(self, iid, n, qs):
        info = issue_by_id(iid)
        if not info:
            return self._page("Unknown", "<h1>Unknown issue</h1>")
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
        return self._page(f"{info['magazine']} p{n}", body,
                    path=f"/issue/{iid}/p/{n}")

    def articles_page(self, qs):
        q = (qs.get("q", [""])[0] or "").strip().lower()
        typ = (qs.get("type", [""])[0] or "").strip()
        stat = (qs.get("status", [""])[0] or "").strip()
        cfgmap = {i["id"]: i for i in cfg()["issues"]}
        rows, types = [], set()
        for iid in cfgmap:
            doc = effective_doc(iid)
            if not doc:
                continue
            for a in doc["articles"]:
                types.add(a.get("type") or "other")
                rows.append({**a, "issue": iid,
                             "words": len((a.get("text") or "").split())})
        total = len(rows)
        if q:
            rows = [r for r in rows
                    if q in (r.get("title") or "").lower()
                    or q in (r.get("author") or "").lower()]
        if typ:
            rows = [r for r in rows if (r.get("type") or "other") == typ]
        if stat:
            rows = [r for r in rows if r.get("status") == stat]
        topts = "<option value=''>all types</option>" + "".join(
            f"<option value='{esc(t)}' {'selected' if t == typ else ''}>"
            f"{esc(t)}</option>" for t in sorted(types))
        sopts = "<option value=''>all statuses</option>" + "".join(
            f"<option value='{s}' {'selected' if s == stat else ''}>{s}"
            f"</option>" for s in ("auto", "modified", "verified"))
        form = (f"<form method='GET' action='/articles' "
                f"style='margin:0 0 14px'>"
                f"<input type='text' name='q' value='{esc(q)}' "
                f"placeholder='title or author' "
                f"style='font-size:14px;padding:4px;border:1px solid #b8a88e'> "
                f"<select name='type' style='font-size:14px;padding:4px'>"
                f"{topts}</select> "
                f"<select name='status' style='font-size:14px;padding:4px'>"
                f"{sopts}</select> "
                f"<button style='font-size:14px;padding:4px 10px'>find"
                f"</button></form>")
        trows = ""
        for r in rows:
            info = cfgmap.get(r["issue"], {})
            trows += (f"<tr><td><a href='/article/{r['article_id']}'>"
                      f"{esc(r.get('title') or '(untitled)')}</a></td>"
                      f"<td>{esc(r.get('author') or '')}</td>"
                      f"<td>{esc(r.get('type') or '')}</td>"
                      f"<td>{STATUS_CHIP.get(r.get('status'), '')}</td>"
                      f"<td><a href='/issue/{r['issue']}'>"
                      f"{esc(info.get('magazine', r['issue']))} "
                      f"{esc(info.get('cover_date', ''))}</a></td>"
                      f"<td class='num'>{r['pages'][0] if r.get('pages') else ''}"
                      f"–{r['pages'][-1] if r.get('pages') else ''}</td>"
                      f"<td class='num'>{r.get('words', '')}</td></tr>")
        nv = sum(1 for r in rows if r.get("status") == "verified")
        body = (howto(
            "Every separately printed unit — stories, serial installments, "
            "poems, features, letters pages, advertisements — one row each, "
            "findable by title or author exactly as printed. The status "
            "column shows whether a row is the machine's untouched output "
            "(automatic), corrected by a person (modified), or checked and "
            "confirmed (verified). Click a title to view — and, with an "
            "annotator account, to fix and verify it.")
            + f"<h1>Articles ({len(rows)} of {total} · {nv} verified)</h1>"
            + form
            + ("<table><tr><th>title as printed</th><th>author as printed"
               "</th><th>type</th><th>status</th><th>issue</th><th>pages"
               "</th><th>words</th></tr>" + trows + "</table>" if trows else
               "<div class='empty'>0 articles match. If the whole table is "
               "empty, the assembly stage (s07) has not run yet.</div>"))
        return self._page("Articles", body, path="/articles")

    def article_page(self, aid):
        art, doc = article_by_id(aid)
        if not art:
            return self._page("Unknown", "<h1>Unknown article</h1>")
        iid = doc["issue"]
        info = issue_by_id(iid) or {}
        can = self.user != "guest"
        others = [a for a in doc["articles"] if a["article_id"] != aid]
        per_page, ids = issue_frag_map(iid, doc)
        overrides = doc.get("frag_overrides", {})

        def hidden(**kw):
            s = (f"<input type='hidden' name='issue' value='{iid}'>"
                 f"<input type='hidden' name='article_id' value='{aid}'>")
            for k, v in kw.items():
                s += f"<input type='hidden' name='{k}' value='{esc(str(v))}'>"
            return s

        def mini(label, **kw):
            return ("<form class='mini' method='POST' action='/annotate'>"
                    + hidden(**kw) + f"<button>{label}</button></form> ")

        st = art.get("status", "auto")
        stline = STATUS_CHIP.get(st, "")
        order_ids = [a["article_id"] for a in doc["articles"]]
        nxt = None
        if aid in order_ids:
            k0 = order_ids.index(aid)
            ring = order_ids[k0 + 1:] + order_ids[:k0]
            nxt = next((x for x in ring
                        if next(a for a in doc["articles"]
                                if a["article_id"] == x)["status"] != "verified"),
                       None)
        if st == "verified":
            stline += (f" <span class='muted'>by "
                       f"{esc(display_name(art['verified_by']))} at "
                       f"{esc(art['verified_at'])}</span>")
            if can:
                stline += " " + mini("remove verification", act="unverify")
        else:
            if art.get("modified_by"):
                stline += (" <span class='muted'>changed by "
                           + esc(", ".join(display_name(u)
                                           for u in art["modified_by"]))
                           + "</span>")
            if can:
                stline += " " + mini("mark as verified", act="verify")
                if nxt:
                    stline += mini("verify, then next unverified",
                                   act="verify", back=f"/article/{nxt}")

        metaform = ""
        if can:
            topts = "".join(
                f"<option value='{t}' "
                f"{'selected' if t == (art.get('type') or 'other') else ''}>"
                f"{t}</option>"
                for t in ("story", "serial_part", "poem", "feature",
                          "letters", "toc", "ad", "other"))
            metaform = ("<form class='annform' method='POST' "
                        "action='/annotate' style='margin:8px 0'>"
                        + hidden(act="meta")
                        + f"<input type='text' name='title' size='40' "
                        f"value='{esc(art.get('title') or '')}' "
                        f"placeholder='title as printed'> "
                        f"<input type='text' name='author' size='24' "
                        f"value='{esc(art.get('author') or '')}' "
                        f"placeholder='author as printed'> "
                        f"<select name='type'>{topts}</select> "
                        f"<button>save title / author / type</button></form>")

        # LEFT: scans with labeled boxes
        left = ""
        for pno in art["pages"]:
            lay = layout_of(iid, pno) or {}
            W = lay.get("width") or 1000
            H = lay.get("height") or 1400
            regs = page_regions(iid, pno)
            boxes = ""
            for e in per_page.get(pno, []):
                mine = e["owner"] == aid
                stroke = "#7a3020" if mine else "#75695a"
                dash = "" if mine else " stroke-dasharray='14,10'"
                inner, lx, ly = "", None, None
                for r in e["region_ids"]:
                    if r < len(regs):
                        x0, y0, x1, y1 = regs[r]["bbox"]
                        inner += (f"<rect x='{x0}' y='{y0}' "
                                  f"width='{x1-x0}' height='{y1-y0}' "
                                  f"fill='rgba(0,0,0,0)' stroke='{stroke}' "
                                  f"stroke-width='{4 if mine else 3}'{dash}/>")
                        if lx is None:
                            lx, ly = x0, y0
                if lx is not None:
                    fs = max(26, int(H * 0.022))
                    w = int(fs * 0.66 * len(e["id"])) + 12
                    ty = max(ly - 8, fs)
                    inner += (f"<rect x='{lx}' y='{max(ly-fs-10, 0)}' "
                              f"width='{w}' height='{fs+8}' fill='{stroke}'/>"
                              f"<text x='{lx+6}' y='{ty}' fill='#faf7f2' "
                              f"font-size='{fs}'>{e['id']}</text>")
                boxes += (f"<g class='fbox' data-key='{e['key']}' "
                          f"data-selkey='{e['key']}'>{inner}</g>")
            left += (f"<div class='scanwrap'>"
                     f"<img src='/img/{iid}/page_{pno:04d}.png' "
                     f"alt='page {pno}'>"
                     f"<svg viewBox='0 0 {W} {H}' "
                     f"preserveAspectRatio='none'>{boxes}</svg>"
                     f"<div class='pgcap'>page {pno} · solid = this "
                     f"article, dashed = other articles</div></div>")
        if not art["pages"]:
            left = "<div class='empty'>No scan pages for this article.</div>"

        # RIGHT: one draggable card per segment
        cards = ""
        for fr in art.get("fragments", []):
            k = fragkey(fr)
            fid = ids.get(k, "?")
            txt = frag_text(iid, fr, overrides)
            corrected = k in overrides
            btns = ""
            if can:
                btns = (mini("not story text", act="furniture", frag=k)
                        + mini("detach", act="detach", frag=k))
                if others:
                    opts = "".join(
                        f"<option value='{o['article_id']}'>"
                        f"{esc((o.get('title') or o['article_id'])[:34])}"
                        f"</option>" for o in others)
                    btns += ("<form class='mini' method='POST' "
                             "action='/annotate'>"
                             + hidden(act="moveto", frag=k)
                             + f"<select name='to_id'>{opts}</select>"
                             f"<button>move to</button></form>")
            cards += (f"<div class='card' data-key='{k}'"
                      f"{' draggable=true' if can else ''}>"
                      f"<div class='ch'>"
                      f"<span class='idchip' data-selkey='{k}'>{fid}</span>"
                      f"<span class='muted'>page {fr['page']}"
                      f"{' · corrected' if corrected else ''}</span>"
                      f"<span style='margin-left:auto'>{btns}</span></div>"
                      f"<div class='cardtext{' edited' if corrected else ''}' "
                      f"data-key='{k}'>{esc(txt)}</div></div>")

        # other segments on the same pages
        oth = ""
        for pno in art["pages"]:
            for e in per_page.get(pno, []):
                if e["owner"] == aid:
                    continue
                fr = {"page": e["page"], "region_ids": e["region_ids"]}
                txt = frag_text(iid, fr, overrides)[:180]
                owner = ("<a href='/article/" + e["owner"] + "'>"
                         + esc((e.get("title") or e["owner"])[:36]) + "</a>"
                         if e["owner"] else "(unsorted)")
                add = (mini("add to this article", act="moveto",
                            frag=e["key"], to_id=aid) if can else "")
                oth += (f"<div class='othercard card' data-key='{e['key']}'>"
                        f"<div class='ch'><span class='idchip other' "
                        f"data-selkey='{e['key']}'>{e['id']}</span>"
                        f"<span class='muted'>belongs to {owner}</span>"
                        f"<span style='margin-left:auto'>{add}</span></div>"
                        f"<div class='cardtext'>{esc(txt)}</div></div>")

        mergeform = ""
        if can and others:
            opts = "".join(
                f"<option value='{o['article_id']}'>"
                f"{esc((o.get('title') or o['article_id'])[:48])}</option>"
                for o in others)
            mergeform = ("<form class='mini' method='POST' "
                         "action='/annotate'>" + hidden(act="merge")
                         + f"<select name='into_id'>{opts}</select>"
                         f"<button>merge this whole article into</button>"
                         f"</form>")

        textblock = ("<details><summary class='muted' "
                     "style='cursor:pointer'>assembled reading text "
                     "(from the segments above)</summary>"
                     "<pre style='max-height:none'>"
                     + esc(art.get("text") or "(no text)") + "</pre>"
                     "</details>")

        meta = (f"{esc(art.get('type') or '')} · "
                f"<a href='/issue/{iid}'>{esc(info.get('magazine', iid))} "
                f"{esc(info.get('cover_date', ''))}</a> · pages "
                f"{art['pages'][0] if art['pages'] else '?'}–"
                f"{art['pages'][-1] if art['pages'] else '?'} · "
                f"{len((art.get('text') or '').split())} words · assembled "
                f"by {esc(doc.get('backend', '?'))}")
        guestnote = ("" if can else
                     "<p class='muted'>You are viewing as guest — log in "
                     "with an annotator account to correct or verify.</p>")
        script = ("<script>"
                  + WB_JS.replace("__ISSUE__", iid).replace("__AID__", aid)
                  .replace("__CAN__", "true" if can else "false")
                  + "</script>")
        body = (howto(
            "Left: the scans, with every segment boxed and labeled "
            "(12A = page 12, first segment). Solid boxes belong to this "
            "article; dashed ones belong elsewhere. Right: the same "
            "segments as cards in reading order, same labels. Click a box "
            "or a label to flash its partner. Drag cards to fix the order "
            "(saved instantly). Double-click a card's text to correct OCR "
            "errors in place. 'Not story text' expels a page number or a "
            "'Continued from page' notice; 'add to this article' pulls in "
            "a segment that was assigned elsewhere. When everything is "
            "right, mark it verified — 'verify, then next unverified' "
            "moves you straight on. Every action is recorded under your "
            "name; the machine's output is never overwritten.")
            + f"<h1>{esc(art.get('title') or '(untitled)')}</h1>"
            + (f"<p>by {esc(art['author'])}</p>" if art.get("author") else "")
            + f"<p class='muted'>{meta}</p>"
            + f"<p>{stline}</p>" + guestnote + metaform
            + "<div class='wb'><div class='wbleft'>" + left + "</div>"
            + "<div class='wbright'>"
            + f"<h2>Segments in this article "
              f"({len(art.get('fragments', []))})</h2>"
            + "<div id='cards'>" + cards + "</div>"
            + (("<h2>On these pages, assigned elsewhere</h2>" + oth)
               if oth else "")
            + (f"<p>{mergeform}</p>" if mergeform else "")
            + textblock
            + "</div></div>" + script)
        return self._page(art.get("title") or aid, body,
                          path=f"/article/{aid}")


    def activity_page(self):
        events = []
        if os.path.isdir(ANNDIR):
            for f in os.listdir(ANNDIR):
                if f.endswith(".jsonl"):
                    for line in open(os.path.join(ANNDIR, f),
                                     encoding="utf-8"):
                        try:
                            events.append(json.loads(line))
                        except Exception:
                            pass
        events.sort(key=lambda e: e.get("ts", ""), reverse=True)
        rows = "".join(
            f"<tr><td class='muted'>{esc(e.get('ts', ''))}</td>"
            f"<td>{esc(display_name(e.get('user', '?')))}</td>"
            f"<td>{esc(e.get('action', ''))}</td>"
            f"<td><a href='/article/{esc(e.get('article_id', ''))}'>"
            f"{esc(e.get('article_id', ''))}</a></td>"
            f"<td class='muted'>{esc(str(e.get('frag') or e.get('into_id') or e.get('to_id') or ''))}</td></tr>"
            for e in events[:400])
        body = (howto(
            "The complete annotation record, newest first: who verified or "
            "changed which article, when, and how. The machine output is "
            "never edited in place, so the original and every human action "
            "are both preserved.")
            + f"<h1>Annotation activity ({len(events)})</h1>"
            + ("<table><tr><th>when</th><th>who</th><th>action</th>"
               "<th>article</th><th>detail</th></tr>" + rows + "</table>"
               if events else
               "<div class='empty'>No annotations yet. Corrections and "
               "verifications made on article pages appear here.</div>"))
        return self._page("Activity", body, path="/activity")


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
        return self._page("Method", body, path="/method")

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
        return self._page("Timing", body, path="/timing")

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
        return self._page("Feedback", body, path="/feedback")


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
