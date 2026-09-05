#!/usr/bin/env python3
"""Reconciliation probe: measure the WHG gateway's post-rebuild behaviour before changing the cascade.

READ-ONLY. Opens the working DB `mode=ro` and never writes a `place` row. Every response is appended
to a JSONL cache so the analysis can be re-run, and the matrix re-extended, without re-querying.

WHY THIS EXISTS
---------------
`reconcile.py`'s cascade was built against the `places_postbarrier-20260502…` index generation, which
has since been deleted. The August/September rebuild (WorldHistoricalGazetteer/indexing) changed four
things that the cascade's design decisions depend on, and every one of those decisions is now a guess:

  1. `containment="fuzzy"` was returning nothing because production held hull-derived `h3_cover`
     values, 79% of the damage being UNDER-coverage (silently omitting genuinely-contained places).
     Remediated 2 Sep; MultiPoint covers fixed 3 Sep. `reconcile.py` pins `containment="exact"`
     solely because fuzzy used to return 0, and `exact` costs a geom-store polygon read per query.
  2. `resolve_region` now borrows a `sameAs`/`exactMatch` co-referent's boundary when a container has
     no polygon of its own (`source="linked-polygon"`). `_resolve_one` discards any parent candidate
     failing `_has_geom`, which may now be throwing away perfectly usable containers.
  3. Scope is fail-closed since 31 Aug, and every scoped response carries a `scope` object saying
     whether containment was actually applied and how. We have never read it; we infer containment
     from which pass won, which cannot distinguish "no parent" from "parent unusable".
  4. `confidence` (absolute, 0-100) exists alongside `score` (normalised by the retrieved pool, so
     the top hit reads ~100 whatever the quality). We threshold on `score`, which is why the median
     score on our Chinese matches is 99.5 and the `score < 90` low_confidence flag catches nothing.

WHAT EACH MEASUREMENT DECIDES
-----------------------------
  fuzzy vs exact containment    -> whether to drop the `containment="exact"` pin (and the geom-store
                                   read) from `_gw_request` / `_resolve_one`
  within vs intersects          -> whether pass 1's `relation="within"` is discarding in-region
                                   matches, as it measurably does for historic borders elsewhere
  has_geom filter vs none       -> whether `_resolve_one`'s `_has_geom(h)` filter should go, and how
                                   many containers only resolve via linked-polygon
  confidence distribution       -> whether `confidence` can replace `score` as the accept/flag
                                   discriminator, and where the threshold sits for THIS corpus
  variants on/off               -> whether to send `extraction.variant_names` as `variants` (36,158
                                   places carry them; we currently send none)
  winner disagreement + km      -> the size of the geolocation change, i.e. how much of the corpus
                                   actually moves if we adopt the above

GATEWAY ONLY. `confidence`, `scope` and `containment` are `/api/reconcile` fields; the public
whgazetteer.org endpoint does not carry them. Run this from a CRC compute node (never a login node):
the cluster-facing interface `gazetteer-clus.crc.pitt.edu:9200` is a direct local connection with no
firewall and no token. The script feature-detects both `confidence` and `scope` and says so loudly if
the deployed gateway predates them, rather than reporting silent zeros.

Usage:
  # On CRC, from a tmux session — an htc CPU allocation, as the reconcile stage uses (never a login node):
  srun --partition=htc --cpus-per-task=4 --mem=8G --time=02:00:00 \
       python3 process/probe_reconcile.py --ccode CN --limit 300 --with-variants

  python3 process/probe_reconcile.py --dry-run                     # print the matrix + query count, no network
  python3 process/probe_reconcile.py --ccode CN --limit 300        # the reported problem case
  python3 process/probe_reconcile.py --ccode CN --limit 300 --with-variants
  python3 process/probe_reconcile.py --control 300                 # random global sample, for comparison
  python3 process/probe_reconcile.py --report-only                 # re-summarise the cache, no network
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
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reconcile import (  # noqa: E402  — sibling module; reuse rather than re-implement
    GATEWAY_URL, _clean_admin, _has_geom, _norm, _post,
)

DEFAULT_DB = "data/gotw_seg.sqlite"
DEFAULT_OUT = "data/probe"
SIZE = 10                 # candidates per query — enough to see the runner-up, cheap enough to fan out
PARENT_SIZE = 8           # matches _resolve_one, so parent resolution is comparable to production
PARENT_THRESHOLD = 80.0   # ditto: the production threshold, deliberately kept so `hasgeom` reproduces it
MIN_AUTO_CONFIDENCE = 30  # the gateway's own recommended line (place#206); below it means phonetic-only

# ── the leaf matrix ──────────────────────────────────────────────────────────
# `parents`: which resolved-parent policy and depth supplies `contained_in`.
#   None            -> no containment at all (the control: production's pass 4)
#   hasgeom-narrow  -> innermost parent resolved under production's `_has_geom` filter
#   any-narrow      -> innermost parent resolved WITHOUT that filter (tests change 2)
#   *-broad         -> the next parent out (production's pass 3)
# A config whose parent policy yields nothing for a place is SKIPPED for that place, exactly as
# `_gw_request` returns None — so the per-config denominators below are honest about coverage.
CONFIGS = [
    # label                    mode        parents           containment  relation
    ("ctl-cc-only",           "phonetic", None,             None,        None),        # = production pass 4
    ("prod-p1-exact-within",  "exact",    "hasgeom-narrow", "exact",     "within"),    # = production pass 1
    ("prod-p2-phon-narrow",   "phonetic", "hasgeom-narrow", "exact",     "intersects"),# = production pass 2
    ("prod-p3-phon-broad",    "phonetic", "hasgeom-broad",  "exact",     "intersects"),# = production pass 3
    ("fuzzy-narrow",          "phonetic", "hasgeom-narrow", "fuzzy",     "intersects"),# fuzzy vs exact
    ("fuzzy-within",          "phonetic", "hasgeom-narrow", "fuzzy",     "within"),    # within vs intersects
    ("anyparent-exact",       "phonetic", "any-narrow",     "exact",     "intersects"),# drop has_geom filter
    ("anyparent-fuzzy",       "phonetic", "any-narrow",     "fuzzy",     "intersects"),# both changes together
    ("anyparent-exactmode",   "exact",    "any-narrow",     "fuzzy",     "intersects"),# precision under both
]
CONTROL = "ctl-cc-only"


# ── sampling ─────────────────────────────────────────────────────────────────
def sample(db, ccode, limit, control, seed):
    """Places from the NAME-cascade population: no printed coordinates (those bypass the cascade via
    the coord-authoritative path, so including them would measure a route this probe cannot change).

    `--ccode` additionally requires a non-empty admin_hierarchy: without one there is no containment
    to test and the place can only ever be the control. `--control` takes a random global sample with
    no such requirement, so the two are NOT like-for-like and the summary keeps them apart."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute("SELECT place_id, name, extraction FROM place WHERE extraction IS NOT NULL"):
        try:
            ext = json.loads(r["extraction"])
        except (ValueError, TypeError):
            continue
        if ext.get("latitude") is not None and ext.get("longitude") is not None:
            continue                                   # coord-authoritative population, not ours
        if not (r["name"] or "").strip():
            continue
        if ccode:
            if ext.get("country_code") != ccode or not (ext.get("admin_hierarchy") or []):
                continue
        rows.append({"place_id": r["place_id"], "name": r["name"], "ext": ext})
    con.close()
    rng = random.Random(seed)
    rng.shuffle(rows)
    return rows[: (limit if ccode else control)]


# ── parent resolution, under both policies ───────────────────────────────────
_PARENTS: dict = {}   # (norm name, cc, container id|None, policy) -> hit dict | None


def _resolve_level(name, cc, container, policy):
    """Resolve ONE admin level to a WHG place id, under `policy`.

    `hasgeom` reproduces production `_resolve_one` exactly: only candidates whose geometries carry
    `has_geom` are eligible, top score wins, threshold 80. `any` drops that filter and takes the top
    candidate outright, on the premise that the gateway can now resolve a region from a point-only id
    by borrowing a co-referent's boundary. Whether it actually can is not assumed here: the leaf
    query's own `scope.mode` reports it, and the summary counts `linked-polygon`.

    Returns the full candidate dict (so `confidence` and `has_geom` reach the summary), or None.
    """
    key = (_norm(name), cc, container, policy)
    if key in _PARENTS:
        return _PARENTS[key]
    res = None
    for mode in ("exact", "phonetic"):
        body = {"query": name, "mode": mode, "size": PARENT_SIZE}
        if cc:
            body["ccodes"] = [cc]
        if container:
            # Descend inside the already-resolved ancestor. `fuzzy`/`intersects` deliberately, even
            # for the `hasgeom` policy: this is resolving the CHAIN, not the leaf, and a strict test
            # here would confound the leaf measurement with a second, unmeasured containment choice.
            body.update(contained_in=[container], containment="fuzzy", relation="intersects")
        resp = _post(f"{GATEWAY_URL}/api/reconcile", body)
        if not resp or "_error" in resp:
            continue
        hits = [h for h in (resp.get("hits") or []) if h.get("score", 0) >= PARENT_THRESHOLD]
        if policy == "hasgeom":
            hits = [h for h in hits if _has_geom(h)]
        if not hits:
            continue
        res = max(hits, key=lambda h: h.get("score", 0))
        break
    _PARENTS[key] = res
    return res


def resolve_chain(place, policy):
    """Resolve a place's admin_hierarchy broadest-first under `policy`; returns the ids, broadest first.

    A level that fails to resolve is SKIPPED but does not reset the container, so the chain degrades
    to the deepest ancestor that did resolve rather than collapsing to nothing — production's
    `resolve_hierarchy` behaves the same way and that behaviour is not what is under test here."""
    ext = place["ext"]
    cc = ext.get("country_code")
    ids, container, seen = [], None, set()
    for raw in (ext.get("admin_hierarchy") or []):
        name = _clean_admin(raw)
        if not name or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        hit = _resolve_level(name, cc, container, policy)
        if not hit:
            continue
        ids.append({"id": hit["place_id"], "title": hit.get("title"), "score": hit.get("score"),
                    "confidence": hit.get("confidence"), "has_geom": _has_geom(hit), "level": raw})
        container = hit["place_id"]
    return ids


def parents_for(place, spec, chains):
    """`contained_in` list for a config's parent spec, or None when the config cannot apply here."""
    if spec is None:
        return []
    policy, depth = spec.split("-")
    ids = chains[policy]
    if depth == "narrow":
        return [ids[-1]["id"]] if ids else None
    return [ids[-2]["id"]] if len(ids) >= 2 else None


# ── querying ─────────────────────────────────────────────────────────────────
def build_query(place, cfg, chains, with_variants):
    label, mode, pspec, containment, relation = cfg
    pids = parents_for(place, pspec, chains)
    if pids is None:
        return None
    body = {"query": place["name"], "mode": mode, "size": SIZE}
    cc = place["ext"].get("country_code")
    if cc:
        body["ccodes"] = [cc]                      # country proxy, on throughout, as in production
    if pids:
        body.update(contained_in=pids, containment=containment, relation=relation)
    if with_variants:
        # The gateway scores a variant at VARIANT_SCORE_WEIGHT and folds it into the same pool, so
        # this is additive recall, not a second query. 36,158 places carry printed variant forms and
        # the cascade sends none of them.
        vs = [v for v in (place["ext"].get("variant_names") or []) if v and v.strip()][:8]
        if vs:
            body["variants"] = vs
    return body


def run(places, args, cache, out_fh):
    """Issue every missing (place, config, variants) query; append each response to the JSONL cache."""
    chains_by_pid = {}

    def chains_for(p):
        if p["place_id"] not in chains_by_pid:
            chains_by_pid[p["place_id"]] = {
                "hasgeom": resolve_chain(p, "hasgeom"),
                "any": resolve_chain(p, "any"),
            }
        return chains_by_pid[p["place_id"]]

    # Parent resolution is sequential and cached: shared ancestors collapse (every "Che-kyang"
    # resolved once), and running it concurrently would multiply the cache misses it exists to avoid.
    for i, p in enumerate(places, 1):
        chains_for(p)
        if i % 25 == 0:
            print(f"  parents resolved for {i}/{len(places)} places "
                  f"({len(_PARENTS)} distinct level lookups)", flush=True)

    variant_modes = [False, True] if args.with_variants else [False]
    jobs = []
    for p in places:
        ch = chains_for(p)
        for cfg in CONFIGS:
            for wv in variant_modes:
                key = f"{p['place_id']}|{cfg[0]}|{'v' if wv else '-'}"
                if key in cache:
                    continue
                body = build_query(p, cfg, ch, wv)
                if body is None:
                    continue
                jobs.append((key, p, cfg, wv, body, ch))

    print(f"  {len(jobs)} queries to issue ({len(cache)} already cached)", flush=True)
    if not jobs:
        return

    def work(job):
        key, p, cfg, wv, body, ch = job
        resp = _post(f"{GATEWAY_URL}/api/reconcile", body)
        return {
            "key": key, "place_id": p["place_id"], "name": p["name"],
            "ccode": p["ext"].get("country_code"),
            "admin_hierarchy": p["ext"].get("admin_hierarchy") or [],
            "config": cfg[0], "variants": wv, "request": body,
            "chains": {k: [{kk: vv for kk, vv in d.items()} for d in v] for k, v in ch.items()},
            "scope": resp.get("scope") if isinstance(resp, dict) else None,
            "error": resp.get("_error") if isinstance(resp, dict) else "no response",
            "hits": [
                {"place_id": h.get("place_id"), "title": h.get("title"), "score": h.get("score"),
                 "confidence": h.get("confidence"), "has_geom": _has_geom(h),
                 "repr_point": ((h.get("geometries") or [{}])[0].get("repr_point")),
                 "match": h.get("match")}
                for h in ((resp.get("hits") or []) if isinstance(resp, dict) else [])
            ],
        }

    done = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for fut in as_completed([pool.submit(work, j) for j in jobs]):
            rec = fut.result()
            out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out_fh.flush()
            cache[rec["key"]] = rec
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(jobs)} queries", flush=True)


# ── analysis ─────────────────────────────────────────────────────────────────
def _km(a, b):
    if not a or not b or len(a) < 2 or len(b) < 2:
        return None
    (lon1, lat1), (lon2, lat2) = a[:2], b[:2]
    r = math.radians
    h = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _pct(n, d):
    return f"{100.0 * n / d:5.1f}%" if d else "    - "


def _quartiles(xs):
    if not xs:
        return "-"
    xs = sorted(xs)
    q = statistics.quantiles(xs, n=4) if len(xs) >= 4 else [xs[0], statistics.median(xs), xs[-1]]
    return f"{q[0]:.0f} / {q[1]:.0f} / {q[2]:.0f}"


def report(cache, out_dir):
    recs = list(cache.values())
    if not recs:
        print("No responses cached — nothing to report.")
        return

    # Feature detection FIRST. Reporting a zero for a field the deployed gateway never sends would
    # look like a finding, and this whole probe exists because a number that cannot fail is worthless.
    any_conf = any(h.get("confidence") is not None for r in recs for h in r["hits"])
    scoped = [r for r in recs if r["request"].get("contained_in")]
    any_scope = any(r.get("scope") for r in scoped)
    print("\n" + "=" * 78)
    print("GATEWAY FEATURE DETECTION")
    print("=" * 78)
    print(f"  absolute `confidence` on hits : {'PRESENT' if any_conf else 'ABSENT — deployed gateway predates place#206; confidence findings unavailable'}")
    print(f"  `scope` on contained_in query : {'PRESENT' if any_scope else 'ABSENT — deployed gateway predates place#144; containment cannot be verified'}")
    errs = Counter(r["error"] for r in recs if r["error"])
    if errs:
        print(f"  transport errors              : {sum(errs.values())} — {errs.most_common(3)}")

    by_pid_cfg = {}
    for r in recs:
        if r["variants"]:
            continue                                   # baseline table is variants-off
        by_pid_cfg[(r["place_id"], r["config"])] = r

    def winner(r):
        return max(r["hits"], key=lambda h: h.get("score") or 0) if r and r["hits"] else None

    print("\n" + "=" * 78)
    print("PER-CONFIG RESULTS  (variants off)")
    print("=" * 78)
    print(f"{'config':<24}{'ran':>6}{'hits':>7}{'scope ok':>10}{'conf>=30':>10}"
          f"{'poly':>7}  {'score q1/med/q3':>17}  {'conf q1/med/q3':>17}")
    summary = {}
    for cfg in CONFIGS:
        label = cfg[0]
        rs = [r for r in recs if r["config"] == label and not r["variants"]]
        if not rs:
            continue
        ws = [winner(r) for r in rs]
        ws = [w for w in ws if w]
        sc = [w["score"] for w in ws if w.get("score") is not None]
        cf = [w["confidence"] for w in ws if w.get("confidence") is not None]
        s_ok = sum(1 for r in rs if (r.get("scope") or {}).get("applied"))
        s_req = sum(1 for r in rs if r["request"].get("contained_in"))
        good = sum(1 for c in cf if c >= MIN_AUTO_CONFIDENCE)
        poly = sum(1 for w in ws if w.get("has_geom"))
        print(f"{label:<24}{len(rs):>6}{len(ws):>7}"
              f"{(_pct(s_ok, s_req) if s_req else '     n/a'):>10}"
              f"{(_pct(good, len(cf)) if cf else '     n/a'):>10}"
              f"{_pct(poly, len(ws)):>7}  {_quartiles(sc):>17}  {_quartiles(cf):>17}")
        summary[label] = {
            "ran": len(rs), "with_hits": len(ws), "scope_requested": s_req, "scope_applied": s_ok,
            "conf_measured": len(cf), "conf_ge_30": good, "winner_has_geom": poly,
            "score_median": statistics.median(sc) if sc else None,
            "confidence_median": statistics.median(cf) if cf else None,
        }

    if any_scope:
        print("\n  scope.mode over scoped queries:")
        modes = Counter((r.get("scope") or {}).get("mode") for r in scoped if r.get("scope"))
        for m, n in modes.most_common():
            print(f"    {str(m):<18}{n:>6}   {_pct(n, sum(modes.values()))}")
        linked = sum(len((r.get('scope') or {}).get('containers_linked') or []) for r in scoped)
        unres = sum(len((r.get('scope') or {}).get('containers_unresolved') or []) for r in scoped)
        print(f"    containers borrowed from a co-referent (linked-polygon): {linked}")
        print(f"    containers that resolved to no boundary at all         : {unres}")

    # Paired comparisons. The per-config table above cannot answer the decision questions, because
    # each config runs on a different subset (a config is skipped where its parent policy yields
    # nothing) and a per-config recall number silently compares different denominators. These pair
    # the SAME place under two configs differing in exactly one knob, which is the only comparison
    # that can justify changing that knob.
    def paired(a_label, b_label):
        """(both hit, only A hit, only B hit, A won on confidence, B won on confidence)."""
        both = only_a = only_b = a_better = b_better = 0
        for pid in {r["place_id"] for r in recs}:
            a, b = by_pid_cfg.get((pid, a_label)), by_pid_cfg.get((pid, b_label))
            if not a or not b:
                continue                      # not run under both — not a pair, so not counted
            wa, wb = winner(a), winner(b)
            if wa and wb:
                both += 1
                ca, cb = wa.get("confidence"), wb.get("confidence")
                if ca is not None and cb is not None:
                    if cb > ca:
                        b_better += 1
                    elif ca > cb:
                        a_better += 1
            elif wa:
                only_a += 1
            elif wb:
                only_b += 1
        return both, only_a, only_b, a_better, b_better

    print("\n" + "=" * 78)
    print("PAIRED COMPARISONS — one knob at a time, same places")
    print("=" * 78)
    for question, a_label, b_label in (
        ("containment exact -> fuzzy", "prod-p2-phon-narrow", "fuzzy-narrow"),
        ("relation intersects -> within", "fuzzy-narrow", "fuzzy-within"),
        ("parent filter has_geom -> none", "prod-p2-phon-narrow", "anyparent-exact"),
        ("both changes together", "prod-p2-phon-narrow", "anyparent-fuzzy"),
    ):
        both, only_a, only_b, a_better, b_better = paired(a_label, b_label)
        n = both + only_a + only_b
        print(f"\n  {question}   ({a_label} -> {b_label})")
        print(f"    pairs where both returned hits      {both:>6}   {_pct(both, n)}")
        print(f"    only the FIRST returned hits        {only_a:>6}   {_pct(only_a, n)}"
              f"   <- recall LOST by the change")
        print(f"    only the SECOND returned hits       {only_b:>6}   {_pct(only_b, n)}"
              f"   <- recall GAINED by the change")
        if a_better or b_better:
            print(f"    of the both-hit pairs, confidence rose {b_better}, fell {a_better}")

    # Parent resolution: the has_geom filter's cost, measured on distinct chains rather than queries.
    print("\n" + "=" * 78)
    print("PARENT RESOLUTION  (does dropping the `has_geom` filter buy containment?)")
    print("=" * 78)
    seen_pid, hg_any, an_any, both, deeper = set(), 0, 0, 0, 0
    for r in recs:
        if r["place_id"] in seen_pid:
            continue
        seen_pid.add(r["place_id"])
        hg, an = r["chains"]["hasgeom"], r["chains"]["any"]
        hg_any += bool(hg)
        an_any += bool(an)
        both += bool(hg and an)
        deeper += len(an) > len(hg)
    n = len(seen_pid)
    print(f"  places sampled                              {n:>6}")
    print(f"  at least one parent under `has_geom` (prod) {hg_any:>6}   {_pct(hg_any, n)}")
    print(f"  at least one parent without the filter      {an_any:>6}   {_pct(an_any, n)}")
    print(f"  deeper chain without the filter             {deeper:>6}   {_pct(deeper, n)}")

    # The thing that actually matters: does the geolocation move, and by how far.
    print("\n" + "=" * 78)
    print(f"WINNER DISAGREEMENT vs the control ({CONTROL} = production pass 4)")
    print("=" * 78)
    print(f"{'config':<24}{'compared':>10}{'same id':>10}{'moved':>9}{'>100km':>9}{'>1000km':>9}  median km")
    for cfg in CONFIGS:
        label = cfg[0]
        if label == CONTROL:
            continue
        pairs, same, dists = 0, 0, []
        for pid in {r["place_id"] for r in recs}:
            a = by_pid_cfg.get((pid, CONTROL))
            b = by_pid_cfg.get((pid, label))
            wa, wb = winner(a), winner(b)
            if not wa or not wb:
                continue
            pairs += 1
            if wa["place_id"] == wb["place_id"]:
                same += 1
                continue
            d = _km(wa.get("repr_point"), wb.get("repr_point"))
            if d is not None:
                dists.append(d)
        far100 = sum(1 for d in dists if d > 100)
        far1000 = sum(1 for d in dists if d > 1000)
        med = f"{statistics.median(dists):,.0f}" if dists else "-"
        print(f"{label:<24}{pairs:>10}{_pct(same, pairs):>10}{_pct(pairs - same, pairs):>9}"
              f"{_pct(far100, pairs):>9}{_pct(far1000, pairs):>9}  {med:>9}")
        summary.setdefault(label, {}).update(
            {"vs_control_compared": pairs, "vs_control_same": same,
             "vs_control_median_km": statistics.median(dists) if dists else None})

    # Variants, if run.
    vrecs = [r for r in recs if r["variants"]]
    if vrecs:
        print("\n" + "=" * 78)
        print("VARIANTS  (sending extraction.variant_names as `variants`)")
        print("=" * 78)
        vmap = {(r["place_id"], r["config"]): r for r in vrecs}
        changed = improved = 0
        for (pid, label), rv in vmap.items():
            rb = by_pid_cfg.get((pid, label))
            wb, wv = winner(rb), winner(rv)
            if not wb or not wv:
                continue
            if wb["place_id"] != wv["place_id"]:
                changed += 1
                if (wv.get("confidence") or 0) > (wb.get("confidence") or 0):
                    improved += 1
        print(f"  pairs compared            {len(vmap):>6}")
        print(f"  winner changed            {changed:>6}   {_pct(changed, len(vmap))}")
        print(f"  ...and confidence rose    {improved:>6}   {_pct(improved, max(changed, 1))} of the changes")

    # Worked examples: the ones a human should read before we change anything.
    print("\n" + "=" * 78)
    print("EXAMPLES — control winner vs best containment-scoped winner")
    print("=" * 78)
    shown = 0
    for pid in sorted({r["place_id"] for r in recs}):
        a = by_pid_cfg.get((pid, CONTROL))
        if not a or not a["hits"]:
            continue
        wa = winner(a)
        alts = [(lbl, winner(by_pid_cfg[(pid, lbl)]))
                for lbl in ("anyparent-fuzzy", "fuzzy-narrow", "prod-p2-phon-narrow")
                if (pid, lbl) in by_pid_cfg and by_pid_cfg[(pid, lbl)]["hits"]]
        alts = [(l, w) for l, w in alts if w and w["place_id"] != wa["place_id"]]
        if not alts:
            continue
        lbl, wb = alts[0]
        d = _km(wa.get("repr_point"), wb.get("repr_point"))
        print(f"  {a['name']}  [{' > '.join(a['admin_hierarchy'])}]")
        print(f"    {CONTROL:<20} {wa['title']} ({wa['place_id']}) "
              f"score {wa.get('score', 0):.1f} conf {wa.get('confidence')}")
        print(f"    {lbl:<20} {wb['title']} ({wb['place_id']}) "
              f"score {wb.get('score', 0):.1f} conf {wb.get('confidence')}"
              + (f"   [{d:,.0f} km apart]" if d is not None else ""))
        shown += 1
        if shown >= 12:
            break

    path = os.path.join(out_dir, "summary.json")
    with open(path, "w") as fh:
        json.dump({"configs": [c[0] for c in CONFIGS], "min_auto_confidence": MIN_AUTO_CONFIDENCE,
                   "confidence_available": any_conf, "scope_available": any_scope,
                   "per_config": summary}, fh, indent=2)
    print(f"\nSummary written to {path}")


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--ccode", help="restrict the sample to one country (e.g. CN) with a non-empty admin_hierarchy")
    ap.add_argument("--limit", type=int, default=300, help="sample size when --ccode is given")
    ap.add_argument("--control", type=int, default=300, help="sample size for a random global sample")
    ap.add_argument("--concurrency", type=int, default=12,
                    help="the external service is the limiter; keep this moderate")
    ap.add_argument("--with-variants", action="store_true",
                    help="also run every config with extraction.variant_names sent as `variants` (doubles queries)")
    ap.add_argument("--seed", type=int, default=20260905, help="sampling seed; keep it fixed to extend a run")
    ap.add_argument("--dry-run", action="store_true", help="print the matrix and query count, issue nothing")
    ap.add_argument("--report-only", action="store_true", help="re-summarise the cache without querying")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    tag = args.ccode or "global"
    cache_path = os.path.join(args.out_dir, f"responses-{tag}.jsonl")

    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                cache[rec["key"]] = rec
        print(f"Loaded {len(cache)} cached responses from {cache_path}")

    if args.report_only:
        report(cache, args.out_dir)
        return

    places = sample(args.db, args.ccode, args.limit, args.control, args.seed)
    print(f"Sampled {len(places)} places ({tag}); {len(CONFIGS)} configs"
          f"{' x2 for variants' if args.with_variants else ''}")

    if args.dry_run:
        print(f"\nGateway: {GATEWAY_URL}   (cluster-facing; CRC compute node only)\n")
        for label, mode, pspec, cont, rel in CONFIGS:
            scope = "no containment (control)" if pspec is None else f"contained_in={pspec}, {cont}/{rel}"
            print(f"  {label:<24} mode={mode:<9} {scope}")
        n = len(places) * len(CONFIGS) * (2 if args.with_variants else 1)
        levels = sum(len(p["ext"].get("admin_hierarchy") or []) for p in places)
        print(f"\n  <= {n:,} leaf queries + up to {levels:,} parent lookups (heavily cached, x2 policies)")
        print("  Nothing was sent. Re-run without --dry-run from a CRC compute node.")
        if places:
            p = places[0]
            print("\n  Example request (first config that applies):")
            print("   ", json.dumps(build_query(p, CONFIGS[0], {"hasgeom": [], "any": []}, False),
                                    ensure_ascii=False))
        return

    with open(cache_path, "a") as out_fh:
        run(places, args, cache, out_fh)
    report(cache, args.out_dir)


if __name__ == "__main__":
    main()
