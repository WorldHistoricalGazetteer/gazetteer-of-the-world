#!/bin/bash
# Run a SECOND, read-only gateway on the staging ES node, so a full re-reconciliation can run at
# real concurrency without touching the VM that serves whgazetteer.org.
#
#   sbatch process/staging_gateway.sbatch
#
# WHY IT MUST LIVE ON THE STAGING NODE: gateway/config.py sets
#     ES_BACKEND = f"http://localhost:{ES_INTERNAL_PORT}"
# The host is hardcoded; only the port is overridable. So the gateway process has to sit on the
# same machine as the Elasticsearch it talks to.
#
# WHY THIS IS NOT A STALENESS RISK (checked, not assumed):
#   * the gateway never reads a `boundaries` index. It is referenced nowhere in gateway/*.py except
#     two English-prose comments. `boundaries` is deprecated.
#   * containment="exact" reads the geom store, a LIVE directory of binary shards on shared /vast
#     (/vast/ishi/geom, ~23 GB). A staging gateway reads the same bytes production reads, so exact
#     containment cannot be stale by construction.
#   * staging's `places`/`toponyms` were restored from prod_repo snapshots dated 5 September, i.e.
#     the current generation.
#
# WHAT THIS SCRIPT IS MOSTLY MADE OF, AND WHY: assertions. A gateway that starts, reports healthy,
# and answers every query slightly differently is the failure that matters here, and none of the
# obvious checks catch it. Two named mechanisms:
#   * PLACES_INDEX / TOPONYMS_INDEX are ALIASES. If a restore leaves concrete index names without
#     recreating the aliases, the gateway starts clean and every search returns nothing.
#   * Symphonym is loaded at startup and /api/reconcile's phonetic mode depends on it. If the model
#     silently fails to resolve (SYMPHONYM_MODEL_DIR is auto-detect by default; see place#242),
#     phonetic discovery degrades to lexical-only and you get plausible, different answers.
# So we assert on RESULTS, not on HTTP 200: a known place must be found, and phonetic mode must
# out-recall exact mode on a deliberately misspelled query. Both would pass trivially on a healthy
# gateway and both fail loudly on the two failure modes above.
#
#SBATCH --job-name=gotw-stagegw
#SBATCH --partition=smp
#SBATCH --nodelist=smp-n227
#SBATCH --oversubscribe
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=2-00:00:00
#SBATCH --output=/vast/ishi/gotw/logs/gotw-stagegw-%j.out
set -uo pipefail

PORT="${GOTW_STAGE_GW_PORT:-9210}"
export GATEWAY_PORT="$PORT"
export PROD_ES_INTERNAL_PORT=9201
export GATEWAY_HOST=0.0.0.0
cd /vast/ishi/elastic

echo "host=$(hostname)  job=${SLURM_JOB_ID:-?}  port=$PORT  started=$(date -Is)"

# The gateway has no environment of its own on the CRC: it is deployed to the production VM and
# runs there as `gazetteer`. /vast/ishi/envs/vllm is the only env on this filesystem carrying
# uvicorn and fastapi, so we borrow it. That is a real risk of a subtly different runtime, and it
# is why the assertions below test RESULTS and why compare_gateways.py must pass before any run:
# the claim we need is not "same environment" but "same answers", and only the paired gate can
# establish that. A missing onnxruntime, for instance, shows up as assertion 2 failing.
PY_BIN=/vast/ishi/envs/vllm/bin/python
nohup "$PY_BIN" -m gateway > "/vast/ishi/gotw/logs/stagegw-proc-${SLURM_JOB_ID}.log" 2>&1 &
GW=$!
echo "gateway pid=$GW"

G="http://localhost:$PORT"
ok=0
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$G/api/health" 2>/dev/null)
  [ "$code" = "200" ] && { ok=1; break; }
  sleep 5
done
[ "$ok" = 1 ] || { echo "FAIL: gateway never became healthy"; tail -40 "/vast/ishi/gotw/logs/stagegw-proc-${SLURM_JOB_ID}.log"; kill $GW 2>/dev/null; exit 1; }
echo "health OK after $((i*5))s"

# ---- ASSERTION 1: the aliases resolve to real data. A gateway on an alias-less restore answers
# 200 with zero hits for everything, which no health check distinguishes from a working one.
n=$(curl -s --max-time 15 -X POST "$G/api/search" -H 'Content-Type: application/json' \
      -d '{"query":"Canterbury","mode":"exact","size":5}' | python3 -c "import sys,json;print(len((json.load(sys.stdin) or {}).get('hits') or []))" 2>/dev/null)
echo "assert-1 aliases/data: exact 'Canterbury' -> ${n:-0} hits"
[ "${n:-0}" -gt 0 ] || { echo "FAIL: no hits for a place that certainly exists — aliases or data missing"; kill $GW 2>/dev/null; exit 1; }

# ---- ASSERTION 2: Symphonym actually loaded. A deliberately misspelled query must do BETTER in
# phonetic mode than exact mode. If the model failed to load, phonetic collapses toward exact and
# this fails — which is the whole point, since the gateway stays healthy either way.
pe=$(curl -s --max-time 15 -X POST "$G/api/search" -H 'Content-Type: application/json' \
      -d '{"query":"Kanterberry","mode":"exact","size":10}' | python3 -c "import sys,json;print(len((json.load(sys.stdin) or {}).get('hits') or []))" 2>/dev/null)
pp=$(curl -s --max-time 15 -X POST "$G/api/search" -H 'Content-Type: application/json' \
      -d '{"query":"Kanterberry","mode":"phonetic","size":10}' | python3 -c "import sys,json;print(len((json.load(sys.stdin) or {}).get('hits') or []))" 2>/dev/null)
echo "assert-2 symphonym: 'Kanterberry' exact=${pe:-0} phonetic=${pp:-0}"
[ "${pp:-0}" -gt "${pe:-0}" ] || { echo "FAIL: phonetic did not beat exact on a misspelling — Symphonym probably did not load"; grep -iE "symphonym|onnx|model" "/vast/ishi/gotw/logs/stagegw-proc-${SLURM_JOB_ID}.log" | tail -20; kill $GW 2>/dev/null; exit 1; }

echo
echo "STAGING GATEWAY READY: http://smp-n227:$PORT"
echo "  reconcile against it with:  WHG_GATEWAY_URL=http://smp-n227:$PORT"
echo "  RUN THE PAIRED GATE FIRST:  python3 process/compare_gateways.py"
echo
wait $GW
