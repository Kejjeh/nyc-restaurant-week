"""A window that ends today is one day, not zero.

Every date test on this site counts inclusively. A restaurant whose window
ends today has not ended (`hasEnded`), still counts as urgent (`isUrgent`), is
still offered that date by the planner (`dateIssue`, `validDates`), and the
countdown tile reads "1 day left" — that last one was itself an audit fix.

`rubric_for` counted exclusively. On the last day of a restaurant's window it
scored 0.0 on the component the row detail labels "Days left to book" — the
same score it gives a restaurant that closed three weeks ago, printed on a page
that says you can still book. 191 restaurants read that way on 16 August; 401
do on 6 September, the last day of the season.

Nothing errored. The number was simply answering a different question than its
label.
"""
from datetime import date, timedelta

import pytest

from export_site_data import _ramp, load_rubric, rubric_for, scoring_day


@pytest.fixture(scope="module")
def cfg():
    return load_rubric()


def row(end_date, **kw):
    r = {"end_date": end_date, "recog_top": None, "subway": {},
         "gap_pct": None, "_rating_pct": None}
    r.update(kw)
    return r


def window(end_date, today, cfg):
    return rubric_for(row(end_date), cfg, date.fromisoformat(today))["window"]


def test_a_window_ending_today_is_not_zero(cfg):
    """The bug, stated as the smallest case that shows it."""
    assert window("2026-08-16", "2026-08-16", cfg) > 0


def test_today_counts_as_a_day(cfg):
    """The contract: an end date N days out is N+1 days of window, because you
    can use today. So a window ending today scores exactly what the ramp gives
    one day, and each further day is one step up."""
    c = cfg["window"]
    for n in range(0, 6):
        end = date(2026, 8, 16) + timedelta(days=n)
        assert window(end.isoformat(), "2026-08-16", cfg) == pytest.approx(
            _ramp(n + 1, c["zero_at_days"], c["full_at_days"])), (
            f"{n} days out should score as {n + 1} days of window")


def test_a_window_that_has_passed_is_zero(cfg):
    """That zero IS a fact, and must stay one -- the rubric's own rule is that
    a component scores 0 only when the zero is true."""
    assert window("2026-08-15", "2026-08-16", cfg) == 0
    assert window("2026-07-01", "2026-08-16", cfg) == 0


def test_it_does_not_go_negative_or_wrap(cfg):
    for ed in ("2026-08-15", "2026-08-01", "2025-01-01"):
        assert window(ed, "2026-08-16", cfg) == 0


def test_a_restaurant_with_no_end_date_is_unknown_not_zero(cfg):
    """Imputed at the component mean, never scored as a closed window."""
    assert rubric_for(row(None), cfg, date(2026, 8, 16))["window"] is None


def test_a_full_window_still_tops_out(cfg):
    c = cfg["window"]
    far = date(2026, 8, 16) + timedelta(days=c["full_at_days"])
    assert window(far.isoformat(), "2026-08-16", cfg) == 100.0


def test_the_component_never_exceeds_the_scale(cfg):
    for n in range(0, 90):
        d = date(2026, 8, 16) + timedelta(days=n)
        v = window(d.isoformat(), "2026-08-16", cfg)
        assert 0.0 <= v <= 100.0


def test_scoring_day_still_freezes_at_program_end():
    """Past the last extension week the countdown is over for everyone alike,
    and a post-season re-export must not reshuffle every published grade."""
    assert scoring_day(date(2027, 1, 1), "2026-09-06") == date(2026, 9, 6)
    assert scoring_day(date(2026, 8, 1), "2026-09-06") == date(2026, 8, 1)


def test_the_config_says_which_way_it_counts(cfg):
    """The cut-points are configurable, so the convention has to be written
    where somebody changing them will read it."""
    assert "INCLUSIVE" in cfg["window"]["_doc"].upper()
