#!/usr/bin/env python3
"""Join our 1856 Chinese headwords to the 1908 atlas index, gated on the printed coordinates.

This is step 3 of place#245 Phase 1. `process/atlas1908_index.py` reconstructs the atlas index from
our own Surya OCR; this decides which of our places it actually identifies, and how confidently.

THE GATE IS THE POINT
---------------------
Stephen's governing principle for this work package: "In our initial experiment, correctness was
sacrificed for match-count. Going forward that principle is unequivocally reversed." So a name match
is not a result here. `Chenki` names two different places in the index (Hunan and Szechwan), our
headwords are short and phonetically dense, and a hyphen-insensitive equality test over 2,079 names
and 6,500 index entries will pair plenty of them wrongly.

What makes this join different from a similarity guess is that BOTH sides carry a printed coordinate
from independent sources - the 1856 gazetteer and the 1908 atlas - so every proposed pair can be
tested against evidence neither side's name supplied. A pair is accepted only if the two coordinates
agree. That is why the headline number here is the VERIFIED count, and the name-match count is
reported beside it only to show how much the gate threw away.

TWO CONTROLS, BOTH OF WHICH CAN FAIL
------------------------------------
  * `--permute` re-runs the whole join with the atlas coordinates shuffled between index entries.
    Everything else is identical, so the pass rate it reports is the rate this instrument accepts
    pairs on no evidence. If that number is not near zero, the gate is decoration and the headline
    means nothing. This is the control that shares the subject's failure mode: it fails in exactly
    the way a too-generous radius or a broken distance function would.
  * The radius is swept rather than chosen. A real join shows a distance distribution piled up near
    zero with a long empty gap; a spurious one is flat. The sweep prints that shape, so the choice of
    GATE_KM is visible as a decision rather than buried as a constant.

Usage:
    python3 process/atlas1908_join.py --index data/atlas1908_index.json      # join + controls
    python3 process/atlas1908_join.py --index data/atlas1908_index.json --out data/atlas1908_bridge.json
    python3 process/atlas1908_join.py --index data/atlas1908_index.json --permute
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas1908_index import _norm  # noqa: E402  - one join key, defined once
from review_store import sig  # noqa: E402  - the repo's stable entry signature

GATE_KM = 25.0            # the same radius probe_reachability.py calls "the right vicinity"
SWEEP = (5, 10, 25, 50, 100, 250, 500)
CN_BBOX = (73, 136, 17, 54)      # as probe_reachability.py: reject impossible printed coordinates


def km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = math.radians
    h = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def our_places(db, ccode="CN"):
    """Our places for one country: name, printed coordinates where the 1856 text gave them.

    `place.extraction` is the extraction blob and is never written by reconciliation, so this
    selection is stable across a re-reconciliation running at the same time.

    Each row also carries the repo's stable signature, `sig(filename, headword_raw, page_start)` plus
    the place's ordinal, matching `review_ui` and `build_eval_set` exactly. `place_id` is emitted too
    but the signature is the one a consumer should key on: place ids do not survive a re-parse or a
    DB rebuild, and this table is meant to be loaded by a later selective re-reconciliation.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lo0, lo1, la0, la1 = CN_BBOX
    out = []
    for pid, name, ext, ordinal, hw_raw, page_start, filename in con.execute(
            "SELECT p.place_id, p.name, p.extraction, p.ordinal, e.headword_raw, e.page_start, "
            "s.filename FROM place p JOIN entry e ON e.entry_id = p.entry_id "
            "JOIN source s ON s.source_id = e.source_id WHERE p.extraction IS NOT NULL"):
        try:
            e = json.loads(ext)
        except (ValueError, TypeError):
            continue
        if e.get("country_code") != ccode:
            continue
        lat, lon = e.get("latitude"), e.get("longitude")
        if lat is None or lon is None:
            lat = lon = None          # a lone latitude is not a location
        elif not (lo0 <= lon <= lo1 and la0 <= lat <= la1):
            lat = lon = None          # corrupt printed coordinate: unusable as ground truth
        out.append({"place_id": pid, "sig": sig(filename, hw_raw, page_start) + f":{ordinal}",
                    "name": name, "lat": lat, "lon": lon,
                    "hierarchy": [a for a in (e.get("admin_hierarchy") or [])]})
    con.close()
    return out


def _row(p, atlas, d, status, n_cands):
    """One alias-table row. `km` is kept per row rather than only the pass/fail verdict so a consumer
    can re-threshold without re-deriving the join - the gate at 25 km is this script's choice, not a
    property of the data."""
    return {"sig": p["sig"], "place_id": p["place_id"],
            "printed_form": p["name"], "postal_form": atlas["name"],
            "status": status, "verified": status == "verified",
            "km": None if d is None else round(d, 1),
            "printed_lat": p["lat"], "printed_lon": p["lon"],
            "atlas_lat": atlas["lat"], "atlas_lon": atlas["lon"],
            "atlas_province": atlas["province"], "atlas_candidates": n_cands}


def build(places, entries, gate_km=GATE_KM):
    """Name-match, then keep only the pairs whose two printed coordinates agree."""
    by_key = defaultdict(list)
    for e in entries:
        if not e["is_range"]:                  # province boxes are not places
            by_key[_norm(e["name"])].append(e)

    joins, stats = [], Counter()
    for p in places:
        cands = by_key.get(_norm(p["name"]))
        if not cands:
            stats["no_name_match"] += 1
            continue
        stats["name_matched"] += 1
        if p["lat"] is None:
            stats["name_matched_uncheckable"] += 1     # no printed coordinate: cannot be verified
            joins.append(_row(p, cands[0], None, "unverifiable_no_coordinate", len(cands)))
            continue
        stats["name_matched_checkable"] += 1
        scored = sorted(((km(p["lat"], p["lon"], c["lat"], c["lon"]), c) for c in cands
                         if c["lat"] is not None and c["lon"] is not None),
                        key=lambda t: t[0])
        if not scored:
            stats["name_matched_uncheckable"] += 1
            joins.append(_row(p, cands[0], None, "unverifiable_no_coordinate", len(cands)))
            continue
        d, best = scored[0]
        if d > gate_km:
            stats["rejected_by_coordinate"] += 1
            joins.append(_row(p, best, d, "rejected_by_coordinate", len(cands)))
            continue
        stats["verified"] += 1
        joins.append(_row(p, best, d, "verified", len(cands)))
    return joins, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/gotw_seg.sqlite")
    ap.add_argument("--index", default="data/atlas1908_index.json")
    ap.add_argument("--ccode", default="CN")
    ap.add_argument("--out", help="write the verified bridge here")
    ap.add_argument("--permute", action="store_true",
                    help="control: shuffle the atlas coordinates between entries and re-join")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    entries = json.loads(Path(a.index).read_text(encoding="utf-8"))["entries"]
    places = our_places(a.db, a.ccode)
    coord_bearing = sum(1 for p in places if p["lat"] is not None)
    distinct = len({_norm(p["name"]) for p in places})
    print(f"ours: {len(places)} {a.ccode} places, {distinct} distinct headwords, "
          f"{coord_bearing} with a usable printed coordinate")
    print(f"atlas: {len(entries)} index entries, "
          f"{len({_norm(e['name']) for e in entries if not e['is_range']})} distinct names\n")

    joins, st = build(places, entries)
    ck = st["name_matched_checkable"]
    print(f"name match (hyphen/case/diacritic-insensitive): {st['name_matched']} places "
          f"({100.0 * st['name_matched'] / max(len(places), 1):.1f}%)")
    print(f"  of which checkable against a printed coordinate: {ck}")
    print(f"  VERIFIED (within {GATE_KM:.0f} km): {st['verified']}"
          f"   rejected by the coordinate: {st['rejected_by_coordinate']}"
          f"   ({100.0 * st['verified'] / max(ck, 1):.1f}% of checkable pass)")
    print(f"  name-matched but no printed coordinate to check: {st['name_matched_uncheckable']}")

    ds = sorted(j["km"] for j in joins if j["km"] is not None)
    print("\ndistance sweep (cumulative share of the %d checkable name matches):" % ck)
    for r in SWEEP:
        n = sum(1 for d in ds if d <= r)
        print(f"   <= {r:4d} km  {n:5d}  {100.0 * n / max(ck, 1):5.1f}%")

    if a.permute:
        # Two nulls, because the easy one is too easy. Shuffling coordinates across all of China
        # tests only that the distance function works - a wrong pair drawn from 6,500 entries spread
        # over a continent lands thousands of km away and would be rejected by any radius at all.
        # The second shuffles only WITHIN a province, which is the confusion that actually threatens
        # this join: two places of the same name a few counties apart. That null shares the subject's
        # failure mode, so if 25 km cannot separate it, the gate is not doing the work claimed for it.
        for label, scope in (("across China", None), ("within the same province", "province")):
            shuffled = [dict(e) for e in entries]
            groups = defaultdict(list)
            for i, e in enumerate(shuffled):
                groups[_norm(e[scope]) if scope else ""].append(i)
            rng = random.Random(a.seed)
            for idxs in groups.values():
                pts = [(shuffled[i]["lat"], shuffled[i]["lon"]) for i in idxs]
                rng.shuffle(pts)
                for i, pt in zip(idxs, pts):
                    shuffled[i]["lat"], shuffled[i]["lon"] = pt
            _, sc = build(places, shuffled)
            ck2 = sc["name_matched_checkable"]
            print(f"CONTROL, coordinates shuffled {label:26}: verified {sc['verified']:3d}/{ck2} "
                  f"({100.0 * sc['verified'] / max(ck2, 1):5.2f}%)")
        print("  (the rate at which the gate accepts a pair on no evidence; the second is the one "
              "that matters)")

    if a.out:
        ver = [j for j in joins if j["verified"]]
        Path(a.out).write_text(json.dumps({
            "source": {"index": a.index, "gate_km": GATE_KM, "ccode": a.ccode,
                       "key": "sig = review_store.sig(filename, headword_raw, page_start) + ':' + "
                              "ordinal; place_id is included but does not survive a re-parse",
                       "usage": "aliases are ADDITIONAL query variants, never replacements for the "
                                "printed form: substituting gains 24 places and loses 15 (n=88), "
                                "because the postal form retrieves different documents rather than "
                                "the same ones better. See process/atlas1908_reach.py.",
                       "false_positives": "the within-province null verifies 1.8% of checkable name "
                                          "matches, so ~3 of the verified rows are expected to be "
                                          "chance. WHICH three is not identifiable per-row; the "
                                          "estimate is aggregate. Tighten with `km` if that matters "
                                          "- 12 of the 90 are within 5 km and 30 within 10 km."},
            "stats": dict(st), "bridge": ver, "rejected": [j for j in joins if not j["verified"]]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nwrote {len(ver)} verified + {len(joins) - len(ver)} rejected/unverifiable rows "
              f"-> {a.out}")


if __name__ == "__main__":
    main()
