#!/usr/bin/env python3
"""Generate the data behind the Pages toponym-review UI: a pick-list per place, not a blank form.

READ-ONLY. Queries a STAGING Elasticsearch directly (no gateway, no production load) and writes
static JSON shards into `docs/toponyms/data/`.

WHY A PICK-LIST RATHER THAN PREFILLED GUESSES
The CSV asked a specialist to type three fields per row for 2,414 rows. That is the wrong shape of
work. Prefilling it with OUR current guess would be worse than blank for most rows, because our
guesses are things like "Hefei Xinqiao International Airport" for a Shantung county — correcting that
is more effort than typing from scratch, and it anchors the reader on a wrong answer.

What actually makes this fast is that for 1,166 of the Chinese places the BOOK PRINTS COORDINATES.
So we can ask the index what is actually near that point and offer those as options. The task becomes
recognition — "which of these eight is it?" — instead of recall. A specialist can do that in seconds
per row, and can still type a free-text answer when none of the options fit.

Two independent candidate sources are merged per place:
  * SPATIAL — places whose repr_point lies within RADIUS_KM of the printed coordinate. This is the
    valuable one, and it needs no name matching at all, so 1856 spelling cannot break it.
  * LEXICAL — places whose toponyms fuzzily match the printed headword or its variants. This covers
    rows with no usable coordinate, where spatial is impossible.

Candidates carry their native-script name where the index has one (`toponyms[].toponym` with a
non-Latin script), which is what lets the UI prefill the characters column for free.

⚠️ Coordinates are cleaned before use, by the same rule as the CSV pack: drop a row's coordinate if
exactly one of lat/lon is present, if longitude is negative (both countries lie east of Greenwich),
or if it falls outside the national bbox. 13.7% of Chinese and 42.7% of Russian coordinate data fails
that test, and a corrupt coordinate would silently produce a pick-list for the wrong part of the
world — which the reviewer would have no way to detect.

Usage:
  python3 process/build_review_data.py --ccode CN --es http://smp-n227:9201
  python3 process/build_review_data.py --ccode RU --es "$ES" --out-dir docs/toponyms/data
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_specialist_pack import PROFILES, PAREN, km  # noqa: E402  — one definition of the profiles
from review_store import sig  # noqa: E402  — stable across re-parses, unlike a rowid


def stable_id(filename, headword_raw, page_start, text, ordinal):
    """A key that survives a re-parse AND is actually unique.

    `review_store.sig(volume, headword, page)` alone is not: the book prints the same headword twice
    on one page often enough to matter — two different CHANG-CHU-FU, one in Kyang-su and one in
    Fokyen; ARCHANGEL the city and ARCHANGEL the government. Measured on this corpus, 13 Chinese and
    34 Russian ids collided, 94 rows in total. A join key that is right 98% of the time silently
    merges two places, which is worse than an unstable key because nothing complains.

    Adding a short digest of the ENTRY TEXT separates them, and stays stable for the same reason the
    signature does: it changes only if the OCR is redone, in which case every id changes anyway."""
    h = hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:6]
    return f"{sig(filename, headword_raw, page_start)}{h}:{ordinal}"

RADIUS_KM = 30.0
MAX_CANDS = 10
PLACES_INDEX = "places_h3ccode-20260805t120000z"
# Scripts whose presence marks a toponym as the "native" form worth showing beside the romanisation.
NATIVE = re.compile(r"[Ѐ-ӿ一-鿿㐀-䶿؀-ۿऀ-ॿ]")


def es_post(es, path, body, timeout=60):
    req = urllib.request.Request(f"{es}{path}", method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_error": str(e)}


def _cand(src, dist=None):
    """One option as the UI needs it: romanised title, native-script form, id, distance, context."""
    native = ""
    for t in (src.get("toponyms") or [])[:40]:
        lab = t.get("label") or ""
        if lab and NATIVE.search(lab):
            native = lab
            break
    geo = (src.get("geometries") or [{}])[0]
    return {
        "n": src.get("title") or "",
        "s": native,
        "id": src.get("place_id") or "",
        "km": None if dist is None else round(dist, 1),
        "ctx": src.get("region") or src.get("admin_unit") or src.get("subregion") or "",
        "pt": geo.get("repr_point"),
    }


def spatial(es, lat, lon):
    """What the index actually holds near the printed point. No name matching — so the 1856 spelling,
    which is the whole problem, cannot break this query."""
    # `geometries` is a NESTED field, so a bare geo_distance matches NOTHING — silently, with zero
    # hits and no error. Verified against the mapping rather than assumed; the first version of this
    # returned 0 candidates for a point in the middle of Shanxi and looked merely unlucky.
    body = {
        "size": MAX_CANDS * 2,
        "_source": ["place_id", "title", "toponyms", "geometries", "region", "admin_unit", "subregion"],
        "query": {"nested": {"path": "geometries", "query": {
            "geo_distance": {"distance": f"{RADIUS_KM}km",
                             "geometries.repr_point": {"lat": lat, "lon": lon}}}}},
        "sort": [{"_geo_distance": {"geometries.repr_point": {"lat": lat, "lon": lon},
                                    "order": "asc", "unit": "km",
                                    "nested": {"path": "geometries"}}}],
    }
    r = es_post(es, f"/{PLACES_INDEX}/_search", body)
    out = []
    for h in ((r.get("hits") or {}).get("hits") or []):
        d = (h.get("sort") or [None])[0]
        out.append(_cand(h.get("_source") or {}, d))
    return out


def lexical(es, name, variants, ccode):
    """Fallback for rows with no usable coordinate: fuzzy name match inside the country."""
    # The nested name field is `toponyms.label`; `toponyms.toponym` does not exist and matched nothing.
    should = [{"nested": {"path": "toponyms", "query": {
                  "match": {"toponyms.label": {"query": q, "fuzziness": "AUTO"}}}}}
              for q in ([name] + list(variants))[:4] if q]
    if not should:
        return []
    body = {
        "size": MAX_CANDS,
        "_source": ["place_id", "title", "toponyms", "geometries", "region", "admin_unit", "subregion"],
        "query": {"bool": {"should": should, "minimum_should_match": 1,
                           "filter": [{"term": {"ccodes": ccode}}]}},
    }
    r = es_post(es, f"/{PLACES_INDEX}/_search", body)
    return [_cand(h.get("_source") or {}) for h in ((r.get("hits") or {}).get("hits") or [])]


ENTRY_CHARS = 1400


def _trim(t):
    """Trim to a sentence boundary near the limit rather than mid-word."""
    t = re.sub(r"\s+", " ", (t or "")).strip()
    if len(t) <= ENTRY_CHARS:
        return t
    cut = t.rfind(". ", 0, ENTRY_CHARS)
    return (t[: cut + 1] if cut > ENTRY_CHARS * 0.6 else t[:ENTRY_CHARS].rsplit(" ", 1)[0]) + " …"


def order_for_review(places, prof):
    """Put the rows a specialist can answer fastest, and most representatively, first.

    The first row the page showed was "Asses' Ears" — an English name for a coastal rock with no
    hierarchy. It is a poor first impression and an unrepresentative task, and it is the same defect
    already fixed in the CSV pilot selection but not carried across, because the page presented rows
    in database order. Ordering, best first:

      1. a usable printed coordinate — these get a real spatial pick-list, so they are quick and
         satisfying, and they are the rows whose answers we can independently verify;
      2. a stated admin hierarchy — context to reason from;
      3. for languages whose transcriptions are characteristically hyphenated (Chinese), a hyphen —
         which separates genuine romanisations from English or Tibetan names in the same territory;
      4. then alphabetically, so the order is stable across regenerations.
    """
    hyph = bool(prof.get("hyphen_only"))
    return sorted(places, key=lambda p: (
        p["lat"] is None,
        not p["hier"],
        (hyph and "-" not in (p["hw"] or "")),
        (p["hw"] or "").lower(),
    ))


def load_places(db, ccode, prof):
    lo0, lo1, la0, la1 = prof["bbox"]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = []
    for r in con.execute(
            "SELECT p.place_id, p.ordinal, p.name, p.extraction, e.headword_raw, "
            "e.page_start, e.text, s.filename FROM place p "
            "JOIN entry e ON e.entry_id = p.entry_id JOIN source s ON s.source_id = e.source_id "
            "WHERE p.extraction IS NOT NULL"):
        try:
            ext = json.loads(r["extraction"])
        except (ValueError, TypeError):
            continue
        if ext.get("country_code") != ccode:
            continue
        lat, lon = ext.get("latitude"), ext.get("longitude")
        flags = []
        if (lat is None) != (lon is None):
            flags.append("half_coordinate"); lat = lon = None
        elif lat is not None:
            if lon < 0 and lo0 >= 0:
                flags.append("negative_longitude"); lat = lon = None
            elif not (lo0 <= lon <= lo1 and la0 <= lat <= la1):
                flags.append("outside_bbox"); lat = lon = None
        out.append({
            # STABLE id, not the rowid. `place_id` is a SQLite rowid that a re-parse renumbers, and
            # this id has to survive a round trip out to a specialist and back — and has to name the
            # same row for the indexing side's train/test split. Same signature the human-review
            # sidecar uses: sig(filename, headword_raw, page_start) plus the place's ordinal within
            # its entry, because one entry can yield several places.
            "i": stable_id(r["filename"], r["headword_raw"], r["page_start"], r["text"], r["ordinal"]),
            "pid": r["place_id"],
            "hw": r["name"],
            "var": [v for v in (ext.get("variant_names") or []) if v][:4],
            "hier": " > ".join(PAREN.sub("", a).strip() for a in (ext.get("admin_hierarchy") or [])),
            "lat": lat, "lon": lon,
            "vol": (re.search(r"-(v\d+)-", r["filename"] or "") or [None, ""])[1],
            "pg": r["page_start"],
            "flags": flags,
            # The entry as the book printed it. This is the context a specialist actually reasons
            # from — "a district city of China, in the province of Shan-se, 40 miles SW of ..." often
            # identifies a place that the headword alone cannot. Trimmed because a few country essays
            # run to tens of thousands of characters and would dominate the payload.
            "txt": _trim(r["text"]),
        })
    con.close()
    # Collapse rows that are the same place twice. Two entries can be byte-identical — same headword,
    # same printed text, same hierarchy, differing only in rowid — because the corpus ingested the
    # entry twice. The stable id correctly identifies them as one thing, and asking a specialist the
    # same question twice wastes their time and produces two answers to reconcile.
    seen, deduped = set(), []
    for p_ in out:
        if p_["i"] in seen:
            continue
        seen.add(p_["i"])
        deduped.append(p_)
    if len(deduped) != len(out):
        print(f"  collapsed {len(out) - len(deduped)} duplicate entries "
              f"(identical headword, text and hierarchy)")
    return deduped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/gotw_seg.sqlite")
    ap.add_argument("--ccode", default="CN", choices=sorted(PROFILES))
    ap.add_argument("--es", required=True, help="STAGING Elasticsearch base URL (never production)")
    ap.add_argument("--out-dir", default="docs/toponyms/data")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="safe against staging; this must never point at production")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    prof = PROFILES[args.ccode]
    places = order_for_review(load_places(args.db, args.ccode, prof), prof)
    # Explicit 1-based rank in the pack as generated. It describes THIS pack, not the underlying
    # entry, so it deliberately does not survive a re-parse — a reordered pack is a new pack.
    # Emitted as a field rather than left implicit in array order, because array order is true until
    # it quietly is not: a UI, a download and a specialist's browser sit between generation and
    # return, and none of them announces a reordering.
    for n, p_ in enumerate(places, 1):
        p_["r"] = n
    if args.limit:
        places = places[: args.limit]
    print(f"{args.ccode}: {len(places):,} places; "
          f"{sum(1 for p in places if p['lat'] is not None):,} with a usable coordinate")

    def tidy(cands):
        """A pick-list is only fast if every option is worth reading.

        Two kinds of noise make it slower than typing: the SAME place listed several times because
        several gazetteers hold it (Guitou Shi at 3.5km and again at 3.6km), and options whose only
        label is a bare Wikidata Q-number, which tells a reviewer nothing. Both are dropped — a
        Q-number is kept only if nothing else in the list carries that native-script name."""
        out, seen = [], set()
        for c in cands:
            title = (c["n"] or "").strip()
            if re.fullmatch(r"Q\d+", title) and not c["s"]:
                continue                       # no human-readable label at all
            key = (title.casefold(), c["s"])
            if key in seen:
                continue
            seen.add(key)
            out.append(c)
        return out

    def work(p):
        cands = spatial(args.es, p["lat"], p["lon"]) if p["lat"] is not None else []
        if len(cands) < 3:
            seen = {c["id"] for c in cands}
            cands += [c for c in lexical(args.es, p["hw"], p["var"], args.ccode) if c["id"] not in seen]
        p["c"] = tidy(cands)[:MAX_CANDS]
        return p

    done = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for fut in as_completed([pool.submit(work, p) for p in places]):
            fut.result()
            done += 1
            if done % 250 == 0:
                print(f"  {done}/{len(places)}", flush=True)

    os.makedirs(args.out_dir, exist_ok=True)
    slug = prof["language"].lower()
    # Sharded at 200 rows so each file stays small and separately cacheable. The UI currently
    # fetches all shards for the chosen language on load (~2 MB for Chinese), which is fine at
    # this size; sharding is what makes lazy loading POSSIBLE later without regenerating.
    shards, per = [], 200
    for i in range(0, len(places), per):
        chunk = places[i: i + per]
        name = f"{slug}-{i // per:03d}.json"
        with open(os.path.join(args.out_dir, name), "w", encoding="utf-8") as fh:
            json.dump(chunk, fh, ensure_ascii=False, separators=(",", ":"))
        shards.append({"file": name, "n": len(chunk)})
    manifest = {
        "language": prof["language"], "ccode": args.ccode,
        "fields": [c for c, _ in prof["fill_cols"]],
        "field_help": {c: re.sub(r"<[^>]+>", "", d) for c, d in prof["fill_cols"]},
        "total": len(places),
        "with_coords": sum(1 for p in places if p["lat"] is not None),
        "shards": shards,
    }
    with open(os.path.join(args.out_dir, f"{slug}-manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    withc = sum(1 for p in places if p["c"])
    print(f"wrote {len(shards)} shards to {args.out_dir}; {withc:,}/{len(places):,} places "
          f"({100.0*withc/max(len(places),1):.0f}%) have at least one candidate to pick from")


if __name__ == "__main__":
    main()
