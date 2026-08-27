"""Sunday and Sunday DINNER are different facts, and the listing carries both.

The Restaurant Week listing's entire meal-type vocabulary is four strings:

    "$N Dinner Price"   "$N Lunch Price"
    "$N Sunday Dinner Price"   "$N Sunday Lunch/Brunch Price"

So it already says, for every restaurant, whether Sunday is dinner or only
brunch. 24 restaurants carry a Sunday lunch/brunch price and no Sunday dinner
price.

The planner has always known the distinction matters — it has a branch that
answers "Sunday brunch only, no dinner". That branch fired on a hand-set
`no_sunday_dinner` flag, which exactly one restaurant carries: Mark's Off
Madison, whose `sunday` is false, so the line above it returns 'no Sunday
service' first. **The branch had never run for anybody.** Meanwhile the other
23 were offered a Sunday date with no qualification at all.

The concept was in the code, the fact was in the data, and nothing joined them.
"""
import json
from pathlib import Path

import pytest

from export_site_data import sunday_dinner_from_meal_types as sdmt

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def test_sunday_dinner_is_true_when_the_listing_says_so():
    assert sdmt(["$60 Dinner Price", "$60 Sunday Dinner Price"]) is True


def test_brunch_only_on_sunday_is_false():
    """The 24. A Sunday price that is lunch/brunch and no Sunday dinner."""
    assert sdmt(["$60 Dinner Price", "$45 Sunday Lunch/Brunch Price"]) is False
    assert sdmt(["$45 Lunch Price", "$45 Sunday Lunch/Brunch Price"]) is False


def test_no_sunday_at_all_is_unknown_not_false():
    """Null means "nothing to describe here"; `sunday` already carries that.
    False would be a claim that Sunday dinner was refused."""
    assert sdmt(["$60 Dinner Price", "$45 Lunch Price"]) is None
    assert sdmt([]) is None
    assert sdmt(None) is None


def test_both_sunday_kinds_present_is_true():
    assert sdmt(["$45 Sunday Lunch/Brunch Price",
                 "$60 Sunday Dinner Price"]) is True


def test_the_hand_flag_still_wins():
    """A transcription from the restaurant's own printed materials beats the
    listing everywhere else in this exporter."""
    assert sdmt(["$60 Sunday Dinner Price"], ["no_sunday_dinner"]) is False


def test_the_planner_reads_the_derived_fact_not_the_flag():
    i = APP.index("Sunday brunch only, no dinner")
    block = APP[i - 400:i + 80]
    assert "r.sunday_dinner === false" in block
    assert "includes('no_sunday_dinner')" not in block, (
        "the planner is back to reading a flag one restaurant carries")


# --- the published payload -------------------------------------------------

@pytest.fixture(scope="module")
def payload():
    p = ROOT / "docs" / "data" / "restaurants.json"
    if not p.exists():
        pytest.skip("payload not built")
    return json.loads(p.read_text(encoding="utf-8"))


def test_every_row_agrees_with_its_own_meal_types(payload):
    for r in payload["restaurants"]:
        expected = sdmt(r["meal_types_raw"], r.get("flags", []))
        assert r["sunday_dinner"] == expected, (
            f"{r['slug']}: sunday_dinner {r['sunday_dinner']} against "
            f"{r['meal_types_raw']}")


def test_no_row_claims_sunday_dinner_without_sunday(payload):
    """sunday_dinner True while sunday is False would be incoherent."""
    for r in payload["restaurants"]:
        if r["sunday_dinner"] is True:
            assert r["sunday"] is True or r["sunday_source"] == "verified", (
                f"{r['slug']}: Sunday dinner but no Sunday")


def test_the_brunch_only_restaurants_are_actually_there(payload):
    """If a data pull ever stops containing them the fix costs nothing, but a
    vacuous pass would stop saying anything — so it names what it found."""
    n = sum(1 for r in payload["restaurants"] if r["sunday_dinner"] is False)
    assert n >= 1, "no brunch-only Sunday restaurants in this payload"
