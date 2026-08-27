"""A preset named after an honour must return everyone who holds it.

The roster's presets — "Michelin starred", "Bib Gourmand", "NYT Top 100",
"Beard winners" — filtered on `top_honor_label`, which is the single HIGHEST
honour a venue holds, not the set of honours it holds. Those are different
questions whenever a venue holds two, and config/awards.json scores a James
Beard win (88) above one Michelin star (84), so Daniel, Gramercy Tavern,
Semma, Café Boulud, Le Coucou, Meju and Four Horsemen all sit outside a filter
called "Michelin starred". The NYT case is the plainest: a button labelled
"NYT Top 100" returned 76 rows.

Nothing errored, and the counts printed beside the facet values were correct
for the question the code was actually asking. app.js made the opposite choice
deliberately for the dashboard ("By TIER, not by source") and says so; this is
the roster catching up.

Two kinds of test here: source contracts over docs/venues.js (there is no JS
harness, so the browser run is the real verification), and arithmetic over the
published payload, which needs no JS at all to state the bug.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "docs" / "venues.js").read_text(encoding="utf-8")
PAYLOAD = ROOT / "docs" / "data" / "venues.json"

PRESET_HONOURS = {
    "Michelin starred": {"One Michelin star", "Two Michelin stars",
                         "Three Michelin stars"},
    "Bib Gourmand": {"Bib Gourmand"},
    "NYT Top 100": {"NYT Top 100"},
    "Beard winners": {"James Beard winner"},
}


@pytest.fixture(scope="module")
def payload():
    if not PAYLOAD.exists():
        pytest.skip("docs/data/venues.json not built")
    return json.loads(PAYLOAD.read_text(encoding="utf-8"))


def held(venue, honors):
    """Every honour label the venue's own award records carry."""
    out = []
    for a in venue["recognition"]:
        label = (honors.get(f"{a['source']}:{a['level']}") or {}).get("label")
        if label and label not in out:
            out.append(label)
    return out


# --- source contracts ------------------------------------------------------

def test_the_honours_held_facet_exists():
    assert "honour_held" in JS
    assert "function honoursHeld(" in JS


def test_no_preset_filters_on_the_highest_honour():
    """That is the bug, stated as a rule. `top_honor_label` stays available as
    a facet — "what is the best it holds" is a real question — but no button
    named after an honour may answer it that way."""
    block = JS[JS.index("const PRESETS = ["):JS.index("function setFilter(")]
    assert "top_honor_label" not in block, (
        "a preset is filtering on the single highest honour again")
    for label in PRESET_HONOURS:
        assert f"'{label}'" in block, f"the {label!r} preset went missing"


def test_honours_held_reads_the_payload_vocabulary():
    """Not a list of honour names spelled out in the JS. config/awards.json is
    the one place an honour gets its English, and the payload carries the map."""
    fn = JS[JS.index("function honoursHeld("):JS.index("const FACETS = [")]
    assert "STATE.data.honors[" in fn
    assert "v.recognition" in fn


# --- the payload, with no JS involved --------------------------------------

def test_the_two_questions_actually_diverge_here(payload):
    """The fix is only load-bearing if this roster contains venues whose best
    honour is not the one the preset names. It does, and if a future data pull
    ever stops containing them the fix costs nothing -- but a test that passed
    vacuously would stop saying anything, so it says which cases it found."""
    honors = payload["honors"]
    diverging = {}
    for label, wanted in PRESET_HONOURS.items():
        by_top = {v["slug"] for v in payload["venues"]
                  if v["top_honor_label"] in wanted}
        by_held = {v["slug"] for v in payload["venues"]
                   if wanted & set(held(v, honors))}
        assert by_top <= by_held, (
            f"{label}: a venue whose HIGHEST honour is {sorted(wanted)} does "
            f"not hold it — the two fields disagree")
        if by_held - by_top:
            diverging[label] = sorted(by_held - by_top)
    assert diverging, (
        "no venue holds an honour above the one a preset names; the presets "
        "would be equivalent either way")


def test_the_nyt_top_100_really_is_100(payload):
    """The list has a fixed length, which makes it the one honour where the
    right answer is knowable without trusting our own join."""
    honors = payload["honors"]
    n = sum(1 for v in payload["venues"] if "NYT Top 100" in held(v, honors))
    assert n == 100, f"the NYT Top 100 join yielded {n} restaurants"


def test_the_highest_honour_is_always_one_of_the_honours_held(payload):
    """If it is not, the two groups disagree about the same venue and one of
    them is reading the wrong field."""
    honors = payload["honors"]
    for v in payload["venues"]:
        if not v["top_honor_label"]:
            continue
        assert v["top_honor_label"] in held(v, honors), (
            f"{v['slug']}: top honour {v['top_honor_label']!r} is not among "
            f"its own award records")
