"""The venue resolver's acceptance rule.

fetch_google_ratings.py judges a candidate on DISTANCE, because every
Restaurant Week row arrives with coordinates. Nothing here has coordinates, so
this file's rule is the weaker one and has to refuse much more readily. These
tests pin what it will and will not accept without ever making a request.
"""
import pytest

from resolve_venues import STATUS_FROM_GOOGLE, in_nyc, judge_no_coords


def cand(name, address=None, lat=40.72, lng=-73.99):
    return {"name": name, "address": address, "lat": lat, "lng": lng}


def test_a_result_outside_the_five_boroughs_is_never_accepted():
    # There is a Balthazar in London and one in Miami; neither is ours.
    ok, why = judge_no_coords(cand("Balthazar", lat=51.51, lng=-0.12), "Balthazar", None)
    assert not ok and "outside New York" in why


def test_an_address_we_hold_must_agree_not_merely_coexist():
    ours = "80 Spring St., New York, NY 10012"
    ok, _ = judge_no_coords(
        cand("Balthazar", "80 Spring St, New York, NY 10012"), "Balthazar", ours)
    assert ok
    ok, why = judge_no_coords(
        cand("Balthazar", "1 Nowhere Ave, Brooklyn, NY 11201"), "Balthazar", ours)
    assert not ok and "disagrees" in why


def test_a_postal_code_rescues_a_second_entrance():
    ok, why = judge_no_coords(
        cand("Ci Siamo", "440 W. 33rd St., New York, NY 10001"),
        "Ci Siamo", "385 Ninth Ave., Manhattan, NY, 10001")
    assert ok and "postal code" in why


def test_with_no_address_on_either_side_the_name_must_carry_it():
    ok, _ = judge_no_coords(cand("Le Bernardin"), "Le Bernardin", None)
    assert ok
    ok, why = judge_no_coords(cand("Joe's Pizza"), "Le Bernardin", None)
    assert not ok and "too low" in why


def test_in_nyc_bounds():
    assert in_nyc(40.7128, -74.0060)          # City Hall
    assert in_nyc(40.58, -73.96)              # Brighton Beach
    assert not in_nyc(40.7128, -75.5)
    assert not in_nyc(None, None)


@pytest.mark.parametrize("google,ours", [
    ("OPERATIONAL", "open"),
    ("CLOSED_PERMANENTLY", "closed"),
    ("CLOSED_TEMPORARILY", "closed"),
])
def test_business_status_maps_to_our_three_states(google, ours):
    assert STATUS_FROM_GOOGLE[google] == ours


def test_an_unknown_business_status_leaves_the_venue_unverified():
    """Google inventing a fourth value must not silently mark a venue open."""
    assert STATUS_FROM_GOOGLE.get("SOMETHING_NEW") is None
