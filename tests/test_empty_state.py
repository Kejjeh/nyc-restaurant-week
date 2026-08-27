"""Say which thing found nothing.

The dashboard answered every empty result with "Nothing matches those filters."
beside a Clear filters button. Searching "le pigen" — a typo, the commonest way
to reach zero rows — got that message with no filters the reader had set and
nothing for that button to clear.

The codebase already states the principle, in the comment on the one case it
did handle: a date past the last window "would send you loosening the wrong
ones". It applies to the search box just as much.

The distinction that decides which advice helps is whether the term matches
anything AT ALL, ignoring the filters — not whether filters happen to be set.
The date filter is on by default, so "are filters set" is always true and
answers nothing.

No JS harness here, so the browser runs are the verification and these hold the
shape.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
I = APP.index("$opt('#emptyMsg').textContent")
BLOCK = APP[APP.index("const deadDate", I - 2000):I + 1200]


def test_the_search_term_is_named():
    assert "$('#q').value.trim()" in BLOCK


def test_the_branch_turns_on_whether_the_search_matched_anything():
    """Not on whether filters are set. bookableBy is set by default, so that
    test is always true and distinguishes nothing."""
    assert "queryHits" in BLOCK
    assert "_hay.includes(QUERY)" in BLOCK


def test_a_term_that_matches_nothing_is_sent_to_the_spelling():
    assert "Check the spelling" in BLOCK


def test_a_term_the_filters_removed_says_so_and_counts_them():
    assert "the filters remove them all" in BLOCK
    assert "queryHits === 1" in BLOCK, "one restaurant should not be 'restaurants'"


def test_an_empty_payload_is_not_blamed_on_filters():
    assert "ROWS.length === 0" in BLOCK
    assert "not with anything you set" in BLOCK


def test_the_dead_date_case_is_still_handled_first_of_the_filter_cases():
    """It was right before this and stays right: a date past the last window
    can never match whatever else is set."""
    assert "deadDate" in BLOCK
    assert BLOCK.index("deadDate\n") < BLOCK.index("Check the spelling")


def test_the_plain_filter_case_still_says_filters():
    assert "'Nothing matches those filters.'" in BLOCK


def test_the_count_is_only_paid_for_when_there_is_nothing_to_show():
    """A pass over every row on every keystroke would be a real cost; this one
    runs inside the empty-state branch."""
    assert "QUERY ? ROWS.filter" in BLOCK
