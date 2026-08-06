"""Fetch Google Places ratings for each restaurant.

Usage: python src/fetch_google_ratings.py [--shard i/k] [--force] [--report]

Key: env GOOGLE_PLACES_KEY, else config/secrets.py (both gitignored). It is
never written into the cache, the payload, or any tracked file.

Billing: one Text Search per uncached restaurant. Results are cached per slug
so re-runs cost nothing; only --force re-bills.

MATCHING IS THE HARD PART, not fetching. Text Search returns a best guess for
a name, and a name is not an identifier -- searching "53" returns a restaurant
621m away. Every result is therefore checked against the coordinates and name
we already hold, and anything that fails is recorded as UNMATCHED with its
reason rather than being accepted. config/google_place_ids.json overrides the
search with a hand-verified place_id, which is the only truly stable key.

Output: data/raw/google/{slug}.json
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from math import radians, sin, cos, asin, sqrt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "google"
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
OVERRIDES = ROOT / "config" / "google_place_ids.json"

TEXT_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
DETAIL_FIELDS = ("name,formatted_address,geometry/location,place_id,rating,"
                 "user_ratings_total,business_status")

# Accept outright when the point is this close -- two geocoders for the same
# address rarely differ by more than a building width.
NEAR_M = 150
# Beyond this, no name similarity rescues it: it is a different restaurant.
FAR_M = 400
NAME_MIN = 0.55
PAUSE = 0.12


def api_key():
    k = os.environ.get("GOOGLE_PLACES_KEY")
    if k:
        return k
    sys.path.insert(0, str(ROOT / "config"))
    try:
        from secrets import GOOGLE_PLACES_KEY  # noqa: F401
        return GOOGLE_PLACES_KEY
    except Exception:
        raise SystemExit(
            "No API key. Set GOOGLE_PLACES_KEY, or copy config/secrets.example.py "
            "to config/secrets.py and fill it in.")


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s)).strip()


def name_sim(a, b):
    A, B = set(_norm(a).split()), set(_norm(b).split())
    if not A or not B:
        return 0.0
    if A <= B or B <= A:
        return 1.0
    return len(A & B) / len(A | B)


def haversine_m(a_lat, a_lng, b_lat, b_lng):
    d1, d2 = radians(b_lat - a_lat), radians(b_lng - a_lng)
    h = (sin(d1 / 2) ** 2
         + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(d2 / 2) ** 2)
    return 2 * 6371000 * asin(sqrt(h))


def get(url, params, tries=3):
    u = f"{url}?{urllib.parse.urlencode(params)}"
    for i in range(tries):
        try:
            with urllib.request.urlopen(u, timeout=30) as r:
                d = json.loads(r.read())
            if d.get("status") == "OVER_QUERY_LIMIT" and i < tries - 1:
                time.sleep(2 + 2 * i)
                continue
            return d
        except urllib.error.URLError:
            if i == tries - 1:
                raise
            time.sleep(1 + i)
    return {"status": "UNKNOWN_ERROR"}


def judge(cand, name, lat, lng):
    """-> (accepted, reason). Distance is the primary evidence; the name only
    rescues a mid-range hit, and nothing rescues a far one."""
    if lat is None or lng is None:
        return False, "no coordinates on our side to corroborate"
    d = haversine_m(lat, lng, cand["lat"], cand["lng"])
    s = name_sim(name, cand["name"])
    if d <= NEAR_M:
        return True, f"{round(d)}m away"
    if d <= FAR_M and s >= NAME_MIN:
        return True, f"{round(d)}m away, name agrees ({s:.2f})"
    return False, f"{round(d)}m away, name similarity {s:.2f}"


def flatten(res):
    loc = (res.get("geometry") or {}).get("location") or {}
    return {
        "place_id": res.get("place_id"),
        "name": res.get("name"),
        "address": res.get("formatted_address"),
        "rating": res.get("rating"),
        "user_ratings_total": res.get("user_ratings_total"),
        "business_status": res.get("business_status"),
        "lat": loc.get("lat"), "lng": loc.get("lng"),
    }


def fetch_one(slug, name, hood, boro, lat, lng, key, place_id=None):
    rec = {"slug": slug, "query_name": name, "matched": None,
           "accepted": False, "reason": None, "source": None, "error": None}
    try:
        if place_id:
            d = get(DETAILS_URL, {"place_id": place_id, "key": key,
                                  "fields": DETAIL_FIELDS})
            if d.get("status") != "OK":
                rec["error"] = f"details {d.get('status')}"
                return rec
            # A hand-pinned place_id IS the verification; no geometry test.
            rec.update(matched=flatten(d["result"]), accepted=True,
                       reason="hand-verified place_id", source="place_id")
            return rec
        q = " ".join(x for x in (name, hood, boro, "New York") if x)
        d = get(TEXT_URL, {"query": q, "key": key})
        rec["source"] = "textsearch"
        if d.get("status") == "ZERO_RESULTS":
            rec["reason"] = "no result for the query"
            return rec
        if d.get("status") != "OK":
            rec["error"] = f"{d.get('status')}: {d.get('error_message', '')[:120]}"
            return rec
        # Check the top few, not just results[0] — the best name match is not
        # always first when a chain's other branches rank higher.
        best = None
        for res in d["results"][:5]:
            c = flatten(res)
            if c["lat"] is None:
                continue
            ok, why = judge(c, name, lat, lng)
            score = (ok, name_sim(name, c["name"]))
            if best is None or score > best[0]:
                best = (score, c, ok, why)
        if best is None:
            rec["reason"] = "no result carried coordinates"
            return rec
        _, c, ok, why = best
        rec.update(matched=c, accepted=ok, reason=why)
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return rec


def targets():
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return con.execute(
        "SELECT slug, name, neighborhood, borough, lat, lng FROM restaurants"
        " ORDER BY slug").fetchall()


def write(rec):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / f"{rec['slug']}.json"
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.copyfile(tmp, OUT / f"{rec['slug']}.json")


def report():
    files = sorted(OUT.glob("*.json"))
    acc = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    ok = [a for a in acc if a["accepted"]]
    rej = [a for a in acc if not a["accepted"] and not a["error"]]
    err = [a for a in acc if a["error"]]
    print(f"cached {len(acc)} · accepted {len(ok)} · rejected {len(rej)} · errored {len(err)}")
    rated = [a for a in ok if a["matched"].get("rating") is not None]
    if rated:
        rs = sorted(a["matched"]["rating"] for a in rated)
        tot = sorted(a["matched"]["user_ratings_total"] or 0 for a in rated)
        print(f"with a rating: {len(rated)} · median {rs[len(rs)//2]}★ "
              f"· median reviews {tot[len(tot)//2]}")
    if rej:
        print("\nrejected (kept as unmatched, never guessed):")
        for a in rej[:25]:
            m = a["matched"] or {}
            print(f"  {a['slug'][:30]:30} {str(m.get('name'))[:26]:26} {a['reason']}")
    if err:
        print("\nerrors:")
        for a in err[:15]:
            print(f"  {a['slug'][:30]:30} {a['error']}")


def main():
    if "--report" in sys.argv:
        return report()
    key = api_key()
    force = "--force" in sys.argv
    overrides = (json.loads(OVERRIDES.read_text(encoding="utf-8")).get("place_ids", {})
                 if OVERRIDES.exists() else {})
    shard_i, shard_k = 0, 1
    if "--shard" in sys.argv:
        shard_i, shard_k = (int(x) for x in sys.argv[sys.argv.index("--shard") + 1].split("/"))

    rows = targets()
    todo = [r for n, r in enumerate(rows)
            if n % shard_k == shard_i
            and (force or not (OUT / f"{r[0]}.json").exists())]
    print(f"shard {shard_i}/{shard_k}: {len(todo)} to fetch "
          f"({len(rows)} total, {len(list(OUT.glob('*.json')))} cached)", flush=True)
    for n, (slug, name, hood, boro, lat, lng) in enumerate(todo, 1):
        rec = fetch_one(slug, name, hood, boro, lat, lng, key, overrides.get(slug))
        write(rec)
        m = rec["matched"] or {}
        print(f"  [{n}/{len(todo)}] {slug}: "
              f"{'OK ' if rec['accepted'] else '-- '}"
              f"{m.get('rating', '?')}* {m.get('user_ratings_total', '?')} "
              f"{rec['reason'] or rec['error'] or ''}", flush=True)
        time.sleep(PAUSE)


if __name__ == "__main__":
    main()
