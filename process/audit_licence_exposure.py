#!/usr/bin/env python3
"""Licence-exposure audit: which of our matches and coordinates carry terms CC BY 4.0 cannot.

READ-ONLY. Reports; changes nothing. This is step 1 of LICENSING-POLICY.md section 5, and it exists
because WorldHistoricalGazetteer/place#240 asks for exactly this order: report before changing
anything.

WHAT IT MEASURES
----------------
Every reconciled place is scored against `data/source_licence_tiers.json`, which tiers each WHG
authority namespace by what we may RETAIN from it (never by what we may query):

  A  permissive      geometry may be published under CC BY 4.0, with attribution
  B  share-alike     ODbL / CC-BY-SA — retaining geometry makes our output a derivative database
  C  non-commercial  restriction incompatible with CC BY 4.0
  D  no-derivatives  or terms not yet read; most restrictive by default
  E  contributed     per the contributing dataset's own recorded licence

A tier-B/C/D match is not an error. It is a citable identification whose GEOMETRY we may not keep, so
the number that matters is not "bad matches" but "places whose stored coordinate has to be re-derived".

  ⚠️  A KNOWN LIMIT, and the reason rule 3 of the policy exists. `place` records no per-coordinate
  provenance, so this audit attributes each coordinate to the namespace of `whg_match_id`. That is a
  LOWER BOUND, not the truth: the non-point tie-break (GOTW_GEOM_MARGIN) can hand the winning slot to
  a polygon-bearing candidate from a different source than the top name match, and OSM is the
  polygon-richest source in the index. The audit says so in its output rather than implying a
  precision it does not have. Fixing this needs a `geom_src` column, not a better query.

Usage:
  python3 process/audit_licence_exposure.py
  python3 process/audit_licence_exposure.py --by-country --top 25
  python3 process/audit_licence_exposure.py --list-exposed data/probe/exposed.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

DEFAULT_DB = "data/gotw_seg.sqlite"
DEFAULT_TIERS = "data/source_licence_tiers.json"
RETAINABLE = {"A"}                       # the only tier whose geometry may reach a CC BY 4.0 output
TIER_ORDER = ["A", "B", "C", "D", "E", "?"]


def load_tiers(path):
    with open(path) as fh:
        doc = json.load(fh)
    srcs = doc["sources"]
    return (
        {ns: meta.get("tier", "D") for ns, meta in srcs.items()},
        {ns: meta for ns, meta in srcs.items()},
        doc.get("_tiers", {}),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--tiers", default=DEFAULT_TIERS)
    ap.add_argument("--by-country", action="store_true", help="also break exposure down by country code")
    ap.add_argument("--top", type=int, default=15, help="countries to show with --by-country")
    ap.add_argument("--list-exposed", metavar="CSV",
                    help="write one row per place whose stored coordinate is not retainable")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"DB not found: {args.db}")
    tier_of, meta, tier_desc = load_tiers(args.tiers)

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    by_tier = Counter()                  # matches by tier
    by_ns = Counter()                    # matches by namespace
    coord_by_tier = Counter()            # places that STORE a coordinate, by the tier it came from
    printed_rescue = Counter()           # exposed places the book itself gives coordinates for
    by_country = defaultdict(Counter)
    unknown_ns = Counter()
    exposed_rows = []
    total = matched = coords_total = 0

    for r in con.execute(
        "SELECT place_id, name, whg_match_id, lat, lon, extraction, recon_pass FROM place"
    ):
        total += 1
        mid = (r["whg_match_id"] or "").strip()
        has_coord = r["lat"] is not None and r["lon"] is not None
        coords_total += 1 if has_coord else 0
        if not mid:
            continue
        matched += 1
        ns = mid.split(":", 1)[0]
        tier = tier_of.get(ns)
        if tier is None:
            tier = "?"                   # not in the file at all -> policy rule 5 treats it as D
            unknown_ns[ns] += 1
        by_tier[tier] += 1
        by_ns[ns] += 1
        try:
            ext = json.loads(r["extraction"]) if r["extraction"] else {}
        except (ValueError, TypeError):
            ext = {}
        cc = ext.get("country_code") or "??"
        by_country[cc][tier] += 1
        if has_coord:
            coord_by_tier[tier] += 1
            if tier not in RETAINABLE:
                printed = ext.get("latitude") is not None and ext.get("longitude") is not None
                printed_rescue[bool(printed)] += 1
                if args.list_exposed:
                    exposed_rows.append({
                        "place_id": r["place_id"], "name": r["name"], "ccode": cc,
                        "whg_match_id": mid, "namespace": ns, "tier": tier,
                        "license_spdx": (meta.get(ns) or {}).get("license_spdx") or "",
                        "recon_pass": r["recon_pass"] or "",
                        "stored_lat": r["lat"], "stored_lon": r["lon"],
                        "printed_lat": ext.get("latitude"), "printed_lon": ext.get("longitude"),
                        "route_1_printed_coords": "yes" if printed else "no",
                    })

    def pct(n, d):
        return f"{100.0 * n / d:5.1f}%" if d else "    - "

    print("=" * 76)
    print("LICENCE EXPOSURE AUDIT")
    print("=" * 76)
    print(f"  db                      {args.db}")
    print(f"  tiers                   {args.tiers}")
    print(f"  places                  {total:>8,}")
    print(f"  reconciled              {matched:>8,}   {pct(matched, total)}")
    print(f"  with a stored coordinate{coords_total:>8,}   {pct(coords_total, total)}")

    print("\n" + "-" * 76)
    print("MATCHES BY RETENTION TIER")
    print("-" * 76)
    for t in TIER_ORDER:
        if not by_tier.get(t):
            continue
        flag = "  retainable" if t in RETAINABLE else "  NOT RETAINABLE"
        print(f"  {t}  {by_tier[t]:>8,}   {pct(by_tier[t], matched)}{flag}")
        desc = tier_desc.get(t)
        if desc:
            print(f"       {desc}")

    print("\n" + "-" * 76)
    print("MATCHES BY SOURCE")
    print("-" * 76)
    print(f"  {'ns':<10}{'tier':>5}{'matches':>10}{'share':>9}   licence")
    for ns, n in by_ns.most_common():
        m = meta.get(ns) or {}
        print(f"  {ns:<10}{tier_of.get(ns, '?'):>5}{n:>10,}{pct(n, matched):>9}   "
              f"{m.get('license_spdx') or 'UNRECORDED'}")
    if unknown_ns:
        print(f"\n  ⚠️  namespaces absent from the tier file (treated as D per policy rule 5): "
              f"{', '.join(sorted(unknown_ns))}")

    exposed = sum(n for t, n in coord_by_tier.items() if t not in RETAINABLE)
    print("\n" + "=" * 76)
    print("THE NUMBER THAT MATTERS: stored coordinates that must be re-derived")
    print("=" * 76)
    for t in TIER_ORDER:
        if coord_by_tier.get(t):
            mark = "ok" if t in RETAINABLE else "MUST RE-DERIVE"
            print(f"  tier {t}  {coord_by_tier[t]:>8,}   {pct(coord_by_tier[t], coords_total)}   {mark}")
    print(f"\n  exposed coordinates      {exposed:>8,}   {pct(exposed, coords_total)} of all stored coordinates")
    if exposed:
        yes = printed_rescue[True]
        print(f"    ...of which the book prints its own coordinates (policy route 1): "
              f"{yes:,}   {pct(yes, exposed)}")
        print(f"    ...needing a permissive co-referent (route 2) or 'matched but not located' "
              f"(route 3): {exposed - yes:,}")
    print("\n  ⚠️  LOWER BOUND. `place` stores no per-coordinate provenance, so each coordinate is")
    print("      attributed to the namespace of its whg_match_id. The non-point tie-break can seat a")
    print("      polygon-bearing candidate from a different (and, since OSM is the polygon-richest")
    print("      source, likelier restricted) record. See LICENSING-POLICY.md rule 3.")

    if args.by_country:
        print("\n" + "-" * 76)
        print(f"EXPOSURE BY COUNTRY (top {args.top} by non-retainable matches)")
        print("-" * 76)
        ranked = sorted(by_country.items(),
                        key=lambda kv: -sum(n for t, n in kv[1].items() if t not in RETAINABLE))
        print(f"  {'cc':<5}{'matched':>9}{'exposed':>9}{'share':>9}   worst source")
        for cc, counts in ranked[: args.top]:
            tot = sum(counts.values())
            exp = sum(n for t, n in counts.items() if t not in RETAINABLE)
            if not exp:
                continue
            worst = min((t for t in counts if t not in RETAINABLE), key=lambda t: TIER_ORDER.index(t))
            print(f"  {cc:<5}{tot:>9,}{exp:>9,}{pct(exp, tot):>9}   worst tier {worst}")

    if args.list_exposed and exposed_rows:
        os.makedirs(os.path.dirname(args.list_exposed) or ".", exist_ok=True)
        with open(args.list_exposed, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(exposed_rows[0].keys()))
            w.writeheader()
            w.writerows(exposed_rows)
        print(f"\n  {len(exposed_rows):,} exposed places written to {args.list_exposed}")


if __name__ == "__main__":
    main()
