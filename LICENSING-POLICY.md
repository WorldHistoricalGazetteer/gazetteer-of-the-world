# Licensing policy: consulting restricted sources without inheriting their terms

**Status:** proposed, 5 September 2026. Written in response to
[WorldHistoricalGazetteer/place#240](https://github.com/WorldHistoricalGazetteer/place/issues/240),
which identifies the general problem for WHG contributors. This document is the specific position for
*this* dataset, which has already run the reconciliation that #240 warns about.

**The goal that constrains everything below:** the Gazetteer of the World linked-data output is to be
released **CC BY 4.0**. Nothing may enter it that cannot travel under those terms.

---

## 1. The distinction the policy rests on

Reconciliation does three separable things to an external source. Only the third is encumbered.

| | Operation | What crosses the boundary | Encumbered? |
|---|---|---|---|
| **Query** | Ask the index for candidates matching a name | Record **ids** and scores | No |
| **Constrain** | Use a source's polygon *server-side* to sieve candidates (`contained_in`) | A **subset of our own candidate ids** | No |
| **Retain** | Copy a coordinate, polygon or centroid into our output | The source's **content** | **Yes** |

The first two produce a *decision about our own records*. The third is extraction, and it is what
engages database right, ODbL share-alike, non-commercial restrictions and no-derivatives terms alike.

**Constraining is the important case**, and it is unusually clean here. When we send
`contained_in=[chgis:1234]`, the CHGIS polygon is evaluated **inside WHG's own index**, by WHG, under
whatever terms WHG holds that data on. We never receive it. What returns is a shorter list of ids we
already had. The polygon acted as a sieve and nothing of it is retained. That is the ordinary "consult
a database to decide something" use, it does not substitute for the source, and it does not conflict
with the source's normal exploitation.

**The honest caveat.** Article 7(5) of the Database Directive reaches *repeated and systematic
extraction of insubstantial parts* where that conflicts with normal exploitation. Constraining stays
outside it only while we retain nothing that reconstitutes the source. That gives a hard, checkable
rule, in section 4. Note also that this reasoning is offered to explain the constraint rather than to
settle it; the tiering below is worth applying regardless, because it costs us very little.

---

## 2. Retention tiers

Machine-readable in [`data/source_licence_tiers.json`](data/source_licence_tiers.json), whose
`license_spdx` values are copied from the indexing repo's own registry (`processing/settings.py`
`AUTHORITIES`), not from prose. Anything not listed there is tier D.

| Tier | Sources | Query | Constrain | **Retain geometry** | What we publish |
|---|---|---|---|---|---|
| **A** Permissive | `wd` `ohm` `po` `gn` `un` `clio` `ofs` `pl` `tgn` `ukhc` | yes | yes | **yes** | coordinate/polygon + attribution |
| **B** Share-alike | `osm` `gb` `iv` `tm` `hgis` `vob_*` | yes | yes | **no** | match id + citation |
| **C** Non-commercial | `chgis` `alc` `og` `dp` `nl` `kain_par` | yes | yes | **no** | match id + citation |
| **D** No-derivatives / unknown | `dgsd`, anything unlisted | yes | yes | **no** | match id + citation |
| **E** Contributed | `whg:*` | only where the dataset records a licence | as recorded | as recorded | as recorded |

A tier-B/C/D match is **not a failure**. It is a correct, citable identification that happens to carry
no geometry we may keep. #240 makes this point in our favour: a reference "serves the reconciliation
purpose and carries nothing across".

---

## 3. Locating a place whose best match is tier B, C or D

Three routes, in order. Each is *independent* of the restricted geometry, which is what makes it a
substitution rather than laundering.

1. **The book's own printed coordinates.** The 1856 edition prints coordinates for roughly 7,600
   places. They are public domain and they are already authoritative in our cascade. Nothing is
   adopted from anyone.
2. **A permissive co-referent.** If the tier-B/C/D record is `sameAs`/`exactMatch` a tier-A record,
   take the **tier-A record's own geometry**. Wikidata's coordinate for the same place was not derived
   from OSM's polygon; it is an independent assertion about the same place. Cite both: *matched via
   `osm:…`, located from `wd:…`*. This is the same hard-link mechanism the gateway already uses to
   give a point-only container a boundary (`source="linked-polygon"`), and it is exposed to us through
   `include_hard_links`.
3. **Matched but not located.** Keep the id, the title and the citation; store no coordinate. The
   place is findable and correctly identified, and simply does not appear as a point on the map.

**The centroid escape is closed.** Replacing an adopted polygon with its centroid does not work: a
centroid computed from an OSM polygon is derived from that polygon, and computing them systematically
across a corpus is exactly the aggregate extraction database right reaches. #240 is explicit that this
"only makes it harder to see". Route 2 is a substitution; a centroid is not.

---

## 4. Hard rules

1. **Never store geometry, or anything derived from geometry, from a tier B/C/D record.** This
   includes `repr_point`, bounding boxes, H3 cells and computed centroids.
2. **Never cache constraint results in a form that reconstitutes a boundary.** Constraining is
   defensible because it retains nothing; probing a source's polygon repeatedly and recording where it
   answers yes would reconstruct it, and would be extraction by another name. We record *which places*
   matched, never *where the boundary ran*.
3. **Record provenance per coordinate, not per match.** The namespace of `whg_match_id` is **not** a
   safe proxy for where a coordinate came from (see section 5). Every stored coordinate needs its own
   source id.
4. **Attribute every tier-A source actually used**, in the dataset's own licence statement and in the
   explorer.
5. **A source not in `data/source_licence_tiers.json` is tier D** until someone has read its actual
   terms. Do not infer a licence from a similar-looking source.

---

## 5. Where this dataset currently stands

This is not hypothetical. Measured against `data/gotw_seg.sqlite` on 5 September 2026:

| Tier | Matches | Share |
|---|---:|---:|
| A permissive | 70,822 | 74.1% |
| **B share-alike** | **24,535** | **25.7%** |
| C non-commercial | 238 | 0.2% |
| D no-derivatives | 20 | 0.0% |
| E contributed (`whg:`, no recorded licence) | 7 | 0.0% |

Of the tier-B total, **22,499 are OpenStreetMap (ODbL)**. 99,587 places carry a stored coordinate
taken from the matched record's `repr_point`, so on the order of a quarter of our published
coordinates are currently OSM-derived, and `docs/geometry.pmtiles` carries adopted boundary polygons
on the same basis. Under this policy that is not publishable as CC BY 4.0.

**A non-obvious contributor.** The non-point tie-break (`GOTW_GEOM_MARGIN`) prefers a polygon-bearing
candidate over a point when scores are near-tied, and it quadrupled the on-map polygons from 3,174 to
13,635. OSM is by far the polygon-richest source in the index, so that tie-break is also, and
silently, a mechanism that **biases coordinate adoption towards the most restrictively licensed
source we use**. The map improvement and the licence exposure are the same change.

### Remediation, in the order #240 asks for it

1. **Report before changing anything.** `process/audit_licence_exposure.py` produces the table above
   and a per-place list. Run it first.
2. Add explicit coordinate provenance to `place` (rule 3), since the match namespace cannot answer
   the question after a tie-break.
3. Re-derive coordinates for tier-B/C/D matches by routes 1 to 3.
4. Rebuild `docs/geometry.pmtiles` from tier-A geometries only.
5. State the position in `README.md` and in the dataset's Zenodo metadata.

---

## 6. What the CC BY 4.0 goal actually costs

Measured 5 September 2026 by `process/measure_licence_tradeoff.py` (600 of the 24,507 exposed places
re-queried permissive-only) and by a direct count against the `/vast/ishi/geom` store. The two halves
of the answer are very different, and only one of them is in doubt.

### Points: the cost is real but bounded, and not yet knowable

| Outcome for an exposed place | Sample | Scaled |
|---|---:|---:|
| A permissive record exists for the same name | 98.0% | ~24,000 |
| ...within 25 km of what we hold, so a free substitution | 52.3% | ~12,800 |
| ...further away, so probably a different place | 45.7% | ~11,200 |
| No permissive record at all | 2.0% | ~500 |

**The 45.7% is not a real cost, and must not be quoted as one.** The re-query used production's
country-only phonetic pass, which is the very defect this work package exists to fix: it is the pass
that put *Chang-Hing-Hyen*, a place in Zhejiang, in Vietnam. A 28% mass in the 100 to 1000 km band is
the signature of that noise, not of a genuinely absent permissive record. So the honest statement is
that the point cost lies **between ~500 places (2%) and ~11,700 (48%)**, and the true figure sits near
the bottom of that range. It cannot be pinned down until containment works, because the fix improves
both sides of the comparison at once. Re-run the measurement then.

### Polygons: the cost is unambiguous and large

Of our 16,837 matches that carry a boundary polygon in the geom store:

| Tier | Polygons | Share |
|---|---:|---:|
| A retainable | 2,572 | 15.3% |
| **B (OSM, ODbL)** | **14,255** | **84.7%** |
| C | 10 | 0.1% |

**Under CC BY 4.0 the boundary layer falls from 16,837 polygons to 2,572, a 6.5-fold reduction.** OSM
is the polygon-richest source in the index by a wide margin and nothing permissive replaces it: only
1.6% of the free point substitutions above carried a polygon of their own.

This is the same effect from section 5 seen from the other end. The non-point tie-break that took
on-map polygons from 3,174 to 13,635 did so almost entirely by adopting OSM boundaries. Relaxing the
licence would preserve that gain; holding CC BY 4.0 largely gives it back.

### What that means for the decision

- The **points** are worth defending. Most of them are recoverable from permissive records, and the
  apparent losses are mostly an artefact of the reconciliation defect we are already fixing.
- The **polygons** are the genuine trade. Keeping CC BY 4.0 means accepting a much thinner boundary
  layer, and saying so plainly rather than discovering it after publication.
- A **share-alike** dataset would keep everything, at the cost of imposing ODbL-style terms on the
  whole corpus, including the 74% of it that never needed them, and on our own transcription and
  typing work, which is the substantial original contribution here.
- There is a **third option** this policy already supports: publish the CC BY 4.0 dataset as the
  product, and render the OSM boundaries in the explorer as a *Produced Work* with the standard
  "© OpenStreetMap contributors" notice. #240 is explicit that displaying a map this way is fine and
  disturbs nobody's licence; only the download is constrained. **We would then show ~16.8k boundaries
  on the map and ship ~2.6k in the data**, which is honest, legal, and loses nothing a reader sees.

That last option looks like the right answer, and it costs almost nothing. It needs the map layer and
the downloadable dataset to be built from different queries, which they already are.

---

## 7. What this means for the reconciliation rebuild

The current work package is improving stage 4 by making the cascade respect administrative
hierarchies, and the reported failure case is China. The two most useful Chinese authorities in the
index are **CHGIS** (~81k Qing-era administrative units, tier C) and **DGSD** (tier D). Under this
policy that is not an obstacle, and the fit is better than it looks:

- CHGIS is exactly what we need it to be, a **containment parent and a verifier**. Its polygons sieve
  candidates inside the gateway and tell us which `wd:`/`gn:` record is the right one.
- What we keep is the permissive match and its permissive coordinate.
- So the restricted sources do the work they are best at (knowing where Qing prefectures were) and
  contribute nothing to our output but better decisions.

Sending `contained_in=[chgis:…]` is therefore **encouraged**, not merely tolerated. The policy
constrains what we keep, and keeping less here costs us no accuracy at all.
