"""Being near the Lexington line must never score worse than being nowhere near it.

The `lex` component had a documented history: scoring 0 for "no 4/5/6 nearby"
was a fixed 15-point tax on 100% of Queens and Staten Island and 78% of
Brooklyn — a fact about the MTA, not about the restaurant. The fix, as stated
in `config/rubric.json` and again in `rubric_for`'s own comment, was that
proximity becomes **a bonus above neutral** and distance **costs nothing**.

Only half of it landed. The no-line case moved to neutral (50), but the ramp
still ran from 0:

    parts["lex"] = _ramp(min(lex), c["worst_minutes"], c["best_minutes"])

so a restaurant twelve minutes from the 6 scored 0.0 while one with no 4/5/6
within walking distance scored 50. **129 restaurants scored below the no-line
neutral for being near the line**, 24 of them at exactly zero — the tax the fix
was written to remove, now charged to the restaurants that are closest to
qualifying.

Nothing errored. The component was internally consistent and pointed the wrong
way at one end.
"""
from datetime import date

import pytest

from export_site_data import MAX_WALK_MIN, load_rubric, rubric_for


@pytest.fixture(scope="module")
def cfg():
    return load_rubric()


def lex(minutes, cfg):
    """minutes = walk to the nearest of the 4/5/6, or None for no line."""
    subway = {} if minutes is None else {"6": minutes, "N": 3}
    r = {"end_date": None, "recog_top": None, "subway": subway,
         "gap_pct": None, "_rating_pct": None}
    return rubric_for(r, cfg, date(2026, 8, 27))["lex"]


def test_no_line_scores_neutral(cfg):
    assert lex(None, cfg) == float(cfg["lex"]["no_lex_score"])


def test_being_near_the_line_never_scores_worse_than_having_none(cfg):
    """The bug, stated as the rule it broke."""
    neutral = lex(None, cfg)
    for m in range(0, MAX_WALK_MIN + 1):
        assert lex(m, cfg) >= neutral, (
            f"{m} minutes from the 4/5/6 scores {lex(m, cfg)}, below the "
            f"{neutral} given to a restaurant with no 4/5/6 at all")


def test_the_worst_walk_scores_exactly_neutral(cfg):
    """Not merely 'not worse'. subway_for drops anything past MAX_WALK_MIN, so
    a restaurant at the cap and one just past it are the same restaurant as far
    as this data goes — they should score the same, and the discontinuity that
    used to sit there was 50 points wide."""
    assert lex(cfg["lex"]["worst_minutes"], cfg) == pytest.approx(lex(None, cfg))


def test_the_best_walk_still_tops_out(cfg):
    assert lex(cfg["lex"]["best_minutes"], cfg) == pytest.approx(100.0)
    assert lex(0, cfg) == pytest.approx(100.0)


def test_closer_is_never_worse(cfg):
    """Monotone: no interior dip."""
    scores = [lex(m, cfg) for m in range(0, MAX_WALK_MIN + 1)]
    assert scores == sorted(scores, reverse=True)


def test_it_stays_on_the_scale(cfg):
    for m in list(range(0, MAX_WALK_MIN + 1)) + [None]:
        assert 0.0 <= lex(m, cfg) <= 100.0


def test_the_ramp_is_read_from_config_not_hardcoded(cfg):
    """Changing the cut-points must move the score, or the config is a lie."""
    tweaked = {**cfg, "lex": {**cfg["lex"], "no_lex_score": 20}}
    assert lex(None, tweaked) == 20.0
    assert lex(cfg["lex"]["worst_minutes"], tweaked) == pytest.approx(20.0)
    assert lex(cfg["lex"]["best_minutes"], tweaked) == pytest.approx(100.0)


def test_the_published_payload_has_no_row_below_neutral():
    """Measured over the real roster, not a synthetic one."""
    import json
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "docs" / "data" / "restaurants.json"
    if not p.exists():
        pytest.skip("payload not built")
    d = json.loads(p.read_text(encoding="utf-8"))
    neutral = float(load_rubric()["lex"]["no_lex_score"])
    bad = []
    for r in d["restaurants"]:
        near = [r["subway"].get(k) for k in ("4", "5", "6") if r.get("subway")]
        near = [x for x in near if x is not None]
        v = r["rubric_parts"].get("lex")
        if near and v is not None and v < neutral:
            bad.append(f"{r['slug']}: {min(near)} min away, scores {v}")
    assert not bad, "\n  ".join([""] + bad[:15])
