"""Export the dashboard payload: docs/data/restaurants.json.

Usage: python src/export_site_data.py [--check] [--quiet] [--allow-shrink]
  --check  build and validate the payload, print the report, write nothing
  --quiet  suppress the per-section summary
  --allow-shrink  write even when the roster lost more than a fifth of its rows

ToS (nyctourism.com: personal/noncommercial, no republication). This exporter
emits DERIVED/FACTUAL fields only. It must never write `menus.raw_text`, a
`menu_items` listing, or anything else that reproduces a menu. Menus are LINKED
via the official S3 PDF url. The only menu-derived text allowed out is a short
`menu_item_tags` snippet, re-centred on the matched keyword and capped by
SNIPPET_PAD below, deduplicated so overlapping snippets can't be reassembled
into a contiguous passage. assert_tos_clean() enforces this at the end.

Precedence rule for every field: the restaurant's own printed materials
(config/verified_values.json) beat the listing API. price_sweep is heuristic
triage and may NEVER populate a "verified" field.
"""
import itertools
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# BOOK_BY is the program's headline deadline (drives the badge); PROGRAM_END is
# the last extension week (drives the countdown). Both live in config/season.json,
# which is the only file a season changeover edits.
from config import (BOOK_BY, GOOGLE_PRIOR, NYC_BOUNDS, PROGRAM_END, SEASON,
                    SEASON_LABEL, SEASON_START, SEASON_YEAR, sane_coords)

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
TAGS_CONFIG = ROOT / "config" / "dish_tags.json"
VERIFIED = ROOT / "config" / "verified_values.json"
SUPPRESS = ROOT / "config" / "recognition_suppress.json"
JB_RAW = ROOT / "data" / "raw" / "recognition" / "james_beard.json"
OUT = ROOT / "docs" / "data" / "restaurants.json"

# ToS guards. matched_text is stored with 60 chars of context each side (up to
# 143 chars) and 97 of 101 item-snippets are an entire dish + description. That
# is too much menu to publish, so every snippet is RE-CENTRED on the keyword at
# this padding -- never right-truncated, which would keep 60 chars of unrelated
# menu and can cut the keyword in half.
SNIPPET_PAD = 25
MAX_SNIPPETS_PER_TAG = 1    # per restaurant
OVERLAP_RUN = 18            # reject a snippet sharing this many chars with a kept one
MIN_SOURCE_GAP = 60         # unpublished chars required between two snippets in
                            # the SAME menu, so they can't be stitched back into
                            # one contiguous passage
# THE RULE, stated once: a restaurant's published snippets may total at most
# 5% of that menu's extracted text, or 40 characters, whichever is greater.
# 40 chars is a few words around the keyword -- a fragment, not a reproduction.
# When a snippet would breach the budget the padding SHRINKS to fit rather than
# the snippet being dropped, so the tag keeps its evidence.
# The exporter enforces a slightly TIGHTER bar (4.5% / 36 chars) than the rule
# above, so an independent auditor measuring at 5%/40 with its own whitespace
# normalisation always passes with margin rather than tripping on rounding.
COVERAGE_CAP = 0.045
COVERAGE_FLOOR = 36
MIN_PAD = 8

# NYC_BOUNDS and sane_coords now live in config.py -- see the note there.


def assert_verified_gaps_reconcile(verified):
    """A hand-verified gap must subtract from its own comparable.

    These are the numbers the dashboard prints as SOLID figures -- the display
    state that means "checked against the restaurant's own printed materials".
    A pair that does not subtract is worse there than anywhere else on the page,
    because the whole point of the treatment is that a reader can trust it
    without checking.

    One entry breached this for weeks: Mark's Off Madison carried a comparable
    of $57 with a $27 gap on a $45 menu, because the decision doc states $57-68
    as the TWO-course a la carte price while the saving is quoted "on three".
    Nothing failed; the dashboard simply printed 57, 45 and 27 side by side.
    """
    bad = []
    for slug, v in (verified or {}).items():
        if slug.startswith("_"):
            continue
        price = v.get("rw_price")
        for comp_key, gap_key in (("comparable_usd", "gap_usd"),
                                  ("comparable_usd_high", "gap_usd_high")):
            comp, gap = v.get(comp_key), v.get(gap_key)
            if None in (comp, gap, price):
                continue          # an absent figure is not a contradiction
            if comp - price != gap:
                bad.append(f"{slug}: {comp_key} {comp} - rw_price {price} "
                           f"= {comp - price}, but {gap_key} says {gap}")
    if bad:
        raise AssertionError(
            "verified figures that do not reconcile (fix config/verified_values.json "
            "against reports/rw-final-bookings.md):\n  " + "\n  ".join(bad))


# Subway proximity. Straight-line distance times a 1.3 grid factor, at 80 m/min
# -- an approximation, labelled as one in the UI. Good enough to answer "can I
# get there on the 6?", not a routing engine.
SUBWAY = ROOT / "data" / "raw" / "subway" / "stations.json"
WALK_M_PER_MIN = 80.0
GRID_FACTOR = 1.3
MAX_WALK_MIN = 12


def _haversine_m(a_lat, a_lng, b_lat, b_lng):
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(b_lat - a_lat)
    dlng = radians(b_lng - a_lng)
    h = (sin(dlat / 2) ** 2
         + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(dlng / 2) ** 2)
    return 2 * 6371000 * asin(sqrt(h))


def load_stations():
    if not SUBWAY.exists():
        return []
    return json.loads(SUBWAY.read_text(encoding="utf-8"))


def subway_for(lat, lng, stations):
    """-> ({route: walk_minutes}, nearest_station_dict) within MAX_WALK_MIN."""
    if lat is None or lng is None or not stations:
        return {}, None
    by_route, nearest = {}, None
    for s in stations:
        d = _haversine_m(lat, lng, s["lat"], s["lng"]) * GRID_FACTOR
        mins = int(round(d / WALK_M_PER_MIN))
        if mins > MAX_WALK_MIN:
            continue
        if nearest is None or mins < nearest["min"]:
            nearest = {"name": s["name"], "min": mins, "routes": s["routes"]}
        for r in s["routes"]:
            if r not in by_route or mins < by_route[r]:
                by_route[r] = mins
    return by_route, nearest


# --------------------------------------------------------------------------
# Outdoor seating
#
# Two tiers, deliberately kept apart, because neither alone is honest:
#
#   licensed  -- the restaurant appears in NYC DOT's Dining Out register
#                (data/raw/outdoor/licenses.json). Authoritative, but covers
#                ONLY the public right of way: sidewalk and roadway sheds.
#   described -- the restaurant's own listing blurb mentions outdoor seating.
#                Weak, but it is the only signal that reaches the things the
#                register structurally cannot see -- rooftops, backyards,
#                courtyards, and the park venues (Tavern on the Green, Bryant
#                Park Grill) which are licensed under a different regime.
#
# Absence of both means UNKNOWN, never "no outdoor seating". The UI must never
# offer a "no outdoor seating" filter off this data.
OUTDOOR = ROOT / "data" / "raw" / "outdoor" / "licenses.json"
OUT_RADIUS_M = 80          # geocodes differ by a building width; 80m is generous
OUT_NAME_MIN = 0.34        # below this a same-house-number match is still required
# The two sources geocode independently and occasionally disagree badly: Little
# Chef Little Cafe sits 118m from its own licence, Quality Italian 168m. When
# the name matches EXACTLY and the street number agrees too, that pair is
# conclusive on its own and the distance is just geocoder noise -- so allow a
# wider radius in that one case. Measured: recovers exactly these 2, adds none.
OUT_FAR_M = 250

# Corporate and generic words that must not be allowed to create a name match:
# "PARK AVENUE KITCHEN LLC" and "KITCHEN GROUP LLC" share only noise.
_OUT_NOISE = {"llc", "inc", "corp", "corporation", "ltd", "co", "the",
              "restaurant", "restaurants", "nyc", "new", "york", "cafe", "bar",
              "grill", "kitchen", "group", "holdings", "enterprises",
              "of", "and", "at", "on", "by"}

_OUTDOOR_RE = re.compile(
    r"outdoor|patio|sidewalk|al ?fresco|terrace|rooftop|roof ?deck|backyard"
    r"|courtyard|open-air|garden", re.I)


def _out_norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def _out_toks(s):
    return {t for t in _out_norm(s).split() if t not in _OUT_NOISE and len(t) > 1}


def _out_sim(a, b):
    """Jaccard over meaningful tokens; full credit when one name contains the other."""
    A, B = _out_toks(a), _out_toks(b)
    if not A or not B or not (A & B):
        return 0.0
    if A <= B or B <= A:
        return 1.0
    return len(A & B) / len(A | B)


def _house_no(addr):
    m = re.match(r"\s*(\d+)", addr or "")
    return m.group(1) if m else None


def load_outdoor():
    if not OUTDOOR.exists():
        return []
    return json.loads(OUTDOOR.read_text(encoding="utf-8"))["rows"]


def outdoor_for(name, address, lat, lng, licences):
    """-> {sidewalk, roadway, licence_name, dist_m} or None.

    A candidate qualifies on a good name similarity, OR on any name overlap at
    all when the street number also agrees -- which is what rescues
    "Empire Steak House" / "EMPIRE STEAKHOUSE" at 237 W 54th.

    Sidewalk and roadway are separate rows in the register, so a place holding
    both appears twice; every qualifying row is folded in rather than just the
    closest one.
    """
    if lat is None or lng is None or not licences:
        return None
    hn = _house_no(address)
    hits = []
    for L in licences:
        d = _haversine_m(lat, lng, L["lat"], L["lng"])
        s = max(_out_sim(name, L["name"]), _out_sim(name, L["legal"]))
        addr_ok = bool(hn and hn == _house_no(L["street"]))
        if d > OUT_RADIUS_M:
            # only an exact name AND an agreeing street number reaches out here
            if not (d <= OUT_FAR_M and addr_ok and s >= 1.0):
                continue
        elif s < OUT_NAME_MIN and not (addr_ok and s > 0):
            continue
        hits.append((s + (0.3 if addr_ok else 0), -d, L))
    if not hits:
        return None
    hits.sort(key=lambda h: (-h[0], -h[1]))
    best = hits[0][2]
    # Fold in only the rows belonging to the SAME business as the best match.
    # "Every qualifying row" folded in every nearby licence that cleared the
    # name test, and a neighbourhood inside a restaurant's own name clears it
    # easily: Boucherie Union Square took a licence from Union Square Cafe, and
    # A Pasta Bar took one from Il Mulino Prime 21m away. Both were published
    # with seating they may not have, credited to a licence_name that did not
    # have it either -- so the row named its own evidence and then contradicted
    # it. One business appearing twice, once for the pavement and once for the
    # roadway, is the case this loop is for; two businesses is not.
    best_key = _out_norm(best["name"] or best["legal"])
    same = [h for h in hits
            if _out_norm(h[2]["name"] or h[2]["legal"]) == best_key]
    return {
        "sidewalk": any(h[2]["sidewalk"] for h in same),
        "roadway": any(h[2]["roadway"] for h in same),
        "licence_name": clean(best["name"] or best["legal"]),
        "dist_m": int(round(-hits[0][1])),
    }


# --------------------------------------------------------------------------
# Off-menu terms (src/menu_term_sweep.py)
#
# These come from the restaurant's OWN website, not the Restaurant Week PDF,
# because some dishes are never on a prix fixe -- a seafood tower is an a la
# carte raw-bar item. A hit means "they serve it", NOT "it's in Restaurant
# Week", and the UI has to keep saying so: these are a separate facet from the
# RW dish tags, never merged into them.
MENUSWEEP = ROOT / "data" / "raw" / "menusweep"
OFFSITE_VERIFIED = ROOT / "config" / "offsite_verified.json"
OFFSITE_SNIPPET_MAX = 90


def build_offsite():
    """slug -> [{tag, confidence, keyword, snippet, url}], best hit per tag.

    Hand checks in config/offsite_verified.json beat the sweep, in both
    directions -- same precedence rule the rest of the exporter follows:
      verdict 'yes' -> confidence 'verified', with the actual item and price
      verdict 'no'  -> the tag is REMOVED. This matters more than the promotion:
                       the sweep can only see that a raw bar exists, and a raw
                       bar is not a tower. 24 of the 31 low-confidence hits were
                       oysters priced by the piece.
    """
    if not MENUSWEEP.exists():
        return {}, 0
    checked = {}
    if OFFSITE_VERIFIED.exists():
        checked = json.loads(OFFSITE_VERIFIED.read_text(encoding="utf-8"))["restaurants"]

    by_slug, swept = {}, 0
    for f in sorted(MENUSWEEP.glob("*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        swept += 1
        slug, v = rec["slug"], checked.get(rec["slug"], {})
        best = {}
        for h in rec.get("hits", []):
            cur = best.get(h["tag"])
            # high beats low; among equals prefer the shorter, tighter snippet
            if cur is None or (
                    (h["confidence"] == "high") > (cur["confidence"] == "high")
                    or (h["confidence"] == cur["confidence"]
                        and len(h["snippet"]) < len(cur["snippet"]))):
                best[h["tag"]] = h

        out = []
        for tag, h in sorted(best.items()):
            ck = v.get(tag)
            if ck and ck["verdict"] == "no":
                continue
            if ck and ck["verdict"] == "yes":
                out.append({"tag": tag, "confidence": "verified",
                            "keyword": clean(ck["item"]),
                            "item": clean(ck["item"]),
                            "price_usd": ck.get("price_usd"),
                            "snippet": clean(ck.get("detail", ""))[:OFFSITE_SNIPPET_MAX],
                            "url": ck.get("source") or h["url"]})
            else:
                out.append({"tag": tag, "confidence": h["confidence"],
                            "keyword": clean(h["keyword"]),
                            "snippet": clean(h["snippet"])[:OFFSITE_SNIPPET_MAX],
                            "url": h["url"]})
        if out:
            by_slug[slug] = out
    return by_slug, swept


# --------------------------------------------------------------------------
# Google ratings (src/fetch_google_ratings.py)
#
# A raw star average is not comparable across restaurants: Maison Madison's
# 4.9 comes from 14 reviews and Manhatta's 4.7 from 3,999. Sorting on the raw
# number puts the 14-review restaurant on top, which is noise, not a finding.
#
# So the published score is a Bayesian shrinkage toward the corpus mean:
#
#     score = (v*R + m*C) / (v + m)
#
# R = the restaurant's average, v = how many reviews it rests on, C = the mean
# across every rated restaurant here, m = how many reviews it takes before we
# mostly believe the restaurant's own average.
#
# m = 150 chosen against the actual distribution, not by feel: the 10th
# percentile is 188 reviews, so most restaurants stay essentially themselves
# (Manhatta 4.7 -> 4.69), while the 27 restaurants under 100 reviews get pulled
# meaningfully toward the middle (Maison Madison 4.9 -> 4.53). Larger values
# over-correct -- at m=400 Le B.'s 3.4 from 227 reviews is dragged to 4.10,
# which hides a real and well-evidenced result.
#
# The raw rating and the count are BOTH published alongside the score. The
# weighting is there to make sorting honest, not to hide the input.
GOOGLE = ROOT / "data" / "raw" / "google"
# GOOGLE_PRIOR now lives in config.py -- three payloads publish a score that
# depends on it, and it had already drifted in one of them.


def bayesian_score(rating, reviews, mean, prior=GOOGLE_PRIOR):
    """A rating shrunk toward the roster mean, worth `prior` reviews of doubt."""
    return (reviews * rating + prior * mean) / (reviews + prior)


def rated(rec):
    """The matched block of an accepted record that carries a rating, else None."""
    m = rec.get("matched") or {}
    return m if rec.get("accepted") and m.get("rating") is not None else None


def google_row(rec, mean):
    """One cached Google record -> the published block, or None when unrated."""
    m = rated(rec)
    if m is None:
        return None
    r, v = float(m["rating"]), int(m.get("user_ratings_total") or 0)
    return {
        "rating": round(r, 2),
        "reviews": v,
        "score": round(bayesian_score(r, v, mean), 3),
        "place_id": m.get("place_id"),
        "matched_name": clean(m.get("name")),
        # 'place_id' means a human pinned it; 'textsearch' means it was
        # accepted on coordinates, which the UI should not overstate.
        "basis": rec.get("source") or "textsearch",
        "closed": m.get("business_status") not in (None, "OPERATIONAL"),
    }


def build_google():
    """slug -> {rating, reviews, score, ...}. Unmatched restaurants get nothing
    rather than a zero, exactly like every other unknown here."""
    if not GOOGLE.exists():
        return {}, 0.0
    recs = [json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(GOOGLE.glob("*.json"))]
    recs = [rec for rec in recs if rated(rec)]
    if not recs:
        return {}, 0.0
    mean = sum(float(rated(rec)["rating"]) for rec in recs) / len(recs)
    return {rec["slug"]: google_row(rec, mean) for rec in recs}, mean


# --------------------------------------------------------------------------
# Rubric: a transparent composite grade (config/rubric.json)
#
# Weights and every cut-point live in config so the score can be argued with
# rather than reverse-engineered. Each component is published next to the
# total; a grade you cannot take apart is not evidence, it is a vibe.
#
# The rule that keeps it honest: a component scores 0 only when the zero is a
# FACT (holds no award; has no 4/5/6 within a 12-minute walk). When the value
# is merely UNKNOWN -- no Google match, no printed end date, no comparable
# price -- the component is IMPUTED at its own mean and the imputation is
# published (see score_parts, which records why redistributing the weight was
# tried and rejected). Scoring unknowns as zero would punish restaurants for
# gaps in our own coverage, which is the failure this project has spent its
# whole life avoiding.
RUBRIC_CONFIG = ROOT / "config" / "rubric.json"


def load_rubric():
    return json.loads(RUBRIC_CONFIG.read_text(encoding="utf-8"))


def _ramp(v, lo, hi):
    """lo -> 0, hi -> 100, clamped. Works in either direction."""
    if v is None:
        return None
    if hi == lo:
        return 100.0
    t = (v - lo) / (hi - lo)
    return max(0.0, min(100.0, t * 100.0))


def scoring_day(today=None, program_end=PROGRAM_END):
    """The day the window component counts down from, clamped to PROGRAM_END.

    Past the last extension week the countdown is over for every restaurant
    alike, so freezing it there costs nothing live and buys determinism: a
    post-season re-export must not silently reshuffle every published grade.
    """
    return min(today or date.today(), date.fromisoformat(program_end))


def rubric_for(r, cfg, today):
    """-> (score, {component: value_or_None}, completeness_pct)."""
    parts = {}

    # award — the distinction held, decayed by its age. 0 is a fact: they hold
    # none. Recency is not an axis of its own; it is a discount on the award,
    # so a 1991 semifinalist cannot score what a 2026 one does. Michelin and
    # the NYT 100 are annual guides and always current, so this only bites JB.
    top = r.get("recog_top")
    if top:
        base = float(cfg["award"]["tiers"].get(top["tier"], 0))
        decay = float(cfg["award"]["recency_decay"].get(top["era"], 1.0))
        parts["award"] = base * decay
    else:
        parts["award"] = 0.0

    # rating — percentile of the WEIGHTED score, set by the caller
    parts["rating"] = r.get("_rating_pct")

    # lex — proximity is a BONUS above neutral; being off the Lexington line
    # costs nothing. A 0 here was a fixed tax on 100% of Queens and Staten
    # Island and 78% of Brooklyn, because the 4/5/6 does not run there: a fact
    # about the MTA, not about the restaurant. Use the subway facet if you need
    # the line as a hard requirement -- a filter beats a weighting for that.
    c = cfg["lex"]
    neutral = float(c["no_lex_score"])
    lex = [r["subway"].get(k) for k in ("4", "5", "6") if r.get("subway")]
    lex = [x for x in lex if x is not None]
    if not lex:
        parts["lex"] = neutral
    else:
        # The ramp is a BONUS ABOVE NEUTRAL, which is what this component has
        # claimed to be since the fixed-tax problem was found -- and it was
        # only half fixed. The no-line case was moved to neutral (50) but the
        # ramp still ran from 0, so a restaurant twelve minutes from the 6
        # scored 0.0 while one with no 4/5/6 within walking distance scored 50.
        # 129 restaurants scored BELOW the no-line neutral for being NEAR the
        # line, 24 of them at exactly zero. Scaling the bonus into
        # [neutral, 100] makes "distance costs nothing" literally true and
        # closes the discontinuity at the 12-minute walk cap: at the far end of
        # the ramp a restaurant now scores exactly what having no line scores,
        # which is the same thing MAX_WALK_MIN already says about it.
        parts["lex"] = neutral + (100.0 - neutral) * _ramp(
            min(lex), c["worst_minutes"], c["best_minutes"]) / 100.0

    # value — gap PERCENT (comparable across the price tiers), then shrunk
    # toward neutral by how much that figure actually rests on. Evidence used
    # to be a component of its own, which double-counted: a heuristic estimate
    # earned value points AND separately earned evidence points for being a
    # heuristic. It is a confidence multiplier on the figure, not a virtue.
    c = cfg["value"]
    if r.get("gap_pct") is None:
        parts["value"] = None
    else:
        v = _ramp(r["gap_pct"], c["zero_at_pct"], c["full_at_pct"])
        k = float(c["confidence"].get(r.get("gap_basis"), 0.0))
        parts["value"] = k * v + (1 - k) * float(c["neutral"])

    # window — days left to book. Flexibility, not urgency.
    #
    # INCLUSIVE of the end date, because every other date test on this site is
    # and this one alone was not. A restaurant whose window ends today has not
    # ended (hasEnded), still counts as urgent (isUrgent), is still offered a
    # date by the planner (dateIssue/validDates), and the countdown tile says
    # "1 day left". The rubric scored it 0.0 on a component the row detail
    # labels "Days left to book" — the same number it gives a restaurant that
    # closed three weeks ago, printed beside a page saying you can still book.
    # 191 restaurants read that way on 16 August; 401 do on the last day of the
    # season.
    if r.get("end_date"):
        try:
            left = (date.fromisoformat(r["end_date"]) - today).days
        except ValueError:
            left = None
        c = cfg["window"]
        parts["window"] = _ramp(max(0, left + 1), c["zero_at_days"],
                                c["full_at_days"]) if left is not None else None
    else:
        parts["window"] = None

    return parts


def score_parts(parts, cfg, means):
    """-> (score, published_parts, completeness).

    A missing component is IMPUTED at the mean of that component across every
    restaurant that has it. Two earlier versions got this wrong in opposite
    directions: redistributing the weight rewarded thin data, and shrinking the
    total toward the overall mean punished it -- the overall mean (41) sits far
    below the mean of the component usually missing (value, 58), so a
    restaurant was dragged down for OUR failure to find its prices. Imputing
    per component is neutral both ways, and the imputation is disclosed.
    """
    w = cfg["weights"]
    keys = [k for k in parts if k in w]
    total_w = sum(w[k] for k in keys)
    if not total_w:
        return None, parts, 0.0
    filled = {k: (parts[k] if parts[k] is not None else means.get(k, 50.0))
              for k in keys}
    score = sum(filled[k] * w[k] for k in keys) / total_w
    known_w = sum(w[k] for k in keys if parts[k] is not None)
    completeness = 100.0 * known_w / total_w
    published = {k: (round(parts[k], 1) if parts[k] is not None else None)
                 for k in keys}
    return round(score, 1), published, round(completeness, 1)


MONTHS = {m: i for i, ms in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
     ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
     ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for m in ms}

WEEK_RE = re.compile(
    r"Week\s+\d+\s*\(\s*[A-Za-z]{3,9}\.?\s*\d{1,2}\s*[-‐-―−]\s*"
    r"(?:(?P<m>[A-Za-z]{3,9})\.?\s*)?(?P<d>\d{1,2})\s*\)", re.I)
FIRST_MONTH_RE = re.compile(r"\(\s*(?P<m>[A-Za-z]{3,9})\.?\s*\d{1,2}", re.I)

RECOG_SOURCE_LABEL = {
    "michelin": "Michelin",
    "james_beard": "James Beard",
    "nyt": "NYT 100",
}
# Michelin's own wording. 'recommended' is The Plate, not a colloquial rec.
MICHELIN_LEVEL_LABEL = {
    "recommended": "The Plate",
    "bib_gourmand": "Bib Gourmand",
    "1 star": "1 star",
    "2 stars": "2 stars",
    "3 stars": "3 stars",
}
NYT_LEVEL_LABEL = {"nyt_100_best": "100 Best", "nyt_starred_review": "starred review"}


# --------------------------------------------------------------------------
# text hygiene
# --------------------------------------------------------------------------

def clean(text):
    """Strip PDF-extraction damage and control characters from published text."""
    if not text:
        return text
    s = unicodedata.normalize("NFC", str(text))
    s = s.replace("�", "'")          # replacement char, almost always an apostrophe
    s = "".join(c for c in s if c == "\n" or unicodedata.category(c)[0] != "C")
    return re.sub(r"\s+", " ", s).strip()


def jload(raw, default=None):
    """json.loads that tolerates SQL NULL and the literal TEXT 'null'."""
    if raw is None:
        return default
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return default if val is None else val


# --------------------------------------------------------------------------
# end dates
# --------------------------------------------------------------------------

def end_date_from_weeks(weeks, year=SEASON_YEAR):
    """Last week label -> ISO end date. 'Week 7 (Sept 1 - Sept 6)' -> 2026-09-06.

    The label carries a month and a day but never a year, so `year` has to come
    from the season -- a winter roster stamped with the summer year lands in the
    past and renders every restaurant closed.
    """
    if not weeks:
        return None
    label = str(weeks[-1])
    m = WEEK_RE.search(label)
    if not m:
        return None
    month_tok = m.group("m")
    if not month_tok:                                   # "Aug 24 - 31"
        fm = FIRST_MONTH_RE.search(label)
        month_tok = fm.group("m") if fm else None
    month = MONTHS.get((month_tok or "").lower())
    if not month:
        return None
    try:
        return date(year, month, int(m.group("d"))).isoformat()
    except ValueError:
        return None


# A restaurant may leave the program early, so the window opens well before the
# headline deadline; 60 days covers every pre-BOOK_BY week a season can have.
END_WINDOW_LEAD_DAYS = 60


def assert_end_dates_in_window(rows, book_by, program_end):
    """Every non-null end_date must land in [book_by - 60 days, program_end].

    A date outside that window means the roster and config/season.json disagree
    about which season this is -- which is exactly what a stale SEASON_YEAR
    produces, silently, on every row.
    """
    lo = (date.fromisoformat(book_by)
          - timedelta(days=END_WINDOW_LEAD_DAYS)).isoformat()
    bad = [(r["slug"], r["end_date"]) for r in rows
           if r.get("end_date") and not lo <= r["end_date"] <= program_end]
    if bad:
        raise SystemExit(
            f"END DATE OUT OF SEASON [{lo} .. {program_end}] -- season.json stale?"
            "\n  " + "\n  ".join(f"{s}: {d}" for s, d in bad[:20]))
    return True


# --------------------------------------------------------------------------
# dish tags
# --------------------------------------------------------------------------

def load_tag_rules():
    """tag -> [compiled regex]. Keys starting with '_' are documentation."""
    cfg = json.loads(TAGS_CONFIG.read_text(encoding="utf-8"))
    return {tag: [re.compile(r["pattern"], re.I) for r in rules]
            for tag, rules in cfg.items() if not tag.startswith("_")}


def recover_keyword(snippet, patterns):
    """The matched keyword is not stored. Re-run the tag's rules and pick the
    match nearest the snippet centre (snippets are built centred on the hit),
    preferring the longest span on a tie."""
    best, centre = None, len(snippet) / 2
    for pat in patterns:
        for m in pat.finditer(snippet):
            if not m.group(0).strip():
                continue
            score = (abs((m.start() + m.end()) / 2 - centre), -(m.end() - m.start()))
            if best is None or score < best[0]:
                best = (score, m.start(), m.end())
    return (snippet[best[1]:best[2]], best[1]) if best else (None, None)


def recentre(snippet, start, length, pad=SNIPPET_PAD):
    """Re-centre on the keyword at `pad` chars each side, snapped to word
    boundaries. Never a right-truncation of the stored 143-char snippet."""
    if start is None:
        return clean(snippet[:pad * 2])[: pad * 2]
    lo, hi = max(0, start - pad), min(len(snippet), start + length + pad)
    if lo > 0:
        sp = snippet.find(" ", lo)
        lo = sp + 1 if 0 <= sp < start else lo
    if hi < len(snippet):
        sp = snippet.rfind(" ", start + length, hi)
        hi = sp if sp > start + length else hi
    out = clean(snippet[lo:hi])
    if lo > 0:
        out = "…" + out
    if hi < len(snippet):
        out = out + "…"
    return out


def _overlaps(a, b, run=OVERLAP_RUN):
    """True if the two snippets share a contiguous run of `run` characters."""
    a2, b2 = re.sub(r"\W+", " ", a.lower()), re.sub(r"\W+", " ", b.lower())
    if len(a2) < run or len(b2) < run:
        return False
    return any(a2[i:i + run] in b2 for i in range(len(a2) - run + 1))


def build_tags(con, rules):
    """slug -> [tag hit]. Snippets are re-centred, capped, overlap-pruned,
    budgeted against the menu's total length, and forced apart in the SOURCE
    text, so the export cannot be reassembled into a contiguous menu passage.

    raw_text is read here only to compute those positions. It is never emitted.

    Candidates are walked per (restaurant, tag) and the FIRST one that survives
    every guard is published. That grouping is the point: this used to walk the
    rows flat, and a rejected first candidate was appended with snippet=None to
    keep the tag filterable -- which then counted as "the tag is represented"
    and locked out every later candidate for it. 152 tag/restaurant pairs went
    out with no text behind 345 candidate rows, and none of the rejections were
    about those later candidates. A snippet is refused for overlapping one
    already published, for sitting within MIN_SOURCE_GAP of one in the same
    menu, or for not fitting what is left of the coverage budget; a hit
    somewhere else on the menu answers all three.

    Nothing here relaxes a guard. Every candidate is checked against the same
    overlap rule, the same source-position gap and the same budget, and the
    budget is what actually bounds how much of a menu can be published.
    """
    by_slug, dropped = {}, 0
    raw_by_slug = {s: re.sub(r"\s+", " ", (t or "")).strip()
                   for s, t in con.execute(
                       "SELECT restaurant_slug, raw_text FROM menus")}
    spans = {}      # slug -> [(start, end)] of already-published source spans
    # Confidence leads the ordering within a tag: only MAX_SNIPPETS_PER_TAG
    # hits survive per tag, so whichever sorts first *represents* the tag in
    # the UI. Sorting by source alone let a low-confidence hit mask a high one
    # on the same menu -- Yakiniku Futago reads "low confidence" off a bare
    # "Negi Toro" while its Ootoro and Maguro (both high) sit unused two lines
    # away.
    rows = con.execute(
        "SELECT restaurant_slug, tag, confidence, matched_text, source "
        "FROM menu_item_tags ORDER BY restaurant_slug, tag, "
        "CASE confidence WHEN 'high' THEN 0 ELSE 1 END, "
        "CASE source WHEN 'item' THEN 0 ELSE 1 END, length(matched_text)").fetchall()

    for (slug, tag), group in itertools.groupby(rows, key=lambda r: (r[0], r[1])):
        candidates = [r for r in group if r[3]]
        if tag not in rules or not candidates:
            continue
        kept = by_slug.setdefault(slug, [])
        published = 0
        best_conf, best_kw = None, None

        for _, _, conf, matched, source in candidates:
            if published >= MAX_SNIPPETS_PER_TAG:
                dropped += 1
                continue

            kw, at = recover_keyword(matched, rules[tag])
            snip = recentre(matched, at, len(kw) if kw else 0)
            if best_conf is None:
                best_conf, best_kw = conf, kw

            reject = False

            # (a) textual overlap with anything already kept for this
            # restaurant -- two different tags can fire on one passage, and
            # publishing both would republish it contiguously.
            if any(_overlaps(snip, t["snippet"]) for t in kept if t["snippet"]):
                reject = True

            # (b) source-position budget: where the snippet sits in the menu --
            raw = raw_by_slug.get(slug, "")
            used = spans.setdefault(slug, [])
            budget = max(COVERAGE_FLOOR, int(len(raw) * COVERAGE_CAP))
            remaining = budget - sum(e - s for s, e in used)

            if not reject and raw:
                # Shrink the padding until the snippet fits what is left.
                pad = SNIPPET_PAD
                while pad > MIN_PAD and len(snip.strip("…").strip()) > remaining:
                    pad -= 4
                    snip = recentre(matched, at, len(kw) if kw else 0, pad=pad)
                core = snip.strip("…").strip()
                if len(core) > remaining:
                    reject = True
                else:
                    at_src = raw.find(core)
                    if at_src >= 0:
                        end_src = at_src + len(core)
                        if any(at_src < e + MIN_SOURCE_GAP
                               and s - MIN_SOURCE_GAP < end_src for s, e in used):
                            reject = True
                        else:
                            used.append((at_src, end_src))
                    else:
                        used.append((-1, -1 + len(core)))  # counts against budget

            if reject:
                dropped += 1
                continue        # try the next candidate for this same tag

            kept.append({"tag": tag, "confidence": conf, "keyword": clean(kw),
                         "snippet": snip, "source": source})
            published += 1

        if not published:
            # Nothing on this menu could be published for the tag. Keep the tag
            # so it stays filterable and searchable, with the strongest match's
            # confidence and no text -- which is what the row and the facet
            # already know how to render.
            #
            # The KEYWORD is menu text too, and it used to go out here
            # unbudgeted. Several tag rules bridge two words with
            # `[^.\n]{0,40}`, so the matched span is a phrase, not a term:
            # "nigiri accompanied by a tuna", "CEVICHE AMARILLO* 22 tuna",
            # "Tuna & Avocado Carpaccio". 139 of these were published with no
            # snippet, i.e. exactly where the budget had just refused to let
            # any more of that menu out -- and counting them put 38
            # restaurants over the 5%/40-char rule this project states.
            #
            # So it is budgeted like a snippet: it goes out only if what is
            # left of the menu's allowance can pay for it, and only if it does
            # not sit inside MIN_SOURCE_GAP of something already published.
            # Otherwise the tag keeps everything it needs to be found -- its
            # name, its confidence -- and loses only the fragment.
            kw = clean(best_kw)
            core = (kw or "").strip()
            raw = raw_by_slug.get(slug, "")
            used = spans.setdefault(slug, [])
            if core and raw:
                budget = max(COVERAGE_FLOOR, int(len(raw) * COVERAGE_CAP))
                remaining = budget - sum(e - s for s, e in used)
                at_src = raw.find(core)
                too_close = (at_src >= 0 and any(
                    at_src < e + MIN_SOURCE_GAP
                    and st - MIN_SOURCE_GAP < at_src + len(core)
                    for st, e in used))
                if len(core) > remaining or too_close:
                    kw = None
                    dropped += 1
                elif at_src >= 0:
                    used.append((at_src, at_src + len(core)))
                else:
                    used.append((-1, -1 + len(core)))
            kept.append({"tag": tag, "confidence": best_conf,
                         "keyword": kw, "snippet": None,
                         "source": candidates[0][4]})
    return by_slug, dropped


# --------------------------------------------------------------------------
# recognition
# --------------------------------------------------------------------------

def load_suppression():
    if not SUPPRESS.exists():
        return set()
    cfg = json.loads(SUPPRESS.read_text(encoding="utf-8"))
    return {(e["slug"], e["source"], e["matched_name"])
            for e in cfg.get("suppress", []) if e.get("active", True)}


def load_jb_awards():
    """(url, level, year, restaurant) -> [award names]. The award name exists
    ONLY in the raw file; the DB never carries it."""
    if not JB_RAW.exists():
        return {}
    idx = {}
    for rec in json.loads(JB_RAW.read_text(encoding="utf-8")):
        key = (rec.get("url"), rec.get("level"), rec.get("year"),
               (rec.get("restaurant") or "").strip().lower())
        label = rec.get("award")
        if rec.get("name"):
            label = f"{label} — {rec['name']}" if label else rec["name"]
        if label:
            idx.setdefault(key, []).append(label)
    return idx


NYT_RANK_RE = re.compile(r"Ranked No\.\s*(\d+)", re.I)

# --------------------------------------------------------------------------
# Distinction: how strong an award is, and how old.
#
# The UI used to collapse both. Every badge rendered as its SOURCE -- so a
# 1991 James Beard semifinalist and a 2026 winner were both "James Beard", and
# 38 of the 42 Michelin-only restaurants carry nothing but The Plate, the
# lowest rung, while reading identically to a starred kitchen.
#
# Strongest first. Ordering judgement, stated so it can be argued with: a James
# Beard WIN outranks a Bib Gourmand (a national award for the kitchen beats a
# value commendation), and a semifinalist nod -- a real longlist -- edges out
# The Plate, which only means an inspector ate there and thought it was good.
PRESTIGE = [
    ("michelin",    r"\d+\s*stars?",  "Michelin star"),
    ("nyt",         None,             "NYT 100 Best"),
    ("james_beard", r"winner",        "James Beard winner"),
    ("michelin",    r"bib gourmand",  "Michelin Bib Gourmand"),
    ("james_beard", r"nominee",       "James Beard nominee"),
    ("james_beard", r"semifinalist",  "James Beard semifinalist"),
    ("michelin",    r"the plate",     "Michelin Plate"),
]

# Michelin and NYT are ANNUAL GUIDES: appearing in the 2025 edition is itself
# the current judgement, so their era is always "current" for the edition we
# hold. James Beard is an EVENT -- the honour never expires, but it ages, and
# that is the distinction this whole block exists to make visible.
ERA_CURRENT, ERA_RECENT, ERA_PAST = 1, 5, 15
ERA_LABEL = {"current": "This year or last", "recent": "Last 5 years",
             "past": "Past decade", "historic": "Over 15 years ago"}
ERA_ORDER = {"current": 0, "recent": 1, "past": 2, "historic": 3, None: 4}


def prestige_of(source, level_raw, level):
    """-> (rank, label). Lower rank is a stronger distinction."""
    hay = f"{level_raw or ''} {level or ''}".lower()
    for i, (src, pat, label) in enumerate(PRESTIGE):
        if src != source:
            continue
        if pat is None or re.search(pat, hay):
            return i, label
    return len(PRESTIGE), RECOG_SOURCE_LABEL.get(source, source)


def era_of(year):
    if year is None:
        return None
    age = SEASON_YEAR - year
    return ("current" if age <= ERA_CURRENT else "recent" if age <= ERA_RECENT
            else "past" if age <= ERA_PAST else "historic")


def build_recognition(con, suppressed, jb_awards):
    """slug -> [badge]. Deduped on (source, level, year) so a restaurant with 21
    rows does not render 'Nominee 1996' three times; the distinct awards behind
    a duplicate are folded into that badge's award list."""
    by_slug, dropped = {}, 0
    rows = con.execute(
        "SELECT restaurant_slug, source, level, year, source_url, match_confidence,"
        " matched_name, notes FROM recognition "
        "ORDER BY restaurant_slug, source, year DESC").fetchall()
    for slug, source, level, year, url, conf, matched, notes in rows:
        if (slug, source, matched) in suppressed:
            dropped += 1
            continue
        badges = by_slug.setdefault(slug, {})
        key = (source, level, year)
        if source == "michelin":
            label = MICHELIN_LEVEL_LABEL.get(level, level)
        elif source == "nyt":
            label = NYT_LEVEL_LABEL.get(level, level)
        else:
            label = (level or "").replace("_", " ")

        badge = badges.get(key)
        if badge is None:
            badge = {
                "source": source,
                "source_label": RECOG_SOURCE_LABEL.get(source, source),
                "level": label,
                "level_raw": level,
                "year": year,
                "url": url,
                "matched_name": clean(matched),
                # JBF publishes no addresses, so every JB row (and every NYT
                # row) is a name-only match. Surfaced as a hint in the UI.
                "name_match_only": bool(conf is not None and conf < 1.0),
                "confidence": conf,
                "awards": [],
            }
            if source == "nyt":
                m = NYT_RANK_RE.search(notes or "")
                if m:
                    badge["rank"] = int(m.group(1))
                # The stored url is a third-party reproduction, not nytimes.com.
                badge["via"] = "secretnyc.co reproduction"
            badges[key] = badge
        for a in jb_awards.get((url, level, year, (matched or "").strip().lower()), []):
            if a not in badge["awards"]:
                badge["awards"].append(a)

    for badges in by_slug.values():
        for b in badges.values():
            rank, label = prestige_of(b["source"], b["level_raw"], b["level"])
            b["tier_rank"], b["tier"] = rank, label
            b["era"] = "current" if b["source"] != "james_beard" else era_of(b["year"])

    # Strongest first, then most recent -- so badges[0] is the headline and the
    # row pill can show it without the UI re-deriving the ranking.
    return ({s: sorted(b.values(),
                       key=lambda x: (x["tier_rank"], ERA_ORDER[x["era"]],
                                      -(x["year"] or 0)))
             for s, b in by_slug.items()}, dropped)


# --------------------------------------------------------------------------
# price comparables (heuristic only)
# --------------------------------------------------------------------------

def build_price(con):
    """slug -> heuristic estimate, or nothing. Never a 'verified' value.

    `gaps` values are read verbatim: they are rounded independently from
    comparable_3course, so recomputing (comparable - tier) disagrees by $1 on
    44 of 588 values. The chosen tier is always reported alongside the gap --
    the largest gap is always the CHEAPEST tier, which would silently inflate
    the headline if left unlabelled.
    """
    out = {}
    for slug, comp, gaps_raw, conf in con.execute(
            "SELECT restaurant_slug, comparable_3course, gaps, confidence "
            "FROM price_sweep"):
        # confidence low/none renders blank; 'none' means the fetch timed out.
        if conf in ("low", "none") or comp is None:
            continue
        gaps = jload(gaps_raw)
        if not isinstance(gaps, dict) or not gaps:
            continue
        tier, gap = max(gaps.items(), key=lambda kv: (kv[1], -int(kv[0].lstrip("$"))))
        price = int(tier.lstrip("$"))
        out[slug] = {
            "gap_usd": gap,
            "gap_pct": round(gap / comp * 100) if comp else None,
            "tier": tier,
            "rw_price": price,
            "comparable_usd": comp,
            "confidence": conf,
            "all_gaps": gaps,
        }
    return out


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def build_payload():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.text_factory = str

    rules = load_tag_rules()
    tags_by_slug, tags_dropped = build_tags(con, rules)
    # Read once more for the budget guard below. Cheap next to the export, and
    # keeping it out of build_tags' return value keeps that function's contract
    # about tags rather than about menus.
    raw_by_slug = {s: re.sub(r"\s+", " ", (t or "")).strip()
                   for s, t in con.execute(
                       "SELECT restaurant_slug, raw_text FROM menus")}
    recog_by_slug, recog_dropped = build_recognition(
        con, load_suppression(), load_jb_awards())
    price_by_slug = build_price(con)

    stations = load_stations()
    licences = load_outdoor()
    offsite, _ = build_offsite()
    google, google_mean = build_google()
    verified = json.loads(VERIFIED.read_text(encoding="utf-8"))["restaurants"]
    # Checked before a single row is built: a figure the dashboard prints as
    # verified must reconcile, and finding out at render time is too late.
    assert_verified_gaps_reconcile(verified)
    menus = {r[0]: r[1] for r in con.execute(
        "SELECT restaurant_slug, parse_quality FROM menus")}

    out, stats = [], {"verified": 0, "estimate": 0, "none": 0, "urgent": 0,
                      "outdoor_licensed": 0, "outdoor_described_only": 0}

    for row in con.execute(
            "SELECT slug, name, borough, neighborhood, address, lat, lng, cuisines,"
            " price_tiers, meal_periods, meal_types_raw, weeks, sunday_participation,"
            " menu_url, website, reservation_link, listing_url, summary"
            " FROM restaurants ORDER BY name"):
        (slug, name, borough, hood, address, lat, lng, cuisines, tiers, periods,
         raw_types, weeks, sunday_api, menu_url, website, res_link, listing,
         summary) = row

        tiers = jload(tiers, [])
        v = verified.get(slug, {})

        # --- window -----------------------------------------------------
        api_end = end_date_from_weeks(jload(weeks, []))
        # api_fallback is checked FIRST and always takes the LIVE listing value.
        # Those entries mean "the restaurant prints no date"; the date written
        # into verified_values.json was only a copy of the API at transcription
        # time, and honouring it would freeze a stale date after the API moves.
        if v.get("end_date_source") == "api_fallback":
            end_date, end_src = api_end, "api_fallback"
        elif v.get("end_date"):
            end_date, end_src = v["end_date"], v.get("end_date_source", "printed")
        else:
            end_date, end_src = api_end, "api"

        # --- sunday -----------------------------------------------------
        if v.get("sunday_verified") is not None:
            sunday, sunday_src = v["sunday_verified"], "verified"
        else:
            sunday, sunday_src = bool(sunday_api), "api"

        # --- gap: verified beats heuristic, heuristic is always labelled --
        est = price_by_slug.get(slug)
        if v.get("gap_usd") is not None:
            gap = {
                "gap_usd": v["gap_usd"], "gap_usd_high": v.get("gap_usd_high"),
                "gap_pct": v.get("gap_pct"), "gap_pct_high": v.get("gap_pct_high"),
                "gap_basis": "verified", "comparable_usd": v.get("comparable_usd"),
                "comparable_usd_high": v.get("comparable_usd_high"),
                "rw_price": v.get("rw_price"),
                # only "verified" if the PRICE itself was verified -- otherwise
                # the tier backfill below supplies it from the listing
                "price_source": "verified" if v.get("rw_price") else None,
            }
            stats["verified"] += 1
        # A verified entry only blocks the estimate if it actually says
        # something about price. Entries that record e.g. Saturday service or a
        # Sunday correction shouldn't strip a restaurant of its estimate.
        elif (est and not v.get("rw_price")
              and "dropped_in_verification" not in v.get("flags", [])):
            gap = {
                "gap_usd": est["gap_usd"], "gap_usd_high": None,
                "gap_pct": est["gap_pct"], "gap_pct_high": None,
                "gap_basis": "estimate", "comparable_usd": est["comparable_usd"],
                "comparable_usd_high": None,
                "rw_price": est["rw_price"], "price_source": "listing",
                "estimate_tier": est["tier"], "estimate_confidence": est["confidence"],
            }
            stats["estimate"] += 1
        else:
            # A verified entry without a numeric gap (e.g. Yingtao) stays blank
            # rather than falling back to a heuristic figure.
            gap = {
                "gap_usd": None, "gap_usd_high": None, "gap_pct": None,
                "gap_pct_high": None, "gap_basis": None,
                "comparable_usd": None, "comparable_usd_high": None,
                "rw_price": v.get("rw_price"),
                "price_source": "verified" if v.get("rw_price") else None,
            }
            stats["none"] += 1

        if gap["rw_price"] is None and tiers:
            gap["rw_price"] = min(int(t.lstrip("$")) for t in tiers)
            gap["price_source"] = gap["price_source"] or "listing"

        # --- verdict (categorical, derived from provenance) -------------
        if "dropped_in_verification" in v.get("flags", []):
            verdict = "Dropped in verification"
        elif v.get("rank"):
            verdict = "Ranked pick"
        elif v:
            verdict = "Verified note"
        elif gap["gap_basis"] == "estimate":
            verdict = "Estimate only"
        else:
            verdict = "No comparable"

        # menu_url is the EMPTY STRING (never NULL) when no menu is published.
        quality = menus.get(slug)
        menu_state = ("none" if not (menu_url or "").strip()
                      else "image_only" if quality == "failed" else "pdf")

        if end_date and end_date <= BOOK_BY:
            stats["urgent"] += 1

        recog = recog_by_slug.get(slug, [])

        glat, glng = sane_coords(lat, lng)
        subway, sub_near = subway_for(glat, glng, stations)
        if lat is not None and glat is None:
            stats["bad_geo"] = stats.get("bad_geo", 0) + 1

        # --- outdoor seating --------------------------------------------
        # Only the boolean survives from the blurb; the sentence itself is
        # listing copy and stays out of the payload (see assert_tos_clean).
        outdoor = outdoor_for(clean(name), address, glat, glng, licences)
        described = bool(_OUTDOOR_RE.search(summary or ""))
        if outdoor or described:
            outdoor = {
                "sidewalk": bool(outdoor and outdoor["sidewalk"]),
                "roadway": bool(outdoor and outdoor["roadway"]),
                "licensed": bool(outdoor),
                "described": described,
                "licence_name": outdoor["licence_name"] if outdoor else None,
                "dist_m": outdoor["dist_m"] if outdoor else None,
            }
            stats["outdoor_licensed"] += bool(outdoor["licensed"])
            stats["outdoor_described_only"] += (described and not outdoor["licensed"])
        else:
            outdoor = None

        out.append({
            "slug": slug,
            "name": clean(name),
            "borough": borough,
            "neighborhood": hood,
            "address": clean(address),
            "lat": glat, "lng": glng,
            "subway": subway,
            "subway_nearest": sub_near,
            "outdoor": outdoor,
            "cuisines": jload(cuisines, []),
            "price_tiers": tiers,
            "meal_periods": jload(periods, []),
            "meal_types_raw": jload(raw_types, []),
            "end_date": end_date,
            "end_date_source": end_src,
            "end_date_api": api_end,
            "days": v.get("days"),
            "sunday": sunday,
            "sunday_source": sunday_src,
            "sunday_api": bool(sunday_api),
            "courses": v.get("courses"),
            "rank": v.get("rank"),
            "grade": v.get("grade"),
            "verdict": verdict,
            "verdict_note": v.get("verdict"),
            "notes": v.get("notes"),
            "flags": v.get("flags", []),
            "menu_state": menu_state,
            "recognition": recog,
            # Headline distinction + the eras present, so the row pill, the
            # facets and the sort all read the same ranking.
            "recog_top": ({"tier": recog[0]["tier"], "tier_rank": recog[0]["tier_rank"],
                           "year": recog[0]["year"], "era": recog[0]["era"],
                           "source": recog[0]["source"]} if recog else None),
            "recog_rank": recog[0]["tier_rank"] if recog else None,
            "recog_eras": sorted({b["era"] for b in recog if b["era"]},
                                 key=lambda e: ERA_ORDER[e]),
            "tags": tags_by_slug.get(slug, []),
            "offsite_tags": offsite.get(slug, []),
            "google": google.get(slug),
            "links": {
                "listing": listing or None,
                "menu": (menu_url or "").strip() or None,
                "reservation": v.get("booking_url") or res_link or None,
                "website": (website or "").strip() or None,
            },
            **gap,
        })

    con.close()

    assert_end_dates_in_window(out, BOOK_BY, PROGRAM_END)
    # Measured against the menus themselves, at the figure the README states.
    assert_snippet_budget(out, raw_by_slug)

    # --- rubric: a second pass, because the rating component is a PERCENTILE
    # and so cannot be known until every restaurant has been read.
    rcfg = load_rubric()
    today = scoring_day()
    scored = sorted((r["google"]["score"] for r in out
                     if r.get("google") and r["google"].get("score") is not None))
    for r in out:
        g = r.get("google")
        if g and g.get("score") is not None and scored:
            below = sum(1 for x in scored if x < g["score"])
            r["_rating_pct"] = 100.0 * below / len(scored)
        else:
            r["_rating_pct"] = None          # unknown, not bad
    raw_parts = {}
    for r in out:
        raw_parts[r["slug"]] = rubric_for(r, rcfg, today)
        r.pop("_rating_pct", None)
    # Component means, over the restaurants that actually have each component.
    means = {}
    for k in rcfg["weights"]:
        vals = [pp[k] for pp in raw_parts.values() if pp.get(k) is not None]
        means[k] = sum(vals) / len(vals) if vals else 50.0
    for r in out:
        sc, parts, comp = score_parts(raw_parts[r["slug"]], rcfg, means)
        r["rubric"] = sc
        r["rubric_parts"] = parts
        r["rubric_completeness"] = comp
        r["rubric_imputed"] = [k for k, v in parts.items() if v is None]

    rubric_means = {k: round(v, 1) for k, v in means.items()}

    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season_label": SEASON_LABEL,
        # the code identifies which season this payload IS, so a reader that
        # holds several can tell them apart; start lets the archive state name
        # the whole run rather than only the day it stopped
        "season_code": SEASON,
        "season_start": SEASON_START,
        "snapshot_date": con_snapshot(),
        "verified_asof": verified_asof(json.loads(VERIFIED.read_text(encoding="utf-8"))
                                       ["_doc"]["provenance"]),
        "book_by": BOOK_BY,
        "program_end": PROGRAM_END,
        "tag_vocabulary": sorted(rules),
        "rubric_weights": load_rubric()["weights"],
        "rubric_component_means": rubric_means,
        "rubric_mean": round(sum(r["rubric"] for r in out if r["rubric"] is not None)
                             / max(1, sum(1 for r in out if r["rubric"] is not None)), 1),
        "google_mean": round(google_mean, 3),
        "google_prior": GOOGLE_PRIOR,
        "restaurants": out,
    }
    return payload, stats, tags_dropped, recog_dropped


VERIFIED_ASOF_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def verified_asof(provenance):
    """The transcription date out of _doc.provenance, found by shape not by
    position -- the sentence is prose, and rewording it used to silently change
    a published date rather than fail."""
    m = VERIFIED_ASOF_RE.search(provenance or "")
    if not m:
        raise SystemExit(
            "verified_values.json _doc.provenance carries no ISO date "
            "(YYYY-MM-DD); verified_asof cannot be published without one.")
    return m.group(0)


def con_snapshot():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("SELECT DISTINCT snapshot_date FROM restaurants "
                      "ORDER BY snapshot_date DESC LIMIT 1").fetchone()
    con.close()
    return row[0] if row else None


# --------------------------------------------------------------------------
# ToS guard
# --------------------------------------------------------------------------

BANNED_KEYS = {"raw_text", "menu_items", "dish", "description", "summary",
               "matched_text", "supplement_price", "course"}


def published_menu_chars(tag_hit):
    """Characters of MENU TEXT a published tag hit carries.

    The snippet when there is one -- the keyword sits inside it, because
    recentre() builds the snippet around the keyword, so counting both would
    double-count. The keyword alone when there is not: it is a span of the
    matched menu text either way, and several tag rules bridge two words with
    `[^.\n]{0,40}`, so that span is routinely a phrase.
    """
    snip = (tag_hit.get("snippet") or "").strip("\u2026").strip()
    if snip:
        return len(snip)
    return len((tag_hit.get("keyword") or "").strip())


def assert_snippet_budget(rows, raw_by_slug):
    """THE RULE, enforced rather than merely intended.

    At most 5% of a menu's extracted text, or 40 characters, whichever is
    greater. build_tags applies a tighter internal cap (COVERAGE_CAP /
    COVERAGE_FLOOR) so an independent auditor measuring at the stated figure
    always has margin; this measures at the STATED figure, over the payload as
    it will actually be written, and refuses to publish if it is breached.

    It exists because the tighter internal cap was applied to snippets only.
    Keywords went out unbudgeted, and 38 restaurants were over the stated rule
    with nothing failing -- the guard and the thing being guarded had drifted
    apart, which a test in this file would not have caught either, because it
    would have measured the same way build_tags did.
    """
    over = []
    for r in rows:
        raw = raw_by_slug.get(r["slug"], "")
        if not raw:
            continue
        used = sum(published_menu_chars(t) for t in r.get("tags", []))
        cap = max(40, len(raw) * 0.05)
        if used > cap:
            over.append(f"{r['slug']}: {used} chars published against a cap of "
                        f"{cap:.0f} ({len(raw)} chars of menu)")
    if over:
        raise AssertionError(
            "ToS: published menu text exceeds 5% of the menu (or 40 chars, "
            "whichever is greater):\n  " + "\n  ".join(over[:20]))
    return True


def assert_tos_clean(payload):
    """Fail loudly rather than publish anything the ToS forbids."""
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, val in node.items():
                if k in BANNED_KEYS:
                    problems.append(f"banned key {path}.{k}")
                walk(val, f"{path}.{k}")
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{path}[{i}]")

    walk(payload, "$")

    # Both snippet sources face the same length and overlap bar. They are
    # checked SEPARATELY because they quote different documents -- the RW menu
    # PDF and the restaurant's own website -- so a run shared across the two is
    # not a contiguous passage of either.
    for r in payload.get("restaurants", payload.get("places", [])):
        for field in ("tags", "offsite_tags"):
            seen = []
            for t in r.get(field, []):
                snip = t.get("snippet")
                if not snip:
                    continue
                if len(snip) > SNIPPET_PAD * 2 + 60:
                    problems.append(
                        f"{r['slug']}: {field} snippet too long ({len(snip)})")
                if any(_overlaps(snip, s) for s in seen):
                    problems.append(
                        f"{r['slug']}: overlapping {field} snippets survived")
                seen.append(snip)
    if problems:
        raise SystemExit("ToS CHECK FAILED:\n  " + "\n  ".join(problems[:20]))
    return True


SHRINK_FLOOR = 0.80


def assert_not_shrunk(old_count, new_count, allow=False):
    """Refuse to replace a published roster with one under 80% of its size.

    A half-fetched listing or a partly built DB produces a perfectly valid
    payload that is simply too small, and the weekly job would commit it without
    a murmur. --allow-shrink is the deliberate override for the season that
    really did lose that many.
    """
    if allow or not old_count or new_count >= old_count * SHRINK_FLOOR:
        return True
    raise SystemExit(
        f"REFUSING TO SHRINK the payload: {old_count} -> {new_count} restaurants"
        f" ({new_count / old_count:.0%} of what is published)."
        " Re-run the fetch, or pass --allow-shrink if the drop is real.")


# --------------------------------------------------------------------------
# Seasons registry
#
# One payload per season under docs/data/seasons/, indexed by docs/data/seasons.json.
# A run speaks only for its OWN code: every other entry is copied through
# untouched, so the Winter build cannot quietly rewrite the Summer archive.
# --------------------------------------------------------------------------

REGISTRY_KEYS = ("code", "label", "year", "start", "book_by", "end")
SEASON_FACTS = {"code": SEASON, "label": SEASON_LABEL, "year": SEASON_YEAR,
                "start": SEASON_START, "book_by": BOOK_BY, "end": PROGRAM_END}


def season_paths(out=None):
    """(season payload, registry), derived from OUT so redirecting it moves all three."""
    d = (out or OUT).parent
    return d / "seasons" / f"{SEASON}.json", d / "seasons.json"


def rel(path):
    """Repo-relative when it can be, absolute when the path is a test's tmp dir."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def jfile(path, default=None):
    """Parsed JSON, or `default` when the file is missing or unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _unstamped(d):
    return {k: v for k, v in d.items() if k != "generated_at"}


def write_if_changed(path, obj, text):
    """-> True when written. `generated_at` is a wall-clock stamp, so comparing
    with it would rewrite every file on every run and the weekly Actions job
    would commit churn forever."""
    old = jfile(path)
    if isinstance(old, dict) and _unstamped(old) == _unstamped(obj):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # build in a temp dir then copy (some mounted filesystems break unlink)
    tmp = Path(tempfile.mkdtemp()) / path.name
    tmp.write_text(text, encoding="utf-8")
    shutil.copyfile(tmp, path)
    return True


def update_registry(existing, facts, today):
    """Merge one season's entry into the registry, newest end date first."""
    day = today.isoformat() if hasattr(today, "isoformat") else str(today)
    entry = {k: facts[k] for k in REGISTRY_KEYS}
    entry["status"] = "archived" if day > facts["end"] else "live"
    entry["file"] = f"seasons/{facts['code']}.json"
    others = [e for e in (existing or []) if e.get("code") != facts["code"]]
    return sorted([*others, entry], key=lambda e: (e["end"], e["code"]), reverse=True)


def main():
    check = "--check" in sys.argv
    quiet = "--quiet" in sys.argv
    allow_shrink = "--allow-shrink" in sys.argv

    payload, stats, tags_dropped, recog_dropped = build_payload()
    assert_tos_clean(payload)

    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    season_out, registry_out = season_paths()
    registry = update_registry((jfile(registry_out) or {}).get("seasons"),
                               SEASON_FACTS, date.today())
    reg_obj = {"seasons": registry}
    reg_text = json.dumps(reg_obj, ensure_ascii=False, indent=1) + "\n"

    # The season file is what this run published last time; the legacy copy is
    # only the fallback for a cached frontend, so it cannot set the floor.
    published = jfile(season_out) or jfile(OUT) or {}
    old_n = len(published.get("restaurants", []))

    wrote = []
    if not check:
        assert_not_shrunk(old_n, len(payload["restaurants"]), allow_shrink)
        wrote = [p for p, o, t in ((season_out, payload, text),
                                   # LEGACY, TEMPORARY: dropped once the frontend
                                   # reads seasons/ -- until then it is the file
                                   # every published page actually fetches.
                                   (OUT, payload, text),
                                   (registry_out, reg_obj, reg_text))
                 if write_if_changed(p, o, t)]

    if not quiet:
        n = len(payload["restaurants"])
        ranked = sum(1 for r in payload["restaurants"] if r["rank"])
        tagged = sum(1 for r in payload["restaurants"] if r["tags"])
        badged = sum(1 for r in payload["restaurants"] if r["recognition"])
        print(f"restaurants   {n}")
        print(f"  verified gap  {stats['verified']}")
        print(f"  estimate gap  {stats['estimate']}")
        print(f"  no comparable {stats['none']}")
        print(f"  ranked picks  {ranked}")
        print(f"  ending by {payload['book_by']}  {stats['urgent']}")
        mapped = sum(1 for r in payload["restaurants"] if r["lat"] and r["lng"])
        print(f"mappable      {mapped}/{n} ({n - mapped} without usable coordinates,"
              f" incl. {stats.get('bad_geo', 0)} geocoded outside NYC)")
        near = sum(1 for r in payload["restaurants"] if r["subway"])
        lex = sum(1 for r in payload["restaurants"]
                  if any(k in r["subway"] for k in ("4", "5", "6")))
        print(f"subway        {near} within {MAX_WALK_MIN} min of a station"
              f" · {lex} near the 4/5/6")
        print(f"outdoor       {stats['outdoor_licensed']} in the city register"
              f" · {stats['outdoor_described_only']} described only"
              f" · {len(payload['restaurants']) - stats['outdoor_licensed'] - stats['outdoor_described_only']} unknown")
        g = [r["google"] for r in payload["restaurants"] if r["google"]]
        print(f"google        {len(g)} rated · mean {payload['google_mean']}* · shrunk toward it with m={GOOGLE_PRIOR}")
        off = sum(1 for r in payload["restaurants"] if r["offsite_tags"])
        swept = len(list(MENUSWEEP.glob("*.json"))) if MENUSWEEP.exists() else 0
        print(f"offsite tags  {off} restaurants (own websites; {swept} swept)")
        print(f"tags          {tagged} restaurants ({tags_dropped} snippets pruned)")
        print(f"recognition   {badged} restaurants ({recog_dropped} rows suppressed)")
        where = ("  (not written: --check)" if check
                 else "  (unchanged, not rewritten)" if not wrote
                 else f"  ({len(wrote)} of 3 files rewritten)")
        print(f"payload       {len(text.encode('utf-8')):,} bytes{where}")
        mine = next(s for s in registry if s["code"] == SEASON)
        print(f"  season      {rel(season_out)}  ({mine['status']})")
        print(f"  registry    {rel(registry_out)}  ({len(registry)} season(s))")
        print(f"  legacy      {rel(OUT)}"
              "  -- temporary, until the frontend reads seasons/")


if __name__ == "__main__":
    main()
