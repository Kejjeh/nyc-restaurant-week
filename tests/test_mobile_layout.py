"""The page must not slip sideways under your thumb.

Both pages scrolled horizontally at a 320px viewport — an iPhone SE, and the
narrow end of what this site is actually used on, which is a phone, outside a
restaurant.

    dashboard   scrollWidth 328 vs 320
    roster      scrollWidth 355 vs 320

One element on each, and the same one: `select.sortSel`. A `<select>`'s
intrinsic width is set by its LONGEST option — "Standing — strongest
recognition first" measured 345px — and the mobile rule handed it
`flex: 1 1 auto; max-width: none`.

Neither `max-width: none` nor a flex-shrink of 1 lets it shrink: a flex item
will not go below its own content width without `min-width: 0`. That is the
part that was missing, and it is the whole fix. On a list you read by scrolling
vertically, a page that also moves horizontally is the worst kind of wrong.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "docs" / "styles.css").read_text(encoding="utf-8")
DOCS = ROOT / "docs"


def rule(selector, css=CSS):
    """The last declaration block for a selector — the one that wins."""
    hits = [m for m in re.finditer(
        r"(^|[,{}\s])" + re.escape(selector) + r"\s*(,[^{]*)?\{([^}]*)\}", css, re.M)]
    assert hits, f"no rule for {selector}"
    return hits[-1].group(3)


def test_the_sort_select_can_shrink_on_a_phone():
    """min-width: 0 is the load-bearing declaration, not max-width."""
    block = rule(".sortSel")
    assert "min-width: 0" in block, (
        "a flex item will not shrink below its content without min-width: 0")


def test_it_is_also_capped():
    block = rule(".sortSel")
    assert "max-width: none" not in block
    assert "max-width: 100%" in block


def phone_media_blocks():
    """Every `@media (max-width: 640px)` body, by brace matching.

    There are three of them in this stylesheet, so a test that takes the first
    one checks a block the rule is not in — which is how this test failed on
    correct code before I fixed the test rather than the CSS.
    """
    out, i = [], 0
    needle = "@media (max-width: 640px)"
    while True:
        i = CSS.find(needle, i)
        if i < 0:
            break
        depth, start = 0, CSS.index("{", i)
        for j in range(start, len(CSS)):
            if CSS[j] == "{":
                depth += 1
            elif CSS[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append(CSS[start:j])
                    i = j
                    break
        else:
            raise AssertionError("unbalanced braces in a phone media query")
    assert out, "no phone media query at all"
    return out


def test_the_shrink_lives_in_the_phone_media_query():
    """The desktop rule caps the select at 46vw and should stay that way; only
    the phone layout needs it to shrink."""
    assert any("min-width: 0" in b and ".sortSel" in b for b in phone_media_blocks()), (
        "the shrink is not inside a phone media query")
    desktop = CSS[:CSS.index("@media (max-width: 640px)")]
    assert "min-width: 0" not in rule(".sortSel", desktop), (
        "the desktop rule should not need the shrink")


def test_both_pages_share_the_stylesheet_that_carries_the_fix():
    """The roster overflowed further than the dashboard and has no .sortSel
    rule of its own — it inherits this one, so one fix covers both."""
    for page in ("restaurant-week.html", "index.html"):
        html = (DOCS / page).read_text(encoding="utf-8")
        assert "styles.css" in html, f"{page} does not load styles.css"
    venues_css = (DOCS / "venues.css").read_text(encoding="utf-8")
    assert "sortSel" not in venues_css, (
        "the roster grew its own rule; this test's premise no longer holds")


@pytest.mark.parametrize("page", ["restaurant-week.html", "index.html"])
def test_the_stylesheet_is_cache_busted_together(page):
    """Both pages must ask for the same build of it, or one keeps the bug."""
    html = (DOCS / page).read_text(encoding="utf-8")
    m = re.search(r"styles\.css\?v=(\d+)", html)
    assert m, f"{page} loads styles.css without a version"
    other = "index.html" if page == "restaurant-week.html" else "restaurant-week.html"
    m2 = re.search(r"styles\.css\?v=(\d+)",
                   (DOCS / other).read_text(encoding="utf-8"))
    assert m.group(1) == m2.group(1), "the two pages pin different builds"
