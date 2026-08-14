"""The my-list tracker: restaurants I want to try that are not in the program.

Nothing here touches the network -- resolving a name against Google is a paid
call and a human confirmation, neither of which belongs in a test run. What is
tested is the part that can silently corrupt the dashboard: every row is keyed
by slug, so a hand-chosen slug that collides with a participant's would overwrite
that restaurant's row.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_slugify_matches_the_shape_a_roster_slug_has():
    from places_cli import slugify

    assert slugify("Le Bernardin") == "le-bernardin"
    assert slugify("Café Boulud") == "cafe-boulud"          # accents fold
    assert slugify("  Peter Luger Steak House  ") == "peter-luger-steak-house"
    # apostrophes close up, exactly as the program's own slugs do
    assert slugify("Mark's Off Madison") == "marks-off-madison"
    assert slugify("Bar Primi (Nolita) & Co.") == "bar-primi-nolita-co"
    assert slugify("") == ""


def test_a_slug_that_would_shadow_a_roster_row_is_refused():
    from places_cli import reject_reason, slugify

    roster, places = {"marks-off-madison", "cafe-boulud"}, [{"slug": "lartusi"}]
    assert reject_reason("tatiana", places, roster) is None
    assert "already" in reject_reason("lartusi", places, roster)
    assert "Restaurant Week" in reject_reason(
        slugify("Mark's Off Madison"), places, roster)
    assert reject_reason("", places, roster)


def test_config_round_trips_without_losing_the_doc_or_the_order(tmp_path):
    from places_cli import add_place, load_places, save_places

    path = tmp_path / "places.json"
    save_places({"_doc": "why this file exists", "places": []}, path)
    assert load_places(path) == {"_doc": "why this file exists", "places": []}

    doc = add_place(load_places(path), {"slug": "tatiana", "name": "Tatiana"}, set())
    doc = add_place(doc, {"slug": "lartusi", "name": "L'Artusi"}, set())
    save_places(doc, path)
    back = load_places(path)
    assert [e["slug"] for e in back["places"]] == ["tatiana", "lartusi"]
    assert back["places"][1]["name"] == "L'Artusi"
    assert back["_doc"] == "why this file exists"

    with pytest.raises(SystemExit, match="already"):
        add_place(back, {"slug": "tatiana", "name": "Tatiana"}, set())
    with pytest.raises(SystemExit, match="Restaurant Week"):
        add_place(back, {"slug": "blue-fin", "name": "Blue Fin"}, {"blue-fin"})
    # a refusal leaves both the loaded doc and the file exactly as they were
    assert len(back["places"]) == 2 and len(load_places(path)["places"]) == 2


def test_remove_drops_the_named_slug_and_nothing_else():
    from places_cli import remove_place

    doc = {"_doc": "d", "places": [{"slug": "tatiana"}, {"slug": "lartusi"}]}
    assert remove_place(doc, "tatiana")["places"] == [{"slug": "lartusi"}]
    assert remove_place(doc, "tatiana")["_doc"] == "d"
    with pytest.raises(SystemExit, match="not on the list"):
        remove_place(doc, "never-added")
    assert len(doc["places"]) == 2


def test_roster_slugs_are_read_from_the_built_db_so_the_check_is_current():
    from places_cli import roster_slugs

    slugs = roster_slugs()
    assert "blue-fin" in slugs and "marks-off-madison" in slugs
    assert "tatiana" not in slugs
    assert len(slugs) == 636


def test_the_committed_config_starts_empty_and_documents_the_entry_shape():
    doc = json.loads((ROOT / "config" / "places.json").read_text(encoding="utf-8"))
    assert doc["places"] == []
    assert "slug" in doc["_doc"] and "place_id" in doc["_doc"]
