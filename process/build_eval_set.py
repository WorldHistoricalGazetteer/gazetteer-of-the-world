#!/usr/bin/env python3
"""Build a hand-adjudicable evaluation set: which places are in the index, and which are genuinely absent.

READ-ONLY against the working DB. Writes its own sidecar, `data/eval_set.sqlite`.

WHY THIS EXISTS
---------------
Every proposal made for the Chinese corpus so far — the admin alias table, administrative-generic
stripping, a radial pass, rejecting matches that contradict their parent — has been assessed with
PROXIES: absolute confidence, hit counts, containment agreement. None of those measures correctness.
That is how the first run reached 71% of the corpus on a country-only phonetic pass at a median score
of 99 and looked healthy, and it is why "Tang-Tu-Heen -> Gushu" (Gushu is Dangtu's county seat, so
arguably right) scored as a non-event in testing.

So this assembles the missing instrument. ⚠️ It is a WORKLIST, not yet a gold standard: only `match`
is asserted automatically, from book evidence alone. Deciding whether an unmatched place is ABSENT
from the index or merely UNREACHABLE through 1856 spelling defeated two automated attempts (both
recorded in `refine_absent`), and that distinction is the whole point — so those rows carry their
evidence and wait for a human.

THE INDEPENDENCE RULE, which is the whole design
------------------------------------------------
⚠️ **A label must never derive from the thing being evaluated.** Auto-labelling by `confidence` and
then using the set to evaluate `confidence` would be circular and would confirm whatever it was built
from. So labels come only from evidence that originates in the BOOK, not in the index:

  * the coordinates the 1856 text printed for the place (1,208 of 2,414 Chinese places have them)
  * the variant name forms the text printed
  * the admin hierarchy the text stated

`score` and `confidence` are RECORDED for every candidate, because that is what we want to evaluate
later, but they are never consulted when assigning a label.

ASYMMETRIC RADII, deliberately
------------------------------
`MATCH_KM` (strict) to assert a match; `ABSENT_KM` (generous) to assert absence. A place is only
labelled `absent` when a deliberately BROAD search — no containment, no country narrowing beyond
ccodes, generous size — finds nothing of that name anywhere near where the book says it is. Both
thresholds err toward `review`, because a wrong gold label is worse than no gold label: it silently
corrupts every experiment scored against it.

The absent-versus-unreachable split is the valuable half, and it is exactly what could NOT be
automated — see `refine_absent` for the two attempts and why each failed. Once adjudicated it converts
"the leaf documents are not in the index" from an inference drawn from hit counts (150 constrained
places yielding 86) into a measurement, which is what the thin-coverage report needs and what the
indexing side's v8 retrieval benchmark needs.

Rows are keyed by the same stable signature the human-review sidecar uses (`source|headword|page`),
so labels survive a re-parse or a DB rebuild.

Usage:
  python3 process/build_eval_set.py --ccode CN --limit 200            # build/extend, auto-label
  python3 process/build_eval_set.py --ccode CN --limit 200 --dry-run  # show the plan, query nothing
  python3 process/build_eval_set.py --report                          # summarise the set as it stands
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reconcile import GATEWAY_URL, _clean_admin, _confidence, _post  # noqa: E402
from review_store import sig  # noqa: E402  — same stable-signature discipline as human review

DEFAULT_DB = "data/gotw_seg.sqlite"
DEFAULT_STORE = "data/eval_set.sqlite"
MATCH_KM = 25.0       # strict: assert a match only this close to the printed point
ABSENT_KM = 60.0      # generous: assert absence only if nothing of the name is even this close
SIZE = 20             # candidates per query — wide, because absence must survive a broad look


def km(a, b):
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    (lon1, lat1), (lon2, lat2) = a[:2], b[:2]
    r = math.radians
    h = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def open_store(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS eval("
              "sig TEXT PRIMARY KEY, place_id INTEGER, name TEXT, ccode TEXT, "
              "printed_lat REAL, printed_lon REAL, variants TEXT, hierarchy TEXT, "
              "label TEXT, label_source TEXT, whg_id TEXT, whg_title TEXT, "
              "evidence TEXT, ts TEXT)")
    c.commit()
    return c


def sample(db, ccode, limit, seed, have):
    """Stratified toward places the BOOK can adjudicate: printed coordinates first, then printed
    variants, then the rest. The last stratum is included deliberately — excluding it would build a
    set that only measures the easy half and would flatter every proposal scored against it."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    strata = {"coords": [], "variants": [], "neither": []}
    # Signature components match `review_ui` exactly — sig(s.filename, e.headword_raw, e.page_start) —
    # so a label here and a human review decision there refer to the same entry, and both survive a
    # re-parse. Getting this subtly wrong would produce two stores that silently disagree.
    for r in con.execute("SELECT p.place_id, p.name, p.ordinal, p.extraction, e.headword_raw, "
                         "e.page_start, s.filename FROM place p "
                         "JOIN entry e ON e.entry_id = p.entry_id "
                         "JOIN source s ON s.source_id = e.source_id "
                         "WHERE p.extraction IS NOT NULL"):
        try:
            ext = json.loads(r["extraction"])
        except (ValueError, TypeError):
            continue
        if ccode and ext.get("country_code") != ccode:
            continue
        s = sig(r["filename"], r["headword_raw"], r["page_start"]) + f":{r['ordinal']}"
        if s in have:
            continue
        rec = {"sig": s, "place_id": r["place_id"], "name": r["name"],
               "ccode": ext.get("country_code"), "ext": ext}
        if ext.get("latitude") is not None and ext.get("longitude") is not None:
            strata["coords"].append(rec)
        elif ext.get("variant_names"):
            strata["variants"].append(rec)
        else:
            strata["neither"].append(rec)
    con.close()
    rng = random.Random(seed)
    for v in strata.values():
        rng.shuffle(v)
    # 60 / 20 / rest — weighted to what the book can adjudicate, without excluding the hard tail.
    # The `neither` stratum is deliberately included: a set built only from places carrying printed
    # coordinates would measure the easy half and flatter every proposal scored against it.
    out = strata["coords"][: int(limit * 0.6)] + strata["variants"][: int(limit * 0.2)]
    out += strata["neither"][: max(0, limit - len(out))]
    return out[:limit], {k: len(v) for k, v in strata.items()}


def gather(place):
    """Broad, deliberately unconstrained candidate collection. No containment and no cascade: the
    question is whether the index contains this place AT ALL, not whether our cascade can find it."""
    ext = place["ext"]
    cc = ext.get("country_code")
    queries = [place["name"]] + [v for v in (ext.get("variant_names") or [])[:4] if v]
    seen, cands = set(), []
    for q in queries:
        for mode in ("exact", "phonetic"):
            body = {"query": q, "mode": mode, "size": SIZE}
            if cc:
                body["ccodes"] = [cc]
            r = _post(f"{GATEWAY_URL}/api/reconcile", body)
            for h in ((r.get("hits") or []) if isinstance(r, dict) else []):
                pid = h.get("place_id")
                if pid in seen:
                    continue
                seen.add(pid)
                rp = ((h.get("geometries") or [{}])[0].get("repr_point"))
                cands.append({"id": pid, "title": h.get("title"), "queried_as": q, "mode": mode,
                              "score": h.get("score"), "confidence": _confidence(h), "point": rp})
    return cands


def adjudicate(place, cands):
    """Assign a label from BOOK evidence only. Returns (label, source, whg_id, whg_title).

    Never reads `score` or `confidence` — those are the quantities under evaluation."""
    ext = place["ext"]
    lat, lon = ext.get("latitude"), ext.get("longitude")
    forms = {norm(place["name"])} | {norm(v) for v in (ext.get("variant_names") or [])}
    forms.discard("")

    if lat is not None and lon is not None:
        here = [c for c in cands if c["point"] and (km([lon, lat], c["point"]) or 1e9) <= MATCH_KM]
        near = [c for c in cands if c["point"] and (km([lon, lat], c["point"]) or 1e9) <= ABSENT_KM]
        # A name-consistent candidate at the printed location is the strongest evidence available.
        named = [c for c in here if norm(c["title"]) in forms or any(f and f in norm(c["title"]) for f in forms)]
        if len(named) == 1:
            return "match", "printed-coords+name", named[0]["id"], named[0]["title"]
        if len(named) > 1:
            return "review", "several name-consistent candidates at the printed point", None, None
        if not near:
            # We know where the book says it is; a broad search finds nothing of that name within
            # ABSENT_KM. That is the measurement the ceiling argument needs.
            return "absent", "printed-coords, nothing within %.0fkm" % ABSENT_KM, None, None
        return "review", "candidates near the printed point but none name-consistent", None, None

    # No printed coordinates: a printed MODERN variant that matches a candidate exactly is still
    # book-sourced evidence, but it cannot confirm location, so it is offered rather than asserted.
    exact = [c for c in cands if norm(c["title"]) in forms]
    if len(exact) == 1:
        return "review", "single exact name match, no printed coords to confirm location", \
               exact[0]["id"], exact[0]["title"]
    if not cands:
        return "review", "no candidates and no printed coords — absence unprovable", None, None
    return "review", "no book evidence to adjudicate", None, None


def refine_absent(store):
    """Attach spatial context to `absent` rows and DOWNGRADE them to `review`. It does not decide.

    ⚠️ TWO FAILED ATTEMPTS ARE RECORDED HERE. Both looked authoritative and both were confounded; do
    not re-invent either.

    **Attempt 1 — `absent` from name queries.** A row was labelled `absent` when our NAME queries
    retrieved nothing within ABSENT_KM of the printed point. That conflates two findings with opposite
    remedies: the index genuinely holds no place there (a coverage gap, needing a new source), and the
    index holds the place but our 1856 spelling cannot retrieve it (a retrieval gap). Given
    `Keang-su -> Gansu`, the second is certainly a large share. Shipping it merged would have answered
    the indexing side's "how much of R@200 ≈ 0.48 is model versus missing documents" wrongly.

    **Attempt 2 — a name-less `bounds` probe.** The gateway accepts `bounds` with no `query`, so
    "what is indexed here" can be asked independently of spelling. If it returned places, call the row
    `unreachable`; if empty, `absent`. It returned places for **80 of 80** rows — and the unanimity was
    the tell. The hits are hotels: "Doubletree By Hilton Langfang", "Sheraton Hotel Changsha". In
    populated China a ±60 km box always contains something indexed, so the test asks only whether the
    box is non-empty, which is trivially true and says nothing about the target place.

    **What is actually needed** is whether the SPECIFIC historical settlement is present — which is the
    hard question the whole exercise exists to answer, and it is not decidable from name-similarity or
    from box-occupancy. It needs a human, or a source that lists Qing-era settlements independently
    (CHGIS would serve, if its names could be reached — see `probe_qing_provinces.py`).

    So this function now does the only honest thing: it records the spatial context as EVIDENCE, marks
    the row `review`, and leaves the verdict to adjudication. The evidence bundle is the deliverable —
    printed coordinates, printed variants, hierarchy, candidates with scores and distances, and what
    else sits nearby — so a human can decide each row without re-querying anything."""
    rows = store.execute("SELECT sig, name, printed_lat, printed_lon FROM eval "
                         "WHERE label IN ('absent','unreachable') AND printed_lat IS NOT NULL").fetchall()
    print(f"re-probing {len(rows)} `absent` rows with a name-less spatial query …")
    d = ABSENT_KM / 111.0
    out = Counter()
    for i, (s, name, lat, lon) in enumerate(rows, 1):
        dl = d / max(0.15, math.cos(math.radians(lat)))
        box = {"type": "Polygon", "coordinates": [[[lon - dl, lat - d], [lon + dl, lat - d],
                                                   [lon + dl, lat + d], [lon - dl, lat + d],
                                                   [lon - dl, lat - d]]]}
        r = _post(f"{GATEWAY_URL}/api/reconcile", {"size": 25, "bounds": box})
        hits = (r.get("hits") or []) if isinstance(r, dict) else []
        # Deliberately NOT a verdict — see the docstring. Box-occupancy cannot distinguish a missing
        # settlement from an unreachable one, so the row goes to a human with the context attached.
        label = "review"
        src = ("no name match near the printed point; %d other places indexed within %.0fkm "
               "— absence vs unreachability needs adjudication" % (len(hits), ABSENT_KM))
        out["review"] += 1
        store.execute("UPDATE eval SET label=?, label_source=?, evidence=json_set(COALESCE(evidence,'[]'),"
                      "'$[#]', json(?)) WHERE sig=?",
                      (label, src, json.dumps({"spatial_probe": [h.get("title") for h in hits[:5]],
                                               "n_indexed_nearby": len(hits)}, ensure_ascii=False), s))
        if i % 20 == 0:
            store.commit()
            print(f"  {i}/{len(rows)}  {dict(out)}", flush=True)
    store.commit()
    print(f"\nrefined: {dict(out)}")
    print("  All moved to `review` with spatial context attached. Neither automated test could")
    print("  distinguish a MISSING settlement from an UNREACHABLE one — see the docstring for both")
    print("  failed attempts. That distinction is the point of the set, and it needs a human.")
    return


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--ccode", default="CN")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=1,
                    help="the gateway shares 8 cores with the live site; 1 was the measured-invisible setting")
    ap.add_argument("--seed", type=int, default=20260905)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--refine-absent", action="store_true",
                    help="split `absent` into genuinely-absent vs present-but-unreachable (see below)")
    args = ap.parse_args()

    if args.refine_absent:
        return refine_absent(open_store(args.store))

    store = open_store(args.store)
    have = {r[0] for r in store.execute("SELECT sig FROM eval")}

    if args.report:
        rows = store.execute("SELECT label, label_source, count(*) FROM eval GROUP BY 1, 2").fetchall()
        by_label = Counter()
        for lab, src, n in rows:
            by_label[lab] += n
        tot = sum(by_label.values())
        print(f"evaluation set: {tot} places in {args.store}")
        for lab, n in by_label.most_common():
            print(f"  {lab:<8}{n:>6}   {100.0*n/max(tot,1):5.1f}%")
        print("\n  by evidence:")
        for lab, src, n in sorted(rows, key=lambda r: -r[2]):
            print(f"    {lab:<8}{n:>5}   {src}")
        print("\n  `match` is asserted from book evidence alone. `review` is everything else, and it is")
        print("  the honest majority: two automated attempts to decide ABSENT vs UNREACHABLE were both")
        print("  confounded (see refine_absent), so the set is a structured worklist with the evidence")
        print("  pre-assembled, not yet a gold standard. Adjudicate before scoring anything against it.")
        return

    places, sizes = sample(args.db, args.ccode, args.limit, args.seed, have)
    print(f"strata available ({args.ccode}): {sizes}")
    print(f"sampling {len(places)} new places ({len(have)} already in the set)")
    if args.dry_run:
        for p in places[:5]:
            e = p["ext"]
            print(f"  {p['name'][:26]:<28} coords={e.get('latitude') is not None} "
                  f"variants={len(e.get('variant_names') or [])} "
                  f"hierarchy={[_clean_admin(a, p['ccode']) for a in (e.get('admin_hierarchy') or [])]}")
        print(f"\n  would issue up to {len(places) * 2 * 5} queries. Nothing sent.")
        return

    def work(p):
        return p, gather(p)

    labels = Counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for i, fut in enumerate(as_completed([pool.submit(work, p) for p in places]), 1):
            p, cands = fut.result()
            label, src, wid, wtitle = adjudicate(p, cands)
            labels[label] += 1
            e = p["ext"]
            store.execute(
                "INSERT OR REPLACE INTO eval(sig, place_id, name, ccode, printed_lat, printed_lon, "
                "variants, hierarchy, label, label_source, whg_id, whg_title, evidence, ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (p["sig"], p["place_id"], p["name"], p["ccode"], e.get("latitude"), e.get("longitude"),
                 json.dumps(e.get("variant_names") or [], ensure_ascii=False),
                 json.dumps(e.get("admin_hierarchy") or [], ensure_ascii=False),
                 label, src, wid, wtitle,
                 json.dumps(cands[:25], ensure_ascii=False)))
            if i % 25 == 0:
                store.commit()
                print(f"  {i}/{len(places)}  {dict(labels)}", flush=True)
    store.commit()

    print(f"\nlabels assigned: {dict(labels)}")
    print(f"  match  — a name-consistent candidate within {MATCH_KM:.0f}km of the printed point")
    print(f"  absent — printed point known, nothing of that name within {ABSENT_KM:.0f}km")
    print(f"  review — needs a human; the honest remainder, not a failure")
    print(f"\nwritten to {args.store}. Candidate evidence is stored per row, so adjudication needs no re-querying.")


if __name__ == "__main__":
    main()
