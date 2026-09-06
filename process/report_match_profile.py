"""Per-country match profile: is a fall in match count CONCENTRATED or UNIFORM?

Run this after any reconciliation policy change. A uniform fall means something broke; a fall
concentrated where evidence is weak is the intended behaviour of a cascade that refuses weak
evidence. The two are indistinguishable from the corpus total, which is the only number anyone
looks at.

MEASURED 2026-09-06, after the seven-change rewrite (run 23990529). Corpus matches fell 15.9%,
98,581 to 82,923, and the profile says why that is a win rather than a regression:

    cc     places matched    rate     pass4%    med conf
    US       8793    8114   92.3%        19%       100.0
    GB      18112   16173   89.3%        21%        83.7
    FR      14042   11895   84.7%        36%        99.9
    RU       3535    1821   51.5%        77%        98.5
    IN       3210    1635   50.9%        85%        39.4
    TR       2078     706   34.0%        96%       100.0
    CN       2414     569   23.6%        72%        38.1

    Latin-script      78967 places   82.1% matched
    non-Latin-script  14178 places   43.7% matched

`pass4%` is the sharpest single diagnostic. Pass 4 is the loosest pass in the cascade, so a high
share means the strict passes found nothing and the match rests on the weakest available evidence.
Corpus-wide it fell from 71% to 42.4%; the residue is concentrated in TR (96%), IN (85%) and CN.

⚠ READ THE COUNTRIES, NOT THE POOLED FIGURE. Pooling non-Latin-script countries gives a median
confidence of 99.5, which looks reassuring and conceals the entire finding: CN 38.1, IN 39.4 and
IE 40.0 are the weak cases, and they are averaged away by RU and TR at ~100. An aggregate over a
population that is not homogeneous reports the majority and hides the question you asked.

CA at 80% pass-4 share is the anomaly worth chasing: a Latin-script country behaving like a
romanisation-gap one, which points at thin index coverage for historical Canadian places rather
than at a name-matching problem.

    python3 process/report_match_profile.py
"""
import sqlite3, json, collections
c = sqlite3.connect("file:data/gotw_seg.sqlite?mode=ro", uri=True)
n = collections.Counter(); m = collections.Counter(); conf = collections.defaultdict(list)
p4 = collections.Counter(); casc = collections.Counter()
for st, rp, ext, rc in c.execute("select status,recon_pass,extraction,reconciliation from place where extraction is not null"):
    try: e = json.loads(ext)
    except Exception: continue
    cc = e.get("country_code")
    if not cc: continue
    n[cc] += 1
    if st == "reconciled": m[cc] += 1
    if rp in ("1-exact-in", "2-phon-in", "3-phon-broad", "4-phon-cc"):
        casc[cc] += 1
        if rp == "4-phon-cc": p4[cc] += 1
    if rc:
        try:
            cd = (json.loads(rc) or {}).get("candidate") or {}
            if cd.get("confidence") is not None: conf[cc].append(cd["confidence"])
        except Exception: pass
print("%-5s %7s %7s %7s   %8s   %9s" % ("cc", "places", "matched", "rate", "pass4%", "med conf"))
for cc, tot in n.most_common(16):
    cs = conf[cc]; cs.sort()
    med = ("%.1f" % cs[len(cs)//2]) if cs else "-"
    p4s = ("%.0f%%" % (100.0*p4[cc]/casc[cc])) if casc[cc] else "-"
    print("%-5s %7d %7d %6.1f%%   %8s   %9s" % (cc, tot, m[cc], 100.0*m[cc]/tot, p4s, med))
print()
lat = ["GB","FR","DE","US","IT","ES","IE","BE","AT","CH","NL","CA","AU","BR"]
nonlat = ["CN","RU","IN","TR","GR","IR","EG","JP"]
for nm, grp in [("Latin-script", lat), ("non-Latin-script", nonlat)]:
    tn = sum(n[x] for x in grp); tm = sum(m[x] for x in grp)
    allc = sorted(v for x in grp for v in conf[x])
    print("%-18s %6d places  %6.1f%% matched   median confidence %s"
          % (nm, tn, 100.0*tm/tn, ("%.1f" % allc[len(allc)//2]) if allc else "-"))
