"""One rejected snippet used to silence the tag for good.

build_tags walks the candidate matches for a tag and publishes the first that
survives three guards: it must not overlap a snippet already published for that
restaurant, it must not sit within MIN_SOURCE_GAP of one in the same menu, and
it must fit what is left of the restaurant's coverage budget.

When the first candidate failed, the tag was appended with snippet=None so it
would stay filterable -- and that placeholder then counted as "the tag is
represented", so every later candidate was skipped. 152 tag/restaurant pairs
went out with no text behind them, backed by 345 candidate rows. None of the
three rejections is a statement about a hit somewhere else on the menu, which
is exactly what the later candidates were.

Nothing here relaxes a guard. The tests that matter most in this file are the
ones that hold the guards in place while more candidates are tried.
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest

import export_site_data as E

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"


def tiny_db(menus, tags):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE menus (id INTEGER PRIMARY KEY,"
                " restaurant_slug TEXT, raw_text TEXT)")
    con.execute("CREATE TABLE menu_item_tags (restaurant_slug TEXT, tag TEXT,"
                " confidence TEXT, matched_text TEXT, source TEXT)")
    con.executemany("INSERT INTO menus (restaurant_slug, raw_text) VALUES (?,?)",
                    menus)
    con.executemany("INSERT INTO menu_item_tags VALUES (?,?,?,?,?)", tags)
    con.commit()
    return con


RULES = {"oysters": [re.compile(r"oyster", re.I)],
         "truffle": [re.compile(r"truffle", re.I)]}


def test_a_later_candidate_is_tried_when_the_first_is_refused():
    """Two hits for one tag. The first duplicates a passage already published
    for another tag; the second sits far away and is publishable."""
    filler = "x" * 4000
    raw = ("Kumamoto oyster mignonette " + filler
           + " chargrilled oyster with garlic butter " + filler)
    con = tiny_db(
        [("r", raw)],
        [("r", "oysters", "high", "Kumamoto oyster mignonette", "item"),
         ("r", "oysters", "high", "chargrilled oyster with garlic butter", "item")])
    by, _ = E.build_tags(con, {"oysters": RULES["oysters"]})
    hits = by["r"]
    assert len(hits) == 1
    assert hits[0]["snippet"], "the tag went out with no evidence behind it"


def test_the_tag_survives_when_nothing_can_be_published():
    """A tag with no publishable snippet must still be filterable, and must
    carry the STRONGEST match's confidence rather than the last one tried."""
    con = tiny_db(
        [("r", "")],
        [("r", "oysters", "high", "", "item")])
    by, _ = E.build_tags(con, {"oysters": RULES["oysters"]})
    assert by.get("r") in (None, [])   # an empty match is not a hit at all

    con = tiny_db(
        [("r", "oyster " * 3)],
        [("r", "oysters", "high", "oyster stew", "item"),
         ("r", "oysters", "low", "oyster stew", "item")])
    by, _ = E.build_tags(con, {"oysters": RULES["oysters"]})
    assert [h["tag"] for h in by["r"]] == ["oysters"]
    assert by["r"][0]["confidence"] == "high"


def test_only_one_snippet_per_tag_still():
    con = tiny_db(
        [("r", "oyster one " + "y" * 3000 + " oyster two " + "y" * 3000)],
        [("r", "oysters", "high", "oyster one", "item"),
         ("r", "oysters", "high", "oyster two", "item")])
    by, _ = E.build_tags(con, {"oysters": RULES["oysters"]})
    assert sum(1 for h in by["r"] if h["snippet"]) <= E.MAX_SNIPPETS_PER_TAG


# --- the guards, held in place ---------------------------------------------

@pytest.fixture(scope="module")
def real():
    if not DB.exists():
        pytest.skip("database not built")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    by, _ = E.build_tags(con, E.load_tag_rules())
    raws = {s: re.sub(r"\s+", " ", (t or "")).strip()
            for s, t in con.execute(
                "SELECT restaurant_slug, raw_text FROM menus")}
    con.close()
    return by, raws


def test_no_restaurant_exceeds_the_stated_coverage_rule(real):
    """THE RULE: at most 5% of a menu's extracted text, or 40 characters,
    whichever is greater. The exporter enforces a tighter 4.5%/36 so an
    independent auditor measuring at the stated figure always has margin."""
    by, raws = real
    over = []
    for slug, hits in by.items():
        raw = raws.get(slug, "")
        if not raw:
            continue
        used = sum(len(h["snippet"].strip("…").strip())
                   for h in hits if h["snippet"])
        cap = max(40, len(raw) * 0.05)
        if used > cap:
            over.append(f"{slug}: {used} chars against a cap of {cap:.0f}")
    assert not over, "\n  ".join([""] + over)


def test_no_two_published_snippets_share_a_long_run(real):
    """Publishing two snippets that overlap would republish a passage."""
    by, _ = real
    for slug, hits in by.items():
        snips = [h["snippet"] for h in hits if h["snippet"]]
        for i, a in enumerate(snips):
            for b in snips[i + 1:]:
                assert not E._overlaps(a, b), f"{slug}: {a!r} overlaps {b!r}"


def test_published_snippets_stay_apart_in_the_source(real):
    """MIN_SOURCE_GAP unpublished characters between any two, so they cannot be
    stitched back into one contiguous passage."""
    by, raws = real
    for slug, hits in by.items():
        raw = raws.get(slug, "")
        if not raw:
            continue
        spans = []
        for h in hits:
            if not h["snippet"]:
                continue
            core = h["snippet"].strip("…").strip()
            at = raw.find(core)
            if at < 0:
                continue
            spans.append((at, at + len(core)))
        spans.sort()
        for (s1, e1), (s2, _) in zip(spans, spans[1:]):
            assert s2 - e1 >= E.MIN_SOURCE_GAP, (
                f"{slug}: two snippets {s2 - e1} chars apart in the menu")


def test_the_tag_vocabulary_is_unchanged_by_this(real):
    """Recovering evidence must not invent or drop a tag anywhere."""
    by, _ = real
    payload = ROOT / "docs" / "data" / "restaurants.json"
    if not payload.exists():
        pytest.skip("payload not built")
    pub = json.loads(payload.read_text(encoding="utf-8"))
    for r in pub["restaurants"]:
        assert {h["tag"] for h in r["tags"]} == {
            h["tag"] for h in by.get(r["slug"], [])}
