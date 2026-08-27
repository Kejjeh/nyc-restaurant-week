"""Emptying the list is only half of clearing it.

`RENDERED` is the cursor `renderPage()` appends from. `apply()` has always
reset it alongside emptying `#rows`; the two error paths emptied the list and
left the cursor where it was.

That matters because an IntersectionObserver watches `#showMore` with a 600px
root margin. Emptying the list makes the page short, the sentinel comes into
view, the observer fires, and `renderPage()` appends `RESULTS.slice(RENDERED,
RENDERED + PAGE_SIZE)` — the NEXT page, into a list that now has nothing before
it.

Driven against a two-season copy of the site with the incoming season's payload
returning 404:

    before   RENDERED=50   first rows: yingtao, 53, david-burke-tavern
    after    RENDERED=100  first rows: the-wolfe, trattoria-dellarte, barbalu-bklyn

The error said "Still showing Summer 2026" and the list came back starting at
row 51. Yingtao, 53 and David Burke Tavern are ranked picks #1, #2 and #3 — the
most important rows on the page, and the ones a reader would notice missing
last, because a list of 50 restaurants looks exactly like a list of 50
restaurants.

The recovery itself was accidental: the error path meant to show only the
message, and the observer silently undid the clear.
"""
import re
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


def test_clearing_the_list_resets_the_cursor():
    fn = body_of("clearRows")
    assert "RENDERED = 0" in fn
    assert "$('#rows').textContent = ''" in fn


def test_nothing_empties_the_list_without_it():
    """The invariant, stated as a rule: the only place allowed to spell the
    clear out is clearRows itself."""
    others = APP.replace(body_of("clearRows"), "")
    assert "$('#rows').textContent = ''" not in others, (
        "something empties #rows without resetting the pagination cursor")


def test_apply_still_restarts_from_the_top():
    fn = body_of("apply")
    assert "clearRows()" in fn
    assert fn.index("clearRows()") < fn.index("renderPage()")


def test_both_error_paths_go_through_it():
    assert APP.count("clearRows()") >= 3, (
        "apply() and both payload-failure paths should all use it")


def test_the_observer_is_still_what_appends():
    """If the sentinel stops driving renderPage this bug's mechanism is gone,
    and this file should be re-read rather than quietly kept."""
    assert "IntersectionObserver" in APP
    assert "RENDERED < RESULTS.length" in APP


def test_render_page_appends_from_the_cursor():
    fn = body_of("renderPage")
    assert "RESULTS.slice(RENDERED" in fn
    assert "RENDERED += " in fn
