"""A fetch that is accepted and never answered hangs forever.

`res.ok` needs a response. A thrown network error needs a response. Neither
arrives when a request is accepted and then abandoned — a stalled CDN, a
captive portal, a phone on one bar — so a payload fetch with no deadline waits
without limit and nothing downstream of it ever runs.

The roster's catch is well written and could not be reached that way: the page
sat blank, with not even a loading line, indefinitely. The dashboard fared
slightly better and said "Loading the season…" — forever.

This is the same hole the map's Leaflet load had, fixed one wave earlier. The
payload fetch those pages sit behind was not checked at the time. It is the
same shape and it gets the same treatment.

No JS harness here, so the browser runs are the verification and these hold the
shape that makes them possible.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
VENUES = (ROOT / "docs" / "venues.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("src,name", [(APP, "app.js"), (VENUES, "venues.js")])
def test_the_payload_fetch_has_a_deadline(src, name):
    assert "FETCH_TIMEOUT_MS" in src, f"{name} fetches the payload without a deadline"
    assert "AbortController" in src
    assert "ctl.abort()" in src


@pytest.mark.parametrize("src,name", [(APP, "app.js"), (VENUES, "venues.js")])
def test_the_deadline_is_named_and_plausible(src, name):
    import re
    m = re.search(r"const FETCH_TIMEOUT_MS = (\d+);", src)
    assert m, f"{name} should name the timeout, not bury it in a call"
    ms = int(m.group(1))
    assert 5000 <= ms <= 60000, f"{name}: {ms}ms is not a plausible wait for a payload"


@pytest.mark.parametrize("src,name", [(APP, "app.js"), (VENUES, "venues.js")])
def test_the_timer_is_always_cleared(src, name):
    """A pending timer that fires after a successful load would abort nothing,
    but leaving one per fetch is how a page accumulates them."""
    assert "clearTimeout(timer)" in src
    assert "finally" in src


@pytest.mark.parametrize("src,name", [(APP, "app.js"), (VENUES, "venues.js")])
def test_a_timeout_reads_as_a_timeout_not_as_an_abort(src, name):
    """"AbortError" is the browser's word for it, not a reader's."""
    assert "AbortError" in src
    assert "did not answer within" in src


def test_the_roster_says_something_while_it_waits():
    """It rendered nothing at all, so a slow connection and a dead one looked
    identical. The dashboard already did this."""
    assert "Loading the roster" in VENUES
    i = VENUES.index("Loading the roster")
    j = VENUES.index("await fetch(DATA_URL")
    assert i < j, "the loading line must go up before the wait"


def test_the_dashboard_still_says_something_while_it_waits():
    assert "Loading the season" in APP


def test_both_failure_paths_clear_the_loading_line():
    """Otherwise the error prints under a line still claiming it is loading.

    Either spelling counts: the dashboard now goes through clearRows(), which
    empties #rows AND resets the pagination cursor -- doing only the first is
    its own bug and has its own test.
    """
    i = VENUES.index("Could not load the roster")
    assert "$('#rows').textContent = ''" in VENUES[max(0, i - 500):i]
    j = APP.index("Could not load ${url}")
    before = APP[max(0, j - 500):j]
    assert "clearRows()" in before or "$('#rows').textContent = ''" in before
