#!/usr/bin/env python3
"""Did a machine run change what the annotators see? Compare the EFFECTIVE
records (machine record + the replayed annotation log, through the site's
own engine) of two states of an issue, for every record a person touched.

    python3 scripts/compare_effective.py --before data/assembly_archive/20260904_100220_refresh/articles --after data/articles
    python3 scripts/compare_effective.py --before ... --after ... --issue td_1932_02 --all-records

--before / --after: directories holding <issue>/articles.json (the live
tree, or an archive's articles/ folder). The annotation log is the live
one (data/annotations) for both sides unless --ann-before / --ann-after
name other folders of <issue>.jsonl. Only records a person modified or
verified are compared unless --all-records. Written 2026-09-04, when a
forced refresh had changed two verified records; the check that the
protected refresh leaves every verified record identical is this script.

--early DIR (a kept copy, data/assembly_archive/<stamp>_refresh): for a
record verified BEFORE that copy's stamp the "before" side is taken from
there — what the person saw when verifying — and from --before for the
rest. That is the check after scripts/switch_assembly.py --refresh
--verified-from DIR: every verified record must come out "identical".
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "webapp"))
for k, v in (("PULP_SITE_PASSWORD_FILE", "/nonexistent"), ("PULP_SECRET_FILE", "/tmp/.pulp_eval_secret"),
             ("PULP_USERS_FILE", "/nonexistent"), ("PULP_API_TOKEN_FILE", "/nonexistent")):
    os.environ.setdefault(k, v)
import app as A  # noqa: E402


def effective(iid, art_dir, ann_dir):
    def articles_of(i):
        p = os.path.join(art_dir, i, "articles.json")
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

    def ann_events(i):
        p = os.path.join(ann_dir, f"{i}.jsonl")
        out = []
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out
    A.articles_of, A.ann_events = articles_of, ann_events
    return A.effective_doc(iid)


def region_text(iid, k):
    if not k[:1].isdigit():
        return ""
    pno, r = k.split(":")
    regs = A.page_regions(iid, int(pno))
    return (regs[int(r)].get("text") or "").replace("\n", " ")[:60] if int(r) < len(regs) else "?"


def sortkey(k):
    return tuple(int(x) for x in k.split(":")) if k[:1].isdigit() else (10 ** 9, 0)


def stamp_of(path):
    """The <stamp> of data/assembly_archive/<stamp>_refresh, as an ISO time, from
    anywhere in the path (the archive's articles/ folder is what is passed, so
    the basename alone is "articles"; the last stamp in the path wins)."""
    import re as _re
    ms = _re.findall(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", os.path.abspath(path))
    return "{}-{}-{}T{}:{}:{}".format(*ms[-1]) if ms else None


def compare(iid, before, after, ann_before, ann_after, all_records=False, early=None):
    db = effective(iid, before, ann_before)
    da = effective(iid, after, ann_after)
    de = effective(iid, early, ann_before) if early else None
    early_stamp = stamp_of(early) if early else None
    if not db or not da:
        print(f"{iid}: missing on one side (before {bool(db)}, after {bool(da)})")
        return 0
    B = {a["article_id"]: a for a in db["articles"]}
    Af = {a["article_id"]: a for a in da["articles"]}
    rb, ra = db["frag_roles"], da["frag_roles"]
    if de:
        # records verified before the early copy's stamp: the state they were verified in is the early one
        E = {a["article_id"]: a for a in de["articles"]}
        for aid, a in list(Af.items()):
            if a.get("verified_at") and early_stamp and a["verified_at"] < early_stamp and aid in E:
                B[aid] = E[aid]
        rb = dict(rb)
        for k, v in de["frag_roles"].items():
            if any(A.fragkey(fr) == k for aid, a in B.items() if a.get("verified_at") and early_stamp and a["verified_at"] < early_stamp for fr in a["fragments"]):
                rb[k] = v
    n_diff = 0
    print(f"\n== {iid}: effective records before {len(B)}, after {len(Af)}")
    for aid in sorted(set(B) | set(Af)):
        x, y = B.get(aid), Af.get(aid)
        if not x or not y:
            z = x or y
            if all_records or z.get("modified_by") or z.get("verified_at"):
                print(f"  {aid}: only {'BEFORE' if x else 'AFTER'} ({z.get('status')}) {(z.get('title') or '')[:40]!r}")
                n_diff += 1
            continue
        if not all_records and not (x.get("modified_by") or x.get("verified_at") or y.get("modified_by") or y.get("verified_at")):
            continue
        kx = [A.fragkey(fr) for fr in x["fragments"]]
        ky = [A.fragkey(fr) for fr in y["fragments"]]
        rx = {k: rb.get(k) for k in kx}
        ry = {k: ra.get(k) for k in ky}
        meta_same = all(x.get(f) == y.get(f) for f in ("title", "author", "type"))
        tag = f"VERIFIED {y['verified_at'][:16]}" if y.get("verified_at") else ("annotated" if (y.get("modified_by") or x.get("modified_by")) else "machine")
        if kx == ky and rx == ry and x["text"] == y["text"] and meta_same and x["status"] == y["status"]:
            print(f"  {aid} [{tag}] identical")
            continue
        n_diff += 1
        print(f"  {aid} [{tag}] DIFFERS: regions {len(kx)}->{len(ky)} same_set={set(kx) == set(ky)} same_order={kx == ky} "
              f"roles_same={rx == ry} text_same={x['text'] == y['text']} meta_same={meta_same} status {x['status']}->{y['status']}")
        for k in sorted(set(ky) - set(kx), key=sortkey):
            print(f"      + {k:8s} {ry.get(k)!s:14s} {region_text(iid, k)}")
        for k in sorted(set(kx) - set(ky), key=sortkey):
            print(f"      - {k:8s} {rx.get(k)!s:14s} {region_text(iid, k)}")
        for k in sorted(set(kx) & set(ky), key=sortkey):
            if rx.get(k) != ry.get(k):
                print(f"      ~ {k:8s} {rx.get(k)!s} -> {ry.get(k)!s}  {region_text(iid, k)}")
        if not meta_same:
            print(f"      meta before {x.get('title')!r} / {x.get('author')!r} / {x.get('type')}; after {y.get('title')!r} / {y.get('author')!r} / {y.get('type')}")
    return n_diff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True, help="directory of <issue>/articles.json")
    ap.add_argument("--after", required=True)
    ap.add_argument("--ann-before", default=os.path.join(ROOT, "data", "annotations"))
    ap.add_argument("--ann-after", default=os.path.join(ROOT, "data", "annotations"))
    ap.add_argument("--issue")
    ap.add_argument("--all-records", action="store_true")
    ap.add_argument("--early", help="a kept copy whose records are the before-state of records verified before its stamp")
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config", "pilot_issues.json"), encoding="utf-8"))
    ids = [args.issue] if args.issue else [i["id"] for i in cfg["issues"]]
    rel = lambda p: p if os.path.isabs(p) else os.path.join(ROOT, p)  # noqa: E731
    if args.early:
        if not stamp_of(args.early):
            sys.exit(f"--early {args.early}: no <stamp>_refresh in the path, so no record could be taken from it")
        print(f"[early] records verified before {stamp_of(args.early)} are compared against {args.early}")
    total = 0
    for iid in ids:
        total += compare(iid, rel(args.before), rel(args.after), rel(args.ann_before), rel(args.ann_after), args.all_records,
                         early=(rel(args.early) if args.early else None))
    print(f"\n{total} record(s) differ")


if __name__ == "__main__":
    main()
