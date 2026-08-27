"""Whose licence is it?

The outdoor register is matched on name similarity plus distance, and a
restaurant's own name frequently contains the neighbourhood it sits in. That is
enough for two different businesses a block apart to look like each other.
"""
import sys

import pytest

from export_site_data import OUT_NAME_MIN, _out_sim, outdoor_for


def lic(name, lat, lng, street="225 PARK AVENUE SOUTH", sidewalk=False, roadway=False):
    return {"name": name, "legal": name, "street": street, "lat": lat, "lng": lng,
            "sidewalk": sidewalk, "roadway": roadway}


# Boucherie Union Square, 225 Park Ave South. Its own licence is sidewalk-only.
# Union Square Cafe is 60m away at 101 East 19 Street with the roadway licence.
HERE = (40.7368, -73.9890)
NEARBY = (40.7373, -73.9889)


def test_a_neighbourhood_in_the_name_is_enough_to_match_the_wrong_business():
    """The premise. Not a bug on its own -- the fix is downstream of it."""
    sim = _out_sim("Boucherie Union Square", "UNION SQUARE CAFE/ DAILY PROVISIONS")
    assert sim >= OUT_NAME_MIN, (
        f"similarity {sim} no longer clears {OUT_NAME_MIN}; this test's premise is stale")


def test_flags_are_not_borrowed_from_another_business():
    """Boucherie Union Square was published with roadway seating. It has none:
    that licence belongs to Union Square Cafe, a block away. The row named
    BOUCHERIE as its evidence and then contradicted it."""
    got = outdoor_for("Boucherie Union Square", "225 Park Ave. South", *HERE, [
        lic("BOUCHERIE", *HERE, sidewalk=True),
        lic("UNION SQUARE CAFE/ DAILY PROVISIONS", *NEARBY,
            street="101 EAST 19 STREET", roadway=True),
    ])
    assert got["licence_name"] == "BOUCHERIE"
    assert got["sidewalk"] is True
    assert got["roadway"] is False, "roadway borrowed from Union Square Cafe"


def test_one_business_with_two_rows_still_folds_together():
    """The case the fold exists for: sidewalk and roadway are separate rows in
    the register, so a place holding both appears twice."""
    got = outdoor_for("Boucherie", "225 Park Ave. South", *HERE, [
        lic("BOUCHERIE", *HERE, sidewalk=True),
        lic("BOUCHERIE", 40.7369, -73.9891, roadway=True),
    ])
    assert (got["sidewalk"], got["roadway"]) == (True, True)


def test_the_named_licence_is_the_one_the_flags_came_from():
    """The invariant the bug broke: a row's evidence must support its claim."""
    got = outdoor_for("Boucherie Union Square", "225 Park Ave. South", *HERE, [
        lic("BOUCHERIE", *HERE, sidewalk=True),
        lic("UNION SQUARE CAFE/ DAILY PROVISIONS", *NEARBY,
            street="101 EAST 19 STREET", roadway=True, sidewalk=True),
    ])
    named = [lic("BOUCHERIE", *HERE, sidewalk=True)]
    alone = outdoor_for("Boucherie", "225 Park Ave. South", *HERE, named)
    assert (got["sidewalk"], got["roadway"]) == (alone["sidewalk"], alone["roadway"])


def test_no_coordinates_or_no_licences_means_no_claim():
    assert outdoor_for("X", "1 Main St", None, None, [lic("X", *HERE)]) is None
    assert outdoor_for("X", "1 Main St", *HERE, []) is None


def test_the_published_payload_never_credits_a_flag_to_a_silent_licence():
    """Against the real payload: every restaurant claiming outdoor seating must
    get it from a licence naming that same business."""
    import json
    from pathlib import Path
    import export_site_data as E
    root = Path(__file__).resolve().parents[1]
    payload = root / "docs" / "data" / "restaurants.json"
    if not payload.exists():
        pytest.skip("payload not built")
    licences = E.load_outdoor()
    bad = []
    for r in json.loads(payload.read_text(encoding="utf-8"))["restaurants"]:
        o = r.get("outdoor")
        if not o or not o.get("licensed") or r.get("lat") is None:
            continue
        key = E._out_norm(o["licence_name"])
        owned = [L for L in licences
                 if E._out_norm(L["name"] or L["legal"]) == key
                 and E._haversine_m(r["lat"], r["lng"], L["lat"], L["lng"]) <= E.OUT_FAR_M]
        if o["sidewalk"] and not any(L["sidewalk"] for L in owned):
            bad.append(f"{r['slug']}: sidewalk not held by {o['licence_name']!r}")
        if o["roadway"] and not any(L["roadway"] for L in owned):
            bad.append(f"{r['slug']}: roadway not held by {o['licence_name']!r}")
    assert not bad, bad
