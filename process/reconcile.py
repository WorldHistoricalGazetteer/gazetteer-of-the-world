#!/usr/bin/env python3
"""Reconciliation stage: geolocate `place` rows against WHG, as a cascade.

Pass 0 (gateway only) first resolves each place's **admin hierarchy** top-down: the LLM-extracted
`admin_hierarchy` levels, broadest first, each resolved to a WHG place id that HAS GEOMETRY (`has_geom`)
and queried `contained_in` the previously-resolved parent — so we descend an actual nested region. The
country level is handled by `ccodes` (a reliable proxy), not an id lookup. The chain is recorded as
Linked-Places `gvp:broaderPartitive` (partOf) relations (stored in `reconciliation.relations`, even when
the leaf stays unmatched).

The leaf then runs the cascade — each pass on the previous one's misses, `ccodes=[cc]` throughout;
precision first, then progressively relaxed recall:

  Pass 1  exact,    contained_in=[narrowest parent], relation=intersects    (strict text, in the region)
  Pass 2  phonetic, contained_in=[narrowest parent], relation=intersects    (Symphonym within the parent —
                    stops a phonetic look-alike matching on the far side of the country)
  Pass 3  phonetic, contained_in=[next-broader parent]                      (relax the region outward)
  Pass 4  phonetic, ccodes only                                             (parent had no geometry / none)
  Pass 5  phonetic, bounds=box around the printed coordinates               (coordinate-bearing fallback)

`contained_in` is WHG's server-side containment (union of the named places' geometries); far better
than the padded centroid-bbox this previously used.

⚠️ CORRECTNESS BEFORE MATCH COUNT. The first run of this cascade traded one for the other and got the
trade backwards: 71% of the corpus (87% of Chinese places) was matched by pass 4, country-only
phonetic, at a median `score` of ~99 and a median absolute `confidence` of 22 — the gateway's
"phonetic-only, no lexical evidence" band. Those are not matches; they are Symphonym observing that
two names sound alike, published at score 99 where nothing downstream can tell them from real ones.
Every gate below is therefore tuned to REFUSE, and the corpus match count is expected to fall. A place
left unmatched is a known gap; a false match is a silent error that propagates into WHG.

What that means concretely (all measured 2026-09-05, paired, against the post-rebuild index):
  * accept on absolute `confidence` (>= GOTW_MIN_CONFIDENCE, default 30), never on `score`, which is
    normalised by the retrieved pool and reads ~100 whether or not anything matched;
  * a phonetic-only ADMIN PARENT must clear the same bar — "Keang-su" resolved to Gansu at score 99.5;
  * containment uses `fuzzy` (the H3 defect that forced `exact` was fixed in the index rebuild; paired
    re-measurement shows fuzzy loses nothing and costs less);
  * `relation=intersects` throughout — `within` cost 5.3% of hits globally for no gain, because
    historic borders do not nest inside modern ones;
  * a point-only source can NEVER be a container, whatever else recommends it (see `_has_geom`);
  * printed admin names are aliased to modern forms before resolution (see `_ADMIN_ALIASES`), which
    fixes published partOf relations but is NOT expected to improve geolocation.

Two backends (same cascade):
  * **gateway** (default) — POST directly to the Pitt ES gateway's `/api/reconcile`
    (`gazetteer-clus.crc.pitt.edu:9200`, the cluster-facing interface — a direct local connection
    from CRC compute nodes, no firewall, no token). One `ReconcileRequest` per query, run
    concurrently; the response carries the centroid inline (`geometries[].repr_point` = [lon,lat]),
    so no separate data-extension call. Fastest; use from CRC.
  * **api** — the public endpoint `https://whgazetteer.org/reconcile` (W3C-style batched
    `{queries:{…}}`, `countries`, token from `.env`; centroid via a second `extend` POST). Works
    anywhere with internet; the gateway proxies the same Symphonym-KNN behind it.

Both honour exact/phonetic + country + spatial bounds server-side. Never filter by AAT `types`/
`fclasses` — sparsely populated, tanks recall. Threshold on `score`, not the conservative `match`.
Writes whg_match_id, whg_score, lat, lon, recon_pass, reconciliation(JSON), status.

Usage:
  python3 process/reconcile.py --seed-demo 8                       # demo (no extraction needed)
  python3 process/reconcile.py --limit 200 --concurrency 24        # gateway (default), on CRC
  python3 process/reconcile.py --backend api --concurrency 6       # public API (needs WHG_API_TOKEN)
"""
from __future__ import annotations
import argparse, json, math, os, re, sqlite3, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

GATEWAY_URL = os.getenv("WHG_GATEWAY_URL", "http://gazetteer-clus.crc.pitt.edu:9200")
DO_URL = "https://whgazetteer.org/reconcile"
USER_AGENT = "GOTW-reconcile/0.3 (https://github.com/WorldHistoricalGazetteer/gazetteer-of-the-world; stephen@docuracy.co.uk)"
CHUNK = 25                # queries per DO-API POST (batched); gateway is one query per POST
DEFAULT_THRESHOLD = 80
DEFAULT_RADIUS_KM = 150

# ── the discriminator ────────────────────────────────────────────────────────
# `score` is normalised by the best candidate in the RETRIEVED POOL, so the top hit reads ~100 whether
# the match is perfect or the best of a bad lot. Thresholding on it is thresholding on nothing: measured
# 2026-09-05, the median score of our Chinese matches was 99.5 and of the global corpus 100 — the two
# populations are indistinguishable by score. `confidence` (place#206) is ABSOLUTE and comparable
# between queries, and separates them completely: 85.7% of global winners clear 30 against 36.8% of
# Chinese ones (median 100 vs 22). The gateway's own calibration:
#
#     100      exactly spelled                      87-91  a derived head-word match
#     ~32      lexically near                       22-26  phonetic-only, NO lexical evidence
#
# 30 is the gateway's recommended line and sits in the observed gap. Below it a match is Symphonym
# saying two names sound alike and nothing more, which is how "Ho-Tsih-Heen" in Shantung came to match
# Hefei Xinqiao International Airport at score 99.2.
MIN_CONFIDENCE = float(os.environ.get("GOTW_MIN_CONFIDENCE", "30"))

# A parent is held to the same bar, because a WRONG parent is worse than no parent: it scopes the leaf
# search into a region the place is not in, so the leaf finds nothing and falls through to the weakest
# pass. Measured: "Keang-su" (Jiangsu) resolved to GANSU at score 99.5, confidence 21.8. Correct
# phonetic parents in the same test sat at 21.6-38.0, i.e. overlapping the wrong one, so a phonetic-only
# parent is not trustworthy at any score. Exact-mode hits are exempt (see `_resolve_one`).
MIN_PARENT_CONFIDENCE = float(os.environ.get("GOTW_MIN_PARENT_CONFIDENCE", "30"))

# Containment test for `contained_in`. `exact` (Shapely, reads a real polygon from the /vast geom store)
# was pinned here only because the gateway's `fuzzy` H3 mode used to return 0 hits — a defect traced in
# 2026-09 to hull-derived `h3_cover` values, 79% of the damage being UNDER-coverage, and remediated in
# the index rebuild. Re-measured paired on the same places afterwards: fuzzy loses nothing and gains
# 3.3% globally / 1.1% on the China sample. So `fuzzy` is now preferred for its cost (it runs entirely
# off ES `_source`, with no geom-store dependency), not for its recall.
CONTAINMENT = os.environ.get("GOTW_CONTAINMENT", "fuzzy")

# (label, mode, contain, relation, coords) — every pass also applies ccodes=[cc] when known (the reliable
# country-level proxy). `contain`: None | "narrow" (innermost polygon-bearing parent) | "broad" (next
# parent out); `relation`: WHG spatial relation for contained_in. Precision first (exact, inside the
# narrowest parent), then recall (phonetic within parent → relax outward → country-only → printed coords).
#
# Pass 1 used `relation="within"`, requiring the candidate's own geometry to sit wholly inside the parent.
# Measured paired: that costs 5.3% of hits globally and gains nothing, because historic borders do not
# nest inside modern ones and a candidate with a large or imprecise geometry fails `within` against a
# parent it genuinely sits in. `intersects` throughout.
#
# Pass 4 (country-only phonetic) is retained but is no longer a recall backstop: it took 71% of the
# corpus and 87% of Chinese places on the previous run, at a median confidence of 22. MIN_CONFIDENCE now
# rejects most of what it returns, which is the intended effect — see `_gw_top`.
#
# NOTE: places that carry printed coordinates are NOT run through this name cascade — coordinates are
# authoritative, so they go through the dedicated coord pass (run_coord_pass): best name match WITHIN a
# radius of the printed point, else located at the coords with a `coord_only` flag. These passes handle
# only the ~93% of places with no printed coordinates.
PASSES = [
    ("1-exact-in",   "exact",    "narrow", "intersects", False),
    ("2-phon-in",    "phonetic", "narrow", "intersects", False),
    ("3-phon-broad", "phonetic", "broad",  "intersects", False),
    ("4-phon-cc",    "phonetic", None,     None,         False),
]


def token() -> str:
    tok = os.getenv("WHG_API_TOKEN")
    if not tok:
        sys.exit("WHG_API_TOKEN not set — add it to .env / ~/.gotw_env (needed for --backend api)")
    return tok


def ensure_columns(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(place)")}
    for col, typ in (("reconciliation", "TEXT"), ("whg_score", "REAL"), ("recon_pass", "TEXT")):
        if col not in cols:
            con.execute(f"ALTER TABLE place ADD COLUMN {col} {typ}")
    con.commit()


def _bounds(lat, lng, radius_km):
    d = radius_km / 111.0
    return {"type": "Polygon", "coordinates": [[
        [lng - d, lat - d], [lng + d, lat - d], [lng + d, lat + d],
        [lng - d, lat + d], [lng - d, lat - d]]]}


# ── coordinate-authoritative matching ─────────────────────────────────────────
# When a place prints its own coordinates we trust them as ground truth for *location* and resolve to the
# best NAME match WITHIN a radius of that point (bounds replaces ccodes). If several qualify we take the
# NEAREST. If none reaches COORD_THRESHOLD we keep the place located at its printed coords with a
# `coord_only` flag (no WHG match). A match below 90 is kept but flagged `low_confidence`.
COORD_RADIUS_KM = float(os.environ.get("GOTW_COORD_RADIUS_KM", "50"))
COORD_THRESHOLD = float(os.environ.get("GOTW_COORD_THRESHOLD", "70"))


def _haversine(lat1, lng1, lat2, lng2):
    p = math.radians
    h = (math.sin(p(lat2 - lat1) / 2) ** 2
         + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lng2 - lng1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _circle_bbox(lat, lng, km):
    """GeoJSON box enclosing the km-radius circle (lng widened by 1/cos lat so the circle isn't clipped E–W)."""
    dlat = km / 111.0
    dlng = km / (111.0 * max(0.15, math.cos(math.radians(lat))))
    return {"type": "Polygon", "coordinates": [[
        [lng - dlng, lat - dlat], [lng + dlng, lat - dlat], [lng + dlng, lat + dlat],
        [lng - dlng, lat + dlat], [lng - dlng, lat - dlat]]]}


def _coords_of(place):
    ext = json.loads(place["extraction"]) if place["extraction"] else {}
    lat, lng = ext.get("latitude"), ext.get("longitude")
    return (lat, lng) if (lat is not None and lng is not None) else (None, None)


def _gw_coord_match(place, radius_km=COORD_RADIUS_KM, threshold=COORD_THRESHOLD):
    """Best within-radius name match for a coord-bearing place, or a coord_only result.
    Returns {coord_only, coords, ...}: coord_only=False carries id/name/score/coords/dist_km/flags."""
    lat, lng = _coords_of(place)
    if lat is None:
        return None
    bbox = _circle_bbox(lat, lng, radius_km)
    cands = []                                       # (dist_km, hit) within the true radius, name score >= threshold
    for mode in ("exact", "phonetic"):
        resp = _post(f"{GATEWAY_URL}/api/reconcile", {"query": place["name"], "mode": mode, "bounds": bbox, "size": 20})
        for h in (resp.get("hits") or []):
            geos = h.get("geometries") or []
            rp = geos[0].get("repr_point") if geos else None
            if not rp or h.get("score", 0) < threshold:
                continue
            if not _confident_enough(h):             # absolute name evidence, as in the name cascade
                continue
            d = _haversine(lat, lng, rp[1], rp[0])
            if d <= radius_km:
                cands.append((d, h))
    if not cands:
        return {"coord_only": True, "coords": (lat, lng)}
    d, top = min(cands, key=lambda t: t[0])          # coords are authoritative -> nearest qualifying name match
    rp = (top.get("geometries") or [{}])[0].get("repr_point")
    # The old `score < 90` test flagged almost nothing, because score is pool-relative and reads ~100
    # everywhere. Flag on the absolute measure instead, at twice the acceptance floor.
    conf = _confidence(top)
    flags = ["low_confidence"] if (conf is not None and conf < 2 * MIN_CONFIDENCE) else []
    return {"coord_only": False, "id": top["place_id"], "name": top.get("title"),
            "score": top.get("score"), "confidence": conf, "coords": (rp[1], rp[0]),
            "dist_km": round(d, 1), "flags": flags}


def _post(url, body, tok=None, tries=4):
    params = {"token": tok} if tok else None
    for i in range(tries):
        try:
            r = requests.post(url, params=params, json=body,
                              headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                              timeout=120)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if i == tries - 1:
                return {"_error": str(e)}
            time.sleep(min(2 ** i, 15))


# ── gateway backend (direct /api/reconcile, single query, coords inline) ──────
def _gw_request(place, pass_cfg, parents, radius_km):
    """Build one gateway query for a leaf place. `parents` = its geometry-bearing parent place ids,
    broadest-first (narrowest last). Returns None when the pass's spatial constraint can't apply (e.g.
    a 'narrow' pass for a place with no resolved parent), so that place falls through to a later pass."""
    _, mode, contain, relation, coords = pass_cfg
    ext = json.loads(place["extraction"]) if place["extraction"] else {}
    body = {"query": place["name"], "mode": mode, "size": 5}
    cc = ext.get("country_code")
    if cc:
        body["ccodes"] = [cc]                              # country-level proxy, always on when known
    if contain == "narrow":
        if not parents:
            return None
        body.update(contained_in=[parents[-1]], containment=CONTAINMENT, relation=relation)
    elif contain == "broad":
        if len(parents) < 2:
            return None
        body.update(contained_in=[parents[-2]], containment=CONTAINMENT, relation=relation)
    if coords:
        lat, lng = ext.get("latitude"), ext.get("longitude")
        if lat is None or lng is None:
            return None
        body["bounds"] = _bounds(lat, lng, radius_km)
    return body


# Non-point tie-break: when the best hit is a point but a POLYGON-bearing candidate (has_geom) sits within
# GEOM_MARGIN of the top score, prefer the polygon — richer geometry + better map display, without reaching
# past a clearly-better match. Tunable via GOTW_GEOM_MARGIN. Set GOTW_MEASURE=1 to log every match's
# top-vs-best-polygon gap (no behaviour change) for impact analysis; dumped on exit to GOTW_MEASURE_OUT.
GEOM_MARGIN = float(os.environ.get("GOTW_GEOM_MARGIN", "1"))
_MEASURE_ON = os.environ.get("GOTW_MEASURE") == "1"
_MEASURE: list = []
if _MEASURE_ON:
    import atexit
    atexit.register(lambda: open(os.environ.get("GOTW_MEASURE_OUT", "/tmp/gotw_measure.json"), "w")
                    .write(json.dumps(_MEASURE)))


def _gw_top(hit_list, threshold, label=None):
    if not hit_list:
        return None
    # Reject on ABSOLUTE confidence before ranking on relative score. A pool where nothing resembles
    # the query still returns a top hit at ~100, so ranking first and gating second would rank noise
    # and then let it through on its own normalised score. Candidates the gateway did not measure
    # (text modes) pass — see `_confidence`.
    hit_list = [h for h in hit_list if _confident_enough(h)]
    if not hit_list:
        return None
    top = max(hit_list, key=lambda h: h.get("score", 0))
    if top.get("score", 0) < threshold:
        return None
    if not _has_geom(top):
        polys = [h for h in hit_list if _has_geom(h)]
        best_poly = max(polys, key=lambda h: h.get("score", 0)) if polys else None
        if _MEASURE_ON:
            _MEASURE.append({"label": label, "top_id": top.get("place_id"), "top_title": top.get("title"),
                             "top_score": top.get("score"), "top_poly": False,
                             "poly_id": best_poly.get("place_id") if best_poly else None,
                             "poly_title": best_poly.get("title") if best_poly else None,
                             "poly_score": best_poly.get("score") if best_poly else None,
                             "gap": round(top.get("score", 0) - best_poly.get("score", 0), 3) if best_poly else None})
        if best_poly and top.get("score", 0) - best_poly.get("score", 0) <= GEOM_MARGIN:
            top = best_poly                       # prefer the (near-tied) polygon
    elif _MEASURE_ON:
        _MEASURE.append({"label": label, "top_id": top.get("place_id"), "top_title": top.get("title"),
                         "top_score": top.get("score"), "top_poly": True})
    pt = None
    geos = top.get("geometries") or []
    if geos and geos[0].get("repr_point"):
        lon, lat = geos[0]["repr_point"][:2]
        pt = (lat, lon)
    return {"id": top["place_id"], "name": top.get("title"), "score": top.get("score"),
            "confidence": _confidence(top), "coords": pt}


# ── admin-hierarchy resolution (top-down, server-side containment) ────────────
# Resolve a place's admin parents broadest-first to WHG place ids that HAVE GEOMETRY (`has_geom`), each
# query contained_in the previously-resolved parent so we descend an actual nested region — not a padded
# bbox. The country level is left to `ccodes` (a reliable proxy), so only sub-country admin units are
# resolved to ids. The leaf cascade then constrains matches with `contained_in=[parent id]`, and the
# chain is recorded as Linked-Places `gvp:broaderPartitive` relations. Gateway-only.
_PARENT_CACHE: dict = {}          # (norm_name, ccode, container_ids|None) -> {id,…} | None
_PARENT_SCOPE_FAIL: list = []     # (level name, container ids) where the gateway could not apply the scope
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")    # drop a trailing type tag, e.g. "Fars (province)" -> "Fars"

# ── admin-name aliases ───────────────────────────────────────────────────────
# The 1856 text romanises non-Latin toponyms by conventions long superseded, and the index is written in
# modern ones. Symphonym does not bridge the gap: measured on the 18 Qing provinces this corpus names,
# the PRINTED form resolves to a usable container for only 11 of 18, and one of those 11 is wrong
# (Keang-su -> Gansu). Normalised to the modern form, 18 of 18 resolve, every one to a polygon-bearing
# Wikidata record.
#
# ⚠️ This buys CORRECT PARENTAGE, not better geolocation. Constraining 150 Chinese places to their
# correct province cut leaf hits from 150 to 86 and the confident share from 29.3% to 17.4%, with the
# scope applied every time — because the leaf places are largely not in the index at all, and the
# unconstrained search was manufacturing a plausible answer rather than finding one. The alias table
# earns its place because `gvp:broaderPartitive` relations are PUBLISHED DATA and "Keang-su is part of
# Gansu" is simply false. Do not expect it to raise the match count; it should lower it.
#
# Keyed by country so other regions (Ottoman, Persian, Indian, Japanese transcriptions) can be added
# without the lists interfering. Lower-cased keys; values are the modern form to query with.
_ADMIN_ALIASES = {
    "CN": {
        "chih-le": "Hebei", "chih-li": "Hebei", "chihli": "Hebei", "pe-chi-li": "Hebei", "pechili": "Hebei",
        "sze-chuen": "Sichuan", "sze-chuan": "Sichuan", "szechuen": "Sichuan",
        "shan-tung": "Shandong", "shantung": "Shandong",
        "shan-se": "Shanxi", "shan-si": "Shanxi", "shansi": "Shanxi",
        "shen-se": "Shaanxi", "shen-si": "Shaanxi", "shensi": "Shaanxi",
        "kwang-tung": "Guangdong", "kwangtung": "Guangdong",
        "kwan-se": "Guangxi", "kwang-si": "Guangxi", "kwangsi": "Guangxi",
        "yun-nan": "Yunnan", "yunnan": "Yunnan",
        "ho-nan": "Henan", "honan": "Henan",
        "keang-se": "Jiangxi", "kiang-si": "Jiangxi", "kiangsi": "Jiangxi",
        "keang-su": "Jiangsu", "kiang-su": "Jiangsu", "kiangsu": "Jiangsu",
        "hu-nan": "Hunan", "hunan": "Hunan",
        "hu-pih": "Hubei", "hu-pe": "Hubei", "hu-peh": "Hubei", "hupeh": "Hubei",
        "kan-suh": "Gansu", "kansuh": "Gansu",
        "che-keang": "Zhejiang", "chi-keang": "Zhejiang", "che-kyang": "Zhejiang", "che-kiang": "Zhejiang",
        "kwei-chu": "Guizhou", "kwei-chow": "Guizhou", "kweichow": "Guizhou",
        "gan-hwuy": "Anhui", "ngan-hwuy": "Anhui", "gan-hway": "Anhui",
        "fo-keen": "Fujian", "fo-keën": "Fujian", "fo-kien": "Fujian", "fuh-kien": "Fujian",
        "chinese turkestan": "Xinjiang", "mandshuria": "Manchuria",
    },
}
# Shanxi vs Shaanxi is a genuine collision in the printed forms: "Shan-se"/"Shan-si" and
# "Shen-se"/"Shen-si" are one vowel apart and the modern romanisations are distinguished only by a
# doubled 'a'. The mapping above follows the 1856 convention (Shan- = Shandong-adjacent Shanxi,
# Shen- = Shaanxi), but any place whose province came from this pair deserves review.


def _clean_admin(name, cc=None):
    """Printed admin name -> the form to query with: trailing type tag dropped, then aliased if we have
    a modern form for it. Falls back to the printed form, which is right for the Latin-script majority."""
    base = _PAREN.sub("", name or "").strip()
    if not base:
        return base
    return (_ADMIN_ALIASES.get(cc or "", {})).get(base.lower(), base)


def _norm(s):
    return " ".join((s or "").lower().split())


def _has_geom(hit):
    # has_geom flags an AREAL geometry. This is not a heuristic or a preference: a container must have
    # an extent, so **no point-only source can ever be used for containment**, whatever else recommends
    # it. The gateway enforces the same rule from its side — `resolve_region` returns None for a
    # point-only container and the request then FAILS CLOSED (place#144), returning nothing rather than
    # answering globally — so passing one costs a wasted round trip and a silent zero.
    #
    # This retired an attractive idea. CHGIS (~82k Qing-era Chinese administrative units) looked like
    # the answer to Chinese containment; every `chgis` record is point-only, and each one tested as a
    # container returned `scope.applied=false, mode=none, containers_linked=[]`. The `linked-polygon`
    # fallback (borrowing a `sameAs`/`exactMatch` co-referent's boundary) does not rescue them either.
    # The same holds for `dgsd` and for any other coordinate-per-place gazetteer.
    #
    # Which is why the has_geom filter in `_resolve_one` STAYS. Dropping it raises the share of places
    # with some resolved parent (72% -> 86%) while cutting hits by 46.7% globally, because the extra
    # parents cannot define a region: `scope.applied` collapses from 88% to 47%.
    return any(g.get("has_geom") for g in (hit.get("geometries") or []))


def _confidence(hit):
    """Absolute 0-100 match quality, or None when the gateway did not measure it.

    None is NOT "poor". The gateway publishes `confidence` only for fuzzy/phonetic discovery; text
    modes (`exact`, `starts`, `in`) leave it null rather than emit a number on a different scale. An
    exact-mode hit is itself direct lexical evidence, so absence of the field means "the mode already
    vouched for the name" and must be read as passing, never as failing. Getting this backwards would
    reject every pass-1 match."""
    c = hit.get("confidence")
    return float(c) if isinstance(c, (int, float)) else None


def _confident_enough(hit, floor=None):
    c = _confidence(hit)
    return True if c is None else c >= (MIN_CONFIDENCE if floor is None else floor)


def _resolve_one(name, cc, container_ids, threshold):
    """Resolve one admin name to a WHG place id WITH AN AREAL GEOMETRY, within its country (ccodes) and
    inside the already-resolved ancestor (`contained_in`). Cached per (name, cc, container).

    Two gates, because a wrong parent is worse than no parent — it scopes the leaf into a region the
    place is not in, the leaf finds nothing, and the place falls through to the weakest pass, which then
    supplies a confident-looking wrong answer:

      * `_has_geom` — a point-only record cannot contain anything (see `_has_geom`).
      * an EXACT-mode hit is accepted on the strength of the mode. A phonetic-only parent must clear
        MIN_PARENT_CONFIDENCE, because relative score cannot separate a right parent from a wrong one:
        measured on the 18 Qing provinces our corpus names, "Keang-su" (Jiangsu) resolved to Gansu at
        score 99.5, while correct phonetic parents sat at 21.6-38.0 — straddling it. Under the old
        `score >= threshold` test (80, which almost everything clears) that wrong parent was adopted
        silently.

    A level that resolves to nothing is skipped, not guessed at. `resolve_hierarchy` then keeps the
    deepest ancestor that did resolve, so the chain degrades rather than collapsing.

    Returns {id,name,score,confidence,mode} | None."""
    key = (_norm(name), cc, tuple(container_ids) if container_ids else None)
    if key in _PARENT_CACHE:
        return _PARENT_CACHE[key]
    res = None
    for mode in ("exact", "phonetic"):
        body = {"query": name, "mode": mode, "size": 8}
        if cc:
            body["ccodes"] = [cc]
        if container_ids:
            body.update(contained_in=list(container_ids), containment=CONTAINMENT, relation="intersects")
        resp = _post(f"{GATEWAY_URL}/api/reconcile", body)
        if not resp or "_error" in resp:
            continue
        # A scope we asked for and the gateway could not apply means the ANCESTOR was unusable; its
        # answers here are unscoped, so do not read them as "found inside the parent".
        if container_ids and (resp.get("scope") or {}).get("applied") is False:
            _PARENT_SCOPE_FAIL.append((name, tuple(container_ids)))
            break
        hits = [h for h in (resp.get("hits") or []) if h.get("score", 0) >= threshold and _has_geom(h)]
        if mode == "phonetic":
            hits = [h for h in hits if _confident_enough(h, MIN_PARENT_CONFIDENCE)]
        if not hits:
            continue
        top = max(hits, key=lambda h: h.get("score", 0))
        res = {"id": top["place_id"], "name": top.get("title"), "score": top.get("score"),
               "confidence": _confidence(top), "mode": mode}
        break
    _PARENT_CACHE[key] = res
    return res


def resolve_hierarchy(rows, threshold):
    """Per place: resolve its admin_hierarchy (broadest→narrowest) to geometry-bearing parent ids, each
    contained_in the previous. Returns (parents {pid: [ids broadest-first]}, relations {pid: [LPF rels]}).
    Sequential, but the cache collapses shared ancestors (every 'Essex' resolved once)."""
    parents_by_pid, relations = {}, {}
    for r in rows:
        ext = json.loads(r["extraction"]) if r["extraction"] else {}
        cc = ext.get("country_code")
        chain, seen = [], set()
        for printed in (ext.get("admin_hierarchy") or []):
            name = _clean_admin(printed, cc)
            if name and _norm(name) not in seen:
                chain.append((printed, name)); seen.add(_norm(name))
        ids, rels, container = [], [], []
        for printed, name in chain:
            par = _resolve_one(name, cc, container, threshold)
            if not par:
                continue
            ids.append(par["id"])
            rel = {"relationType": "gvp:broaderPartitive", "relationTo": par["id"],
                   "label": par["name"], "when": None}
            # Keep the printed form when we queried something else, so a reader can see that
            # "Keang-su" was resolved as "Jiangsu" and audit the alias rather than trust it.
            if name != _PAREN.sub("", printed or "").strip():
                rel["sourceLabel"] = printed
                rel["queriedAs"] = name
            rels.append(rel)
            container = [par["id"]]        # next level must lie within this parent
        if ids:
            parents_by_pid[r["place_id"]] = ids
        if rels:
            relations[r["place_id"]] = rels
    return parents_by_pid, relations


def run_pass_gateway(rows, pass_cfg, radius_km, threshold, concurrency, parents_by_pid):
    items = [(r, _gw_request(r, pass_cfg, parents_by_pid.get(r["place_id"], []), radius_km)) for r in rows]
    items = [(r, b) for r, b in items if b is not None]
    best = {}

    def work(item):
        r, body = item
        return r["place_id"], _post(f"{GATEWAY_URL}/api/reconcile", body)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for fut in as_completed([pool.submit(work, it) for it in items]):
            pid, resp = fut.result()
            if not resp or "_error" in resp:
                continue
            cand = _gw_top(resp.get("hits") or [], threshold, pass_cfg[0])
            if cand:
                best[pid] = cand
    return best


def run_coord_pass(rows, radius_km, threshold, concurrency):
    """Coord-authoritative pass: {place_id -> coord-match-or-coord_only result} for coord-bearing places."""
    out = {}
    def work(r):
        return r["place_id"], _gw_coord_match(r, radius_km, threshold)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for fut in as_completed([pool.submit(work, r) for r in rows]):
            pid, res = fut.result()
            if res:
                out[pid] = res
    return out


# ── DO public-API backend (batched /reconcile, countries, extend centroids) ──
# No per-place hierarchy here (the W3C result carries no geometry/has_geom inline), so the containment
# passes collapse: a "narrow" pass runs as country-only, "broad"/country-only passes are skipped as
# redundant, and the coords pass uses bounds. Effectively exact-cc → phonetic-cc → coords.
def _api_request(place, pass_cfg, radius_km):
    _, mode, contain, relation, coords = pass_cfg
    ext = json.loads(place["extraction"]) if place["extraction"] else {}
    q = {"query": place["name"], "mode": mode, "limit": 5}
    cc = ext.get("country_code")
    if cc:
        q["countries"] = [cc]
    if coords:
        lat, lng = ext.get("latitude"), ext.get("longitude")
        if lat is None or lng is None:
            return None
        q["bounds"] = _bounds(lat, lng, radius_km)
    elif contain != "narrow":          # "broad"/country-only passes duplicate the "narrow"→cc query here
        return None
    return q


def run_pass_api(rows, pass_cfg, radius_km, threshold, concurrency, tok):
    chunks = []
    for i in range(0, len(rows), CHUNK):
        queries = {}
        for r in rows[i:i + CHUNK]:
            q = _api_request(r, pass_cfg, radius_km)
            if q is not None:
                queries[f"q{r['place_id']}"] = q
        if queries:
            chunks.append(queries)
    best = {}

    def work(queries):
        return _post(DO_URL, {"queries": queries}, tok=tok), queries

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for fut in as_completed([pool.submit(work, c) for c in chunks]):
            resp, queries = fut.result()
            if not resp or "_error" in resp:
                continue
            for qid in queries:
                pid = int(qid[1:])
                res = (resp.get(qid) or {}).get("result") or []
                if res:
                    top = max(res, key=lambda c: c.get("score", 0))
                    if top.get("score", 0) >= threshold:
                        best[pid] = {"id": top["id"], "name": top.get("name"),
                                     "score": top.get("score"), "coords": None}
    return best


def fetch_centroids_api(ids, tok):
    out = {}
    for i in range(0, len(ids), CHUNK):
        d = _post(DO_URL, {"extend": {"ids": ids[i:i + CHUNK],
                                      "properties": [{"id": "whg:geometry_centroid"}]}}, tok=tok)
        for pid, props in ((d or {}).get("rows") or {}).items():
            vals = props.get("whg:geometry_centroid") or []
            if vals and vals[0].get("str"):
                lat_s, lng_s = vals[0]["str"].split(",")
                out[pid] = (float(lat_s), float(lng_s))
    return out


def reconcile(con, rows, backend, threshold, radius_km, concurrency, tok=None, hierarchy=True):
    # Pass 0: resolve admin hierarchies top-down (gateway only) so the leaf passes can be bounded by the
    # parent footprint, and so we can record the chain as LOD relations.
    parents_by_pid, relations = {}, {}
    use_hier = hierarchy and backend == "gateway"
    if use_hier:
        print("resolving admin hierarchies (top-down, server-side containment) …", flush=True)
        parents_by_pid, relations = resolve_hierarchy(rows, threshold)
        resolved = sum(1 for v in _PARENT_CACHE.values() if v)
        by_mode = {m: sum(1 for v in _PARENT_CACHE.values() if v and v.get("mode") == m)
                   for m in ("exact", "phonetic")}
        print(f"  areal parent chain for {len(parents_by_pid)}/{len(rows)} places; "
              f"relations for {len(relations)}; {resolved}/{len(_PARENT_CACHE)} distinct levels resolved "
              f"({by_mode['exact']} exact, {by_mode['phonetic']} phonetic above "
              f"confidence {MIN_PARENT_CONFIDENCE:g})", flush=True)
        if _PARENT_SCOPE_FAIL:
            # Not an error: the gateway fails closed when a container cannot define a region, which is
            # what we want. Surfaced because a rising count means our parents are increasingly unusable.
            print(f"  {len(_PARENT_SCOPE_FAIL)} level lookups abandoned — the ancestor could not define "
                  f"a region (scope.applied=false)", flush=True)

    # Coordinates are authoritative: coord-bearing places skip the name cascade and go through the dedicated
    # coord pass (gateway only). The other places run the name cascade as before.
    coord_rows, name_rows = [], []
    if backend == "gateway":
        for r in rows:
            (coord_rows if _coords_of(r)[0] is not None else name_rows).append(r)
    else:
        name_rows = rows

    matched = {}      # place_id -> (pass_label, candidate{id,name,score,coords})  — name cascade
    for cfg in PASSES:
        label, _, contain, _, _ = cfg
        if contain in ("narrow", "broad") and not use_hier:   # containment passes need the resolved parents
            continue
        todo = [r for r in name_rows if r["place_id"] not in matched]
        if not todo:
            break
        got = (run_pass_gateway(todo, cfg, radius_km, threshold, concurrency, parents_by_pid)
               if backend == "gateway" else run_pass_api(todo, cfg, radius_km, threshold, concurrency, tok))
        for pid, cand in got.items():
            matched[pid] = (label, cand)
        print(f"pass {label:13} on {len(todo):>6}  -> matched {len(got):>5}  "
              f"(cumulative {len(matched)}/{len(name_rows)})", flush=True)

    coord_res = {}    # place_id -> coord match / coord_only result
    if coord_rows:
        coord_res = run_coord_pass(coord_rows, COORD_RADIUS_KM, COORD_THRESHOLD, concurrency)
        cm = sum(1 for v in coord_res.values() if not v["coord_only"])
        print(f"coord pass ({COORD_RADIUS_KM:.0f}km) on {len(coord_rows):>6}  -> matched {cm:>5}, "
              f"coord-only {len(coord_rows) - cm}", flush=True)

    if backend == "api":      # gateway already returned coords inline
        need = [c["id"] for _, c in matched.values() if c["coords"] is None]
        cents = fetch_centroids_api(need, tok)
        for _, c in matched.values():
            if c["coords"] is None:
                c["coords"] = cents.get(c["id"], (None, None))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in rows:
        pid = r["place_id"]
        rels = relations.get(pid) or []      # partOf chain recorded even when the leaf itself is unmatched
        cr = coord_res.get(pid)
        if pid in matched:                   # name-cascade match (non-coord place)
            label, cand = matched[pid]
            lat, lon = cand["coords"] or (None, None)
            conf = cand.get("confidence")
            flags = ["low_confidence"] if (conf is not None and conf < 2 * MIN_CONFIDENCE) else []
            con.execute("UPDATE place SET whg_match_id=?, whg_score=?, lat=?, lon=?, recon_pass=?, "
                        "reconciliation=?, status='reconciled', created_at=? WHERE place_id=?",
                        (cand["id"], cand.get("score"), lat, lon, label,
                         json.dumps({"pass": label, "candidate": cand, "relations": rels,
                                     "flags": flags}), now, pid))
        elif cr and not cr["coord_only"]:    # coord-authoritative match (nearest qualifying name match in radius)
            lat, lon = cr["coords"]
            con.execute("UPDATE place SET whg_match_id=?, whg_score=?, lat=?, lon=?, recon_pass='coord-match', "
                        "reconciliation=?, status='reconciled', created_at=? WHERE place_id=?",
                        (cr["id"], cr.get("score"), lat, lon,
                         json.dumps({"pass": "coord-match",
                                     "candidate": {"id": cr["id"], "name": cr["name"], "score": cr["score"],
                                                   "coords": [lat, lon], "dist_km": cr["dist_km"]},
                                     "relations": rels, "flags": cr["flags"]}), now, pid))
        elif cr:                             # coord-only: located at the printed point, no WHG match
            lat, lon = cr["coords"]
            con.execute("UPDATE place SET whg_match_id=NULL, whg_score=NULL, lat=?, lon=?, recon_pass='coord-only', "
                        "reconciliation=?, status='unmatched', created_at=? WHERE place_id=?",
                        (lat, lon, json.dumps({"pass": "coord-only", "coords": [lat, lon],
                                               "relations": rels, "flags": ["coord_only"]}), now, pid))
        else:                                # no coords and no name match
            con.execute("UPDATE place SET recon_pass='unmatched', status='unmatched', reconciliation=?, "
                        "created_at=? WHERE place_id=?",
                        (json.dumps({"relations": rels}) if rels else None, now, pid))
    con.commit()
    cmatch = sum(1 for v in coord_res.values() if not v["coord_only"])
    conly = len(coord_res) - cmatch
    by = lambda lbl: sum(1 for v in matched.values() if v[0] == lbl)
    print(f"reconciled {len(matched) + cmatch}/{len(rows)} via {backend} ("
          + ", ".join(f"{by(c[0])} {c[0]}" for c in PASSES)
          + f", {cmatch} coord-match) + {conly} coord-only; "
          + f"{sum(len(v) for v in relations.values())} partOf relations")
    # Evidence profile of what we accepted. Under correctness-before-count this is the number that
    # matters, not the total: a run whose matches are mostly unmeasured (exact-mode) or comfortably
    # above the floor is a good run, however many places it left unmatched.
    confs = [c.get("confidence") for _, c in matched.values() if c.get("confidence") is not None]
    unmeasured = len(matched) - len(confs)
    if confs:
        confs.sort()
        med = confs[len(confs) // 2]
        weak = sum(1 for c in confs if c < 2 * MIN_CONFIDENCE)
        print(f"  evidence: {unmeasured} exact-mode (unmeasured), {len(confs)} measured — median "
              f"confidence {med:.1f}, {weak} flagged low_confidence (< {2 * MIN_CONFIDENCE:g}); "
              f"floor was {MIN_CONFIDENCE:g}")
    elif matched:
        print(f"  evidence: all {unmeasured} matches were exact-mode (confidence not measured)")


DEMO = [
    ("Lutterworth", "300008375", "GB", None, None), ("Luristan", "300236157", "IR", None, None),
    ("Luton", "300008375", "GB", None, None), ("Macao", "300008375", "MO", 22.19, 113.50),
    ("Luroe", "300008791", "NO", 66.39, 12.92), ("Lutry", "300008375", "CH", None, None),
    ("Lusatia", "300236157", "DE", None, None), ("Lustleigh", "300000773", "GB", None, None),
]


def seed_demo(con, n):
    ensure_columns(con)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute("DELETE FROM place WHERE status='demo'")
    for i, (name, aat, cc, lat, lng) in enumerate(DEMO[:n], 1):
        ext = json.dumps({"name": name, "country_code": cc, "latitude": lat, "longitude": lng})
        con.execute("INSERT INTO place(entry_id,ordinal,name,extraction,aat_type_id,status,created_at)"
                    " VALUES(NULL,?,?,?,?,'demo',?)", (i, name, ext, aat, now))
    con.commit()
    print(f"seeded {min(n, len(DEMO))} demo places (status='demo')")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/gotw.sqlite")
    ap.add_argument("--backend", choices=["gateway", "api"], default="gateway")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--seed-demo", type=int, metavar="N")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--radius-km", type=float, default=DEFAULT_RADIUS_KM)
    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--retry-unmatched", action="store_true")
    ap.add_argument("--no-hierarchy", action="store_true",
                    help="skip top-down admin-parent resolution / partOf relations (gateway only)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    ensure_columns(con)
    if args.seed_demo:
        seed_demo(con, args.seed_demo)

    states = "('extracted','demo','unmatched')" if args.retry_unmatched else "('extracted','demo')"
    sql = (f"SELECT place_id, name, extraction, aat_type_id FROM place "
           f"WHERE whg_match_id IS NULL AND status IN {states} ORDER BY place_id")
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = con.execute(sql).fetchall()
    endpoint = GATEWAY_URL if args.backend == "gateway" else DO_URL
    hier = (not args.no_hierarchy) and args.backend == "gateway"
    print(f"{len(rows)} places to reconcile — cascade via {args.backend} ({endpoint}), threshold "
          f"{args.threshold}{', hierarchy-aware' if hier else ''}")

    if args.dry_run:
        parents = ({} if (args.no_hierarchy or args.backend != "gateway")
                   else resolve_hierarchy(rows[:8], args.threshold)[0])
        for r in rows[:8]:
            if args.backend == "gateway":
                qs = {c[0]: _gw_request(r, c, parents.get(r["place_id"], []), args.radius_km) for c in PASSES}
            else:
                qs = {c[0]: _api_request(r, c, args.radius_km) for c in PASSES}
            print(f"  {r['name'][:22]:22} {json.dumps(qs)}")
        print("(dry run: no calls)")
        return
    if rows:
        tok = token() if args.backend == "api" else None
        reconcile(con, rows, args.backend, args.threshold, args.radius_km, args.concurrency, tok,
                  hierarchy=not args.no_hierarchy)


if __name__ == "__main__":
    main()
