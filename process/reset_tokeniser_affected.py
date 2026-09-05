"""Reset ONLY what the tokeniser fix invalidated, keeping the rest.

Decision rule (indexing-c7, verified against their tokeniser diff): a query is affected if it contains
a space, is CJK/Kana/Hangul, is not already NFC, or has more non-letters than letters. Everything else
is byte-identical through both the legacy and canonical tokenisers and its result stands.

Independently confirmed before running this: the Qing-province test reproduces its pre-deploy result
exactly (11/18 usable, Keang-su -> Gansu at 99.5), i.e. hyphenated Latin forms really are unaffected.
"""
import json, re, sqlite3, sys, unicodedata
from collections import Counter

DB = sys.argv[1]
APPLY = "--apply" in sys.argv
CJK = re.compile(r"[一-鿿㐀-䶿豈-﫿぀-ゟ゠-ヿ가-힯]")

def affected(s):
    s = s or ""
    if " " in s.strip():
        return "space"
    if CJK.search(s):
        return "cjk"
    if unicodedata.normalize("NFC", s) != s:
        return "not-nfc"
    letters = sum(1 for ch in s if ch.isalpha())
    if len(s) - letters > letters:
        return "punct-heavy"
    return None

con = sqlite3.connect(DB)

# --- places -------------------------------------------------------------
pids, why = [], Counter()
for pid, name, ext in con.execute(
        "SELECT place_id, name, extraction FROM place WHERE status IN ('reconciled','unmatched')"):
    try:
        e = json.loads(ext) if ext else {}
    except Exception:
        e = {}
    a = affected(name) or next((affected(v) for v in (e.get("variant_names") or [])[:8] if affected(v)), None)
    if a:
        pids.append(pid); why[a] += 1

# --- parent cache -------------------------------------------------------
# Key is JSON [norm_name, ccode, containers]; the norm_name is the string that was embedded.
ckeys, cwhy = [], Counter()
for (k,) in con.execute("SELECT key FROM parent_cache"):
    try:
        name = json.loads(k)[0]
    except Exception:
        continue
    a = affected(name)
    if a:
        ckeys.append(k); cwhy[a] += 1
tot_c = con.execute("SELECT count(*) FROM parent_cache").fetchone()[0]

print("places to re-run    : %5d of %d processed   %s" % (
    len(pids), con.execute("SELECT count(*) FROM place WHERE status IN ('reconciled','unmatched')").fetchone()[0], dict(why)))
print("parent-cache to drop: %5d of %d cached      %s" % (len(ckeys), tot_c, dict(cwhy)))
print("kept (byte-identical through both tokenisers): %d places, %d cached parents" % (
    con.execute("SELECT count(*) FROM place WHERE status IN ('reconciled','unmatched')").fetchone()[0] - len(pids),
    tot_c - len(ckeys)))

if not APPLY:
    print("\n(dry run — pass --apply to write)")
    raise SystemExit

con.executemany("UPDATE place SET whg_match_id=NULL, whg_score=NULL, lat=NULL, lon=NULL, "
                "recon_pass=NULL, reconciliation=NULL, status='extracted' WHERE place_id=?",
                [(p,) for p in pids])
con.executemany("DELETE FROM parent_cache WHERE key=?", [(k,) for k in ckeys])
con.commit()
print("\napplied.")
print("corpus:", con.execute("SELECT status, count(*) FROM place GROUP BY 1").fetchall())
print("parent_cache:", con.execute("SELECT count(*) FROM parent_cache").fetchone()[0])
