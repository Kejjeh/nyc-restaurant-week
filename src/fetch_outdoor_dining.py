"""Fetch NYC's licensed outdoor dining setups; commit the result.

Usage: python src/fetch_outdoor_dining.py [--refresh]

Source: NYC Open Data "Dining Out NYC Locations" (dataset fpeh-f7ci), the DOT
register of licensed sidewalk and roadway dining setups. Public domain.

This is the ONLY defensible basis for an outdoor-seating filter. The Restaurant
Week listing API carries no such field, and the marketing summaries mention a
patio for only 27 of 645 -- inferring from those would mostly be inferring from
whether a copywriter felt like mentioning it.

Licences are issued and expire continuously, so unlike stations.json this IS
re-pulled by refresh.py. One request a week against a city endpoint.

Output: data/raw/outdoor/licenses.json
  {"fetched": "...", "rows": [{"name":.., "legal":.., "street":.., "borough":..,
                               "type":"sidewalk|roadway", "lat":.., "lng":..}]}
"""
import json
import shutil
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "outdoor" / "licenses.json"
API = ("https://data.cityofnewyork.us/resource/fpeh-f7ci.json"
       "?$limit=50000&$order=license_issue_date")
UA = "Mozilla/5.0 (compatible; nyc-restaurant-week/1.0; personal use)"

# A licence that is applied-for or revoked does not put a table on the pavement.
LIVE_STATUS = {"issued"}


def main():
    if OUT.exists() and "--refresh" not in sys.argv:
        d = json.loads(OUT.read_text(encoding="utf-8"))
        print(f"{len(d['rows'])} licences already cached at "
              f"{OUT.relative_to(ROOT)} (fetched {d['fetched']}; "
              f"use --refresh to re-pull)")
        return

    req = urllib.request.Request(API, headers={"User-Agent": UA})
    raw = json.loads(urllib.request.urlopen(req, timeout=120).read())

    rows, skipped = [], 0
    for r in raw:
        if (r.get("license_status") or "").strip().lower() not in LIVE_STATUS:
            skipped += 1
            continue
        try:
            lat, lng = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        # "Sidewalk", "Roadway", occasionally "Sidewalk and Roadway"
        kind = (r.get("license_type") or "").strip().lower()
        rows.append({
            "name": (r.get("assumed_name_s") or "").strip(),
            "legal": (r.get("business_legal_name") or "").strip(),
            "street": (r.get("street") or "").strip(),
            "borough": (r.get("borough") or "").strip(),
            "postcode": (r.get("postcode") or "").strip(),
            "sidewalk": "sidewalk" in kind,
            "roadway": "roadway" in kind,
            "lat": round(lat, 6),
            "lng": round(lng, 6),
        })

    payload = {
        "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "NYC Open Data fpeh-f7ci (Dining Out NYC Locations)",
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / OUT.name
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    shutil.copyfile(tmp, OUT)

    sw = sum(1 for r in rows if r["sidewalk"])
    rw = sum(1 for r in rows if r["roadway"])
    print(f"{len(rows)} live licences -> {OUT.relative_to(ROOT)} "
          f"({skipped} skipped: not issued, or no coordinates)")
    print(f"  sidewalk {sw} · roadway {rw} · both {sum(1 for r in rows if r['sidewalk'] and r['roadway'])}")


if __name__ == "__main__":
    main()
