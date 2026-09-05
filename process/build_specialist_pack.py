#!/usr/bin/env python3
"""Build the specialist request pack: one CSV of every Chinese place, and a two-page PDF brief.

READ-ONLY. Writes to ~/Downloads by default.

WHY ONE FILE RATHER THAN TWO REQUESTS
A pilot of 30 was proposed to size the value before asking for more. Stephen's call is that a
specialist may well work through the whole list about as easily as a sample, and that two separate
asks cost more goodwill than one. So the CSV carries ALL Chinese places, with a `pilot` column marking
30 that would answer the question on their own. If they stop after those 30, we still learn what we
needed; if they continue, we get the corpus.

WHAT WE ARE ASKING FOR, and why it is not something we can derive
The 1856 text transcribes Chinese names by conventions that predate Wade-Giles (Wade's syllabary is
1859, Giles's dictionary 1892), so no published romanisation table maps them. Measured: `Keang-su`
resolves to GANSU at confidence 99.5 — a confident wrong answer. Nothing in the string says that
`Chang-Che-Hyen` is 長治縣; it has to be known. Three columns are left blank for exactly that.

Ordering is by how much each row would teach us: pilot rows first, then places whose coordinates the
book printed (so any answer can be verified against a known location), then the rest.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sqlite3
from collections import defaultdict

PAREN = re.compile(r"\s*\([^)]*\)\s*$")
CJK_RANGE = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")

# Per-country profile. Everything that differs between language packs lives here, so adding one is
# data rather than code. `bbox` is used to drop printed coordinates that cannot be right — 2.3% of the
# Chinese ones carry a negative longitude, a hemisphere error, and a corrupt coordinate is worse than
# an absent one because it silently poisons any verification done against it.
PROFILES = {
    "CN": {
        "language": "Chinese", "adjective": "Chinese",
        "bbox": (73, 136, 17, 54),
        "fill_cols": [("modern_pinyin", "The modern name, e.g. <i>Changzhi</i>"),
                      ("chinese_characters", "If known, e.g. 長治縣 / 长治县"),
                      ("modern_province", "e.g. <i>Shanxi</i>")],
        "headline": ("The book prints <b>Keang-su</b> for the province we now call Jiangsu. Our matching "
                     "engine resolves that to <b>Gansu</b> — a different province, 1,500 km away — "
                     "and reports high confidence while doing so."),
        "why": ("The 1856 transcriptions predate Wade-Giles (Wade's syllabary is 1859, Giles's dictionary "
                "1892), so no published romanisation table maps them. And the mapping is not derivable "
                "from the spelling: nothing in <i>Chang-Che-Hyen</i> tells you it is 長治縣. "
                "It has to be known."),
        "reach": 18, "control": 76,
        "hyphen_only": True,
        "province_hint": (r"chih-le|chih-li|sze-chuen|shan-tung|shan-se|shan-si|shen-se|shen-si|"
                          r"kwang-tung|kwan-se|kwang-si|yun-nan|ho-nan|honan|keang-se|kiang-si|keang-su|"
                          r"kiang-su|hu-nan|hu-pih|hu-pe|kan-suh|che-keang|chi-keang|che-kyang|kwei-chu|"
                          r"kwei-chow|gan-hwuy|ngan-hwuy|fo-keen|fo-ke|fuh-kien"),
    },
    "RU": {
        "language": "Russian", "adjective": "Russian",
        "bbox": (19, 190, 41, 82),
        "fill_cols": [("modern_cyrillic", "The name in Cyrillic, e.g. Курск"),
                      ("modern_transliteration", "Standard romanisation, e.g. <i>Kursk</i>"),
                      ("modern_region", "Oblast / krai / republic, e.g. <i>Kursk Oblast</i>")],
        "headline": None,      # filled from the data — see headline_from_data()
        "why": ("The book transcribes Russian names into mid-19th-century English spelling, before any "
                "standard romanisation existed. Some survive unchanged (<i>Kursk</i>), but many do not, "
                "and the ones that do not cannot be recovered from the spelling alone. Places may also "
                "have been renamed, absorbed or abolished since 1856, which is itself useful to record."),
        "reach": 25, "control": 76,
        "hyphen_only": False,
        "province_hint": None,
    },
}


def km(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = math.radians
    h = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def collect(db, ccode):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute(
            "SELECT p.place_id, p.name, p.lat, p.lon, p.recon_pass, p.reconciliation, p.extraction, "
            "e.page_start, s.filename FROM place p "
            "JOIN entry e ON e.entry_id = p.entry_id "
            "JOIN source s ON s.source_id = e.source_id "
            "WHERE p.extraction IS NOT NULL"):
        try:
            ext = json.loads(r["extraction"])
        except (ValueError, TypeError):
            continue
        if ext.get("country_code") != ccode:
            continue
        try:
            rec = json.loads(r["reconciliation"]) if r["reconciliation"] else {}
        except (ValueError, TypeError):
            rec = {}
        cand = rec.get("candidate") or {}
        plat, plon = ext.get("latitude"), ext.get("longitude")
        d = km(plat, plon, r["lat"], r["lon"]) if (plat is not None and r["lat"] is not None) else None
        vol = (re.search(r"-(v\d+)-", r["filename"] or "") or [None, ""])[1]
        rows.append({
            "place_id": r["place_id"],
            "headword": r["name"],
            "variants": "; ".join(ext.get("variant_names") or []),
            "hierarchy": " > ".join(PAREN.sub("", a).strip() for a in (ext.get("admin_hierarchy") or [])),
            "printed_lat": plat, "printed_lon": plon,
            "volume": vol, "page": r["page_start"],
            "our_match": cand.get("name") or "",
            "our_match_id": cand.get("id") or "",
            "our_match_conf": cand.get("confidence"),
            "km_from_printed": round(d, 1) if d is not None else None,
        })
    con.close()
    return rows


def choose_pilot(rows, n, prof):
    """Rows that would answer the question alone.

    Four requirements, and the fourth was learned by getting it wrong: the first version picked
    "Asses' Ears" — an English descriptive name for a rock off the coast — as pilot row 1. A specialist
    cannot demonstrate a romanisation mapping on a name that was never a romanisation. So a pilot case
    must actually BE the problem under study:

      * printed coordinates, so any answer can be verified independently;
      * currently wrong (unmatched, or matched >25km from where the book puts it);
      * inside one of the 18 Qing provinces, which is the population this affects;
      * a hyphenated transcription — the characteristic 1856 form ("Chang-Che-Hyen") rather than an
        English or Tibetan/Mongolian name that happens to fall in Chinese territory.

    Then spread across provinces, so one region's peculiarities cannot dominate the result.
    """
    hint = re.compile(prof["province_hint"], re.I) if prof.get("province_hint") else None
    by_prov = defaultdict(list)
    for r in rows:
        if r["printed_lat"] is None or not r["hierarchy"]:
            continue
        # `hyphen_only` is a Chinese-specific test: the 1856 transcriptions of Chinese names are
        # characteristically hyphenated ("Chang-Che-Hyen"), so it separates them from English or
        # Tibetan names that merely fall in Chinese territory. Russian transcriptions are not
        # hyphenated ("Balachna", "Orenburg"), so the profile switches it off rather than inheriting
        # a rule that would silently reject the entire population.
        if prof.get("hyphen_only") and "-" not in (r["headword"] or ""):
            continue
        if hint and not hint.search(r["hierarchy"]):
            continue
        wrong = (not r["our_match"]) or (r["km_from_printed"] is not None and r["km_from_printed"] > 25)
        if not wrong:
            continue
        by_prov[(r["hierarchy"].split(" > ") or [""])[0]].append(r)
    pilot, provinces = [], sorted(by_prov, key=lambda p: -len(by_prov[p]))
    i = 0
    while len(pilot) < n and provinces:
        p = provinces[i % len(provinces)]
        if by_prov[p]:
            pilot.append(by_prov[p].pop(0))
        else:
            provinces.remove(p)
            continue
        i += 1
    return {r["place_id"] for r in pilot}


def write_csv(rows, pilot_ids, path, prof):
    fill = [c for c, _ in prof["fill_cols"]]
    cols = (["pilot", "row", "headword_as_printed", "printed_variants", "printed_hierarchy",
             "printed_lat", "printed_lon", "volume", "page"]
            + fill + ["confident", "notes",
                      "our_current_guess", "our_guess_id", "our_guess_confidence",
                      "km_from_printed_point"])
    # pilot first, then coordinate-bearing, then the rest — most informative work first.
    rows.sort(key=lambda r: (r["place_id"] not in pilot_ids,
                             r["printed_lat"] is None,
                             (r["headword"] or "").lower()))
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for i, r in enumerate(rows, 1):
            w.writerow({
                "pilot": "YES" if r["place_id"] in pilot_ids else "",
                "row": i,
                "headword_as_printed": r["headword"],
                "printed_variants": r["variants"],
                "printed_hierarchy": r["hierarchy"],
                "printed_lat": r["printed_lat"], "printed_lon": r["printed_lon"],
                "volume": r["volume"], "page": r["page"],
                **{c: "" for c in fill},
                "confident": "", "notes": "",
                "our_current_guess": r["our_match"], "our_guess_id": r["our_match_id"],
                "our_guess_confidence": r["our_match_conf"],
                "km_from_printed_point": r["km_from_printed"],
            })
    return len(rows)


def _register_fonts():
    """Base font must cover Cyrillic; CJK needs a separate CID font.

    reportlab's default Helvetica is Latin-1 only, so Курск renders as boxes and 長治縣 renders as
    nothing at all — silently, with no error. DejaVuSans covers Latin and Cyrillic; STSong-Light is a
    CID font built into reportlab and needs no file. Returns (base_font_name, cjk_font_name).
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    base, cjk = "Helvetica", None
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if os.path.exists(path):
            pdfmetrics.registerFont(TTFont("DejaVuSans", path))
            bold = path.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
            if os.path.exists(bold):
                pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
                pdfmetrics.registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
                                              italic="DejaVuSans", boldItalic="DejaVuSans-Bold")
            base = "DejaVuSans"
            break
    try:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        cjk = "STSong-Light"
    except Exception:
        cjk = None
    return base, cjk


def _cjk(text, cjk_font):
    """Wrap CJK runs in the CID font so they render; leave everything else in the base font."""
    if not cjk_font:
        return CJK_RANGE.sub(lambda m: "[%s]" % "CJK omitted - no font", text)
    return CJK_RANGE.sub(lambda m: '<font name="%s">%s</font>' % (cjk_font, m.group(0)), text)


def write_pdf(path, n_rows, n_pilot, n_coords, csv_name, prof, headline):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    base, cjkf = _register_fonts()
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontName=base, fontSize=9.5, leading=13.5,
                          spaceAfter=6)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName=base, fontSize=15, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=base, fontSize=11, spaceBefore=10,
                        spaceAfter=4, textColor=colors.HexColor("#333333"))
    small = ParagraphStyle("small", parent=body, fontName=base, fontSize=8.5, leading=11,
                           textColor=colors.HexColor("#555555"))
    P = lambda t, st: Paragraph(_cjk(t, cjkf), st)   # every paragraph goes through the CJK wrapper

    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=16 * mm,
                            title="GOTW — Chinese place-name identification request")
    lang = prof["language"]
    S = []
    S.append(P(f"{lang} place names: identification request", h1))
    S.append(P(
        "<b>Gazetteer of the World</b> (Royal Geographical Society, 1856) is being turned into a "
        "linked-data authority gazetteer for the World Historical Gazetteer. We can reconcile most of "
        f"the world automatically. We reconcile {lang} names badly, and the reason is one a specialist "
        "can fix in a way no amount of engineering can.", body))

    S.append(P("The problem, in one example", h2))
    S.append(P(headline + " It is not a near miss; it is a confident wrong answer, and there are "
               "thousands like it.", body))
    S.append(P(prof["why"], body))

    S.append(P("What we are asking for", h2))
    S.append(P(
        f"A single CSV, <b>{csv_name}</b>, with {n_rows:,} rows — every {lang} place in the book. "
        "Three columns are blank for you to fill:", body))
    t = Table([[P("<b>%s</b>" % c, small), P(d, small)] for c, d in prof["fill_cols"]]
              + [[P("<b>confident</b>", small),
                  P("y / n / guess — please do mark uncertainty", small)]],
              colWidths=[46 * mm, 114 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#DDDDDD")),
    ]))
    S.append(t)
    S.append(Spacer(1, 4))
    S.append(P(
        "An uncertain answer marked uncertain is genuinely useful; a confident-looking guess is not, "
        "because we cannot tell it apart from the errors we are trying to remove.", body))

    S.append(P("If you have an hour rather than a day", h2))
    S.append(P(
        f"<b>{n_pilot} rows are marked YES in the <i>pilot</i> column and sorted to the top.</b> They are "
        "spread across provinces, they all carry coordinates the book printed (so we can verify any "
        "answer independently), and they are all cases we currently get wrong. Those alone would tell "
        "us whether this approach works. Anything beyond them is a bonus, not an expectation.", body))

    S.append(P("What each row gives you", h2))
    S.append(P(
        f"The headword as printed, the book's own variant spellings, the administrative hierarchy as "
        f"the book states it, and — for {n_coords:,} rows — the "
        "latitude and longitude the book printed. Volume and page are included so anything ambiguous "
        "can be checked against the original. Our current wrong guess is shown too, with how far it "
        "sits from the printed coordinates; please ignore it except as a warning.", body))

    S.append(P("Why it is worth your time", h2))
    S.append(P(
        f"For {lang} places whose true location we know from the printed coordinates, our search "
        f"reaches the right vicinity about {prof['reach']}% of the time. The same measurement on "
        f"British place names — where the 1856 spellings are close to modern ones — reaches "
        f"{prof['control']}%. That gap is what this list is for.", body))
    S.append(P(
        "The benefit is not only ours. These identifications would improve the World Historical "
        "Gazetteer's coverage for every historical source that transcribes Chinese names this way, "
        "and would supply training data that the underlying name-matching model currently lacks "
        "entirely — its developers have confirmed that historic romanisation cannot be fixed by "
        "modelling alone, because the data does not exist.", body))

    S.append(Spacer(1, 6))
    S.append(P(
        "Please return the CSV with whatever you have completed, however partial. Rows left blank cost "
        "us nothing; a wrong answer offered confidently costs us a great deal, so please leave "
        "anything doubtful blank or mark it <i>guess</i>.", small))
    doc.build(S)


def headline_from_data(rows, pilot_ids, prof):
    """The worst pilot case, stated concretely. A confident wrong answer communicates the problem
    faster than any recall statistic, so the brief opens with a real one from this very list rather
    than a hypothetical."""
    if prof.get("headline"):
        return prof["headline"]
    cands = [r for r in rows if r["place_id"] in pilot_ids and r["our_match"]
             and r["km_from_printed"] is not None]
    if not cands:
        return ("The book's spellings frequently retrieve a place of a similar-sounding name "
                "hundreds of kilometres from where the book itself places it.")
    w = max(cands, key=lambda r: r["km_from_printed"])
    return ("The book prints <b>%s</b>, and places it at a location it also prints. Our matching "
            "engine resolves it to <b>%s</b> — <b>%s km</b> from that location — and reports high "
            "confidence while doing so." % (w["headword"], w["our_match"], f"{w['km_from_printed']:,.0f}"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/gotw_seg.sqlite")
    ap.add_argument("--ccode", default="CN", choices=sorted(PROFILES))
    ap.add_argument("--pilot", type=int, default=30)
    ap.add_argument("--out-dir", default=os.path.expanduser("~/Downloads"))
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    prof = PROFILES[args.ccode]
    rows = collect(args.db, args.ccode)
    # Drop coordinates that cannot be right before they are used as ground truth anywhere.
    lo0, lo1, la0, la1 = prof["bbox"]
    dropped = 0
    for r in rows:
        # A row can carry one coordinate and not the other; a half-coordinate is not ground truth.
        if (r["printed_lat"] is None) != (r["printed_lon"] is None):
            r["printed_lat"] = r["printed_lon"] = r["km_from_printed"] = None
            dropped += 1
            continue
        if r["printed_lat"] is not None and not (lo0 <= r["printed_lon"] <= lo1
                                                 and la0 <= r["printed_lat"] <= la1):
            r["printed_lat"] = r["printed_lon"] = r["km_from_printed"] = None
            dropped += 1
    pilot_ids = choose_pilot(rows, args.pilot, prof)
    n_coords = sum(1 for r in rows if r["printed_lat"] is not None)
    headline = headline_from_data(rows, pilot_ids, prof)

    slug = prof["language"].lower()
    csv_path = os.path.join(args.out_dir, f"gotw-{slug}-placenames.csv")
    pdf_path = os.path.join(args.out_dir, f"gotw-{slug}-placenames-request.pdf")
    n = write_csv(rows, pilot_ids, csv_path, prof)
    write_pdf(pdf_path, n, len(pilot_ids), n_coords, os.path.basename(csv_path), prof, headline)

    print(f"{n:,} rows  ({len(pilot_ids)} pilot, {n_coords:,} with usable printed coordinates, "
          f"{dropped} coordinates dropped as impossible for {args.ccode})")
    print(f"  CSV: {csv_path}")
    print(f"  PDF: {pdf_path}")


if __name__ == "__main__":
    main()
