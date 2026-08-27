"""Numbers published side by side must add up.

Every figure checked here is one a reader can do arithmetic on. The dashboard
prints a comparable, a Restaurant Week price and a gap in the same row; if the
three do not subtract, the reader is right and the page is wrong.
"""
import json
from pathlib import Path

import pytest

from build_db import reconciled_gaps
from export_site_data import assert_verified_gaps_reconcile

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# the heuristic path: two figures rounded independently from one source
# --------------------------------------------------------------------------

def test_a_gap_is_derived_from_the_comparable_that_gets_published():
    """price_sweep rounded the comparable and each gap independently from the
    same unrounded number, so 60.5 was published as a comparable of 60 with a
    $45 gap of 16. Twenty-nine rows visibly failed to add up."""
    fixed = reconciled_gaps({"comparable_3course": 60, "gaps": {"$45": 16, "$30": 31}})
    assert fixed == {"$45": 15, "$30": 30}
    for tier, gap in fixed.items():
        assert 60 - int(tier.strip("$")) == gap


@pytest.mark.parametrize("rec", [
    {"comparable_3course": None, "gaps": {"$45": 16}},   # nothing to reconcile against
    {"gaps": {"$45": 16}},
    {"comparable_3course": 60, "gaps": None},
    {"comparable_3course": 60},
    {},
])
def test_reconciled_gaps_passes_through_what_it_cannot_reconcile(rec):
    """Inventing a comparable would be worse than leaving the cache alone."""
    assert reconciled_gaps(rec) == rec.get("gaps")


def test_an_unparseable_tier_keeps_its_cached_value():
    got = reconciled_gaps({"comparable_3course": 60, "gaps": {"$45": 16, "prix": 9}})
    assert got == {"$45": 15, "prix": 9}


def test_every_published_gap_subtracts_from_its_published_comparable():
    """The property, against the real payload rather than a fixture."""
    payload = ROOT / "docs" / "data" / "restaurants.json"
    if not payload.exists():
        pytest.skip("payload not built")
    bad = []
    for r in json.loads(payload.read_text(encoding="utf-8"))["restaurants"]:
        comp, price, gap = r.get("comparable_usd"), r.get("rw_price"), r.get("gap_usd")
        if None in (comp, price, gap):
            continue
        if comp - price != gap:
            bad.append(f"{r['slug']}: {comp} - {price} != {gap}")
    assert not bad, bad


# --------------------------------------------------------------------------
# the verified path: the figures a reader is told were checked by hand
# --------------------------------------------------------------------------

def test_the_guard_catches_a_verified_pair_that_does_not_subtract():
    """Mark's Off Madison carried a $57 comparable with a $27 gap on a $45
    menu, because the decision doc quotes $57-68 as the TWO-course a la carte
    price while the saving is stated "on three". Nothing failed; the dashboard
    simply printed all three numbers together."""
    with pytest.raises(AssertionError, match="do not reconcile"):
        assert_verified_gaps_reconcile({
            "marks-off-madison": {"rw_price": 45, "comparable_usd": 57, "gap_usd": 27},
        })


def test_the_guard_checks_the_high_figures_too():
    with pytest.raises(AssertionError, match="comparable_usd_high"):
        assert_verified_gaps_reconcile({
            "x": {"rw_price": 45, "comparable_usd_high": 68, "gap_usd_high": 38},
        })


@pytest.mark.parametrize("entry", [
    {"rw_price": 45, "comparable_usd": 72, "gap_usd": 27},     # reconciles
    {"rw_price": 45, "gap_usd": 27},                           # no comparable stated
    {"rw_price": 45, "comparable_usd": 72},                    # no gap stated
    {"comparable_usd": 72, "gap_usd": 27},                     # no price stated
    {},
])
def test_an_absent_figure_is_not_a_contradiction(entry):
    """The decision doc often states a saving without stating a comparable.
    Silence is not a disagreement."""
    assert_verified_gaps_reconcile({"x": entry}) is None


def test_the_doc_key_is_not_mistaken_for_a_restaurant():
    assert_verified_gaps_reconcile({"_doc": {"purpose": "words"}})


def test_the_committed_verified_values_reconcile():
    """Runs against the real config, so a future hand edit that breaks this
    fails here rather than on the published page."""
    d = json.loads((ROOT / "config" / "verified_values.json").read_text(encoding="utf-8"))
    assert_verified_gaps_reconcile(d["restaurants"])
