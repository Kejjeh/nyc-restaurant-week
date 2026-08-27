"""Do not tell a reader a restaurant closed for good when Google said otherwise.

`google_row` computed:

    "closed": m.get("business_status") not in (None, "OPERATIONAL")

which collapses three different statements into one boolean: CLOSED_PERMANENTLY,
CLOSED_TEMPORARILY, and Google not saying anything at all.

The dashboard renders that boolean as a red pill reading "permanently closed",
titled "Google reports this location as permanently closed". Antica Pesa's
cached record says CLOSED_TEMPORARILY. The site stated the opposite of its own
cited source, about a named restaurant, in the one way that stops somebody
booking.

The absent case broke the project's other standing rule -- null means unknown,
never false -- by publishing `closed: false` for a restaurant whose status
Google never gave.
"""
import json
from pathlib import Path

import pytest

from export_site_data import google_row

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "docs" / "app.js").read_text(encoding="utf-8")
VENUES_JS = (ROOT / "docs" / "venues.js").read_text(encoding="utf-8")


def rec(business_status):
    m = {"rating": 4.2, "user_ratings_total": 753, "place_id": "p",
         "name": "X", "lat": 40.7, "lng": -74.0}
    if business_status is not None:
        m["business_status"] = business_status
    return {"accepted": True, "matched": m}


def test_permanently_closed_is_closed():
    assert google_row(rec("CLOSED_PERMANENTLY"), 4.4)["closed"] is True


def test_temporarily_closed_is_not_closed():
    """The bug. It has not shut for good and the page must not say it has."""
    row = google_row(rec("CLOSED_TEMPORARILY"), 4.4)
    assert row["closed"] is False
    assert row["status"] == "CLOSED_TEMPORARILY"


def test_operational_is_not_closed():
    row = google_row(rec("OPERATIONAL"), 4.4)
    assert row["closed"] is False
    assert row["status"] == "OPERATIONAL"


def test_an_absent_status_is_unknown_not_open():
    """Null means unknown here as it does everywhere else in this payload."""
    row = google_row(rec(None), 4.4)
    assert row["closed"] is None
    assert row["status"] is None


def test_the_verbatim_answer_is_published():
    """So the page can quote the source rather than paraphrase it."""
    for st in ("OPERATIONAL", "CLOSED_TEMPORARILY", "CLOSED_PERMANENTLY"):
        assert google_row(rec(st), 4.4)["status"] == st


# --- what the pages say ----------------------------------------------------

def test_the_dashboard_distinguishes_the_two_closures():
    assert "CLOSED_TEMPORARILY" in APP
    assert "temporarily closed" in APP
    i = APP.index("permanently closed")
    j = APP.index("CLOSED_TEMPORARILY")
    assert abs(i - j) < 1200, (
        "the temporary case should be handled beside the permanent one")


def test_the_roster_does_not_strike_through_a_temporary_closure():
    """Line-through is this page's mark for gone for good."""
    assert "Temporarily closed" in VENUES_JS
    assert "CLOSED_TEMPORARILY" in VENUES_JS
    css = (ROOT / "docs" / "venues.css").read_text(encoding="utf-8")
    assert ".pill.status-closed.temporary" in css
    block = css[css.index(".pill.status-closed.temporary"):][:160]
    assert "text-decoration: none" in block


# --- the published payload -------------------------------------------------

def test_no_published_row_claims_a_closure_its_source_did_not():
    payload = ROOT / "docs" / "data" / "restaurants.json"
    cache = ROOT / "data" / "raw" / "google"
    if not payload.exists() or not cache.exists():
        pytest.skip("payload or cache not built")
    d = json.loads(payload.read_text(encoding="utf-8"))
    said = {}
    for f in cache.glob("*.json"):
        r = json.loads(f.read_text(encoding="utf-8"))
        if r.get("accepted"):
            said[r["slug"]] = (r.get("matched") or {}).get("business_status")
    bad = []
    for r in d["restaurants"]:
        g = r.get("google")
        if not g:
            continue
        actual = said.get(r["slug"])
        if g["closed"] and actual != "CLOSED_PERMANENTLY":
            bad.append(f"{r['slug']}: published closed, Google said {actual!r}")
        if g["closed"] is None and actual is not None:
            bad.append(f"{r['slug']}: published unknown, Google said {actual!r}")
        if g.get("status") != actual:
            bad.append(f"{r['slug']}: status {g.get('status')!r} != {actual!r}")
    assert not bad, "\n  ".join([""] + bad[:10])
