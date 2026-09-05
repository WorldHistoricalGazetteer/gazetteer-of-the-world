#!/usr/bin/env bash
# Watch the Pitt gateway VM while a long reconciliation run is hammering it, and ALARM (exit 1) if it
# degrades. Designed to run ON THE VM:  ssh pitt 'bash -s' < process/watch_gateway_health.sh
#
# WHY: reconcile.py issues one POST per query — /api/reconcile has no batch form — so a full-corpus run
# is ~300k requests over hours. The same gateway process serves whgazetteer.org, and the VM has only
# EIGHT CORES with Elasticsearch already holding ~48 GB of its 62 GB. Our backfill competes directly
# with live visitors for those cores. place#235's argument is exactly this cost, "on the gateway — our
# side". A run that quietly makes the public site slow is not an acceptable trade.
#
# WHAT IT MEASURES, and why these two things:
#   * 1-minute load average against core count. This is the direct measure of the harm we can do, and
#     it is why this script wants to run on the VM: from outside you can only infer load from latency,
#     and latency stays flat until saturation and then collapses.
#   * latency of a representative /api/search — the query path a live visitor actually waits on.
#     /api/health is deliberately NOT the alarm signal: it can stay fast while the query path
#     saturates, because it does no Elasticsearch work.
#
# Measured 2026-09-05 on this VM (8 cores):
#   idle-ish        load ~3.0    health ~0.020s   search ~0.113s   reconcile ~0.10s
#   under our run   load 9.4-10.3 at --concurrency 24   <- ~3x oversubscribed, the reason for the cap
#
# Usage (args optional):
#   watch_gateway_health.sh [LOAD_RATIO] [SEARCH_THRESHOLD_S] [CONSECUTIVE] [INTERVAL_S]
#   defaults: 1.5  1.0  3  30
# LOAD_RATIO is load-average per core; 1.5 on 8 cores = 12.
set -uo pipefail

RATIO="${1:-1.5}"
THRESH="${2:-1.0}"
CONSEC="${3:-3}"
INTERVAL="${4:-30}"
G="${WHG_GATEWAY_URL:-http://127.0.0.1:9200}"
LOG="${GATEWAY_HEALTH_LOG:-$HOME/gateway-health.log}"

CORES=$(nproc)
LOAD_MAX=$(awk -v c="$CORES" -v r="$RATIO" 'BEGIN{printf "%.2f", c*r}')
echo "# $(date -Is) watching: cores=$CORES load_max=$LOAD_MAX search_max=${THRESH}s consec=$CONSEC" | tee -a "$LOG"

consecutive=0
while true; do
  load=$(cut -d' ' -f1 /proc/loadavg)
  s=$(curl -s -o /dev/null -w '%{time_total}' --max-time 25 -X POST "$G/api/search" \
        -H 'Content-Type: application/json' \
        -d '{"query":"Canterbury","mode":"fuzzy","size":10}' 2>/dev/null || echo 99)
  ts=$(date -Is)
  echo "$ts load=$load search=$s" >> "$LOG"

  bad=0
  awk -v a="$load" -v b="$LOAD_MAX" 'BEGIN{exit !(a+0 > b+0)}' && bad=1
  awk -v a="$s"    -v b="$THRESH"   'BEGIN{exit !(a+0 > b+0)}' && bad=1

  if [ "$bad" = 1 ]; then
    consecutive=$((consecutive + 1))
    echo "$ts  BREACH $consecutive/$CONSEC  load=$load (max $LOAD_MAX)  search=${s}s (max ${THRESH}s)" | tee -a "$LOG"
    if [ "$consecutive" -ge "$CONSEC" ]; then
      echo "ALARM: gateway VM degraded on $CONSEC consecutive probes."
      echo "whgazetteer.org shares this process and these $CORES cores. Remedy, in order:"
      echo "  1. ssh crc1 'scancel <jobid>'                      — stop imposing load"
      echo "  2. resubmit with a lower --concurrency             — the run is resumable"
      echo "Recent samples:"; tail -12 "$LOG"
      exit 1
    fi
  else
    consecutive=0
  fi
  sleep "$INTERVAL"
done
