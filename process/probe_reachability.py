"""Reachability by country: does our query reach the right vicinity, where the book tells us where it is?

This is the instrument behind the "Chinese places are badly geolocated" claim, and the "before" number
for place#245. It is deliberately crude and deliberately generous, because it is trying to measure the
INDEX AND THE QUERY, not our matching policy:

  * For places whose coordinates the 1856 text actually printed, query the printed headword and ask
    whether ANY candidate in the returned pool lies within MATCH_KM of that point.
  * Name-consistency is NOT required. Requiring our 1856 spelling to resemble a modern title is
    self-defeating for non-Latin-script regions: it would measure the filter rather than the index.
  * It takes any candidate in the pool, not the top one, so it is an upper bound on what any
    downstream ranking policy could achieve. If a place is unreachable here, no amount of
    re-ranking, tie-breaking or containment can recover it.

MEASURED 2026-09-05 against the live gateway (n=100 per country, seed 7):

    CN  coord-bearing=1208 (dropped  42 out-of-bbox)  sampled=100  reached=18   18.0%
    IN  coord-bearing= 351 (dropped   7 out-of-bbox)  sampled=100  reached=50   50.0%
    RU  coord-bearing= 213 (dropped  12 out-of-bbox)  sampled=100  reached=25   25.0%
    GB  coord-bearing= 199 (dropped   6 out-of-bbox)  sampled=100  reached=76   76.0%

GB is the control: same instrument, same n, a region whose 1856 spellings are close to modern ones.
The gap between 18% and 76% is the thing place#245 is trying to close, and it is a property of the
romanisation, not of the pipeline.

READ THE SAMPLING BEFORE COMPARING ANYTHING TO THESE NUMBERS. n=100 gives a standard error of about
4 points, so a 95% interval on CN's 18% runs roughly 11-27%. An "after" number of 24% from a FRESH
sample proves nothing. To compare, either re-run this script unchanged (the seed and the bbox filter
make the sample reproducible, and `place.extraction` is not written by reconciliation, so the sample
survives a re-reconciliation), or better, evaluate before and after on the SAME 100 places and report
the paired difference — that removes sampling variance from the comparison entirely.

The out-of-bbox drop is a ground-truth filter, not a convenience: a printed coordinate that falls
outside the country's own bounding box is an OCR or extraction error and cannot serve as truth. It is
reported rather than silently applied.

Usage:
    python3 process/probe_reachability.py                 # all four countries
    python3 process/probe_reachability.py --ccode CN      # just one
    python3 process/probe_reachability.py --ccode CN -n 200

Run it from a Slurm job, never a login node and never the gateway VM. It issues ~2 queries per
sampled place at concurrency 1, which is the setting measured not to move live site latency.
"""
import argparse, json, math, sys, sqlite3, random

sys.path.insert(0, "process")
from reconcile import GATEWAY_URL, _post

MATCH_KM = 25.0

# Country bounding boxes, used ONLY to reject impossible printed coordinates.
BBOX = {"CN": (73, 136, 17, 54), "IN": (68, 98, 6, 36), "RU": (19, 190, 41, 82), "GB": (-9, 2, 49, 61)}


def km(a, b):
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    if any(not isinstance(v, (int, float)) for v in (a[0], a[1], b[0], b[1])):
        return None
    (lo1, la1), (lo2, la2) = a[:2], b[:2]
    r = math.radians
    h = (math.sin(r(la2 - la1) / 2) ** 2
         + math.cos(r(la1)) * math.cos(r(la2)) * math.sin(r(lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def reaches(q, cc, lat, lon):
    """True if any candidate from either mode lands within MATCH_KM of the printed point."""
    for mode in ("exact", "phonetic"):
        r = _post(GATEWAY_URL + "/api/reconcile",
                  {"query": q, "mode": mode, "size": 20, "ccodes": [cc]})
        for h in ((r.get("hits") or []) if isinstance(r, dict) else []):
            rp = ((h.get("geometries") or [{}])[0].get("repr_point"))
            d = km([lon, lat], rp)
            if d is not None and d <= MATCH_KM:
                return True, h.get("title"), round(d, 1)
    return False, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/gotw_seg.sqlite")
    ap.add_argument("--ccode", action="append", help="repeatable; default all four")
    ap.add_argument("-n", type=int, default=100, help="sample size per country")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    c = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    for cc in (a.ccode or ["CN", "IN", "RU", "GB"]):
        lo0, lo1, la0, la1 = BBOX[cc]
        rows, bad = [], 0
        for name, ext in c.execute("select name, extraction from place where extraction is not null"):
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
                bad += 1          # corrupt printed coordinate — cannot be ground truth
                continue
            rows.append((name, lat, lon))
        random.Random(a.seed).shuffle(rows)
        sample = rows[:a.n]
        hit, ex = 0, []
        for name, lat, lon in sample:
            ok, title, d = reaches(name, cc, lat, lon)
            hit += ok
            if ok and len(ex) < 3:
                ex.append((name, title, d))
        n = len(sample)
        se = 100.0 * math.sqrt((hit / max(n, 1)) * (1 - hit / max(n, 1)) / max(n, 1))
        print("%s  coord-bearing=%4d (dropped %3d as out-of-bbox)  sampled=%3d  reached=%3d  %5.1f%% (SE %.1f)"
              % (cc, len(rows) + bad, bad, n, hit, 100.0 * hit / max(n, 1), se))
        for nm, t, d in ex:
            print("      %-24s -> %-28s %.1f km" % (nm, t, d))


if __name__ == "__main__":
    main()
