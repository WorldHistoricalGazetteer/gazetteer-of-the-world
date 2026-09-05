#!/usr/bin/env python3
"""Reproducible test of the 1856-romanisation gap: do printed Qing province names resolve?

READ-ONLY, gateway-only, ~40 queries. Keep this runnable — it is cited in the indexing repo's
`developer/plan-symphonym-v8.md` as the independent, external confirmation that the model's
historic-romanisation weakness is real rather than an artefact of their own analysis, so it needs to
stay reproducible by someone who did not write it.

WHAT IT MEASURES
Each of the 18 Qing provinces our corpus actually names, in the book's own 1856 English transcription,
resolved the way `reconcile.py` resolves an admin parent: permissive namespaces, geometry-bearing
candidates only, ranked by absolute confidence, then tested by asking whether the resulting id can in
fact define a containment region (`scope.applied`).

RESULT, unchanged across three runs (2026-09-05: pre-deploy, post-place#241, post-tokeniser-fix):

    usable containers as printed: 11/18,  of which 1 resolves to the WRONG province
    Keang-su (Jiangsu) -> GANSU  wd:Q42392  score 99.5  confidence 21.8

Normalised to modern pinyin the same method resolves 18/18. The gap is therefore not phonetic
similarity but 19th-century transcription convention (Wade-Giles-era orthography against pinyin), and
the training corpus contains essentially none of it. Confirmed by the indexing side as a DATA problem
before a model one, and explicitly DE-SCOPED from Symphonym v8, which targets cross-script phonetic
matching only. Do not budget for this gap closing: design around it.

The lever that does work is a transcription-convention mapping applied as query expansion before the
phonetic pass — the same shape as the gateway's `derive_name_forms`. `reconcile.py`'s `_ADMIN_ALIASES`
is that lever for the 18 admin names; extending it to leaf toponyms is a GOTW-side asset, not a
Symphonym one.

Usage:  python3 process/probe_qing_provinces.py        # from a CRC compute node
"""
import sys
sys.path.insert(0, "process")
from reconcile import GATEWAY_URL, _post, _has_geom

PERMISSIVE = ["wd", "gn", "ohm", "tgn", "pl", "po", "clio", "un"]
MAP = [("Chih-le","Hebei"),("Sze-chuen","Sichuan"),("Shan-tung","Shandong"),("Shan-se","Shanxi"),
       ("Kwang-tung","Guangdong"),("Yun-nan","Yunnan"),("Ho-nan","Henan"),("Keang-se","Jiangxi"),
       ("Kwan-se","Guangxi"),("Hu-nan","Hunan"),("Shen-se","Shaanxi"),("Keang-su","Jiangsu"),
       ("Hu-pih","Hubei"),("Kan-suh","Gansu"),("Che-keang","Zhejiang"),("Kwei-chu","Guizhou"),
       ("Gan-hwuy","Anhui"),("Fo-keen","Fujian")]

def resolve(name):
    for mode in ("exact", "fuzzy"):
        r = _post(GATEWAY_URL + "/api/reconcile",
                  {"query": name, "mode": mode, "size": 10, "ccodes": ["CN"], "namespaces": PERMISSIVE})
        hits = (r.get("hits") or []) if isinstance(r, dict) else []
        geo = [h for h in hits if _has_geom(h)]
        if not geo:
            continue
        top = max(geo, key=lambda h: h.get("confidence") or 0)
        r2 = _post(GATEWAY_URL + "/api/reconcile",
                   {"query": "x", "mode": "fuzzy", "size": 1, "ccodes": ["CN"],
                    "contained_in": [top["place_id"]], "containment": "fuzzy", "relation": "intersects"})
        sc = (r2 or {}).get("scope") or {}
        return top, sc, mode
    return None, None, None

ok = wrong = 0
print("%-12s %-30s %6s %7s  %s" % ("printed", "resolved", "score", "conf", "usable"))
print("-" * 78)
for printed, modern in MAP:
    top, sc, mode = resolve(printed)
    if not top:
        print("%-12s %-30s %6s %7s  no" % (printed, "(no geometry-bearing hit)", "-", "-"))
        continue
    usable = bool(sc and sc.get("applied"))
    title = str(top.get("title"))
    hit = modern.lower() in title.lower() or title.lower() in modern.lower()
    if usable:
        ok += 1
        if not hit:
            wrong += 1
    print("%-12s %-30s %6.1f %7s  %s%s" % (
        printed, (title[:22] + " " + top["place_id"])[:30], top.get("score") or 0,
        top.get("confidence"), "YES" if usable else "no",
        "" if hit else "   <-- NOT %s" % modern))
print("\nusable containers as printed: %d/%d   (of which resolving to the WRONG province: %d)"
      % (ok, len(MAP), wrong))
