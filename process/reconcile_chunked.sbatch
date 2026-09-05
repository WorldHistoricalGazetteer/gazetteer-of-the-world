#!/bin/bash
# Full-corpus re-reconciliation, submitted as a batch job so it survives a dropped session.
#
#   sbatch process/reconcile_chunked.sbatch
#
# WHY smp AND NOT A GPU ARRAY: the limiter is the external gateway, not us. This job spends
# almost all of its wall time waiting on HTTP, at a concurrency of 1-2. Two cores is generous.
#
# WHY THE PRODUCTION GATEWAY AND NOT STAGING (measured 2026-09-05, and the reason is not obvious):
#   * staging (smp-n227:9201) serves RAW ELASTICSEARCH, not the gateway API. A POST to
#     /api/reconcile there returns HTTP 400 — ES parsing it as an ES request — not a gateway
#     response. reconcile.py speaks the gateway API, so staging cannot serve it at all.
#   * staging carries only two aliases, `places` and `toponyms`. There is NO `boundaries`.
#     Containment is the centre of this re-reconciliation, so running against staging would
#     silently degrade the exact thing the run exists to improve. It would complete, report
#     plausible numbers, and be wrong.
# Either fact alone rules staging out. Both are worth stating because "we restored staging so
# use staging" is the obvious inference and it is wrong here.
#
# WALL TIME: measured throughput is 500 places per ~108 s at concurrency 2, so ~110k places is
# about 7 h if the live site stays quiet and ~13 h if the controller holds at 1 throughout.
# 24 h leaves room for stand-downs without the job being killed mid-chunk. Being killed is not
# a disaster — the run is resumable, matches commit per pass and parent lookups persist in
# `parent_cache` — but a resubmit costs a queue wait.
#
#SBATCH --job-name=gotw-recon
#SBATCH --partition=smp
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --output=/vast/ishi/gotw/logs/gotw-recon-%j.out
set -uo pipefail
cd /vast/ishi/gotw

# CHUNK 500 rather than the script's 3000 default: the controller re-probes live latency between
# chunks, so a smaller chunk means it can stand down sooner after the site gets busy. The cost is
# a few extra probes per hour, which is nothing.
export CHUNK=500
export MAXC=2

echo "host=$(hostname)  job=${SLURM_JOB_ID:-?}  started=$(date -Is)"
bash process/reconcile_chunked.sh
