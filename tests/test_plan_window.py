"""A programme has two ends and the planner only ever guarded one.

`dateIssue` refused a date in the past and a date after the restaurant's window
closed. It never refused a date BEFORE the programme opened.

The listing appears when a season is announced, weeks before it starts, so
between announcement and opening day every date from today onwards was offered
as bookable. Driven against a two-season copy of the site: on 1 December, for a
season starting 19 January, the planner offered "Tue Dec 1, Wed Dec 2, Thu Dec
3, Fri Dec 4" and 60 more — seven weeks of dates for a prix fixe that does not
exist yet.

It is not only a changeover problem. Driving the LIVE payload at 1 July, three
weeks before this season's own 20 July start, showed the same thing.

`season_start` has been in the payload the whole time and nothing read it.

There is no JS harness in this repo, so the browser runs are the verification
and these hold the shape that makes them possible.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")


def body_of(name):
    start = APP.index(f"function {name}(")
    depth = 0
    i = APP.index("{", start)
    for j in range(i, len(APP)):
        if APP[j] == "{":
            depth += 1
        elif APP[j] == "}":
            depth -= 1
            if depth == 0:
                return APP[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_the_planner_refuses_a_date_before_the_season_opens():
    fn = body_of("dateIssue")
    assert "DATA.season_start" in fn, "the planner does not read season_start"
    assert "before the programme opens" in fn


def test_it_guards_both_ends():
    fn = body_of("dateIssue")
    assert "in the past" in fn
    assert "after it closes" in fn
    assert "season_start" in fn


def test_the_start_guard_does_not_bind_your_own_places():
    """They are not in the programme, so none of its rules apply to them --
    the same reason the Saturday and Sunday rules sit below the isMine check."""
    fn = body_of("dateIssue")
    assert fn.index("isMine(r)") < fn.index("if (DATA.season_start")


def test_a_payload_without_the_field_still_plans():
    """A browser holding a payload from before season_start existed must not
    lose every date. The guard is conditional for that reason."""
    fn = body_of("dateIssue")
    # The code occurrence, not the one in the comment above it.
    assert "if (DATA.season_start && iso < DATA.season_start)" in fn


# --- the payload side ------------------------------------------------------

@pytest.fixture(scope="module")
def payload():
    p = ROOT / "docs" / "data" / "restaurants.json"
    if not p.exists():
        pytest.skip("payload not built")
    return json.loads(p.read_text(encoding="utf-8"))


def test_season_start_is_published(payload):
    assert payload.get("season_start")
    assert payload["season_start"] < payload["program_end"]


def test_the_season_bounds_come_from_the_season_file(payload):
    cfg = json.loads((ROOT / "config" / "season.json").read_text(encoding="utf-8"))
    assert payload["season_start"] == cfg["start"]
    assert payload["program_end"] == cfg["end"]
    assert payload["book_by"] == cfg["book_by"]


def test_no_published_end_date_precedes_the_season_start(payload):
    """If one did, the planner would offer nothing at all for that restaurant
    and the two guards would be contradicting each other."""
    bad = [r["slug"] for r in payload["restaurants"]
           if r.get("end_date") and r["end_date"] < payload["season_start"]]
    assert not bad, bad[:10]
