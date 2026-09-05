# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An OCR-to-linked-data pipeline that turns the 7-volume *Gazetteer of the World* (RGS, 1856) into an
AAT-typed, WHG-reconciled authority gazetteer, plus a static GitHub Pages explorer for the result.
`README.md` is the long-form design record (stage by stage, with the rationale and the failures that
shaped each decision); `WHG-LESSONS.md` is the write-up of what transfers to WHG's own platform.
Read the relevant README section before changing a stage — most non-obvious code exists because a
documented failure mode required it.

The pipeline has already run end-to-end: `data/gotw_seg.sqlite` holds 94,763 entries → 116,292 places
(~98.6k reconciled, ~17.7k unmatched), 1,774 vision-digitised tables. Work is now incremental
(fixes, re-runs of single stages, explorer changes), not a first build.

## Where things run

**Heavy stages only run on the Pitt CRC** (Slurm + vLLM + `/vast/ishi` storage): OCR (Surya, GPU
array), LLM extraction (Llama-3.3-70B), VLM work (Qwen2.5-VL), and the fast reconcile backend (the
ES gateway's cluster-facing interface is reachable only from compute nodes). Never run compute on a
login node — always `srun`/`sbatch`; the `submit_*_slurm.py` scripts encode the correct partitions,
envs and resources.

**Locally runnable:** parsing, AAT build, toponym dictionary, `--backend api` reconciliation,
the export/tiling scripts (minus the `/vast` geom-store), the review UI, and everything in `docs/`.
No requirements file; the light deps are `pymupdf tiktoken pydantic requests beautifulsoup4 lxml`
(plus `flask` for the review UI, `Pillow` for plate work). A `.venv/` exists at the repo root.

## Commands

```bash
# Whole pipeline (on CRC, from a tmux session) — stages: ocr merge parse extract ingest vlm reconcile export publish
process/run_pipeline.sh --list
process/run_pipeline.sh --dry-run                       # print every command/sbatch, submit nothing
process/run_pipeline.sh --from vlm                      # resume
process/run_pipeline.sh --only reconcile --reconcile-reset

# Individual stages (see each script's module docstring for the full usage block)
python3 process/parse_ocr.py txt/gotw-v1-ocr.txt --volume v1 --db data/gotw_seg.sqlite [--dry-run]
python3 process/extract.py --dry-run --limit 1          # show prompt+schema+request, no API/GPU needed
python3 process/extract.py --ingest 'llama_seg/llama.*.jsonl' --db data/gotw_seg.sqlite
python3 process/reconcile.py --limit 200 --concurrency 24            # gateway backend (CRC only)
python3 process/reconcile.py --backend api --concurrency 6           # public API, needs WHG_API_TOKEN
python3 process/reconcile.py --seed-demo 8                           # demo rows, no extraction needed

# Site rebuild + publish
process/build_tiles.sh                                  # places.pmtiles (+ geometry.pmtiles on CRC)
python3 process/export_reader.py --db data/gotw_seg.sqlite --out-dir docs/reader --plates-manifest docs/plates/manifest.json
python3 process/build_search_db.py --db data/gotw_seg.sqlite --out docs/search/gotw-fts.sqlite.png
process/publish_assets.sh                               # large assets -> 'site-assets' GitHub release (needs gh)

# QA / human review (no automated test suite exists — validation is by these)
python3 process/flag_suspects.py                        # intrinsic + LLM reasonableness flags -> qa
python3 process/review_ui.py                            # local Flask UI over the flagged work-list
python3 process/crosscheck_headwords.py                 # headword-count agreement vs a reference transcript
python3 process/vlm_validate.py                         # per-volume VLM heading recall/precision metric
```

There are **no unit tests**. Verify changes with `--dry-run`/`--limit` runs against the real DB, the
QA scripts above, and spot-checks of specific headwords.

## Data model and flow

One SQLite working store, `data/gotw_seg.sqlite` (git-ignored, ~300 MB, rebuildable). SQLite rather
than DuckDB because the workload is write-heavy incremental updates; export to Parquet for analytics.

```
source (1/volume) → entry (1/headword block; kind='entry'|'crossref') → place (1/place; the unit of interest)
                  ↘ back_matter (v7 Appendix)   ↘ table_data (vision tables)   ↘ qa / vlm_qa / llm_cache
```

- `entry.headword` is the printed ALL-CAPS form; `entry.headword_disp` is the title-cased display
  form. Use `headword_disp` in anything user-facing — uppercase toponyms are not wanted in the UI.
- `place.status` is `pending` → `extracted` → `reconciled`/`unmatched`; `place.reconciliation` holds
  the JSON blob (match toponym, pass, flags, partOf relations), `recon_pass` the winning pass.
- `llm_cache` is keyed by `(provider, model, prompt+schema signature, entry text)`. Editing a prompt
  or the Pydantic schema deliberately invalidates the cache — expect a full re-run cost when you do.
- `data/review.sqlite` is a **signature-keyed** sidecar (`source|headword|page`, never `entry_id`) so
  human review decisions survive a re-parse or a DB rebuild. Don't key it by row id.

## Stage-specific invariants

**OCR (`ocr_pages.py`)** — Surya won't split the dense two-column body, so reading order is
reconstructed from line-box geometry; unruled statistical tables are found by a *gutter detector* on
the line boxes (never by digit density or column rules) and routed out of the prose as
`<!-- table bbox=… -->` markers. Table candidates downstream are the **union** of that detector and
the VLM page triage — they fail on disjoint pages. One file per page, atomic writes, resumable.

**Extraction (`extract.py`)** — input is capped to the first 6,000 chars of an entry (the long
country essays otherwise overflow the 32k context and truncate the JSON). AAT type is a **closed
enum** built from `data/aat_shortlist.json`, plus `"other"`; a bad/stale id must fail loudly.
Coordinates are kept only when the source actually printed them (`scrutinise.py` enforces this) —
never let the model supply a plausible coordinate from world knowledge.

**Reconciliation (`reconcile.py`)** — the hard-won rules: never filter by `types`/`fclasses` (sparsely
populated, tanks recall); threshold on `score`, not the conservative `match` flag; use
`containment="exact"` (the gateway's `fuzzy`/H3 mode returns 0 even for genuinely contained places).
Printed coordinates are authoritative for location and bypass the name cascade (match within
`GOTW_COORD_RADIUS_KM`, else keep as `coord-only`). A polygon-bearing candidate wins over a point
within `GOTW_GEOM_MARGIN`. The external service is the limiter, so this runs as one moderate-
concurrency CPU job, not a GPU array.

**VLM stages** — must be self-hosted Qwen2.5-VL via vLLM (`TABLE_BACKEND=vllm`). The Gemini backend
in `extract_tables.py`/`extract_appendix.py` exists only for the historical cost comparison; don't
make an external LLM the default path. Tables are stored as structured JSON (CSVW-flavoured:
`{title, columns:[{label,group,unit,type}], rows, source_note, footnotes}`), **never HTML** — the
`type:"place"` column is reconcilable like any other toponym.

## The explorer (`docs/`)

Single page, no build step, no backend: `docs/index.html` (~1,000 lines) plus self-hosted libraries in
`docs/lib/` and `docs/search/lib/`. **No script CDNs** — only basemap tiles are third-party. Data is
fetched only as needed: PMTiles over range requests, sharded `docs/detail/<id%N>.json` records, a
chunked reader store, FTS5 over `sql.js-httpvfs` (served as `…/gotw-fts.sqlite.png` so Pages' gzip
doesn't break range requests), and a Symphonym ONNX phonetic index.

Large generated assets (reader, plates, `geometry.pmtiles`, FTS DB, Symphonym) are **git-ignored** and
served from the `site-assets` GitHub Release, pulled in at deploy time by `.github/workflows/pages.yml`.
After regenerating them, run `process/publish_assets.sh`; the workflow only redeploys on pushes that
touch `docs/**`, so a `process/`-only commit won't refresh the site. `docs/detail/` and
`docs/places.pmtiles` *are* committed.

Explorer error reports arrive as GitHub issues (Issue Form, labelled by `.github/workflows/label-reports.yml`).
Plate-orientation reports are cleared by `process/apply_plate_orientation.py`, which records a
cumulative override in `data/plate_orientation_overrides.json` so a full re-run keeps the fix.

## Environment

Secrets live in `.env` (git-ignored; on the CRC, `~/.gotw_env`, chmod 600): `WHG_API_TOKEN` is the only
one the production path needs. `ANTHROPIC_API_KEY`/`GEMINI_API_KEY` are optional and only for the
archived model A/B. Other knobs read from the environment: `GOTW_COORD_RADIUS_KM`, `GOTW_GEOM_MARGIN`,
`GOTW_COORD_THRESHOLD`, `WHG_GATEWAY_URL`, `GEOM_STORE`, `VLLM_BASE_URL`/`VLLM_MODEL`,
`QWEN_BASE_URL`/`QWEN_MODEL`/`QWEN_THINKING`, `TABLE_BACKEND`/`TABLE_VL_BASE`/`TABLE_VL_MODEL`,
`PLATE_VL_BASE`/`PLATE_VL_MODEL`, `CONDA_SH`/`CONDA_ENV`/`VLLM_ENV`, `HF_HOME`.

## Sourcing constraint

The corpus is the **1856 first edition, HathiTrust volumes 1-7** (catalogue record 011407465); the
undated 8-14 set on the same record is a different edition and must never be mixed in. The project
deliberately uses no third-party OCR transcript (licence encumbrance) — only our own Surya OCR of the
public-domain scans. Reference transcripts may be used for headword-count QA only, never ingested.
