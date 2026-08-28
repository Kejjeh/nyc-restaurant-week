"""venue_slug as a durable identity rather than a fact about one build.

Written against the case that motivated it (issue 25): 156 RW-seeded venues take
their slug from the programme's listing, and when one of those restaurants is
absent from the next listing it is rebuilt from its award records under a
different slug. Nothing raises. The weekly report calls it a departure and an
arrival, and any link to the old slug 404s.

The real changeover is exercised at the bottom against the committed database;
the unit cases above it pin the rules that make that safe.
"""
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from build_venues import Ledger, Roster, build, load_awards_config, slugify

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
LEDGER = ROOT / "data" / "venue_slugs.json"


def ledger_of(*entries):
    return Ledger([dict(e) for e in entries], today="2026-01-01")


def test_a_venue_keeps_the_slug_it_had_when_its_name_would_give_it_another():
    """The whole point. Barawine was seeded as barawine-harlem by the programme;
    rebuilt from its award record alone, slugify would call it barawine."""
    led = ledger_of({"slug": "barawine-harlem", "norm": "barawine",
                     "street": ["200"], "zip": "10027"})
    r = Roster(led)
    v = r.add("Barawine", "james_beard", address="200 Lenox Ave, New York, NY 10027")
    assert v["venue_slug"] == "barawine-harlem"
    assert slugify("Barawine") == "barawine", "the test is only meaningful if these differ"


def test_an_empty_ledger_changes_nothing():
    """The first build after this lands must produce exactly the roster that is
    committed today -- a fix for churn that itself churns 1400 slugs is not one."""
    led = Ledger(today="2026-01-01")
    r = Roster(led)
    v = r.add("Cosme", "rw", address="35 E 21st St, New York, NY 10010")
    assert v["venue_slug"] == slugify("Cosme")


def test_a_contradicting_address_does_not_inherit_the_slug():
    """Two restaurants share a name often enough that the roster already carries
    '-2' slugs for them. Handing one the other's slug would hand it the other's
    identity, and after issue 2 the Places lookup that was paid for."""
    led = ledger_of({"slug": "sushi-nakazawa", "norm": "sushi nakazawa",
                     "street": ["23"], "zip": "10014"})
    r = Roster(led)
    v = r.add("Sushi Nakazawa", "michelin",
              address="1 Rockefeller Plaza, New York, NY 10020")
    assert v["venue_slug"] != "sushi-nakazawa"
    assert led.reserved("sushi-nakazawa"), "the absent venue must keep its claim"


def test_no_address_on_either_side_still_inherits():
    """Most award records carry no address at all. Refusing those would mint a
    new slug every build for exactly the venues the ledger exists to protect."""
    # norm_name strips the leading article, so the key is "dutch" -- which is
    # also exactly the slug slugify would mint, and the churn issue 25 measured.
    led = ledger_of({"slug": "the-dutch", "norm": "dutch",
                     "street": None, "zip": None})
    v = Roster(led).add("The Dutch", "nyt")
    assert v["venue_slug"] == "the-dutch"


def test_a_zip_corroborates_when_the_street_number_does_not():
    """Ci Siamo is published as both '385 Ninth Ave.' and '440 W. 33rd St.' --
    one restaurant, two entrances, no shared digits."""
    led = ledger_of({"slug": "ci-siamo", "norm": "ci siamo",
                     "street": ["385"], "zip": "10001"})
    v = Roster(led).add("Ci Siamo", "michelin", address="440 W 33rd St, New York, NY 10001")
    assert v["venue_slug"] == "ci-siamo"


def test_a_new_venue_may_not_mint_a_slug_held_by_an_absent_one():
    """Otherwise the restaurant that owns it cannot have it back when it returns,
    and the squatter inherits its inbound links."""
    led = ledger_of({"slug": "veselka", "norm": "veselka something else"})
    v = Roster(led).add("Veselka", "nyt")
    assert v["venue_slug"] != "veselka"


def test_a_folded_venue_retires_its_slug_and_it_is_never_reissued():
    """A merge is the one case where two ledger entries become one. The loser is
    kept, not deleted -- the restaurant did not stop existing, its row did."""
    led = ledger_of({"slug": "uncle-boons", "norm": "uncle boons"},
                    {"slug": "uncle-boon", "norm": "uncle boon"})
    led.retire("uncle-boon", "uncle-boons")
    entry = next(e for e in led.entries if e["slug"] == "uncle-boon")
    assert entry["merged_into"] == "uncle-boons"
    assert not led.reserved("uncle-boon")
    assert led.claim("Uncle Boon", None) is None, "a retired slug was reissued"


def test_the_committed_ledger_covers_the_committed_roster():
    """A ledger missing a venue is a venue whose identity is not actually held."""
    doc = json.loads(LEDGER.read_text(encoding="utf-8"))
    held = {e["slug"] for e in doc["entries"] if not e.get("merged_into")}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        live = {r[0] for r in con.execute("SELECT venue_slug FROM venues")}
    finally:
        con.close()
    assert not live - held, f"{len(live - held)} venues have no ledger entry"


def test_the_changeover_that_motivated_this_no_longer_changes_any_identity():
    """The measurement from issue 25, run for real: delete every award-holding
    venue whose slug came from the programme's listing, rebuild, and compare.

    Before the ledger this produced 11 departures and 11 arrivals for 11
    restaurants that had gone nowhere.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        base = {r[0] for r in con.execute("SELECT venue_slug FROM venues")}
        at_risk = [r[0] for r in con.execute(
            "SELECT rw_slug, name FROM venues"
            " WHERE rw_slug IS NOT NULL AND award_count > 0") if r[0] != slugify(r[1])]
    finally:
        con.close()
    assert at_risk, "nothing at risk means this test proves nothing"

    tmp = Path(tempfile.mkdtemp()) / "sim.sqlite"
    shutil.copyfile(DB, tmp)
    sim = sqlite3.connect(tmp)
    sim.executemany("DELETE FROM restaurants WHERE slug=?", [(s,) for s in at_risk])
    doc = json.loads(LEDGER.read_text(encoding="utf-8"))
    led = Ledger(doc["entries"], today="2026-01-01")
    roster, *_ = build(sim, load_awards_config(), quiet=True, ledger=led)
    sim.close()

    after = set(roster.venues)
    assert not base - after, f"identities lost in a changeover: {sorted(base - after)[:10]}"
    assert not after - base, f"identities invented in a changeover: {sorted(after - base)[:10]}"
