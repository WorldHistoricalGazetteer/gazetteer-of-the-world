"""Detect whether the gateway's SCORING changed underneath a long run.

WHY THIS EXISTS. A full re-reconciliation takes ~10 hours against a live service we do not control.
If anything alters ranking mid-run (any change to corpus statistics moves BM25 scores: ordinary
segment merging shifts docCount, which drives IDF; so does a reindex, an alias re-point or a model
swap), the
first half of the run and the second half were produced by different instruments. The output gives
no sign of it: every row still looks reasonable, and the corpus-level numbers still add up. It is
the difference between one measurement and two averaged together, and nothing downstream can
recover the distinction afterwards.

We cannot read production's index statistics directly (ES returns 401 without credentials we do not
hold), so we measure the thing we actually care about instead: does a fixed set of queries still
return the same candidates in the same order?

    python3 process/scoring_canary.py --capture   # at run start, writes the baseline
    python3 process/scoring_canary.py --check     # at run end (or any time), compares

A PASS IS INFORMATIVE ONLY BECAUSE THE INSTRUMENT IS KNOWN TO BE STABLE. Production was measured at
100.00% agreement with itself on both candidate set and ordering across 594 paired queries
(process/compare_gateways.py), so it is deterministic within a run and any disagreement here is a
real change rather than noise. Without that prior, a clean canary would mean nothing.
"""
import argparse, hashlib, json, os, random, sqlite3, sys, urllib.request

GW = os.environ.get("WHG_GATEWAY_URL", "http://gazetteer-clus.crc.pitt.edu:9200")
BASELINE = "data/scoring_canary.json"


def post(base, body, timeout=30):
    req = urllib.request.Request(base + "/api/reconcile", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ids(resp):
    return [h.get("place_id") or h.get("id") for h in ((resp or {}).get("hits") or [])]


def queries(db, n, seed):
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    rows = []
    for name, ext in c.execute("select name, extraction from place where extraction is not null"):
        try:
            cc = (json.loads(ext) or {}).get("country_code")
        except Exception:
            cc = None
        if name:
            rows.append((name, cc))
    random.Random(seed).shuffle(rows)
    out = []
    for i, (nm, cc) in enumerate(rows[:n]):
        # Weighted toward the lexical shape: that is where a term-statistics change shows up first
        # (measured 3.2x concentration, place#244 discussion), so it is the sensitive detector.
        out.append({"query": nm, "mode": "exact" if i % 3 else "phonetic", "size": 10})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/gotw_seg.sqlite")
    ap.add_argument("--gateway", default=GW)
    ap.add_argument("--out", default=BASELINE)
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.capture == a.check:
        print("choose exactly one of --capture / --check"); return 2

    qs = queries(a.db, a.n, a.seed)
    got = {}
    for q in qs:
        try:
            got[json.dumps(q, sort_keys=True)] = ids(post(a.gateway, q))
        except Exception as e:
            print("query failed (%s): %s" % (q["query"], str(e)[:70])); return 2

    if a.capture:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w") as f:
            json.dump({"gateway": a.gateway, "n": len(got), "results": got}, f)
        h = hashlib.sha256(json.dumps(got, sort_keys=True).encode()).hexdigest()[:16]
        print("captured %d queries from %s -> %s  (digest %s)" % (len(got), a.gateway, a.out, h))
        return 0

    if not os.path.exists(a.out):
        print("no baseline at %s; nothing to check against. That is a FAIL, not a pass." % a.out)
        return 1
    base = json.load(open(a.out))["results"]
    common = set(base) & set(got)
    if not common:
        print("baseline and current share no queries — cannot compare. FAIL."); return 1
    same_order = sum(base[k] == got[k] for k in common)
    same_set = sum(set(base[k]) == set(got[k]) for k in common)
    n = len(common)
    print("compared %d queries against the baseline" % n)
    print("  same ordering : %6.2f%%" % (100.0 * same_order / n))
    print("  same candidate set: %6.2f%%" % (100.0 * same_set / n))
    if same_order == n:
        print("\nPASS - scoring is unchanged since the baseline was captured.")
        return 0
    for k in list(common):
        if base[k] != got[k]:
            q = json.loads(k)
            print("  CHANGED %-22s %-8s base=%s now=%s" % (q["query"], q["mode"], base[k][:3], got[k][:3]))
            break
    print("\nFAIL - the gateway is not scoring the way it did when this run started.\n"
          "Results produced before and after the change are NOT comparable. Establish what changed\n"
          "before treating the run as one measurement.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
