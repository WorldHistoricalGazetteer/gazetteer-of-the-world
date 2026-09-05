"""Paired gate: does the staging gateway return the SAME answers as production?

This is a gate, not a reassurance. Run it before committing a re-reconciliation to the staging
gateway, and abandon the fast path if it fails. An endpoint that answers is not evidence that it
answers the same, and a gateway can start healthy, pass every health check, and return subtly
different candidates for every query (a missing alias, a Symphonym model that did not load).

WHAT IT COMPARES: identical request bodies are POSTed to both gateways and the returned hit ids
are compared two ways, as a SET and as an ORDERED LIST.

IT ALSO COMPARES PRODUCTION AGAINST ITSELF, and that control is the whole point. The first version
of this script did not, reported 97.66% order agreement, and declared staging unusable. It was
measuring Elasticsearch tie-breaking: the corpus is full of queries where a dozen candidates all
score exactly 100.0, ES does not impose a deterministic tiebreaker across shards, and the order
comes back different from one call to the NEXT ON THE SAME ENDPOINT. Two of the seven "differences"
agreed perfectly when re-queried moments later.

Without a same-endpoint control, "the two gateways disagree" and "this gateway is nondeterministic"
are indistinguishable, and the first reading is the one that costs you fifteen hours. So the
verdict is relative: staging is acceptable when its SET agreement with production is total AND its
order disagreement is no worse than production's disagreement with itself.

Queries are drawn from the real corpus rather than invented, and deliberately span the cascade:
exact and phonetic modes, with and without a country filter. A gate built from easy queries passes
on a broken endpoint.

Read-only. Writes nothing to either gateway or to the DB.

    python3 process/compare_gateways.py --n 300
    python3 process/compare_gateways.py --n 300 --staging http://smp-n227:9210
"""
import argparse, json, random, sqlite3, sys, urllib.request

PROD = "http://gazetteer-clus.crc.pitt.edu:9200"


def post(base, path, body, timeout=30):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def ids(resp):
    return [h.get("place_id") or h.get("id") or h.get("_id")
            for h in ((resp or {}).get("hits") or [])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/gotw_seg.sqlite")
    ap.add_argument("--staging", default="http://smp-n227:9210")
    ap.add_argument("--prod", default=PROD)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()

    c = sqlite3.connect("file:%s?mode=ro" % a.db, uri=True)
    rows = []
    for name, ext in c.execute("select name, extraction from place where extraction is not null"):
        try:
            cc = (json.loads(ext) or {}).get("country_code")
        except Exception:
            cc = None
        if name:
            rows.append((name, cc))
    random.Random(a.seed).shuffle(rows)
    sample = rows[:a.n]

    # Span the cascade deliberately: an easy-query gate passes on a broken endpoint.
    shapes = [
        lambda q, cc: {"query": q, "mode": "exact", "size": 10},
        lambda q, cc: {"query": q, "mode": "phonetic", "size": 10},
        lambda q, cc: ({"query": q, "mode": "phonetic", "size": 10, "ccodes": [cc]} if cc else None),
    ]

    ab_order = ab_set = ctl_order = ctl_set = tot = err = 0
    examples = []
    for i, (name, cc) in enumerate(sample):
        body = shapes[i % len(shapes)](name, cc)
        if body is None:
            continue
        try:
            p1 = ids(post(a.prod, "/api/reconcile", body))
            p2 = ids(post(a.prod, "/api/reconcile", body))     # the control: prod vs ITSELF
            st = ids(post(a.staging, "/api/reconcile", body))
        except Exception as e:
            err += 1
            if len(examples) < 5:
                examples.append(("ERROR", name, str(e)[:80]))
            continue
        tot += 1
        ab_order += (p1 == st)
        ab_set += (set(p1) == set(st))
        ctl_order += (p1 == p2)
        ctl_set += (set(p1) == set(p2))
        if set(p1) != set(st) and len(examples) < 8:
            only_p = [x for x in p1 if x not in set(st)]
            only_s = [x for x in st if x not in set(p1)]
            examples.append(("SETDIFF", name, "prod-only=%s staging-only=%s" % (only_p[:3], only_s[:3])))

    if not tot:
        print("VERDICT: FAIL - nothing was compared, which is not a pass.")
        return 1
    print("compared=%d  errors=%d" % (tot, err))
    print("  staging vs production :  set %.2f%%   order %.2f%%"
          % (100.0*ab_set/tot, 100.0*ab_order/tot))
    print("  production vs ITSELF  :  set %.2f%%   order %.2f%%   <- the control"
          % (100.0*ctl_set/tot, 100.0*ctl_order/tot))
    for k, n, d in examples:
        print("  %-7s %-24s %s" % (k, n, d))

    # A gate has to be able to fail. Anything short of total agreement means the two endpoints are
    # not interchangeable, and the whole argument for the fast path was that they are.
    if err:
        print("\nVERDICT: FAIL - %d requests errored; an endpoint that cannot answer cannot be compared." % err)
        return 1
    if ab_set != tot:
        print("\nVERDICT: FAIL - staging returns DIFFERENT DOCUMENTS, not merely a different order.\n"
              "Do NOT reconcile against staging; the fast run would be fast and wrong. Check that the\n"
              "`places`/`toponyms` aliases point where you think and that Symphonym loaded.")
        return 1
    if ab_order < ctl_order:
        print("\nVERDICT: FAIL - staging's ordering is LESS stable than production's is with itself,\n"
              "so the difference is not explained by tie-breaking alone.")
        return 1
    print("\nVERDICT: PASS - identical candidate SETS on all %d paired queries, and staging's order\n"
          "agreement (%.2f%%) is no worse than production's with itself (%.2f%%), so the residual\n"
          "ordering differences are Elasticsearch tie-breaking, not a difference between the two."
          % (tot, 100.0*ab_order/tot, 100.0*ctl_order/tot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
