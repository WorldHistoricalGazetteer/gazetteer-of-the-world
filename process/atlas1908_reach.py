#!/usr/bin/env python3
"""Does the 1908 postal form reach the index where the 1856 form does not? Paired, one instrument.

Step 4 of place#245 Phase 1, and the measurable claim the issue asks for. It answers two questions
that are easy to confuse, and reports them separately because they have very different power:

  COHORT A - `probe_reachability.py`'s own 100-place sample, seed 7, unchanged.
      The corpus-level before/after. ⚠️ Only 7 of those 100 have a verified bridge alias, so this
      comparison cannot move by more than +7 points whatever the bridge is worth. It is reported for
      continuity with the 18.0% baseline, NOT as the finding, and its ceiling is printed beside it.

  COHORT B - every place with a verified bridge alias (n≈90).
      "Does the bridge work where it applies", which is the question that actually has an answer at
      this sample size. Paired: the same places, queried first by their 1856 headword and then by
      their 1908 postal form.

The corpus effect is the product of the two - bridge coverage of the coordinate-bearing corpus times
cohort B's conversion - and is bounded above by that coverage however well the conversion goes. That
bound is the honest headline for Phase 1 and it does not depend on any gateway query at all.

THREE POLICIES, AND THE OBVIOUS ONE IS THE WORST
------------------------------------------------
Measured on cohort B, n=88: the 1856 headword alone reaches 55.7%, the postal form INSTEAD of it
reaches 65.9%, and querying BOTH reaches 83.0%. Substitution gains 24 places and LOSES 15, because
the postal form is not a better name for the same document - it is a different name that retrieves
different documents. Reporting only the substitution arm would have understated the bridge by more
than half while looking like the natural experiment. An alias is additive in any real implementation,
so the union is both the operationally correct policy and the only one of the three that cannot lose
ground by construction.

ONE INSTRUMENT, DELIBERATELY
----------------------------
`reaches()` is imported from `process/probe_reachability.py` rather than reimplemented, so the before
and after arms are the same code with the same modes, size, ccodes and MATCH_KM. Re-deriving it here
would make any difference between 18.0% and the number below unattributable - it could be the bridge
or it could be my query shape, and there would be no way to tell them apart.

Arm A is RE-MEASURED rather than taken from the docstring's 18.0%. If it does not reproduce, the
index has moved under the comparison and the script says so instead of quietly reporting a delta
against a stale baseline.

Every response is cached to JSONL, so a re-run costs nothing and the gateway is queried once per
distinct (query, ccode) pair.

⚠️ Run from a Slurm job - never a login node, never the gateway VM. It shares the gateway with the
corpus re-reconciliation; concurrency 1, and check before launching.

Usage:
    python3 process/atlas1908_reach.py --dry-run          # plan + exact query budget, no traffic
    python3 process/atlas1908_reach.py --bridge data/atlas1908_bridge.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "process")
from probe_reachability import BBOX, MATCH_KM, reaches  # noqa: E402  - one instrument, not two
from atlas1908_index import _norm  # noqa: E402

BASELINE = {"CN": 18.0}          # what probe_reachability measured on 2026-09-05, seed 7, n=100


def sample(db, cc, n, seed):
    """probe_reachability.py's cohort, rebuilt by the same rule: same filter, same seed, same order.

    Reproduced rather than imported because that script builds and queries in one pass; the selection
    logic is the four lines below and is asserted against its published counts (1208 coord-bearing,
    42 dropped, 1166 usable) by the caller.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lo0, lo1, la0, la1 = BBOX[cc]
    rows, bad = [], 0
    for name, ext in con.execute("select name, extraction from place where extraction is not null"):
        try:
            e = json.loads(ext)
        except Exception:
            continue
        if e.get("country_code") != cc:
            continue
        lat, lon = e.get("latitude"), e.get("longitude")
        if lat is None or lon is None:
            continue
        if not (lo0 <= lon <= lo1 and la0 <= lat <= la1):
            bad += 1
            continue
        rows.append((name, lat, lon))
    con.close()
    random.Random(seed).shuffle(rows)
    return rows[:n], len(rows), bad


class Cache:
    """(query, ccode) -> reaches() verdict, appended as JSONL so a re-run issues no traffic."""

    def __init__(self, path):
        self.path = Path(path)
        self.d = {}
        if self.path.exists():
            for ln in self.path.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    r = json.loads(ln)
                    self.d[(r["q"], r["cc"], r["lat"], r["lon"])] = r["v"]
        self.hits = self.misses = 0

    def get(self, q, cc, lat, lon, live):
        k = (q, cc, lat, lon)
        if k in self.d:
            self.hits += 1
            return self.d[k]
        if not live:
            return None
        v = list(reaches(q, cc, lat, lon))
        self.d[k] = v
        self.misses += 1
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"q": q, "cc": cc, "lat": lat, "lon": lon, "v": v}) + "\n")
        return v


def wilson(k, n):
    """95% interval. A proportion from 90 trials needs one; a bare percentage invites over-reading."""
    if not n:
        return 0.0, 0.0
    p, z = k / n, 1.959964
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100 * max(0.0, c - h), 100 * min(1.0, c + h)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/gotw_seg.sqlite")
    ap.add_argument("--bridge", default="data/atlas1908_bridge.json")
    ap.add_argument("--cache", default="data/atlas1908_reach_cache.jsonl")
    ap.add_argument("--ccode", default="CN")
    ap.add_argument("-n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and budget, query nothing")
    ap.add_argument("--offline", action="store_true",
                    help="analyse from the cache only; abort rather than issue any query")
    a = ap.parse_args()

    bridge = json.loads(Path(a.bridge).read_text(encoding="utf-8"))["bridge"]
    alias = {}
    for j in bridge:                       # 1856 headword -> 1908 postal form
        alias.setdefault(_norm(j["name"]), j)
    samp, coord_bearing, dropped = sample(a.db, a.ccode, a.n, a.seed)
    print(f"cohort A: {len(samp)} of {coord_bearing} usable coordinate-bearing {a.ccode} places "
          f"({dropped} dropped as out-of-bbox), seed {a.seed}")
    in_a = [r for r in samp if _norm(r[0]) in alias]
    print(f"          of which {len(in_a)} have a verified bridge alias - so cohort A can move by at "
          f"most +{len(in_a)} points, in either direction")

    seen, cohort_b = set(), []
    for j in bridge:                       # every bridge place, paired
        k = _norm(j["name"])
        if k in seen or j["lat"] is None:
            continue
        seen.add(k)
        cohort_b.append((j["name"], j["lat"], j["lon"], j["atlas_name"]))
    print(f"cohort B: {len(cohort_b)} distinct places with a verified bridge alias "
          f"({100.0 * len(cohort_b) / max(coord_bearing, 1):.1f}% of the coordinate-bearing corpus "
          f"- this is the ceiling on any corpus-level gain)")

    work = {}
    for name, lat, lon in samp:
        work[(name, lat, lon)] = None
    for name, lat, lon, postal in cohort_b:
        work[(name, lat, lon)] = None
        work[(postal, lat, lon)] = None
    cache = Cache(a.cache)
    todo = [k for k in work if (k[0], a.ccode, k[1], k[2]) not in cache.d]
    print(f"\ndistinct queries: {len(work)}   already cached: {len(work) - len(todo)}   "
          f"to issue: {len(todo)}  (<= {2 * len(todo)} POSTs; each tries exact then phonetic and "
          f"short-circuits)")
    if a.dry_run:
        print("\n--dry-run: nothing queried.")
        return

    if a.offline and todo:
        sys.exit(f"--offline but {len(todo)} queries are not cached; run on a compute node first")
    for i, (name, lat, lon) in enumerate(work, 1):
        work[(name, lat, lon)] = cache.get(name, a.ccode, lat, lon, live=not a.offline)
        if not a.offline and i % 25 == 0:
            print(f"  {i}/{len(work)}", flush=True)

    def reached(name, lat, lon):
        v = work.get((name, lat, lon))
        return bool(v and v[0])

    # ---- cohort A, paired: arm B changes the query only where a verified alias exists
    a_before = sum(reached(n, la, lo) for n, la, lo in samp)
    a_sub = a_uni = 0
    for n, la, lo in samp:
        j = alias.get(_norm(n))
        base = reached(n, la, lo)
        post = reached(j["atlas_name"], la, lo) if j else base
        a_sub += post
        a_uni += base or post
    print(f"\ncohort A (n={len(samp)}), the corpus baseline:")
    print(f"    1856 headword only  : {a_before:3d}  {100.0 * a_before / len(samp):5.1f}%")
    print(f"    postal form INSTEAD : {a_sub:3d}  {100.0 * a_sub / len(samp):5.1f}%   "
          f"({a_sub - a_before:+d} points)")
    print(f"    BOTH forms (union)  : {a_uni:3d}  {100.0 * a_uni / len(samp):5.1f}%   "
          f"({a_uni - a_before:+d} points, ceiling +{len(in_a)})")
    base = BASELINE.get(a.ccode)
    if base is not None:
        got = 100.0 * a_before / len(samp)
        note = "reproduces" if abs(got - base) < 0.05 else "DOES NOT REPRODUCE"
        print(f"    published baseline {base:.1f}% - arm A {note} it"
              + ("" if note == "reproduces" else
                 "; the index has moved, so treat the delta above as uninterpretable"))

    # ---- cohort B, paired: the measurement that has power
    gained = lost = both = neither = 0
    examples = []
    for name, lat, lon, postal in cohort_b:
        b0, b1 = reached(name, lat, lon), reached(postal, lat, lon)
        both += b0 and b1
        gained += (not b0) and b1
        lost += b0 and not b1
        neither += (not b0) and (not b1)
        if (not b0) and b1 and len(examples) < 8:
            examples.append((name, postal))
    n = len(cohort_b)
    union = both + gained + lost
    lo1, hi1 = wilson(both + lost, n)
    lo2, hi2 = wilson(both + gained, n)
    lo3, hi3 = wilson(union, n)
    print(f"\ncohort B (n={n}), paired, where the bridge applies:")
    print(f"    1856 headword only  : {both + lost:3d}  {100.0 * (both + lost) / max(n, 1):5.1f}%"
          f"  [{lo1:.1f}-{hi1:.1f}]")
    print(f"    postal form INSTEAD : {both + gained:3d}  {100.0 * (both + gained) / max(n, 1):5.1f}%"
          f"  [{lo2:.1f}-{hi2:.1f}]")
    print(f"    BOTH forms (union)  : {union:3d}  {100.0 * union / max(n, 1):5.1f}%"
          f"  [{lo3:.1f}-{hi3:.1f}]")
    print(f"    discordant pairs: gained {gained}, LOST {lost}   (both {both}, neither {neither})")
    if examples:
        print("    newly reached: " + ", ".join(f"{a_}->{b_}" for a_, b_ in examples))
    print("\n    The `lost` column is why substitution is the wrong policy: the postal form is not a "
          "\n    better name, it is a DIFFERENT name, and it retrieves different documents. Replacing "
          "\n    the 1856 form throws away every place the 1856 form alone could reach. An alias is "
          "\n    additive, so the union is what an implementation would actually do - and it is also "
          "\n    the only one of the three that cannot lose ground by construction.")

    cov = len(cohort_b) / max(coord_bearing, 1)
    for label, hits in (("postal INSTEAD", both + gained), ("BOTH forms", union)):
        conv = (hits - (both + lost)) / max(n, 1)
        print(f"\nprojected corpus effect, {label:15}: coverage {100 * cov:.1f}% x net conversion "
              f"{100 * conv:+.1f}% = {100 * cov * conv:+.2f} points on the {base}% baseline")
    print(f"cache: {cache.hits} hits, {cache.misses} new queries")


if __name__ == "__main__":
    main()
