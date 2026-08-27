"""Numbers published side by side must add up.

Every figure checked here is one a reader can do arithmetic on. The dashboard
prints a comparable, a Restaurant Week price and a gap in the same row; if the
three do not subtract, the reader is right and the page is wrong.
"""
import inspect
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


# --------------------------------------------------------------------------
# every writer and every reader of the sweep cache, not just the first one
#
# The rule above was fixed in price_sweep.sweep_one and repaired on load in
# build_db. But price_rescue is a SECOND writer to data/raw/pricesweep, and it
# kept rounding each gap independently -- three of the records it wrote hold a
# comparable and a gap that do not subtract. price_sweep.report() is a third
# surface: it reads those files raw and prints both numbers on one line.
#
# Nothing errored, and the dashboard was never wrong, because build_db
# re-derives on the way in. The cache and the report were.
# --------------------------------------------------------------------------

def test_gaps_for_is_the_only_definition():
    """Both writers must call it rather than each spell the arithmetic out."""
    import price_rescue
    import price_sweep

    assert price_rescue.gaps_for is price_sweep.gaps_for
    for mod in (price_sweep.sweep_one, price_rescue.rescue_one):
        src = inspect.getsource(mod)
        assert "gaps_for(" in src, f"{mod.__name__} does not use gaps_for"
        assert "int(t.strip(" not in src, (
            f"{mod.__name__} is spelling the gap arithmetic out again")


@pytest.mark.parametrize("comparable,tiers", [
    (60, ["$30", "$45", "$60"]),
    (61, ["$45"]),
    (83, ["$30", "$45"]),
])
def test_gaps_for_subtracts_from_the_published_comparable(comparable, tiers):
    from price_sweep import gaps_for
    for tier, gap in gaps_for(comparable, tiers).items():
        assert comparable - int(tier.strip("$")) == gap


def test_the_half_dollar_case_that_started_this():
    """comp=60.5 rounds to 60. The $45 gap must be 15, not round(15.5)=16."""
    from price_sweep import gaps_for
    comp_r = round(60.5)
    assert gaps_for(comp_r, ["$45"])["$45"] == comp_r - 45


def test_the_report_reconciles_what_it_prints():
    """report() prints a comparable and a gap on the same line, off cached
    records written before the fix."""
    import price_sweep

    src = inspect.getsource(price_sweep.report)
    assert "reconciled_gaps(r)" in src, (
        "report() is reading r['gaps'] raw again")


def test_every_cached_sweep_reconciles_once_re_derived():
    """The repair has to cover the whole cache, whichever writer made it."""
    from price_sweep import reconciled_gaps

    cache = ROOT / "data" / "raw" / "pricesweep"
    if not cache.exists():
        pytest.skip("no cached sweeps")
    for f in cache.glob("*.json"):
        rec = json.loads(f.read_text())
        comp = rec.get("comparable_3course")
        gaps = reconciled_gaps(rec)
        if comp is None or not isinstance(gaps, dict):
            continue
        for tier, gap in gaps.items():
            try:
                want = comp - int(str(tier).strip("$"))
            except ValueError:
                continue
            assert want == gap, f"{f.name}: {tier} {gap} != {comp} - {tier}"


def test_the_cache_on_disk_already_reconciles():
    """Stronger than the test above: not "we repair it on read" but "there is
    nothing left to repair". The 44 records that did not subtract were rewritten
    through gaps_for, so anything reading these files directly -- the report,
    a notebook, a person -- gets numbers that add up without knowing to ask."""
    cache = ROOT / "data" / "raw" / "pricesweep"
    if not cache.exists():
        pytest.skip("no cached sweeps")
    bad = []
    for f in sorted(cache.glob("*.json")):
        rec = json.loads(f.read_text())
        comp, gaps = rec.get("comparable_3course"), rec.get("gaps")
        if comp is None or not isinstance(gaps, dict):
            continue
        for tier, gap in gaps.items():
            try:
                want = comp - int(str(tier).strip("$"))
            except ValueError:
                continue
            if want != gap:
                bad.append(f"{f.name}: {comp} - {tier} = {want}, cached {gap}")
    assert not bad, "\n  ".join([""] + bad[:10])


def test_render_mode_reports_the_pages_it_actually_read():
    """It hard-coded 1 while clicking through to a second page, so "high" --
    twelve prices off two pages -- was unreachable for every rendered record."""
    import price_rescue

    src = inspect.getsource(price_rescue.rescue_one)
    # Only the render branch. The fetch branch legitimately starts at 1 for the
    # landing page and increments per page, which is the behaviour render mode
    # was missing.
    branch = src[src.index("if render:"):src.index("else:")]
    assert 'rec["pages_fetched"] = 1' not in branch, (
        "render mode is asserting a page count again instead of counting")
    assert 'rec["prices"], rec["pages_fetched"] = render_prices(website)' in branch
    assert "return ps, pages" in inspect.getsource(price_rescue.render_prices)
