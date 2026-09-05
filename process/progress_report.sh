#!/usr/bin/env bash
# One compact progress block for the chunked reconciliation run. Prints a single burst of lines so a
# Monitor watch turns it into one notification. Read-only; safe to call as often as you like.
#
# Usage:  process/progress_report.sh [JOBID]
set -uo pipefail
cd "$(dirname "$0")/.." 2>/dev/null || cd /vast/ishi/gotw
JOB="${1:-}"
DB="${DB:-data/gotw_seg.sqlite}"

STATE="unknown"
if [ -n "$JOB" ]; then
  STATE=$(sacct -X -n -o State -j "$JOB" 2>/dev/null | head -1 | tr -d ' ' || echo unknown)
  [ -z "$STATE" ] && STATE="unknown"
fi

# Live-site latency: the number that decides whether we should still be running at all.
LAT=$(curl -s -o /dev/null -w '%{time_total}' --max-time 20 \
      -X POST "${WHG_GATEWAY_URL:-http://gazetteer-clus.crc.pitt.edu:9200}/api/search" \
      -H 'Content-Type: application/json' \
      -d '{"query":"Canterbury","mode":"fuzzy","size":10}' 2>/dev/null || echo "n/a")

echo "[$(date -Is)] gotw-chunked ${JOB:-?} state=$STATE  live_search=${LAT}s"

python3 - "$DB" <<'PY'
import sqlite3, sys, json
try:
    c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
    tot = c.execute("select count(*) from place").fetchone()[0]
    st = dict(c.execute("select status, count(*) from place group by 1").fetchall())
    done = st.get("reconciled", 0) + st.get("unmatched", 0)
    left = st.get("extracted", 0) + st.get("demo", 0)
    pct = 100.0 * done / tot if tot else 0
    print("  progress %d/%d (%.1f%%)  reconciled=%d unmatched=%d remaining=%d"
          % (done, tot, pct, st.get("reconciled", 0), st.get("unmatched", 0), left))
    rp = c.execute("select recon_pass, count(*) from place where recon_pass is not null "
                   "group by 1 order by 2 desc").fetchall()
    if rp:
        print("  passes: " + ", ".join("%s=%d" % (k, v) for k, v in rp))
        # The quality signal: pass 4 is country-only phonetic. It took 71% of the corpus last time.
        m = dict(rp)
        p4 = m.get("4-phon-cc", 0)
        matched = sum(v for k, v in rp if k not in ("unmatched", "coord-only"))
        if matched:
            print("  4-phon-cc share of matches: %.1f%%%s"
                  % (100.0 * p4 / matched,
                     "   <-- WATCH: was 71%% before the confidence gate" if p4 / matched > 0.35 else ""))
    confs = []
    for (rec,) in c.execute("select reconciliation from place where reconciliation is not null "
                            "order by place_id desc limit 20000"):
        d = json.loads(rec)
        cand = d.get("candidate") or {}
        if cand.get("confidence") is not None:
            confs.append(cand["confidence"])
    if confs:
        confs.sort()
        print("  confidence (recent %d): p10=%.1f median=%.1f p90=%.1f"
              % (len(confs), confs[len(confs)//10], confs[len(confs)//2], confs[(9*len(confs))//10]))
    print("  parent_cache: %d" % c.execute("select count(*) from parent_cache").fetchone()[0])
except Exception as e:
    print("  DB read failed: %s" % e)
PY

# Last meaningful driver line: a chunk start, a stand-down, or an error.
if [ -n "$JOB" ] && [ -f "logs/gotw-chunked-$JOB.out" ]; then
  tail -40 "logs/gotw-chunked-$JOB.out" 2>/dev/null \
    | grep -E "chunk |STAND DOWN|gave up|exited|Error|Traceback" | tail -2 | sed 's/^/  /'
fi
