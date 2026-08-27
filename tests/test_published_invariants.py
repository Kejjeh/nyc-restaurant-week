"""What the site claims, checked against what it holds.

Every bug in this audit's first three waves was found the same way: not by
reading code, but by asking whether a published number agreed with the data it
came from. None of them raised, errored or logged anything.

  wave 1  the roster plotted three restaurants in California and Texas
  wave 1  29 rows printed a comparable and a gap that did not subtract
  wave 1  a verified row printed 57 - 45 = 27
  wave 3  a restaurant was credited with another restaurant's roadway licence

So the questions are asked every run now, against the real payloads, rather than
once by hand. Wave 4 asked four more and found nothing -- those are here too,
because a check that passes today is what catches tomorrow's regression, and
the expensive part was working out which question to ask.
"""
import json
import math
import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
sys.path.insert(0, str(ROOT / "src"))


def payload(name):
    p = ROOT / "docs" / "data" / name
    if not p.exists():
        pytest.skip(f"{name} not built")
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dash():
    return payload("restaurants.json")["restaurants"]


@pytest.fixture(scope="module")
def roster():
    return payload("venues.json")


# --------------------------------------------------------------------------
# the payload describes the database it was built from
# --------------------------------------------------------------------------

def test_the_dashboard_covers_every_restaurant_in_the_database(dash):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        db = {s for (s,) in con.execute("SELECT slug FROM restaurants")}
    finally:
        con.close()
    published = {r["slug"] for r in dash}
    assert db == published, f"missing {db - published}, extra {published - db}"


def test_the_roster_covers_every_venue_in_the_database(roster):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        db = {s for (s,) in con.execute("SELECT venue_slug FROM venues")}
    finally:
        con.close()
    assert db == {v["slug"] for v in roster["venues"]}


@pytest.mark.parametrize("count,field", [
    ("venues", None), ("in_restaurant_week", "rw"), ("mappable", "lat"),
])
def test_every_headline_count_matches_the_rows_it_summarises(roster, count, field):
    rows = roster["venues"]
    actual = len(rows) if field is None else sum(1 for v in rows if v.get(field) is not None)
    assert roster["counts"][count] == actual


# --------------------------------------------------------------------------
# numbers printed beside each other
# --------------------------------------------------------------------------

def test_no_published_gap_disagrees_with_its_comparable(dash):
    bad = [f"{r['slug']}: {r['comparable_usd']} - {r['rw_price']} != {r['gap_usd']}"
           for r in dash
           if None not in (r.get("comparable_usd"), r.get("rw_price"), r.get("gap_usd"))
           and r["comparable_usd"] - r["rw_price"] != r["gap_usd"]]
    assert not bad, bad


def test_no_published_percentage_disagrees_with_its_own_gap(dash):
    bad = []
    for r in dash:
        comp, gap, pct = r.get("comparable_usd"), r.get("gap_usd"), r.get("gap_pct")
        if None in (comp, gap, pct) or not comp:
            continue
        if abs(gap / comp * 100 - pct) > 1.0:
            bad.append(f"{r['slug']}: {gap}/{comp} is {gap / comp * 100:.0f}%, printed {pct}%")
    assert not bad, bad


# --------------------------------------------------------------------------
# geography
# --------------------------------------------------------------------------

def test_nothing_is_plotted_outside_new_york(dash, roster):
    from config import in_nyc
    stray = [r["slug"] for r in dash if r.get("lat") is not None
             and not in_nyc(r["lat"], r["lng"])]
    stray += [v["slug"] for v in roster["venues"] if v.get("lat") is not None
              and not in_nyc(v["lat"], v["lng"])]
    assert not stray, stray


def test_every_walk_time_is_recomputable_from_the_station_list(dash):
    """Not a spot check: the whole column, recomputed from the raw MTA file."""
    stations = json.loads(
        (ROOT / "data" / "raw" / "subway" / "stations.json").read_text(encoding="utf-8"))
    sts = stations["stations"] if isinstance(stations, dict) else stations
    pts = [(s["lat"], s["lng"]) for s in sts if s.get("lat") is not None]

    def metres(a1, o1, a2, o2):
        p1, p2 = math.radians(a1), math.radians(a2)
        h = (math.sin(math.radians(a2 - a1) / 2) ** 2
             + math.cos(p1) * math.cos(p2) * math.sin(math.radians(o2 - o1) / 2) ** 2)
        return 2 * 6371000 * math.asin(math.sqrt(h))

    bad = []
    for r in dash:
        if r.get("lat") is None or not r.get("subway_nearest"):
            continue
        nearest = min(metres(r["lat"], r["lng"], la, lo) for la, lo in pts)
        if abs(round(nearest * 1.3 / 80) - r["subway_nearest"]["min"]) > 1:
            bad.append(r["slug"])
    assert not bad, bad


# --------------------------------------------------------------------------
# a claim must be supported by the evidence it cites
# --------------------------------------------------------------------------

def test_outdoor_seating_is_held_by_the_licence_the_row_names(dash):
    import export_site_data as E
    licences = E.load_outdoor()
    bad = []
    for r in dash:
        o = r.get("outdoor")
        if not o or not o.get("licensed") or r.get("lat") is None:
            continue
        key = E._out_norm(o["licence_name"])
        owned = [L for L in licences
                 if E._out_norm(L["name"] or L["legal"]) == key
                 and E._haversine_m(r["lat"], r["lng"], L["lat"], L["lng"]) <= E.OUT_FAR_M]
        for kind in ("sidewalk", "roadway"):
            if o[kind] and not any(L[kind] for L in owned):
                bad.append(f"{r['slug']}: {kind} not held by {o['licence_name']!r}")
    assert not bad, bad


def test_every_offsite_tag_cites_a_source(dash):
    """These say "they serve it", from the restaurant's own site rather than the
    Restaurant Week menu, so the url is the whole basis of the claim."""
    bad = [r["slug"] for r in dash
           for t in (r.get("offsite_tags") or []) if not t.get("url")]
    assert not bad, bad


# --------------------------------------------------------------------------
# the one hard rule
# --------------------------------------------------------------------------

def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def test_published_snippets_stay_inside_the_menu_budget(dash):
    """Checked against the raw menu text, not against the exporter's own
    accounting. assert_tos_clean() measures what it decided to publish; this
    measures the published file against the menu it came from."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        raw = {s: (t or "") for s, t in con.execute(
            "SELECT restaurant_slug, raw_text FROM menus")}
    finally:
        con.close()
    bad = []
    for r in dash:
        snips = [t["snippet"] for t in (r.get("tags") or []) if t.get("snippet")]
        menu = _norm(raw.get(r["slug"], ""))
        if not snips or not menu:
            continue
        used = sum(len(_norm(s).strip("…")) for s in snips)
        cap = max(40, 0.05 * len(menu))
        if used > cap:
            bad.append(f"{r['slug']}: {used} chars published against a cap of {cap:.0f}")
    assert not bad, bad


def test_two_snippets_can_never_be_stitched_into_one_passage(dash):
    """The rule is not only about volume: snippets far apart in the menu cannot
    be reassembled into a contiguous quote, and MIN_SOURCE_GAP is what keeps
    them apart."""
    from export_site_data import MIN_SOURCE_GAP
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        raw = {s: (t or "") for s, t in con.execute(
            "SELECT restaurant_slug, raw_text FROM menus")}
    finally:
        con.close()
    bad = []
    for r in dash:
        menu = _norm(raw.get(r["slug"], ""))
        snips = [_norm(t["snippet"]).strip("…")
                 for t in (r.get("tags") or []) if t.get("snippet")]
        if len(snips) < 2 or not menu:
            continue
        at = sorted(p for s in snips if (p := menu.find(s)) >= 0)
        for a, b in zip(at, at[1:]):
            if b - a < MIN_SOURCE_GAP:
                bad.append(f"{r['slug']}: snippets {b - a} chars apart")
    assert not bad, bad


@pytest.mark.parametrize("banned", ["raw_text", "menu_items", "menu", "menus"])
def test_no_payload_row_carries_menu_text(dash, roster, banned):
    assert not [r["slug"] for r in dash if banned in r]
    assert not [v["slug"] for v in roster["venues"] if banned in v]
