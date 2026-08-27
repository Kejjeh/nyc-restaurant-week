"""What the weekly report says about the roster.

The listing diff next door cannot be tested -- it reads two dated snapshots and
prints. That is exactly what let a wrong `parents[]` index silently skip the
entire SHORTLIST ALERTS block on every run for weeks, with the report happily
printing everything else. So the roster comparison is a pure function and these
are its cases.
"""
import pytest

from diff_report import roster_changes


def venue(slug, name=None, status="unknown", awards=1, honour="NYT Top 100",
          seeded="michelin"):
    return {"slug": slug, "name": name or slug.title(), "status": status,
            "status_source": "google: CLOSED_PERMANENTLY" if status == "closed" else None,
            "award_count": awards, "top_honor_label": honour, "seeded_from": seeded}


def payload(venues, **counts):
    base = {"unverified": 0, "mappable": len(venues)}
    base.update(counts)
    return {"venues": venues, "counts": base}


def test_an_identical_roster_reports_nothing():
    p = payload([venue("estela"), venue("lilia")])
    d = roster_changes(p, p)
    assert not any(d[k] for k in ("added", "removed", "closed", "reopened",
                                  "gained", "lost"))
    assert d["counts"] == {}


def test_a_closure_is_detected():
    """The single most booking-relevant fact this repo holds. It is not a
    listing change, so nothing else in the report would ever print it."""
    was = payload([venue("estela", status="open")])
    now = payload([venue("estela", status="closed")])
    d = roster_changes(now, was)
    assert d["closed"] == ["estela"]
    assert d["reopened"] == []


def test_going_unverified_to_closed_still_counts_as_closing():
    """Most of the roster starts `unknown`, so the first Places run is when a
    long-closed restaurant is discovered. That must report as a closure."""
    d = roster_changes(payload([venue("x", status="closed")]),
                       payload([venue("x", status="unknown")]))
    assert d["closed"] == ["x"]


def test_going_unverified_to_open_is_not_a_reopening():
    """Nothing reopened -- we simply looked for the first time. Reporting it as
    a reopening would invent an event out of our own ignorance."""
    d = roster_changes(payload([venue("x", status="open")]),
                       payload([venue("x", status="unknown")]))
    assert d["reopened"] == []
    assert d["closed"] == []


def test_gained_and_lost_recognition():
    was = payload([venue("a", awards=2), venue("b", awards=5)])
    now = payload([venue("a", awards=4), venue("b", awards=3)])
    d = roster_changes(now, was)
    assert d["gained"] == ["a"]
    assert d["lost"] == ["b"]


def test_added_and_removed_venues():
    d = roster_changes(payload([venue("a"), venue("c")]),
                       payload([venue("a"), venue("b")]))
    assert (d["added"], d["removed"]) == (["c"], ["b"])


def test_resolution_progress_is_reported_so_769_does_not_quietly_stay_769():
    d = roster_changes(payload([venue("a")], unverified=700, mappable=705),
                       payload([venue("a")], unverified=769, mappable=634))
    assert d["counts"] == {"unverified": (769, 700), "mappable": (634, 705)}


def test_a_count_that_did_not_move_is_not_reported():
    d = roster_changes(payload([venue("a")], unverified=5),
                       payload([venue("a")], unverified=5))
    assert "unverified" not in d["counts"]


def test_an_empty_previous_payload_reads_as_everything_being_new():
    d = roster_changes(payload([venue("a"), venue("b")]), {"venues": [], "counts": {}})
    assert d["added"] == ["a", "b"]
    assert d["removed"] == []


def test_previous_payload_returns_none_rather_than_raising_when_git_cannot_help():
    """A shallow checkout or a first run is not an error, and must not take the
    whole weekly report down with it."""
    from pathlib import Path
    from diff_report import previous_payload
    assert previous_payload(Path("/nonexistent/never/venues.json")) is None


# --------------------------------------------------------------------------
# rulings a human owes the pipeline
# --------------------------------------------------------------------------

def test_count_rulings_reads_both_file_shapes():
    """recognition_review.json is {source: [...]}, venue_merge_review.json is
    {section: [...]} with a _doc string mixed in. Same question either way."""
    from diff_report import count_rulings
    assert count_rulings({"michelin": [1, 2], "nyt": [], "james_beard": [1]}) == {
        "michelin": 2, "james_beard": 1}
    assert count_rulings({"_doc": "words", "refused": [1], "confirm": []}) == {"refused": 1}
    assert count_rulings([1, 2, 3]) == {"items": 3}
    assert count_rulings(None) == {"items": 0}


def test_settled_records_are_not_counted_as_waiting():
    """A fold already applied and a ruling already written into
    venue_aliases.json are recorded for auditing, not waiting on anyone.
    Reporting them as waiting trains people to ignore the number."""
    from diff_report import pending_rulings
    lines = pending_rulings()
    joined = " ".join(lines)
    assert "folded_spelling_variants" not in joined
    assert "ruled_out_by_venue_aliases" not in joined


def test_pending_rulings_names_the_file_to_open():
    """A file nothing points at is a file nobody opens."""
    from diff_report import pending_rulings
    lines = pending_rulings()
    assert lines, "the review files exist; the report must mention them"
    for line in lines:
        assert "nothing waiting" in line or line.rstrip().endswith(".json")
