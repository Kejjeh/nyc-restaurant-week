"""A blank panel is not a state.

`loadLeaflet` resolved on the script tag's `load` event and rejected on its
`error` event. A script tag fires `error` when a request is REFUSED. It fires
nothing at all when the request is accepted and never answered — a stalled CDN,
a captive portal, a proxy that swallows it, a phone on one bar. That is how
maps actually fail, and it left the promise pending forever.

Measured against a route that accepts the connection and never replies: at 3,
10 and 25 seconds the map panel had zero child elements and empty text. No
message, no spinner, nothing. The honest failure copy — "The map needs to load
Leaflet from unpkg.com and tiles from CARTO … The list view works offline" —
existed the whole time and could not be reached by the common failure.

A second defect in the same function: `MAP_LOADING` cached the promise, so a
single rejection poisoned the map for the rest of the session with no way to
retry.

No JS harness here, so the browser runs are the verification and these hold the
shape that makes them possible.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def body_of(name):
    start = APP.index(f"function {name}(")
    depth, i = 0, APP.index("{", start)
    for j in range(i, len(APP)):
        if APP[j] == "{":
            depth += 1
        elif APP[j] == "}":
            depth -= 1
            if depth == 0:
                return APP[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_the_load_has_a_deadline():
    fn = body_of("loadLeaflet")
    assert "LEAFLET_TIMEOUT_MS" in fn
    assert "setTimeout(" in fn


def test_the_deadline_is_a_named_constant_in_seconds_a_person_would_wait():
    import re
    m = re.search(r"const LEAFLET_TIMEOUT_MS = (\d+);", APP)
    assert m, "the timeout should be named, not buried in a call"
    ms = int(m.group(1))
    assert 3000 <= ms <= 30000, f"{ms}ms is not a plausible wait"


def test_it_settles_exactly_once():
    """load, error and the deadline race each other; two of them firing must
    not resolve a promise that already rejected."""
    fn = body_of("loadLeaflet")
    assert "settled" in fn
    assert "clearTimeout(timer)" in fn


def test_all_three_outcomes_are_wired():
    fn = body_of("loadLeaflet")
    assert "js.onload" in fn and "js.onerror" in fn
    assert fn.count("finish(") >= 3


def test_a_failure_is_not_cached():
    """One bad moment on the network must not break the map for the session."""
    fn = body_of("loadLeaflet")
    assert "MAP_LOADING = null" in fn
    assert ".catch(" in fn


def test_the_panel_says_something_while_it_waits():
    fn = body_of("openMap")
    assert "Loading the map" in fn
    i, j = fn.index("Loading the map"), fn.index("await loadLeaflet()")
    assert i < j, "the loading line must go up before the wait, not after"


def test_the_loading_line_is_cleared_before_leaflet_takes_the_container():
    fn = body_of("openMap")
    i = fn.index("L.map('map'")
    assert "$('#map').textContent = ''" in fn[:i]


def test_the_failure_copy_still_names_both_third_parties_and_the_way_out():
    fn = body_of("openMap")
    assert "unpkg.com" in fn and "CARTO" in fn
    assert "list view works offline" in fn
