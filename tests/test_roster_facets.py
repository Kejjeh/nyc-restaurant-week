"""A facet count is a promise about what clicking it will give you.

The roster counted every facet against the WHOLE roster, so filtered to the 62
Michelin-starred venues the Borough group still read "Manhattan 1104" and
"Queens 55" — and clicking Queens returned nothing at all. The dashboard next
door had this right from the start and says why in its own comment.

There is no JS test harness in this repo, so the browser run is the real
verification and these are source contracts: they fail if the shape that makes
the behaviour possible is removed, which is the part a future edit is most
likely to undo by accident.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "docs" / "venues.js").read_text(encoding="utf-8")
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def body_of(src, name):
    """The source of one top-level function, by brace matching."""
    start = src.index(f"function {name}(")
    depth, i = 0, src.index("{", start)
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_facets_are_counted_against_the_filtered_rows():
    """Not against STATE.rows wholesale, which is what made the counts lie."""
    fn = body_of(JS, "renderFacets")
    assert "matches(v, f.key)" in fn, (
        "renderFacets must count each facet against the rows surviving the "
        "OTHER filters, or the numbers describe a roster nobody is looking at")


def test_a_facet_is_excluded_from_its_own_count():
    """Counting against the fully filtered set zeroes every unselected value in
    the group you just used, so a second choice in the same group — Brooklyn OR
    Queens — becomes unreachable."""
    fn = body_of(JS, "matches")
    assert "exceptKey" in fn and "key === exceptKey" in fn


def test_a_ticked_value_survives_at_zero():
    """Otherwise there is no way to untick it."""
    fn = body_of(JS, "renderFacets")
    assert "counts.set(val, 0)" in fn


def test_the_facets_redraw_whenever_the_rows_do():
    """The counts depend on the filters now, so a stale panel is a wrong panel.
    apply() used to redraw them only when it was asked to."""
    fn = body_of(JS, "apply")
    assert "renderFacets()" in fn
    assert "refreshFacets" not in JS, "the opt-in flag should be gone"


def test_focus_is_handed_back_after_the_redraw():
    """Redrawing steals focus from the checkbox you just used, which makes the
    filter panel unusable from the keyboard."""
    fn = body_of(JS, "apply")
    assert "activeElement" in fn and ".focus()" in fn


def test_the_roster_and_the_dashboard_agree_on_the_rule():
    """The dashboard states it outright; the roster should not quietly differ."""
    assert "surviving every OTHER facet" in APP or "never its own" in APP
    assert "exceptKey" in JS
