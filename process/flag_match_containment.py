#!/usr/bin/env python3
"""Cross-check each MATCHED point against the place's own resolved admin parent.

READ-ONLY unless --apply. The companion to `flag_coord_containment.py`, which tests the coordinate the
BOOK printed; this tests the coordinate the MATCH supplied, which is a different and larger exposure.

WHY THIS EXISTS
---------------
Absolute `confidence` gates the cascade on whether a candidate's NAME is real evidence. It cannot see
whether the candidate is in the right PLACE. Those come apart precisely on pass 4 (`4-phon-cc`), which
searches a whole country with no containment: an exact match on "Bramley" in GB returns confidence 100
and tells you nothing about WHICH Bramley. Measured on the first 1,000 places of the 2026-09 re-run,
pass 4 supplied 47.8% of all matches, every one of them spatially unconstrained.

So this is the check confidence structurally cannot perform: the place's admin hierarchy says where it
should be, the match says where it is, and this asks whether those agree.

Passes 1-3 constrained the query server-side with `contained_in`, so a violation there is not a data
problem but a discrepancy between the gateway's H3/Shapely containment and this point-in-polygon test.
Those are reported SEPARATELY and never rejected: if they are non-zero, the bug is ours or the
gateway's, and rejecting the rows would hide it.

WHAT IT CANNOT DO — READ THIS BEFORE QUOTING ANY NUMBER FROM IT
---------------------------------------------------------------
⚠️ **A violation does NOT mean the match is wrong.** The test asks only whether the matched point lies
inside the polygon we resolved for the place's stated parent, and that question has FOUR ways to fail.
Verified by hand on the first run (2026-09-05, 8,713 places, pass 4 at 97.2% "outside"), every example
inspected was a CORRECT match flagged for a different reason:

  1. **Defective source polygon.** `ohm:r2696305` "Regno Lombardo-Veneto" stores bounds
     (10.62, 44.77, 13.67, 46.68) — the Venetian half only, no Lombardy. Abbiategrasso sits west of
     its western edge, so a correct match reads as outside.
  2. **Boundary drift since 1856.** The book puts Acámbaro in Querétaro; it is 45 km inside modern
     Guanajuato. The polygon is right, the match is right, and the century between them is the fault.
  3. **Wrong parent resolved.** "Naples" in the 1856 sense is the Kingdom; we resolved
     `ohm:r2808397`, the modern city (bounds 13.8-14.62 E). Accumoli was in the Kingdom and is
     nowhere near the city.
  4. A genuinely wrong match — the case this was built to find, and a minority of the total.

So this is a **review flag, not a rejection criterion**, and `--reject` should stay off for the corpus
unless a sample has been hand-checked first. Its most useful output turned out to be class 3: it
surfaces systematic parent-resolution errors, where a historic polity name resolves to a modern
same-named city.

Only places whose hierarchy resolved to a geometry-bearing parent can be tested at all. On the current
run depth-1 parents resolve for only ~35% of keys, so a large share of pass-4 matches — the ones most
in need of checking — have no parent to check against and are counted as `untestable`, not as passing.
Silence here is not a clean bill of health, and the summary says so.

Usage:
  python3 process/flag_match_containment.py --db data/gotw_seg.sqlite            # report only
  python3 process/flag_match_containment.py --apply                              # write the flag
  python3 process/flag_match_containment.py --apply --reject                     # + demote violators
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict, Counter

TOL_DEG = 0.02          # ~2 km: a point just outside a boundary edge is not a violation
FLAG = "match_outside_parent"
# Only passes that had NO server-side containment are eligible for rejection. Rejecting a pass-1/2/3
# match would be rejecting a row the gateway already vouched for spatially.
UNCONSTRAINED = ("4-phon-cc", "coord-match")


def open_store(store):
    """Return (lookup(pid) -> [(file, offset, length)], close()). Prefers the SQLite index: the store
    holds ~11.7M geometries and the JSON index of that is punishing to load."""
    sq = os.path.join(store, "index.sqlite")
    js = os.path.join(store, "index.json")
    if os.path.exists(sq):
        con = sqlite3.connect(f"file:{sq}?mode=ro", uri=True)
        shard = lambda n: os.path.join(store, "geom_shard_%04d.bin" % n)

        # Keys are "{place_id}_{geometry_index}". A RANGE scan over the primary key uses the index;
        # `k = ? OR k LIKE ?` does not, and turns each lookup into a full scan of ~11.7M rows — which
        # is why the first version of this ran for minutes and was killed.
        def lookup(pid):
            lo, hi = pid + "_", pid + "_￿"
            return [(shard(s), o, l) for s, o, l in con.execute(
                "SELECT shard, off, len FROM geom WHERE k >= ? AND k <= ?", (lo, hi)).fetchall()]
        return lookup, con.close
    if os.path.exists(js):
        idx = json.load(open(js))
        by_pid = defaultdict(list)
        for k, e in idx.items():
            by_pid[k.rsplit("_", 1)[0]].append(e)

        def lookup(pid):
            return [(os.path.join(store, e["file"]), e["offset"], e["length"]) for e in by_pid.get(pid, [])]
        return lookup, (lambda: None)
    return None, (lambda: None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/gotw_seg.sqlite")
    ap.add_argument("--store", default=os.environ.get("GEOM_STORE", "/vast/ishi/geom"))
    ap.add_argument("--apply", action="store_true", help="write the flag (default: report only)")
    ap.add_argument("--reject", action="store_true",
                    help="with --apply, also demote violating UNCONSTRAINED matches to unmatched")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    lookup, close = open_store(args.store)
    if lookup is None:
        print(f"geom-store index not found under {args.store} — cannot cross-check")
        return 0
    try:
        from shapely import wkb as shapely_wkb
        from shapely.geometry import Point
        from shapely.ops import unary_union
        from shapely.prepared import prep
    except ImportError:
        print("shapely unavailable (needs the `whg` env on CRC) — skipping")
        return 0

    handles: dict = {}
    cache: dict = {}

    def parent_geom(pid):
        """Prepared union of a parent's polygons, or None. Prepared because one parent is tested
        against many places and `prep` makes the repeat containment tests cheap."""
        if pid in cache:
            return cache[pid]
        gs = []
        for path, off, length in lookup(pid):
            fh = handles.get(path) or handles.setdefault(path, open(path, "rb"))
            fh.seek(off)
            try:
                gs.append(shapely_wkb.loads(fh.read(length)))
            except Exception:
                pass
        g = None if not gs else (gs[0] if len(gs) == 1 else unary_union(gs))
        cache[pid] = (prep(g), g) if g is not None else None
        return cache[pid]

    con = sqlite3.connect(args.db)
    sql = ("SELECT place_id, name, lat, lon, recon_pass, reconciliation FROM place "
           "WHERE whg_match_id IS NOT NULL AND lat IS NOT NULL AND reconciliation IS NOT NULL")
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"

    tested = Counter(); failed = Counter(); untestable = Counter()
    updates, demotions, examples = [], [], []
    for pid, name, lat, lon, rpass, rec in con.execute(sql).fetchall():
        try:
            j = json.loads(rec)
        except Exception:
            continue
        parents = [r.get("relationTo") for r in (j.get("relations") or []) if r.get("relationTo")]
        # Test against the parent the PASS ACTUALLY USED, not simply the narrowest. `3-phon-broad`
        # deliberately relaxes to the next-broader parent, so testing it against the narrowest asks a
        # question the query never claimed to answer — and duly reported a meaningless 95.8% "outside"
        # in the first run. Unconstrained passes are tested against the narrowest available, which is
        # the strongest claim the record makes about where the place should be.
        cand_parents = parents[:-1] if (rpass == "3-phon-broad" and len(parents) >= 2) else parents
        pg = par_id = None
        for par in reversed(cand_parents):      # relations are broadest-first
            pg = parent_geom(par)
            if pg is not None:
                par_id = par
                break
        if pg is None:
            untestable[rpass] += 1
            continue
        prepared, raw = pg
        tested[rpass] += 1
        pt = Point(lon, lat)
        inside = prepared.contains(pt) or raw.distance(pt) <= TOL_DEG
        flags = set(j.get("flags") or [])
        before = FLAG in flags
        if inside:
            flags.discard(FLAG)
        else:
            flags.add(FLAG)
            failed[rpass] += 1
            if len(examples) < 12:
                cand = j.get("candidate") or {}
                examples.append((name, rpass, cand.get("name"), cand.get("confidence"), par_id))
        if (FLAG in flags) != before:
            j["flags"] = sorted(flags)
            updates.append((json.dumps(j), pid))
            if FLAG in flags and args.reject and rpass in UNCONSTRAINED:
                demotions.append(pid)

    def pct(n, d):
        return f"{100.0*n/d:5.1f}%" if d else "    - "

    print("=" * 74)
    print("MATCH-vs-PARENT CONTAINMENT" + ("" if args.apply else "   (report only, nothing written)"))
    print("=" * 74)
    print(f"  {'pass':<16}{'tested':>9}{'outside':>9}{'rate':>9}{'untestable':>12}")
    for p in sorted(set(tested) | set(untestable), key=lambda k: -(tested[k] + untestable[k])):
        print(f"  {str(p):<16}{tested[p]:>9,}{failed[p]:>9,}{pct(failed[p], tested[p]):>9}{untestable[p]:>12,}")
    T, F, U = sum(tested.values()), sum(failed.values()), sum(untestable.values())
    print(f"  {'TOTAL':<16}{T:>9,}{F:>9,}{pct(F, T):>9}{U:>12,}")

    constrained_fail = sum(v for k, v in failed.items() if k not in UNCONSTRAINED)
    if constrained_fail:
        print(f"\n  ⚠️  {constrained_fail:,} violations on passes that used server-side containment.")
        print("      Those queries were already scoped by this same parent, so this is a discrepancy")
        print("      between the gateway's containment and this test — a bug to investigate, not data")
        print("      to reject. They are flagged and never demoted.")

    print(f"\n  untestable = {U:,} matched places whose hierarchy resolved to no geometry-bearing parent.")
    print("      Not a pass: they are the places this check cannot see, and pass 4 is over-represented")
    print("      among them precisely because a missing parent is why they fell to pass 4.")

    if examples:
        print("\n  examples of matches outside their own stated parent:")
        for nm, p, cand, conf, par in examples:
            print(f"    {str(nm)[:24]:<26} [{p}] -> {str(cand)[:26]:<28} conf={conf} outside {par}")

    if args.apply and updates:
        con.executemany("UPDATE place SET reconciliation=? WHERE place_id=?", updates)
        if args.reject and demotions:
            con.executemany(
                "UPDATE place SET whg_match_id=NULL, whg_score=NULL, lat=NULL, lon=NULL, "
                "recon_pass='unmatched', status='unmatched' WHERE place_id=?",
                [(p,) for p in demotions])
        con.commit()
        print(f"\n  {len(updates):,} rows re-flagged"
              + (f"; {len(demotions):,} unconstrained matches demoted to unmatched" if args.reject else ""))
    elif updates:
        print(f"\n  {len(updates):,} rows WOULD be re-flagged (--apply to write)"
              + (f"; {sum(1 for _ in demotions):,} would be demoted" if args.reject else ""))

    for fh in handles.values():
        fh.close()
    close()
    print("MATCH_CONTAINMENT_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
