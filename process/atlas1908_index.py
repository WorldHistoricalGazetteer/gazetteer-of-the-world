#!/usr/bin/env python3
"""Reconstruct the alphabetical index of the 1908 Stanford/CIM *Atlas of the Chinese Empire*
from our own Surya OCR, as the postal-romanisation bridge for place#245.

WHY THIS EXISTS
---------------
The 1856 *Gazetteer of the World* transcribes Chinese names by conventions that predate Wade-Giles,
so no published romanisation table maps them to anything the WHG index holds. Chinese POSTAL
romanisation (standardised 1906) sits between the two, and the 1908 atlas prints an alphabetical
index of every name on its maps with the province and the latitude and longitude - which makes a
name-to-name join VERIFIABLE against a coordinate rather than a similarity guess.

⚠️ OWN OCR ONLY. `CLAUDE.md` forbids ingesting a third-party OCR transcript. archive.org ships an
Abbyy/DjVu text for this item; it is used here for nothing, not even as a starting point. The input
to this script is the geometry cache written by our own `process/ocr_pages.py --save-geom` run on the
public-domain page images (item `atlasofchineseem00stan_0`, leaf images 0050-0064). Its quality
argues the same way independently: the third-party OCR produced "Sinkinng" and "Fowclieng".

WHAT THE PAGE LOOKS LIKE, AND WHY THAT MAKES REASSEMBLY POSSIBLE
----------------------------------------------------------------
Four columns per page, each `Name and Province. | Lat. | Long.`, separated by vertical rules:

    Abakan River, Russia .   52.40 N   89.20 E   | Angti, Tibet .  .  30.29 N  98.21 E  | ...
    Abakumovsk, Central                          | Anh Son, Annam  .  18.54 N 105.20 E  | ...
        Asia   .    .    .   45.27 N   79.27 E   | Anhai, Fukien   .  24.44 N 118.26 E  | ...

Surya returns line boxes, not a text stream, so the columns do not have to be recovered from
ordering at all - they are recovered from x-geometry, which is what the line boxes actually record.
A row is then the set of line boxes sharing a y-band WITHIN one column, concatenated in x order.
Where a name wraps, the coordinates sit on its LAST line (see `Abakumovsk` above), so a coordinate-
less row is a name prefix and is carried forward - that rule is the whole of the wrap handling.

COORDINATES ARE PRINTED AS `DD.MM`, NOT DECIMAL
------------------------------------------------
`30.32 N 117.6 E` is 30°32'N 117°6'E - Anking/Anqing, which is at 30.51°N 117.05°E. Minutes are
printed without a leading zero, so `.6` is six minutes and not six-tenths of a degree. Reading these
as decimal degrees would displace nearly every entry by up to half a degree in a direction that
correlates with the minutes digit, which is exactly the sort of error that still leaves a join
looking plausible. The `< 60` constraint on the minutes field is also a free OCR check.

THE VALIDATION, AND WHY IT IS THIS ONE
---------------------------------------
The index prints its own province entries with coordinate RANGES - `Anhwei, China . 29-34 N
115-119 E` - i.e. a printed bounding box for each province, from the same pages, set by the same
compositor. So every leaf entry can be checked against the box its own stated province claims,
with no external dataset and no licence question.

⚠️ That check has to be able to fail the way the subject fails. The failure mode being guarded
against is ROW MISALIGNMENT - a name joined to the coordinates of a different row. A province box is
several degrees across and neighbouring index rows are usually in the same province, so a box test
alone would pass through a one-row slip and certify it. It is therefore paired with two checks that
do catch a slip: alphabetical monotonicity within each column (a slip breaks the sort), and the
minutes < 60 constraint. Reported together; a page that passes the box test and fails monotonicity
is not a clean page.

Usage:
    python3 process/atlas1908_index.py --geom-dir <dir of p*.geom.json> --out data/atlas1908_index.json
    python3 process/atlas1908_index.py --geom-dir <dir> --report        # QA only, write nothing
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

# Four columns. Boundaries are derived from where the NAME text actually starts on the page, not from
# the column headers: the headers are set a dozen-odd pixels in from their own column's text, and on
# some leaves that is enough to throw a whole column into its left-hand neighbour.
N_COLS = 4
BAND_LEAD = 8             # a band opens this far left of its name-start peak (leader dots, kerning)
CROSS_PAD = 10            # a box only counts as crossing the rule if it runs this far past the peak
ROW_TOL = 0.55            # row clustering tolerance, as a fraction of the median line height

# `52.40 N`, `89.1 E`, and the range form `29-34 N` used for provinces. Comma for point is a common
# Surya slip on this typeface and is accepted here, then validated by the minutes < 60 rule.
_D = r"\d{1,3}"
_DM = rf"{_D}\s*[.,]\s*\d{{1,2}}|{_D}\s*-\s*{_D}|{_D}"
_PAIR = rf"(?P<lat>{_DM})\s*[NnSs]\b\.?\s*(?P<lon>{_DM})\s*[EeWw]\b\.?"
COORD_RE = re.compile(_PAIR + r"\s*$")
COORD_ANY_RE = re.compile(_PAIR)        # unanchored: used to cut a line that merged across the rule
NAME_CLEAN_RE = re.compile(r"\s*\.(\s*\.)*\s*$")
RULE_RE = re.compile(r"^[|Il!\[\]]\s+")     # a column rule Surya read as a character

# Every coordinate in this atlas is north and east, so the plausible window is a hard gate rather than
# a heuristic. It earns its place: the column rule is intermittently read as a `1`, which turns
# `37.19 N` into `137.19 N` - a value that survives the minutes < 60 test, lands the row thousands of
# km away, and (being a real number in a real field) is invisible to every structural check.
LAT_MAX, LON_MIN, LON_MAX = 90.0, 20.0, 180.0


def _norm(s: str) -> str:
    """Join key: strip diacritics, hyphens, apostrophes, spaces and case.

    Our 1856 forms differ from postal forms mainly by hyphenation (`Yang-Sin` / `Yangsin`), which is
    a typographic difference and not a linguistic one, so it is removed on both sides rather than
    modelled. Nothing else is removed: this must stay a strict equality test on the residue, because
    the whole value of the join is that it is not a similarity guess.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _sortkey(s: str) -> str:
    """The book's own alphabetisation, for the monotonicity check only - NOT the join key.

    The index sorts word by word, not letter by letter: `Yen shui`, `Yen tse`, then `Yenan`. That is
    only monotonic if the space sorts before letters, so spaces (and the parentheses around feature
    terms, which order `Yu ho (canal)` before `Yu ho (river)`) are kept rather than stripped. Using
    the hyphen-insensitive join key here instead would report ~7% inversions that are properties of
    the key, not of the reassembly.
    """
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ()]", "", s.lower()).strip()


def _dm(tok: str):
    """`DD.MM` (degrees and minutes, minutes printed without a leading zero) -> decimal degrees.

    Returns (value, is_range). A range (`29-34`) is a province box edge, not a point; its midpoint is
    returned so a province still gets a usable centre, flagged so it is never treated as a leaf fix.
    """
    tok = tok.replace(" ", "")
    if "-" in tok:
        a, b = tok.split("-", 1)
        if not (a.isdigit() and b.isdigit()):
            return None, False
        return (int(a) + int(b)) / 2.0, True
    m = re.fullmatch(rf"({_D})[.,](\d{{1,2}})", tok)
    if m:
        deg, mins = int(m.group(1)), int(m.group(2))
        if mins >= 60:                       # impossible minutes: an OCR slip, not a coordinate
            return None, False
        return deg + mins / 60.0, False
    if tok.isdigit():
        return float(tok), False
    return None, False


def bands(lines, w):
    """Column x-boundaries, per page - the scans are not registered to a common origin.

    Each column has ~90 rows whose name begins at the same x, so the four name-start positions are by
    far the strongest peaks in the histogram of line-box left edges. Coordinate fragments that Surya
    split off start further right and fall to the nearest peak on their left, which is their own
    column. Taking the peaks rather than the `Name and Province.` header positions matters: the header
    is inset from its column's text by 13-21 px, and on some leaves the inter-column gap is only ~20,
    so a header-derived boundary puts an entire column into its neighbour.
    """
    xs = [ln["bbox"][0] for ln in lines]
    if not xs:
        return [0, w + 1], False
    hist = Counter(x // 4 for x in xs)
    peaks, guard = [], w / (2.0 * N_COLS)
    for b, _ in hist.most_common():
        x = b * 4
        if all(abs(x - p) > guard for p in peaks):
            peaks.append(x)
        if len(peaks) == N_COLS:
            break
    peaks.sort()
    if len(peaks) != N_COLS:
        peaks = [round(i * w / N_COLS) for i in range(N_COLS)]
    edges = [max(0, p - BAND_LEAD) for p in peaks] + [w + 1]
    edges[0] = 0
    return edges, peaks, len(peaks) == N_COLS


def split_lines(lines, edges, peaks):
    """Normalise the line list so that every box lies within one column and holds one printed row.

    Surya merges in both directions on this typeface, and each merge would otherwise fabricate a row
    that pairs one entry's name with another entry's coordinates - the precise failure this whole
    reconstruction has to avoid:

      * VERTICALLY, two stacked rows come back as one box with a literal `<br>`. Split on it and
        divide the box's height between the parts.
      * HORIZONTALLY, a box crosses a column rule (`. 31.52 N 120.19 E Kinkiangkai, Yunnan .`). The
        printed format is rigid enough to cut on: everything up to and including the FIRST complete
        coordinate pair belongs to the left column, the remainder starts the right-hand one. Applied
        repeatedly, since a box occasionally spans two rules.

    A box that crosses a rule but holds no coordinate cannot be cut safely, so it is dropped and
    counted rather than guessed at.
    """
    out, merged_v, merged_h, unsplit = [], 0, 0, 0
    stack = []
    for ln in lines:
        if "<br>" in ln["text"]:
            parts = [p for p in ln["text"].split("<br>") if p.strip()]
            x0, y0, x1, y1 = ln["bbox"]
            step = (y1 - y0) / max(len(parts), 1)
            merged_v += len(parts) - 1
            for i, p in enumerate(parts):
                stack.append({"bbox": [x0, round(y0 + i * step), x1, round(y0 + (i + 1) * step)],
                              "text": p.strip()})
        else:
            stack.append(dict(ln))
    while stack:
        ln = stack.pop(0)
        x0, y0, x1, y1 = ln["bbox"]
        # Judged against the NEXT column's text start, not against the band edge: a full-width row
        # legitimately runs to within a few pixels of the rule, and on the tighter leaves the band edge
        # sits only ~16 px beyond it, which condemned ~1,600 sound rows. The band is taken from the
        # edges first and the peak read off that index - asking for "the next peak right of x0"
        # instead returns a line's OWN column start whenever it begins inside the BAND_LEAD tolerance.
        band = max(i for i in range(len(edges) - 1) if edges[i] <= x0)
        nxt = peaks[band + 1] if band + 1 < len(peaks) else None
        if nxt is None or x1 <= nxt + CROSS_PAD:
            out.append(ln)
            continue
        m = COORD_ANY_RE.search(ln["text"])
        if not m or not ln["text"][m.end():].strip():
            unsplit += 1                      # crosses a rule with nothing to cut on - do not guess
            continue
        merged_h += 1
        cut = m.end()
        frac = cut / max(len(ln["text"]), 1)
        xm = round(x0 + (x1 - x0) * frac)
        out.append({"bbox": [x0, y0, min(xm, nxt - 1), y1], "text": ln["text"][:cut].strip()})
        stack.insert(0, {"bbox": [max(xm, nxt), y0, x1, y1], "text": ln["text"][cut:].strip()})
    out.sort(key=lambda l: ((l["bbox"][1] + l["bbox"][3]) / 2, l["bbox"][0]))
    return out, {"merged_vertical": merged_v, "merged_horizontal": merged_h,
                 "uncuttable_crossings": unsplit}


def rows_of(lines, lo, hi, tol):
    """Lines whose box STARTS in [lo, hi) grouped into rows by y-centre, each row in x order.

    Assignment is by x0 rather than centre so that a wide row (`Yu kiang (river), Kwangsi 23.18 N
    107.0 E`) is not pushed into its right-hand neighbour by its own width.
    """
    mine = [ln for ln in lines if lo <= ln["bbox"][0] < hi]
    mine.sort(key=lambda ln: (ln["bbox"][1] + ln["bbox"][3]) / 2)
    out, cur, anchor = [], [], None
    for ln in mine:
        yc = (ln["bbox"][1] + ln["bbox"][3]) / 2
        if anchor is None or yc - anchor <= tol:
            cur.append(ln)
            anchor = yc if anchor is None else anchor
        else:
            out.append(sorted(cur, key=lambda l: l["bbox"][0]))
            cur, anchor = [ln], yc
    if cur:
        out.append(sorted(cur, key=lambda l: l["bbox"][0]))
    return out


def _subrows(text):
    """One row's text -> one string per printed entry it actually contains.

    Surya sometimes merges several stacked rows into a single box with no `<br>` to mark it, which is
    invisible to the vertical splitter. A hand count of one column found this costs two entries
    outright and corrupts a third: `Menglai … 24.9 N 99.57 E Menglang … 24.32 N 100.17 E Mengli` came
    back as one row named after all three and carrying only the last one's coordinates - a fabricated
    name on a real coordinate, which no structural check downstream would question.

    The cut is the same one the rule-crossing splitter uses and is safe for the same reason: the
    printed format ends every entry with a coordinate pair, so each pair terminates exactly one entry.
    A row holding a single pair (the overwhelming majority) is returned untouched.
    """
    hits = list(COORD_ANY_RE.finditer(text))
    if len(hits) < 2:
        return [text]
    out, prev = [], 0
    for m in hits:
        out.append(text[prev:m.end()].strip())
        prev = m.end()
    tail = text[prev:].strip()
    if tail:
        out.append(tail)                     # a trailing name with no coordinates: the next wrap head
    return [t for t in out if t]


def parse_column(rows, page, col, carry, stats):
    """Rows of one column -> index entries, carrying a wrapped name forward onto the row that
    actually holds the coordinates.

    `carry` is passed in and returned rather than being local, because the compositor's text flows on
    across a column break and across the page: an entry whose name wraps at the foot of a column has
    its coordinates on the first line of the NEXT column. Handled locally, those entries came out as
    the tail of their own name (`kiang`, `tung`, `bed)`), which is both a lost entry and a fabricated
    one.
    """
    entries = []
    for row in rows:
        joined = " ".join(ln["text"].strip() for ln in row).strip()
        joined = RULE_RE.sub("", re.sub(r"\s+", " ", joined))
        if not joined or len(joined) < 2:
            continue
        subs = _subrows(joined)
        if len(subs) > 1:
            stats["merged_rows_split"] += len(subs) - 1
        for text in subs:
            entries, carry = _parse_row(text, entries, carry, page, col, stats)
    return entries, carry


def _parse_row(text, entries, carry, page, col, stats):
    m = COORD_RE.search(text)
    if not m:
        head = NAME_CLEAN_RE.sub("", text).strip(" .,")
        if head and re.search(r"[A-Za-z]", head):
            carry.append(head)
        if len(carry) > 3:                       # runaway: a rule, a header, a bad page region
            carry = carry[-1:]
        return entries, carry
    lat, lat_rng = _dm(m.group("lat"))
    lon, lon_rng = _dm(m.group("lon"))
    name = NAME_CLEAN_RE.sub("", text[:m.start()]).strip(" .,")
    full = " ".join(carry + [name]).strip() if carry else name
    carry = []
    full = re.sub(r"\s+", " ", full).strip(" .,")
    if not full or lat is None or lon is None:
        return entries, carry
    if not (0 <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        stats["implausible"] += 1        # a rule read as a digit; see LAT_MAX above
        return entries, carry
    # Trailing `, Province`; a parenthetical feature term (`(river)`, `(mts.)`) stays with the name.
    if "," in full:
        head, prov = full.rsplit(",", 1)
    else:
        head, prov = full, ""
    entries.append({
        "name": head.strip(" ."), "province": prov.strip(" ."),
        "lat": round(lat, 4), "lon": round(lon, 4),
        "is_range": bool(lat_rng or lon_rng),
        "page": page, "col": col, "raw": text,
    })
    return entries, carry


def parse_page(geom, page, carry, stats):
    lines = [ln for ln in geom["lines"] if ln["text"].strip()]
    # Running head and column headers: drop everything above the `Name and Province.` header, PER
    # COLUMN. The four headers are not set on a common baseline and Surya's boxes for them differ by
    # a few pixels more; one global cutoff at the lowest of them clips the first data row off the
    # other three columns, which silently costs the top entry of each column on several leaves.
    edges, peaks, peaks_ok = bands([ln for ln in lines if ln["bbox"][1] > geom["h"] * 0.06],
                                   geom["w"])
    hdrs = [ln for ln in lines if re.search(r"Name\s+and\s+Prov", ln["text"], re.I)]
    tops = []
    for i in range(N_COLS):
        own = [h["bbox"][3] for h in hdrs if edges[i] <= h["bbox"][0] < edges[i + 1]]
        tops.append(max(own) if own else None)
    fallback = max([t for t in tops if t] or [geom["h"] * 0.05])
    tops = [t if t is not None else fallback for t in tops]
    body = []
    for ln in lines:
        b = max((i for i in range(N_COLS) if edges[i] <= ln["bbox"][0]), default=0)
        if ln["bbox"][1] >= tops[b]:
            body.append(ln)
    body, diag = split_lines(body, edges, peaks)
    heights = sorted(ln["bbox"][3] - ln["bbox"][1] for ln in body) or [18]
    lh = heights[len(heights) // 2]
    out = []
    for i in range(N_COLS):
        got, carry = parse_column(rows_of(body, edges[i], edges[i + 1], lh * ROW_TOL),
                                  page, i, carry, stats)
        out.extend(got)
    diag.update(four_columns_found=peaks_ok, line_height=lh, lines=len(body))
    return out, diag, carry


# ---------------------------------------------------------------------------------------------
# QA. Three checks with DIFFERENT failure modes, because the one that is easiest to state - "does
# the coordinate fall inside its stated province" - is the one that cannot catch a one-row slip.

def province_boxes(entries):
    """Printed province boxes, from the index's own range-form entries (`Anhwei, China 29-34 N …`)."""
    boxes = {}
    for e in entries:
        if not e["is_range"]:
            continue
        m = COORD_RE.search(e["raw"])
        if not m:
            continue
        la, lo = m.group("lat"), m.group("lon")
        if "-" not in la or "-" not in lo:
            continue
        try:
            la0, la1 = (int(v) for v in la.replace(" ", "").split("-"))
            lo0, lo1 = (int(v) for v in lo.replace(" ", "").split("-"))
        except ValueError:
            continue
        boxes[_norm(e["name"])] = (lo0 - 1, lo1 + 1, la0 - 1, la1 + 1)   # 1 deg for the box's own rounding
    return boxes


# The eighteen provinces of China proper, as the atlas spells them. The box test is pooled over these
# and reported separately for everything else, because several non-Chinese "boxes" the index prints
# are not bounding boxes at all: `Russia . . 49-53 N 80-90 E` describes the one corner of Siberia the
# atlas maps in detail, while the index lists Russian places out to 134 E. Pooling those in would
# report ~200 sound rows as reassembly errors and put a made-up 5% error bar on the deliverable.
CHINA_PROPER = {_norm(p) for p in (
    "Anhwei Chekiang Chihli Fukien Honan Hunan Hupeh Kansu Kiangsi Kiangsu Kwangsi Kwangtung "
    "Kweichow Shansi Shantung Shensi Szechwan Yunnan").split()}


def qa(entries):
    boxes = province_boxes(entries)
    inside = outside = untested = 0
    per = defaultdict(lambda: [0, 0])
    for e in entries:
        b = boxes.get(_norm(e["province"]))
        if not b:
            untested += 1
            continue
        lo0, lo1, la0, la1 = b
        ok = lo0 <= e["lon"] <= lo1 and la0 <= e["lat"] <= la1
        per[e["province"]][0 if ok else 1] += 1
        if _norm(e["province"]) not in CHINA_PROPER:
            continue
        inside += ok
        outside += not ok

    # Alphabetical monotonicity WITHIN a column. This is the check that can see a row slip: the index
    # is sorted, so a name paired with a neighbour's coordinates leaves the sort intact but a name
    # DROPPED or DUPLICATED by mis-clustering breaks it. Measured per column, since column order
    # across a page is a separate assumption.
    seq = defaultdict(list)
    for e in entries:
        seq[(e["page"], e["col"])].append(_sortkey(e["name"]))
    inv = tot = 0
    for k, names in seq.items():
        for a, b in zip(names, names[1:]):
            tot += 1
            inv += (a > b)
    return {"province_boxes": len(boxes), "in_box": inside, "out_of_box": outside,
            "province_untested": untested,
            "per_province": {k: {"in": v[0], "out": v[1]} for k, v in sorted(per.items())},
            "alpha_pairs": tot, "alpha_inversions": inv,
            "alpha_inversion_rate": round(inv / tot, 4) if tot else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--geom-dir", required=True, help="dir of p*.geom.json from ocr_pages.py --save-geom")
    ap.add_argument("--out", help="write the reconstructed index here as JSON")
    ap.add_argument("--report", action="store_true", help="QA summary only")
    a = ap.parse_args()

    files = sorted(Path(a.geom_dir).glob("p*.geom.json"), key=lambda p: int(p.stem.split(".")[0][1:]))
    if not files:
        sys.exit(f"no p*.geom.json in {a.geom_dir}")
    entries, pages, carry, stats = [], [], [], Counter()
    for f in files:
        idx = int(f.stem.split(".")[0][1:])
        got, diag, carry = parse_page(json.loads(f.read_text(encoding="utf-8")), idx, carry, stats)
        entries.extend(got)
        diag.update(page=idx, entries=len(got))
        pages.append(diag)

    q = qa(entries)
    q["rejected_implausible"] = stats["implausible"]
    print(f"pages {len(files)}  entries {len(entries)}  "
          f"(range-form/province rows {sum(e['is_range'] for e in entries)})")
    for p in pages:
        flag = "" if p["four_columns_found"] else "  <- 4 columns NOT found, even split used"
        print(f"  p{p['page']:05d}: lines {p['lines']:4d}  entries {p['entries']:4d}  "
              f"split v/h {p['merged_vertical']:3d}/{p['merged_horizontal']:3d}  "
              f"dropped-crossing {p['uncuttable_crossings']:2d}{flag}")
    print(f"\nQA  rows rejected as implausible coordinates:   {q['rejected_implausible']}")
    print(f"    province boxes recovered from the index itself: {q['province_boxes']}")
    tot = q["in_box"] + q["out_of_box"]
    print(f"    inside its stated province box, China proper: {q['in_box']}/{tot}"
          f"  ({100.0 * q['out_of_box'] / max(tot, 1):.1f}% out)"
          f"   [rows with no box for their province: {q['province_untested']}]")
    worst = sorted(q["per_province"].items(), key=lambda kv: -kv[1]["out"])[:6]
    print("    worst provinces (out/total): "
          + ", ".join(f"{k or '(none)'} {v['out']}/{v['in'] + v['out']}" for k, v in worst))
    print(f"    alphabetical inversions within a column:   {q['alpha_inversions']} / {q['alpha_pairs']}"
          f"  ({q['alpha_inversion_rate']})")
    top = Counter(e["province"] for e in entries).most_common(12)
    print("    provinces: " + ", ".join(f"{p or '(none)'} {n}" for p, n in top))

    if a.out and not a.report:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(
            {"source": {"item": "atlasofchineseem00stan_0",
                        "title": "Atlas of the Chinese Empire (Edward Stanford / China Inland Mission, 1908)",
                        "leaves": [int(f.stem.split('.')[0][1:]) for f in files],
                        "ocr": "own Surya run (process/ocr_pages.py --save-geom); no third-party transcript"},
             "qa": q, "pages": pages, "entries": entries}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"\nwrote {len(entries)} entries -> {a.out}")


if __name__ == "__main__":
    main()
