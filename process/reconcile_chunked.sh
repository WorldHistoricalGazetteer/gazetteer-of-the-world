#!/usr/bin/env bash
# Reconcile the corpus in chunks at a concurrency the live site cannot feel, backing off whenever it
# can. Submit with sbatch; see the header block below for resources.
#
# WHY THIS EXISTS
# The gateway has no batch endpoint, so a full run is ~300k individual queries, and the VM serving
# whgazetteer.org has eight cores. Measured 2026-09-05, live /api/search latency against an idle
# baseline of ~0.11s:
#
#     concurrency 12   1.0 - 3.8 s     ~16x   unacceptable
#     concurrency  3   0.2 - 0.66 s   2 - 6x   noticeable
#     concurrency  1   0.109 - 0.116s    1x   INVISIBLE
#
# So the cap is not a guess: it is the largest setting measured not to move live latency, plus a
# feedback loop for everything the measurement cannot predict (real visitors, other jobs, ES merges).
#
# WHY LATENCY AND NOT LOAD AVERAGE
# We cannot read the VM's /proc/loadavg from a compute node, and load average is a lagging 1-minute
# EMA anyway: when the concurrency-12 job was killed, latency recovered to 0.107s on the very next
# probe while load average still read 9.32. Latency is what a visitor experiences and it responds
# immediately. Load average would have kept us throttled long after the harm had stopped.
#
# THE CONTROLLER
# Before each chunk, probe live search latency three times and take the median. Above HIGH, wait and
# re-probe — a real visitor's session must win over our backfill. Below LOW for two consecutive
# checks, allow concurrency 2; otherwise stay at 1. It never exceeds MAXC, so the worst case is the
# setting already measured as invisible.
#
# The run is resumable by construction, so being stopped at any point costs only the chunk in flight:
# matches commit per pass, and parent lookups persist in `parent_cache`.
set -uo pipefail

DB="${DB:-data/gotw_seg.sqlite}"
GW="${WHG_GATEWAY_URL:-http://gazetteer-clus.crc.pitt.edu:9200}"
CHUNK="${CHUNK:-3000}"
MAXC="${MAXC:-2}"
LOW="${LOW:-0.20}"        # below this, the site is quiet and we may use MAXC
HIGH="${HIGH:-0.30}"      # above this, stand down entirely
BACKOFF="${BACKOFF:-300}" # seconds to wait when standing down
MAX_WAITS="${MAX_WAITS:-72}"   # give up after this many consecutive back-offs (6h at 300s)

cd "$(dirname "$0")/.."

# NOTE (2026-09-05, measured): the median-of-three does NOT protect against a COLD start. On the
# first probe of a fresh job the gateway measured 0.45s and the job stood down for 300s, while 18
# probes taken moments later from the same host all read 0.10-0.13s. A cold start slows all three
# samples together, so taking a median of them changes nothing — the samples are correlated, which
# is exactly the case a median cannot help with. It self-corrects on the next cycle and costs only
# one BACKOFF, so it is not worth restarting a run over; but a warm-up query whose result is
# DISCARDED, taken once before the loop begins, would remove it. Do not add that mid-run: bash
# reads a script incrementally as it executes, so editing this file while a job is running it can
# corrupt the run.
probe() {
  # Median of three, so one unlucky sample cannot swing the decision. See the note above for the
  # case it does not cover.
  local a b c
  a=$(curl -s -o /dev/null -w '%{time_total}' --max-time 20 -X POST "$GW/api/search" \
        -H 'Content-Type: application/json' -d '{"query":"Canterbury","mode":"fuzzy","size":10}' 2>/dev/null || echo 99)
  b=$(curl -s -o /dev/null -w '%{time_total}' --max-time 20 -X POST "$GW/api/search" \
        -H 'Content-Type: application/json' -d '{"query":"Ravenna","mode":"fuzzy","size":10}' 2>/dev/null || echo 99)
  c=$(curl -s -o /dev/null -w '%{time_total}' --max-time 20 -X POST "$GW/api/search" \
        -H 'Content-Type: application/json' -d '{"query":"Nagasaki","mode":"fuzzy","size":10}' 2>/dev/null || echo 99)
  printf '%s\n%s\n%s\n' "$a" "$b" "$c" | sort -g | sed -n 2p
}

remaining() {
  python3 - "$DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
print(c.execute("select count(*) from place where whg_match_id is null "
                "and status in ('extracted','demo')").fetchone()[0])
PY
}

quiet_streak=0
waits=0
chunk_n=0

while true; do
  left=$(remaining)
  if [ "${left:-0}" -eq 0 ]; then
    echo "$(date -Is)  nothing left to reconcile — done."
    break
  fi

  lat=$(probe)
  if awk -v a="$lat" -v b="$HIGH" 'BEGIN{exit !(a+0 > b+0)}'; then
    quiet_streak=0
    waits=$((waits + 1))
    echo "$(date -Is)  STAND DOWN  live search ${lat}s > ${HIGH}s  (${left} places left, wait ${waits}/${MAX_WAITS})"
    if [ "$waits" -ge "$MAX_WAITS" ]; then
      echo "$(date -Is)  gave up: the gateway has been busy for $((waits * BACKOFF / 60)) minutes. Not a failure — resubmit later."
      exit 0
    fi
    sleep "$BACKOFF"
    continue
  fi
  waits=0

  if awk -v a="$lat" -v b="$LOW" 'BEGIN{exit !(a+0 < b+0)}'; then
    quiet_streak=$((quiet_streak + 1))
  else
    quiet_streak=0
  fi
  conc=1
  [ "$quiet_streak" -ge 2 ] && conc="$MAXC"

  chunk_n=$((chunk_n + 1))
  echo
  echo "$(date -Is)  chunk $chunk_n: $left left, live search ${lat}s, concurrency $conc"
  python3 -u process/reconcile.py --db "$DB" --limit "$CHUNK" --concurrency "$conc"
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "$(date -Is)  reconcile.py exited $rc — stopping. Progress is committed; resubmit to resume."
    exit $rc
  fi
done

echo
echo "=== final distribution ==="
python3 - "$DB" <<'PY'
import sqlite3, sys, json
c = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
print(c.execute("select status, count(*) from place group by 1").fetchall())
for r in c.execute("select recon_pass, count(*) from place group by 1 order by 2 desc"):
    print("  ", r)
confs = []
for (rec,) in c.execute("select reconciliation from place where reconciliation is not null"):
    d = json.loads(rec)
    cand = d.get("candidate") or {}
    if cand.get("confidence") is not None:
        confs.append(cand["confidence"])
if confs:
    confs.sort()
    print("measured confidence: n=%d p10=%.1f median=%.1f p90=%.1f" % (
        len(confs), confs[len(confs)//10], confs[len(confs)//2], confs[(9*len(confs))//10]))
PY
