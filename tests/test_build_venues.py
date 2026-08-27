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
    r.awards = [{"venue_slug": "frenchette", "source": "james_beard",
                 "level": "winner", "year": 2019, "matched_name": "Frenchette"},
                {"venue_slug": "le-rock", "source": "michelin",
                 "level": "recommended", "year": 2025, "matched_name": "Le Rock"}]
    deferred = [{"name": "Frenchette, Le Veau d' Or, and Le Rock",
                 "source": "james_beard",
                 "award": {"source": "james_beard", "level": "winner",
                           "award": "Outstanding Restaurateur", "year": 2025}}]
    attached, kept_whole, unresolved, created = resolve_group_awards(r, deferred)
    # Le Veau d'Or is a real restaurant that appears in these files only inside
    # this string. It used to be dropped; now the list vouches it into existence
    # with its apostrophe tidied.
    assert sorted(slug for slug, _ in attached) == ["frenchette", "le-rock", "le-veau-dor"]
    assert created == ["Le Veau d'Or"]
    assert kept_whole == []
    assert unresolved == []


def test_a_real_name_that_looks_like_a_list_is_left_alone():
    """Two hits are required without a marker, and 'Gage & Tollner' cannot
    produce them -- there is no restaurant called Gage and none called Tollner."""
    r = roster_with(("Gage & Tollner", {"seeded_from": "james_beard"}))
    deferred = [{"name": "Gage & Tollner", "source": "james_beard",
                 "award": {"source": "james_beard", "level": "nominee", "year": 2022}}]
    attached, kept_whole, _, created = resolve_group_awards(r, deferred)
    assert attached == [] and created == []
    assert [k["name"] for k in kept_whole] == ["Gage & Tollner"]


def test_a_marked_list_matching_nothing_becomes_a_review_row_not_a_venue():
    r = roster_with(("Somewhere Else", {}))
    deferred = [{"name": "B.R. Guests Restaurants (Fiamma, Ruby Foo's, Vento)",
                 "source": "james_beard",
                 "award": {"source": "james_beard", "level": "winner", "year": 2007}}]
    attached, kept_whole, _, created = resolve_group_awards(r, deferred)
    assert (attached, kept_whole, created) == ([], [], [])
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
    # `dishes` was banned here too until it started carrying tag NAMES, which
    # are derived facts and not menu text. The stricter contract it lives under
    # now -- configured names only, nothing longer than a tag -- is enforced by
    # test_dishes_are_tag_names_and_never_menu_text below.
    banned = {"menu", "menus", "menu_items", "raw_text", "menu_state", "snippet"}
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


# --------------------------------------------------------------------------
# hand rulings (config/venue_aliases.json)
# --------------------------------------------------------------------------

def test_an_award_body_entity_never_becomes_a_venue():
    """'Dale DeGroff Co., Inc.' is a bartender's company and 'Founders, "Food &
    Wine" and "Food Arts"' is two magazines. Both were rows on the roster."""
    from build_venues import load_aliases
    not_venues, _ = load_aliases()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        names = {n for (n,) in con.execute("SELECT name FROM venues")}
    finally:
        con.close()
    from build_venues import norm_name
    leaked = {n for n in names if norm_name(n) in not_venues}
    assert not leaked, f"ruled-out names are on the roster: {leaked}"
    assert "Dale DeGroff Co., Inc." not in names


def test_a_hand_ruled_split_creates_both_restaurants():
    """The automatic rule cannot split 'Zaab Zaab, Zaab Zaab Talay' -- neither
    part was on the roster, so nothing could prove it was a list. A human
    ruling can, and then the parts are created rather than required."""
    from build_venues import load_aliases
    _, split_into = load_aliases()
    assert split_into, "venue_aliases.json has no hand-ruled splits"
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = dict(con.execute(
            "SELECT name, award_count FROM venues WHERE name IN"
            " ('Zaab Zaab', 'Zaab Zaab Talay', 'Zaab Zaab, Zaab Zaab Talay')"))
    finally:
        con.close()
    assert rows.get("Zaab Zaab"), "the split's first part is not a venue"
    assert rows.get("Zaab Zaab Talay"), "the split's second part is not a venue"
    assert "Zaab Zaab, Zaab Zaab Talay" not in rows
    # the awards followed the split rather than being dropped with the husk
    assert rows["Zaab Zaab"] and rows["Zaab Zaab Talay"]


def test_a_ruling_is_matched_on_the_normalised_name():
    """So a ruling written with different punctuation still applies."""
    from build_venues import load_aliases, norm_name
    not_venues, _ = load_aliases()
    assert norm_name("dale degroff co, inc") in not_venues


def test_aliases_load_to_empty_when_the_file_is_absent():
    from build_venues import load_aliases
    assert load_aliases(ROOT / "does-not-exist.json") == ({}, {})


def test_the_reference_year_comes_from_the_season_file_not_from_awards_json():
    """config/season.json is the only file in this repo allowed to carry a year.
    reference_year sat hard-coded in awards.json, so the first changeover would
    have decayed every venue's standing against a stale year and nothing would
    have failed to say so."""
    from config import SEASON_YEAR
    from build_venues import reference_year
    assert reference_year(load_awards_config()) == SEASON_YEAR
    assert reference_year({}) == SEASON_YEAR
    assert reference_year({"recency": {}}) == SEASON_YEAR
    assert reference_year({"recency": {"reference_year": None}}) == SEASON_YEAR
    # pinning a rebuild to a past scoring run is still possible on purpose
    assert reference_year({"recency": {"reference_year": 2019}}) == 2019


def test_awards_json_carries_no_hard_coded_year():
    raw = (ROOT / "config" / "awards.json").read_text(encoding="utf-8")
    cfg = json.loads(raw)
    assert cfg["recency"]["reference_year"] is None, (
        "pin it only deliberately; a stale year here is silent")


def test_a_non_integer_reference_year_fails_the_run_rather_than_scoring_wrongly():
    import build_venues
    bad = json.loads((ROOT / "config" / "awards.json").read_text(encoding="utf-8"))
    bad["recency"]["reference_year"] = "2026"
    tmp = ROOT / "data" / "processed" / "_awards_test.json"
    tmp.write_text(json.dumps(bad), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="reference_year"):
            build_venues.load_awards_config(tmp)
    finally:
        tmp.unlink()


def test_recency_still_separates_an_old_award_from_a_current_one_after_the_change():
    cfg = load_awards_config()
    from config import SEASON_YEAR
    now = [{"source": "james_beard", "level": "winner", "year": SEASON_YEAR}]
    then = [{"source": "james_beard", "level": "winner", "year": SEASON_YEAR - 25}]
    assert prestige_for(now, cfg, closed=False)[0] > prestige_for(then, cfg, closed=False)[0]


# --------------------------------------------------------------------------
# portfolio awards must not depend on who joined Restaurant Week this summer
# --------------------------------------------------------------------------

@pytest.mark.parametrize("part,ok", [
    ("Pastis", True),
    ("Le Veau d'Or", True),
    ("The Breslin", True),
    ("Tosca Cafe", True),
    # the three shapes that are not restaurant names
    ("LA", False),              # a city abbreviation
    ("NY", False),
    ("NY/Matsuhisa", False),    # two things packed into one part
    ("", False),
    ("  ", False),
    ("--", False),
])
def test_plausible_venue_name(part, ok):
    """Splitting 'Nobu, NY/Matsuhisa, LA' once put a venue called 'LA' on the
    roster. This is the test on the STRING that stops it, and deliberately not
    a test on whether we have heard of the name."""
    from build_venues import plausible_venue_name
    assert plausible_venue_name(part) is ok


def test_a_portfolio_part_does_not_need_to_be_in_restaurant_week_to_get_its_award():
    """Morandi holds no award of its own and appears only inside Keith
    McNally's restaurateur awards. It was a venue solely because it joined this
    summer's programme, so dropping it from the listing silently deleted three
    real awards. Whether the Beard Foundation named a restaurant is a fact about
    the award, not about Restaurant Week."""
    from build_venues import resolve_group_awards
    # Only Balthazar is known, and it holds an award of its own.
    r = roster_with(("Balthazar", {"seeded_from": "michelin"}))
    r.awards = [{"venue_slug": "balthazar", "source": "michelin",
                 "level": "recommended", "year": 2025, "matched_name": "Balthazar"}]
    deferred = [{"name": "Balthazar, Lucky Strike, Morandi, Pastis, and Pravda",
                 "source": "james_beard",
                 "award": {"source": "james_beard", "level": "winner",
                           "award": "Outstanding Restaurateur", "year": 2010}}]
    attached, kept_whole, unresolved, created = resolve_group_awards(r, deferred)
    got = {slug for slug, _ in attached}
    assert got == {"balthazar", "lucky-strike", "morandi", "pastis", "pravda"}
    assert sorted(created) == ["Lucky Strike", "Morandi", "Pastis", "Pravda"]
    assert unresolved == []


def test_a_city_abbreviation_in_a_confirmed_list_is_still_refused():
    from build_venues import resolve_group_awards
    r = roster_with(("Nobu", {"seeded_from": "michelin"}))
    r.awards = [{"venue_slug": "nobu", "source": "michelin", "level": "recommended",
                 "year": 2025, "matched_name": "Nobu"}]
    deferred = [{"name": "Nobu, NY/Matsuhisa, LA", "source": "james_beard",
                 "award": {"source": "james_beard", "level": "winner", "year": 2003}}]
    attached, _, unresolved, created = resolve_group_awards(r, deferred)
    assert [slug for slug, _ in attached] == ["nobu"]
    assert created == []
    assert sorted(u["part"] for u in unresolved) == ["LA", "NY/Matsuhisa"]


def test_a_venue_that_only_joined_the_programme_cannot_vouch_for_a_list():
    """Without a marker, two parts must resolve to AWARD-HOLDING venues before
    the string counts as a list. A Restaurant Week row holds no award and so
    proves nothing -- which is what keeps this stable across a changeover."""
    from build_venues import resolve_group_awards
    r = roster_with(("Clover Club", {"seeded_from": "rw"}),
                    ("Flatiron Lounge", {"seeded_from": "rw"}))
    r.awards = []            # neither holds an award of its own
    deferred = [{"name": "Clover Club and Flatiron Lounge", "source": "james_beard",
                 "award": {"source": "james_beard", "level": "nominee", "year": 2011}}]
    attached, kept_whole, _, created = resolve_group_awards(r, deferred)
    assert attached == [] and created == []
    assert [k["name"] for k in kept_whole] == ["Clover Club and Flatiron Lounge"]


def test_the_source_apostrophe_typo_is_tidied_when_splitting():
    """The Beard file writes "Le Veau d' Or". Left alone it becomes a venue
    under that spelling and never meets the real name again -- norm_name keeps
    "d or" as two tokens and "dor" as one, so even the spelling-variant pass
    cannot reunite them."""
    assert "Le Veau d'Or" in split_group("Frenchette, Le Veau d' Or, and Le Rock")


def test_no_award_on_the_roster_depends_on_this_season_being_this_season():
    """The property, checked against the real database: every award record is
    attached to a venue that either holds it directly or was named in a list
    alongside award-holding restaurants. None of them rest on a Restaurant Week
    row that will not be there next summer."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT COUNT(*) FROM venue_awards a JOIN venues v"
            " ON v.venue_slug = a.venue_slug"
            " WHERE v.rw_slug IS NOT NULL AND v.award_count = 0").fetchone()[0]
    finally:
        con.close()
    assert rows == 0


def test_build_does_not_write_the_review_file():
    """build() takes whatever connection it is handed, including a temp copy in
    a test or a changeover simulation. A module-level output path does not care
    which, so it wrote the COMMITTED review file from a mutated database --
    caught only because the Ci Siamo entry it should have contained had quietly
    become an empty list. main() writes it now; build() only returns it."""
    import shutil
    import sqlite3 as sq
    import tempfile

    import build_venues

    review = ROOT / "data" / "processed" / "venue_merge_review.json"
    before = review.read_bytes() if review.exists() else None

    tmp = Path(tempfile.mkdtemp()) / "sim.sqlite"
    shutil.copyfile(DB, tmp)
    con = sq.connect(tmp)
    # a changeover: most of the listing replaced
    con.execute("DELETE FROM restaurants WHERE slug NOT IN"
                " (SELECT slug FROM restaurants ORDER BY slug LIMIT 50)")
    con.commit()
    roster, _, _, payload = build_venues.build(con, load_awards_config(), quiet=True)
    con.close()

    assert review.read_bytes() == before, (
        "build() wrote over the committed review file from a simulated database")
    # and it still reports what a human has to rule on, as data
    assert set(payload) >= {"refused", "confirm", "folded_spelling_variants"}


def test_the_committed_review_file_matches_the_committed_database():
    """A stale review file is a list of rulings that no longer correspond to
    anything, which is worse than no list."""
    import build_venues
    review = json.loads(
        (ROOT / "data" / "processed" / "venue_merge_review.json").read_text(encoding="utf-8"))
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        # every confirm entry must name a venue that really is one row
        for entry in review.get("confirm", []):
            row = con.execute("SELECT COUNT(*) FROM venues WHERE venue_slug = ?",
                              (entry["merged_into"],)).fetchone()[0]
            assert row == 1, f"{entry['merged_into']} in confirm is not a venue"
        for entry in review.get("folded_spelling_variants", []):
            kept = con.execute("SELECT name FROM venues WHERE venue_slug = ?",
                               (entry["kept"],)).fetchone()
            assert kept, f"{entry['kept']} was folded into a venue that is gone"
            gone = con.execute("SELECT COUNT(*) FROM venues WHERE venue_slug = ?",
                               (entry["folded"],)).fetchone()[0]
            assert gone == 0, f"{entry['folded']} was folded but is still a venue"
    finally:
        con.close()


# --------------------------------------------------------------------------
# dish tags on the roster
# --------------------------------------------------------------------------

def _payload():
    out = ROOT / "docs" / "data" / "venues.json"
    if not out.exists():
        pytest.skip("payload not built")
    return json.loads(out.read_text(encoding="utf-8"))


def test_dishes_are_tag_names_and_never_menu_text():
    """restaurants.json carries a snippet with each tag, under a 5%-of-a-menu
    budget. This payload carries the NAME only, so it holds no menu text at all
    and has no budget to manage. A dict here would mean somebody started
    copying the snippet across."""
    payload = _payload()
    vocabulary = set()
    for v in payload["venues"]:
        for d in (v.get("dishes") or []):
            assert isinstance(d, str), f"{v['slug']}: dish is a {type(d).__name__}"
            assert len(d) <= 40, f"{v['slug']}: {d!r} is too long to be a tag name"
            vocabulary.add(d)
    # every published name must be a configured tag, not a phrase off a menu
    configured = set(json.loads(
        (ROOT / "config" / "dish_tags.json").read_text(encoding="utf-8"))) - {"_doc"}
    assert vocabulary <= configured, f"not configured tags: {vocabulary - configured}"


def test_dishes_are_absent_rather_than_empty_where_there_is_no_menu():
    """Only Restaurant Week rows have a parsed menu. An empty list would read as
    'we looked and this menu has none of them', which is a different claim."""
    payload = _payload()
    for v in payload["venues"]:
        if not v["rw"]:
            assert v.get("dishes") is None, f"{v['slug']} is not in RW but has dishes"


def test_the_dish_facet_counts_match_the_rows():
    payload = _payload()
    from collections import Counter
    counted = Counter(d for v in payload["venues"] for d in (v.get("dishes") or []))
    assert payload["facets"]["dish"] == dict(
        sorted(counted.items(), key=lambda kv: (-kv[1], kv[0])))


def test_the_dish_tags_still_answer_the_question_they_were_added_for():
    """The roster exists to answer 'what does this place actually cook', which
    the cuisine facet cannot answer for the 778 venues never in the programme.
    Game meats is the sharpest of the tags and the reason this was built."""
    payload = _payload()
    game = [v["name"] for v in payload["venues"] if "game meats" in (v.get("dishes") or [])]
    assert len(game) >= 5, f"only {len(game)} venues tagged with game meats"
