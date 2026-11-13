#!/usr/bin/env python3
"""The continuity upgrade's result read (12 November 2026). Read-only.

scripts/upgrade_continuity.py leaves one receipt per issue (data/corpus/continuity-2026-10-07/<issue>/receipt.json)
and results.json for the last run. This script reads the receipts of the issues a run named and reports the issues
the guard refused by magazine, by decade and by reason, so that the refusals of an install can be judged as a
group (one title? one decade? one cause?) before anything further is done to them. It works on any root: the
corpus, or the separate root of a trial (--root).

    python3 scripts/upgrade_report.py [--root DIR] [--issues-file JSON] [--out-dir DIR] [--name upgrade_report]
    python3 scripts/upgrade_report.py --selftest
"""
import argparse
import collections
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join("data", "corpus", "continuity-2026-10-07")


def reason_of(error, iid=""):
    """The refusal's reason without the record it names: 'cannot safely retain cross-issue metadata' and the like."""
    e = (error or "").strip()
    if iid:
        e = e.replace(iid, "<issue>")
    e = re.sub(r"<issue>_[au]\d+\w*", "<record>", e)
    e = re.sub(r"\s+for <record>$", "", e)
    e = re.sub(r"\[.*\]|\(.*\)", "…", e)
    return e[:90] or "(no message)"


def decade_of(iid, meta=None):
    y = (meta or {}).get("year")
    if not isinstance(y, int):
        m = re.search(r"_((?:18|19)\d\d)(?:_|$)", iid)
        y = int(m.group(1)) if m else None
    return f"{y // 10 * 10}s" if y else "undated"


def magazine_of(iid, meta=None):
    return (meta or {}).get("magazine") or re.sub(r"_(18|19)\d\d.*$|_undated.*$", "", iid).replace("_", " ")


def read(root, ids=None):
    base = os.path.join(root, BASE)
    if ids is None:
        try:
            ids = json.load(open(os.path.join(base, "results.json"), encoding="utf-8"))["run"]["issues"]
        except (OSError, ValueError, KeyError):
            ids = sorted(d for d in os.listdir(base) if os.path.exists(os.path.join(base, d, "receipt.json"))) if os.path.isdir(base) else []
    rows = []
    for iid in ids:
        try:
            r = json.load(open(os.path.join(base, iid, "receipt.json"), encoding="utf-8"))
        except (OSError, ValueError):
            r = {"status": "no receipt"}
        try:
            backend = json.load(open(os.path.join(root, "data", "assembly_v2", "rules", iid, "articles.json"), encoding="utf-8")).get("backend")
        except (OSError, ValueError):
            backend = None
        rows.append({"issue": iid, "status": r.get("status"), "target": r.get("target"), "error": r.get("error"), "backend": backend})
    return rows


def report(rows, cfg=None, stamp="", totals_by_magazine=None):
    cfg = cfg or {}
    st = collections.Counter(r["status"] for r in rows)
    bad = [r for r in rows if r["status"] != "complete"]
    L = [f"# The continuity upgrade read ({stamp})", "",
         f"{len(rows):,} issues named by the run: " + ", ".join(f"{k} {v:,}" for k, v in st.most_common()) + ". Installed backends now: " +
         ", ".join(f"{k} {v:,}" for k, v in collections.Counter(r["backend"] or "(none)" for r in rows).most_common()) + ".", ""]
    by_reason = collections.Counter(reason_of(r["error"], r["issue"]) for r in bad)
    by_dec, all_dec = collections.Counter(decade_of(r["issue"], cfg.get(r["issue"])) for r in bad), collections.Counter(decade_of(r["issue"], cfg.get(r["issue"])) for r in rows)
    by_mag, all_mag = collections.Counter(magazine_of(r["issue"], cfg.get(r["issue"])) for r in bad), collections.Counter(magazine_of(r["issue"], cfg.get(r["issue"])) for r in rows)
    L += ["## Not installed, by reason", "", "| reason | issues |", "|---|---|"] + [f"| {k} | {v} |" for k, v in by_reason.most_common()]
    L += ["", "## Not installed, by decade", "", "| decade | not installed | of | share |", "|---|---|---|---|"]
    L += [f"| {d} | {by_dec.get(d, 0)} | {all_dec[d]} | {by_dec.get(d, 0) / all_dec[d]:.1%} |" for d in sorted(all_dec)]
    L += ["", "## Not installed, by magazine (every title with one or more)", "", "| magazine | not installed | of | share | reasons |", "|---|---|---|---|---|"]
    for m, n in sorted(by_mag.items(), key=lambda kv: (-kv[1], kv[0])):
        rs = collections.Counter(reason_of(r["error"], r["issue"]) for r in bad if magazine_of(r["issue"], cfg.get(r["issue"])) == m)
        L.append(f"| {m} | {n} | {all_mag[m]} | {n / all_mag[m]:.0%} | {'; '.join(f'{k} ({v})' for k, v in rs.most_common(3))} |")
    L += ["", "## The issues not installed", "", "| issue | decade | status | backend left | message |", "|---|---|---|---|---|"]
    L += [f"| {r['issue']} | {decade_of(r['issue'], cfg.get(r['issue']))} | {r['status']} | {r['backend']} | {(r['error'] or '')[:140].replace('|', '/')} |" for r in sorted(bad, key=lambda r: r["issue"])]
    summary = {"issues": len(rows), "status": dict(st), "by_reason": dict(by_reason), "by_decade": {d: [by_dec.get(d, 0), all_dec[d]] for d in sorted(all_dec)},
               "by_magazine": {m: [n, all_mag[m]] for m, n in by_mag.items()}, "not_installed": [r for r in bad]}
    return "\n".join(L) + "\n", summary


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        for iid, rec, backend in (("weird_tales_1936_10_x", {"status": "complete"}, "rules_v2.15"),
                                  ("weird_tales_1937_01_y", {"status": "failed", "error": "cannot safely retain cross-issue metadata for weird_tales_1937_01_y_a041"}, "rules_v2.14"),
                                  ("adventure_1925_03_z", {"status": "failed", "error": "candidate loses region identities: [(3, 4)]"}, "rules_v2.13")):
            os.makedirs(os.path.join(d, BASE, iid)); os.makedirs(os.path.join(d, "data", "assembly_v2", "rules", iid))
            json.dump(rec, open(os.path.join(d, BASE, iid, "receipt.json"), "w"))
            json.dump({"backend": backend}, open(os.path.join(d, "data", "assembly_v2", "rules", iid, "articles.json"), "w"))
        rows = read(d)
        md, s = report(rows, {"adventure_1925_03_z": {"magazine": "Adventure", "year": 1925}}, "test")
    assert s["status"] == {"complete": 1, "failed": 2}
    assert s["by_reason"] == {"cannot safely retain cross-issue metadata": 1, "candidate loses region identities: …": 1}, s["by_reason"]
    assert s["by_decade"] == {"1920s": [1, 1], "1930s": [1, 2]} and s["by_magazine"] == {"weird tales": [1, 2], "Adventure": [1, 1]}
    assert "| weird tales | 1 | 2 | 50% |" in md and "rules_v2.14" in md
    print("selftest ok")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--issues-file")
    ap.add_argument("--out-dir")
    ap.add_argument("--name", default="upgrade_report")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    ids = json.load(open(a.issues_file, encoding="utf-8")) if a.issues_file else None
    cfg = {}
    try:
        cfg = {i["id"]: i for i in json.load(open(os.path.join(a.root, "config", "corpus_issues.json"), encoding="utf-8")).get("issues", [])}
    except (OSError, ValueError):
        pass
    rows = read(a.root, ids)
    md, s = report(rows, cfg, time.strftime("%Y-%m-%d %H:%M"))
    out = a.out_dir or os.path.join(a.root, "pilot_export", "upgrade-2.15-" + time.strftime("%Y-%m-%d"))
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, a.name + ".md"), "w", encoding="utf-8").write(md)
    json.dump(s, open(os.path.join(out, a.name + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(md.splitlines()[2])
    print("by reason: " + "; ".join(f"{k} = {v}" for k, v in sorted(s["by_reason"].items(), key=lambda kv: -kv[1])[:6]))
    print("by decade (not installed/of): " + ", ".join(f"{d} {v[0]}/{v[1]}" for d, v in s["by_decade"].items()))
    print(f"written {out}/{a.name}.md and .json")


if __name__ == "__main__":
    main()
