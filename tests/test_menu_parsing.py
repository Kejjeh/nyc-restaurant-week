"""Grading a menu parse, and not counting the same dish twice.

Both bugs are the same mistake: counting occurrences where the thing being
counted is a distinct item.
"""
import json
from pathlib import Path

import pytest

from parse_menus import dedupe, grade

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# grade
# --------------------------------------------------------------------------

def test_one_heading_printed_twice_is_not_two_courses():
    """Harta's parse was courses ['Desserts', 'Desserts'], graded `full`. The
    parser had found a single heading late in the PDF and swallowed everything
    after it as dessert items -- 'graham cracker crust', 'market berries,
    chantilly cream'. The docstring promises ">=2 course SECTIONS detected"."""
    text = "x" * 200
    items = [{"course": "Desserts", "dish": f"d{i}", "description": None,
              "supplement_price": None} for i in range(6)]
    assert grade(text, ["Desserts", "Desserts"], items) == "partial"
    assert grade(text, ["Desserts", "Mains"], items) == "full"


def test_a_repeated_heading_still_counts_once_among_several():
    text = "x" * 200
    items = [{"course": "c", "dish": f"d{i}", "description": None,
              "supplement_price": None} for i in range(6)]
    assert grade(text, ["Starters", "Starters", "Mains", "Mains"], items) == "full"


@pytest.mark.parametrize("text", ["", "   ", "short"])
def test_no_usable_text_is_still_failed(text):
    assert grade(text, ["A", "B"], [{"course": "A", "dish": "x",
                                     "description": None,
                                     "supplement_price": None}] * 6) == "failed"


def test_too_few_dishes_is_partial_however_many_courses():
    assert grade("x" * 200, ["A", "B", "C"], []) == "partial"


# --------------------------------------------------------------------------
# dedupe
# --------------------------------------------------------------------------

def item(course, dish, desc=None):
    return {"course": course, "dish": dish, "description": desc,
            "supplement_price": None}


def test_an_identical_row_is_collapsed_and_order_is_kept():
    got = dedupe([item("A", "one"), item("A", "two"), item("A", "one"),
                  item("A", "three")])
    assert [x["dish"] for x in got] == ["one", "two", "three"]


def test_the_same_dish_in_a_different_course_is_a_different_row():
    got = dedupe([item("Starters", "soup"), item("Mains", "soup")])
    assert len(got) == 2


def test_the_same_dish_with_a_different_description_is_a_different_row():
    got = dedupe([item("A", "soup", "with bread"), item("A", "soup", "with rice")])
    assert len(got) == 2


def test_dedupe_is_idempotent():
    once = dedupe([item("A", "x"), item("A", "x")])
    assert dedupe(once) == once


# --------------------------------------------------------------------------
# the database that was built from them
# --------------------------------------------------------------------------

def test_no_menu_in_the_database_repeats_a_dish():
    """11% of all parsed rows were exact repeats -- a PDF printing the same
    section on two pages, or carrying lunch and dinner under one set of
    headings. Anyone counting dishes off this dataset over-counted."""
    import sqlite3
    db = ROOT / "data" / "processed" / "restaurant_week.sqlite"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dups = con.execute(
            "SELECT COUNT(*) FROM (SELECT menu_id, course, dish, description,"
            " COUNT(*) n FROM menu_items GROUP BY 1,2,3,4 HAVING n > 1)"
        ).fetchone()[0]
    finally:
        con.close()
    assert dups == 0


def test_no_menu_is_graded_full_on_one_distinct_course():
    """The grade reaches the published CSV as menu_parse_quality, so it is part
    of the dataset even though the dashboard collapses it to 'pdf'."""
    import sqlite3
    db = ROOT / "data" / "processed" / "restaurant_week.sqlite"
    parsed = json.loads(
        (ROOT / "data" / "raw" / "menus" / "parsed.json").read_text(encoding="utf-8"))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        full = {s for (s,) in con.execute(
            "SELECT restaurant_slug FROM menus WHERE parse_quality = 'full'")}
    finally:
        con.close()
    bad = [s for s in full
           if len(set(parsed.get(s, {}).get("courses") or [])) < 2]
    assert not bad, f"graded full on fewer than two distinct courses: {bad}"
