"""The merge rules, pinned to the cases that actually went wrong.

Every test below is a real pair from data/raw/recognition/. They are written as
cases rather than properties because the failure mode here is silent: a wrong
merge does not raise, it produces one row where there were two restaurants, and
nobody notices until they book the wrong one.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from build_venues import (Roster, addresses_compatible, borough_from,
                          group_marker, load_awards_config, match_name_only,
                          match_with_address, merge_spelling_variants,
                          postal_key, prestige_for, resolve_group_awards,
                          slugify, spelling_variant, split_group)

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"


def roster_with(*venues):
    r = Roster()
    for name, kw in venues:
        r.add(name, kw.pop("seeded_from", "rw"), **kw)
    return r


# --------------------------------------------------------------------------
# spelling variants: the pairs that must fold, and the pairs that must not
# --------------------------------------------------------------------------

FOLD = [
    ("Momofuku Ssam Bar", "Momfuku Ssam Bar"),
    ("Uncle Boons", "Uncle Boon"),
    ("Sant Ambroeus", "Saint Ambroeus"),
    ("Locanda Verde", "Locande Verde"),
]
KEEP_APART = [
    # A contrast word is not a typo, and scores HIGHER on a plain ratio than
    # the real variants above -- which is why a ratio alone cannot be used.
    ("La Pecora Bianca Upper East Side", "La Pecora Bianca Upper West Side"),
    ("Empire Steak House Midtown East", "Empire Steak House Midtown West"),
    ("Crave Fishbar Upper East Side", "Crave Fishbar Upper West Side"),
    ("Miriam Upper East Side", "Miriam Upper West Side"),
    # One-word names have nothing left to corroborate a one-letter difference.
    ("Isa", "Insa"),
    ("Mắm", "Mamo"),
    ("Sevilla Restaurant", "Semilla"),
    # An added qualifier is a different branch, not a misspelling.
    ("Tonchin", "Tonchin Brooklyn"),
    ("Fish Cheeks", "Fish Cheeks Brooklyn"),
]


@pytest.mark.parametrize("a,b", FOLD)
def test_spelling_variants_fold(a, b):
    assert spelling_variant(a, b) and spelling_variant(b, a)


@pytest.mark.parametrize("a,b", KEEP_APART)
def test_lookalikes_never_fold(a, b):
    assert not spelling_variant(a, b) and not spelling_variant(b, a)


def test_fold_keeps_the_spelling_the_sources_use_most():
    """The surviving row is chosen for its data, the surviving NAME for its
    frequency. Before this split, Uncle Boons was displayed as 'Uncle Boon'."""
    r = roster_with(("Uncle Boons", {"seeded_from": "james_beard"}),
                    ("Uncle Boon", {"seeded_from": "james_beard"}))
    r.awards = ([{"venue_slug": "uncle-boons", "matched_name": "Uncle Boons"}] * 6
                + [{"venue_slug": "uncle-boon", "matched_name": "Uncle Boon"}])
    merges = merge_spelling_variants(r)
    assert len(merges) == 1
    assert merges[0]["kept_name"] == "Uncle Boons"
    assert len(r.venues) == 1
    # the folded row's awards must follow it, not vanish
    assert {a["venue_slug"] for a in r.awards} == {merges[0]["kept"]}


def test_fold_refuses_when_the_two_addresses_disagree():
    r = roster_with(
        ("Uncle Boons", {"seeded_from": "james_beard",
                         "address": "7 Spring St., New York, NY 10012"}),
        ("Uncle Boon", {"seeded_from": "james_beard",
                        "address": "1 Bedford Ave., Brooklyn, NY 11211"}))
    assert merge_spelling_variants(r) == []
    assert len(r.venues) == 2


# --------------------------------------------------------------------------
# address matching
# --------------------------------------------------------------------------

def test_same_building_two_entrances_merges_rather_than_duplicating():
    """Ci Siamo is listed by Restaurant Week at 385 Ninth Ave. and by Michelin
    at 440 W. 33rd St. -- one restaurant in Manhattan West, no shared digits.
    It used to produce two rows, so the NYT and Beard records for it landed on
    neither."""
    r = roster_with(("Ci Siamo", {"address": "385 Ninth Ave., Manhattan, NY, 10001"}))
    venue, how, decision = match_with_address(
        r, "Ci Siamo", "440 W. 33rd St., Ste. 100, New York, NY 10001")
    assert decision == "confirm", how
    assert venue["venue_slug"] == "ci-siamo"


def test_our_row_having_no_address_is_not_a_contradiction():
    """Alta Calidad is in the listing without an address; Michelin has one.
    Refusing that merge dropped a real Bib Gourmand."""
    r = roster_with(("Alta Calidad", {}))
    venue, how, decision = match_with_address(
        r, "Alta Calidad", "552 Vanderbilt Ave., Brooklyn, NY 11238")
    assert decision == "merge", how
    assert venue["venue_slug"] == "alta-calidad"


def test_same_name_different_building_becomes_a_second_venue():
    r = roster_with(("Tonchin", {"address": "13 W. 36th St., New York, NY 10018"}))
    venue, _, decision = match_with_address(
        r, "Tonchin", "109 N. 3rd St., Brooklyn, NY 11249")
    assert (venue, decision) == (None, "create")


def test_two_same_named_venues_and_no_address_match_is_refused_not_guessed():
    r = roster_with(("Fish Cheeks", {"address": "55 Bond St., Manhattan, NY, 10012"}),
                    ("Fish Cheeks", {"address": "661 Driggs Ave., Brooklyn, NY, 11211"}))
    venue, _, decision = match_with_address(
        r, "Fish Cheeks", "1 Nowhere Rd., New York, NY 10099")
    assert (venue, decision) == (None, "refuse")


def test_name_only_source_never_picks_between_two_candidates():
    """The whole James Beard file has no addresses. Two candidates is a row for
    a human, not a coin flip."""
    r = roster_with(("Fish Cheeks", {"address": "55 Bond St., Manhattan, NY, 10012"}),
                    ("Fish Cheeks", {"address": "661 Driggs Ave., Brooklyn, NY, 11211"}))
    assert match_name_only(r, "Fish Cheeks")[2] == "refuse"
    assert match_name_only(r, "Somewhere New")[2] == "create"


def test_addresses_compatible_treats_missing_as_unknown_not_conflicting():
    assert addresses_compatible(None, "55 Bond St., NY 10012")
    assert addresses_compatible("55 Bond St., NY 10012", "55 Bond St., NY 10012")
    assert not addresses_compatible("55 Bond St., NY 10012",
                                    "661 Driggs Ave., Brooklyn, NY 11211")


# --------------------------------------------------------------------------
# boroughs, slugs, scoring
# --------------------------------------------------------------------------

@pytest.mark.parametrize("address,city,expected", [
    ("11 Madison Ave., New York, NY 10010", None, "Manhattan"),
    ("47 S. 5th St., Brooklyn, NY 11249", None, "Brooklyn"),
    ("10-07 50th Ave., Long Island City, NY 11101", None, "Queens"),
    ("2397 Arthur Ave., Bronx, NY 10458", None, "The Bronx"),
    ("1 Richmond Terrace, Staten Island, NY 10301", None, "Staten Island"),
    # No address at all: the James Beard city hint is the only thing there is.
    (None, "Long Island City", "Queens"),
    (None, "Brooklyn", "Brooklyn"),
    # and when there is nothing, say nothing rather than defaulting to Manhattan
    (None, None, None),
    (None, "Chicago", None),
])
def test_borough_from(address, city, expected):
    assert borough_from(address, city) == expected


def test_borough_spelling_matches_the_listings_own():
    """One borough must be one filter value. The listing says 'The Bronx'."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        listing = {b for (b,) in con.execute(
            "SELECT DISTINCT borough FROM restaurants WHERE borough IS NOT NULL")}
        venues = {b for (b,) in con.execute(
            "SELECT DISTINCT borough FROM venues WHERE borough IS NOT NULL")}
    finally:
        con.close()
    assert venues <= listing, f"venues invented a borough spelling: {venues - listing}"


def test_postal_key_takes_the_zip_not_the_street_number():
    assert postal_key("440 W. 33rd St., Ste. 100, New York, NY 10001") == "10001"
    assert postal_key("no numbers here") is None


def test_slugify_matches_the_places_cli_shape():
    assert slugify("Mark's Off Madison") == "marks-off-madison"
    assert slugify("Café Boulud") == "cafe-boulud"
    assert slugify("The Russian Tea Room") == "russian-tea-room"


def test_prestige_orders_the_way_the_config_says():
    cfg = load_awards_config()
    three = [{"source": "michelin", "level": "3 stars", "year": 2025}]
    bib = [{"source": "michelin", "level": "bib_gourmand", "year": 2025}]
    assert prestige_for(three, cfg, closed=False)[0] > prestige_for(bib, cfg, closed=False)[0]
    assert prestige_for(three, cfg, closed=False)[1] == "michelin:3 stars"
    assert prestige_for([], cfg, closed=False) == (0, None, None)


def test_three_juries_agreeing_outscores_one_saying_it_twice():
    cfg = load_awards_config()
    broad = [{"source": "michelin", "level": "1 star", "year": 2025},
             {"source": "nyt", "level": "nyt_100_best", "year": 2026, "rank": 40},
             {"source": "james_beard", "level": "nominee", "year": 2025}]
    narrow = [{"source": "michelin", "level": "1 star", "year": 2025},
              {"source": "michelin", "level": "1 star", "year": 2025}]
    assert prestige_for(broad, cfg, closed=False)[0] > prestige_for(narrow, cfg, closed=False)[0]


def test_an_old_award_scores_below_the_same_award_won_recently():
    cfg = load_awards_config()
    now = [{"source": "james_beard", "level": "winner", "year": 2026}]
    then = [{"source": "james_beard", "level": "winner", "year": 1994}]
    assert prestige_for(now, cfg, closed=False)[0] > prestige_for(then, cfg, closed=False)[0]


def test_closed_never_outranks_the_same_honour_still_trading():
    cfg = load_awards_config()
    aw = [{"source": "michelin", "level": "3 stars", "year": 2025}]
    assert prestige_for(aw, cfg, closed=True)[0] < prestige_for(aw, cfg, closed=False)[0]


def test_every_honour_in_the_data_has_a_score_in_the_config():
    """A level nobody scored is a venue silently ranked at zero."""
    cfg = load_awards_config()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        pairs = {f"{s}:{l}" for s, l in con.execute(
            "SELECT DISTINCT source, level FROM venue_awards WHERE level IS NOT NULL")}
    finally:
        con.close()
    missing = pairs - set(cfg["honors"])
    assert not missing, f"config/awards.json has no points for: {sorted(missing)}"


def test_the_roster_kept_every_restaurant_week_participant():
    """Venues is a superset of the listing. Losing one to a bad merge would be
    invisible on the site -- the row would simply not be there."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        listed = con.execute("SELECT COUNT(*) FROM restaurants").fetchone()[0]
        linked = con.execute(
            "SELECT COUNT(DISTINCT rw_slug) FROM venues WHERE rw_slug IS NOT NULL"
        ).fetchone()[0]
        orphans = con.execute(
            "SELECT COUNT(*) FROM venue_awards a LEFT JOIN venues v"
            " ON v.venue_slug = a.venue_slug WHERE v.venue_slug IS NULL").fetchone()[0]
    finally:
        con.close()
    assert linked == listed
    assert orphans == 0, "awards pointing at a venue that no longer exists"


# --------------------------------------------------------------------------
# portfolio awards: one string naming several restaurants
# --------------------------------------------------------------------------

def test_split_group_pulls_the_list_out_of_a_parenthetical():
    assert split_group("Gracious Hospitality (COTE, Undercote, and COQODAQ)") == [
        "COTE", "Undercote", "COQODAQ"]
    assert split_group("Diner, Marlow & Sons, Reynard, and others") == [
        "Diner", "Marlow & Sons", "Reynard"]


@pytest.mark.parametrize("name,marked", [
    ("The Spotted Pig, The Breslin, Tosca Cafe, and others", True),
    ("Gracious Hospitality (COTE, Undercote, and COQODAQ)", True),
    ("Red Cat Restaurants (The Red Cat and The Harrison)", True),
    ("Nobu, NY/Matsuhisa, LA", True),
    # Real names that merely contain the same punctuation.
    ("Gage & Tollner", False),
    ("Milk & Honey", False),
    ("Grand Central Oyster Bar and Restaurant", False),
    ("Fifty Seven Fifty Seven, The Four Seasons Hotel", False),
    ("Lorenzo's Restaurant, Bar & Cabaret", False),
    ("Please Don't Tell (PDT)", False),
    ("Mas (farmhouse)", False),
])
def test_group_marker(name, marked):
    assert bool(group_marker(name)) is marked


def test_a_restaurateur_award_reaches_every_restaurant_it_names():
    """'Frenchette, Le Veau d' Or, and Le Rock' used to become a VENUE, and the
    2025 Outstanding Restaurateur award landed on that instead of on either
    restaurant."""
    r = roster_with(("Frenchette", {"seeded_from": "james_beard"}),
                    ("Le Rock", {"seeded_from": "michelin"}))
    deferred = [{"name": "Frenchette, Le Veau d' Or, and Le Rock",
                 "source": "james_beard",
                 "award": {"source": "james_beard", "level": "winner",
                           "award": "Outstanding Restaurateur", "year": 2025}}]
    attached, kept_whole, unresolved = resolve_group_awards(r, deferred)
    assert sorted(slug for slug, _ in attached) == ["frenchette", "le-rock"]
    assert kept_whole == []
    # the part we could not resolve is recorded, never invented as a venue
    assert [u["part"] for u in unresolved] == ["Le Veau d' Or"]
    assert len(r.venues) == 2


def test_a_real_name_that_looks_like_a_list_is_left_alone():
    """Two hits are required without a marker, and 'Gage & Tollner' cannot
    produce them -- there is no restaurant called Gage and none called Tollner."""
    r = roster_with(("Gage & Tollner", {"seeded_from": "james_beard"}))
    deferred = [{"name": "Gage & Tollner", "source": "james_beard",
                 "award": {"source": "james_beard", "level": "nominee", "year": 2022}}]
    attached, kept_whole, _ = resolve_group_awards(r, deferred)
    assert attached == []
    assert [k["name"] for k in kept_whole] == ["Gage & Tollner"]


def test_a_marked_list_matching_nothing_becomes_a_review_row_not_a_venue():
    r = roster_with(("Somewhere Else", {}))
    deferred = [{"name": "B.R. Guests Restaurants (Fiamma, Ruby Foo's, Vento)",
                 "source": "james_beard",
                 "award": {"source": "james_beard", "level": "winner", "year": 2007}}]
    attached, kept_whole, _ = resolve_group_awards(r, deferred)
    assert (attached, kept_whole) == ([], [])
    assert len(r.refused) == 1
    assert len(r.venues) == 1


def test_no_venue_on_the_roster_is_a_list_of_restaurants():
    """The failure this whole path exists to prevent, checked against the real
    database rather than a fixture."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        names = [n for (n,) in con.execute("SELECT name FROM venues")]
    finally:
        con.close()
    listish = [n for n in names if group_marker(n)]
    assert not listish, f"these venues are lists, not restaurants: {listish}"


# --------------------------------------------------------------------------
# the published payload
# --------------------------------------------------------------------------

def test_the_payload_never_carries_menu_text():
    """The one line that must never be crossed: the Restaurant Week menus are
    linked, not reproduced. validate() enforces it; this checks the file."""
    import export_venues
    out = ROOT / "docs" / "data" / "venues.json"
    if not out.exists():
        pytest.skip("payload not built")
    payload = json.loads(out.read_text(encoding="utf-8"))
    banned = {"menu", "menus", "menu_items", "raw_text", "dishes", "menu_state"}
    for v in payload["venues"]:
        assert not (banned & set(v)), f"{v['slug']} carries menu data"
    errors, _ = export_venues.validate(payload["venues"], load_awards_config())
    assert errors == []


def test_every_payload_row_is_recognised_or_participating():
    """A venue that is neither is a row with no reason to exist -- it means a
    merge dropped an award and left the husk behind."""
    out = ROOT / "docs" / "data" / "venues.json"
    if not out.exists():
        pytest.skip("payload not built")
    payload = json.loads(out.read_text(encoding="utf-8"))
    orphans = [v["slug"] for v in payload["venues"]
               if not v["award_count"] and not v["rw"]]
    assert orphans == []


def test_the_exporter_does_not_rewrite_a_payload_for_the_clock_alone():
    """A 1.2 MB file rewritten every Monday for a timestamp buries the one week
    something actually changed among fifty-one weeks that did not."""
    from export_venues import _same_but_for_the_clock
    out = ROOT / "docs" / "data" / "venues.json"
    if not out.exists():
        pytest.skip("payload not built")
    payload = json.loads(out.read_text(encoding="utf-8"))

    moved_on = dict(payload, generated_at="2099-01-01T00:00:00+00:00")
    assert _same_but_for_the_clock(out, moved_on)

    real_change = dict(payload, counts=dict(payload["counts"], venues=1))
    assert not _same_but_for_the_clock(out, real_change)

    assert not _same_but_for_the_clock(ROOT / "does-not-exist.json", payload)
