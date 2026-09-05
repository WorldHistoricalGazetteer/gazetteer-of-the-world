#!/usr/bin/env python3
"""What does the CC BY 4.0 goal actually cost us in geolocation? Measure it before deciding.

READ-ONLY. Reports; changes nothing. Gateway backend only (CRC compute node).

THE QUESTION
------------
`audit_licence_exposure.py` shows that ~24.5k of our ~99.6k stored coordinates came from sources
whose terms CC BY 4.0 cannot carry (overwhelmingly OSM, ODbL). The book's own printed coordinates
rescue only ~700 of them. So the honest choice is between:

  * keep the CC BY 4.0 goal, and locate those places some other way or not at all; or
  * relax the goal to a share-alike licence, and keep the coordinates we already have.

That choice should be made on a number, not on a fear. The number is: **how many of those places can
be located just as well from a permissively-licensed record?** If a Wikidata or GeoNames record for
the same place sits a few hundred metres away, the CC BY 4.0 goal costs us nothing but a re-run. If
there is no permissive record at all, it costs us that place on the map.

HOW IT MEASURES
---------------
For each sampled place whose current match is tier B/C/D, re-run the SAME query shape against the
gateway with `exclude_namespaces` set to every non-tier-A namespace, and compare the best permissive
candidate with the restricted coordinate we currently hold.

This is deliberately not a hard-link lookup. Re-querying measures what we would actually DO (re-run
the cascade permissive-only), it does not depend on hard-link coverage, and it yields the distance
between the two answers — which is the only way to tell a genuine substitution from a different place
with a similar name. A permissive record 12,000 km away is not a rescue.

  ⚠️  The restricted coordinate is treated as the reference point, not as ground truth. It is our
  current best estimate and it is sometimes wrong (that is the rest of this work package). So the
  distance bands below say "agrees with what we have", not "is correct". Both numbers move once the
  containment fixes land, which is why this should be re-run after them.

Substitution is counted ACCEPTABLE when the permissive candidate lies within --radius-km of the
restricted one: same place, independently sourced. Anything further is reported separately and should
default to "matched but not located" (LICENSING-POLICY.md route 3) rather than a silent relocation.

Usage:
  python3 process/measure_licence_tradeoff.py --dry-run
  python3 process/measure_licence_tradeoff.py --limit 600
  python3 process/measure_licence_tradeoff.py --ccode CN --limit 300
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reconcile import GATEWAY_URL, _has_geom, _post  # noqa: E402

DEFAULT_DB = "data/gotw_seg.sqlite"
DEFAULT_TIERS = "data/source_licence_tiers.json"
DEFAULT_OUT = "data/probe"
RETAINABLE = {"A"}
SIZE = 10


def load_tiers(path):
    with open(path) as fh:
        srcs = json.load(fh)["sources"]
    return {ns: m.get("tier", "D") for ns, m in srcs.items()}


def km(a, b):
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    (lon1, lat1), (lon2, lat2) = a[:2], b[:2]
    r = math.radians
    h = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def sample(db, tier_of, ccode, limit, seed):
    """Places whose stored coordinate came from a non-retainable source — the population at risk."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute("SELECT place_id, name, whg_match_id, lat, lon, extraction, recon_pass "
                         "FROM place WHERE whg_match_id IS NOT NULL AND whg_match_id != '' "
                         "AND lat IS NOT NULL"):
        ns = r["whg_match_id"].split(":", 1)[0]
        if tier_of.get(ns, "D") in RETAINABLE:
            continue
        try:
            ext = json.loads(r["extraction"]) if r["extraction"] else {}
        except (ValueError, TypeError):
            ext = {}
        if ccode and ext.get("country_code") != ccode:
            continue
        rows.append({"place_id": r["place_id"], "name": r["name"], "match": r["whg_match_id"],
                     "ns": ns, "tier": tier_of.get(ns, "D"), "lat": r["lat"], "lon": r["lon"],
                     "pass": r["recon_pass"], "ext": ext})
    con.close()
    rng = random.Random(seed)
    rng.shuffle(rows)
    return rows[:limit], len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--tiers", default=DEFAULT_TIERS)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--ccode")
    ap.add_argument("--limit", type=int, default=600)
    ap.add_argument("--radius-km", type=float, default=25.0,
                    help="a permissive candidate within this distance counts as the same place")
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20260905)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tier_of = load_tiers(args.tiers)
    permissive = sorted(ns for ns, t in tier_of.items() if t in RETAINABLE)
    restricted = sorted(ns for ns, t in tier_of.items() if t not in RETAINABLE)
    places, population = sample(args.db, tier_of, args.ccode, args.limit, args.seed)

    print(f"Exposed population: {population:,}"
          f"{f' (ccode={args.ccode})' if args.ccode else ''};  sampling {len(places):,}")
    print(f"Permissive namespaces kept : {', '.join(permissive)}")
    print(f"Excluded from the re-query : {', '.join(restricted)}")

    if args.dry_run:
        if places:
            p = places[0]
            print("\nExample re-query:")
            print(" ", json.dumps({"query": p["name"], "mode": "phonetic", "size": SIZE,
                                   "ccodes": [p["ext"].get("country_code")],
                                   "exclude_namespaces": restricted}, ensure_ascii=False))
        print(f"\n{len(places):,} queries would be issued. Nothing was sent.")
        return

    def work(p):
        body = {"query": p["name"], "mode": "phonetic", "size": SIZE,
                "exclude_namespaces": restricted}
        cc = p["ext"].get("country_code")
        if cc:
            body["ccodes"] = [cc]
        resp = _post(f"{GATEWAY_URL}/api/reconcile", body)
        hits = (resp.get("hits") or []) if isinstance(resp, dict) else []
        best = max(hits, key=lambda h: h.get("score", 0)) if hits else None
        out = dict(p)
        out.pop("ext", None)
        if best:
            rp = ((best.get("geometries") or [{}])[0].get("repr_point"))
            out.update(alt_id=best.get("place_id"), alt_title=best.get("title"),
                       alt_score=best.get("score"), alt_confidence=best.get("confidence"),
                       alt_has_geom=_has_geom(best), alt_point=rp,
                       dist_km=km([p["lon"], p["lat"]], rp))
        return out

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for i, fut in enumerate(as_completed([pool.submit(work, p) for p in places]), 1):
            results.append(fut.result())
            if i % 100 == 0:
                print(f"  {i}/{len(places)}", flush=True)

    os.makedirs(args.out_dir, exist_ok=True)
    tag = args.ccode or "all"
    path = os.path.join(args.out_dir, f"licence-tradeoff-{tag}.jsonl")
    with open(path, "w") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ── the answer ───────────────────────────────────────────────────────────
    n = len(results)
    no_alt = [r for r in results if not r.get("alt_id")]
    with_alt = [r for r in results if r.get("alt_id")]
    dists = [r["dist_km"] for r in with_alt if r.get("dist_km") is not None]
    near = [r for r in with_alt if (r.get("dist_km") is not None and r["dist_km"] <= args.radius_km)]
    far = [r for r in with_alt if (r.get("dist_km") is not None and r["dist_km"] > args.radius_km)]
    nogeo = [r for r in with_alt if r.get("dist_km") is None]

    def pct(x):
        return f"{100.0 * x / n:5.1f}%" if n else "  -  "

    print("\n" + "=" * 74)
    print(f"WHAT THE CC BY 4.0 GOAL COSTS  (sample {n:,} of {population:,} exposed places)")
    print("=" * 74)
    print(f"  a permissive record exists for the same name   {len(with_alt):>7,}   {pct(len(with_alt))}")
    print(f"    ...within {args.radius_km:g} km of what we hold (SAME PLACE) {len(near):>7,}   {pct(len(near))}"
          "   <- free substitution")
    print(f"    ...further away (probably a DIFFERENT place)  {len(far):>7,}   {pct(len(far))}"
          "   <- do not substitute")
    if nogeo:
        print(f"    ...permissive record has no coordinate       {len(nogeo):>7,}   {pct(len(nogeo))}")
    print(f"  no permissive record at all                    {len(no_alt):>7,}   {pct(len(no_alt))}"
          "   <- lost from the map")

    recoverable = len(near)
    lost = n - recoverable
    print("\n" + "-" * 74)
    print("SCENARIOS, scaled to the whole exposed population")
    print("-" * 74)
    scale = population / n if n else 0
    print(f"  Relax the goal to share-alike (ODbL / CC BY-SA):")
    print(f"    keeps all {population:,} exposed coordinates as they are, and makes the WHOLE")
    print(f"    dataset share-alike, including the {100 - 100.0 * population / max(population, 1):.0f}"
          f"% that never needed to be.")
    print(f"  Keep CC BY 4.0:")
    print(f"    ~{recoverable * scale:>10,.0f} of the {population:,} recovered from a permissive record nearby")
    print(f"    ~{lost * scale:>10,.0f} become 'matched but not located' (id + citation, no point)")
    print(f"    net cost to the map: about {100.0 * lost / n:.1f}% of the exposed set, "
          f"{100.0 * lost * scale / 99587:.1f}% of all located places")

    if dists:
        print("\n" + "-" * 74)
        print("HOW FAR THE PERMISSIVE ALTERNATIVE SITS FROM WHAT WE HOLD")
        print("-" * 74)
        for lo, hi in ((0, 1), (1, 5), (5, 25), (25, 100), (100, 1000), (1000, 1e9)):
            c = sum(1 for d in dists if lo <= d < hi)
            hl = "inf" if hi > 1e8 else f"{hi:g}"
            print(f"  {lo:>5g} - {hl:<6} km {c:>7,}   {100.0 * c / len(dists):5.1f}%")
        print(f"  median {statistics.median(dists):,.1f} km")

    poly = sum(1 for r in near if r.get("alt_has_geom"))
    print(f"\n  of the free substitutions, {poly:,} ({100.0 * poly / max(len(near), 1):.1f}%) "
          "carry a polygon, so the boundary layer survives too")

    tiers = Counter(r["tier"] for r in results)
    lost_by_ns = Counter(r["ns"] for r in results if r not in near)
    print(f"\n  sampled tiers: {dict(tiers)}")
    print(f"  losses by source: {dict(lost_by_ns.most_common(6))}")
    print(f"\n  per-place detail written to {path}")
    print("\n  ⚠️  The restricted coordinate is the reference, not ground truth. Re-run this after the")
    print("      containment fixes land, since both sides of the comparison move.")


if __name__ == "__main__":
    main()
