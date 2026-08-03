"""Fetch MTA subway station locations once; commit the result.

Usage: python src/fetch_subway.py [--refresh]

Source: NY State Open Data "MTA Subway Stations" (dataset 39hk-dx4f), public
domain. Like data/raw/recognition/*.json this is committed and NOT regenerated
by refresh.py -- station locations move about once a decade, and re-fetching
them weekly would be pointless traffic. Pass --refresh to re-pull deliberately.

Output: data/raw/subway/stations.json
  [{"name": "59 St-Lexington Av", "lat": .., "lng": .., "routes": ["4","5","6","N","R","W"]}]
"""
import json
import re
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "subway" / "stations.json"
API = "https://data.ny.gov/resource/39hk-dx4f.json?$limit=2000"
UA = "Mozilla/5.0 (compatible; nyc-restaurant-week/1.0; personal use)"


def main():
    if OUT.exists() and "--refresh" not in sys.argv:
        rows = json.loads(OUT.read_text(encoding="utf-8"))
        print(f"{len(rows)} stations already cached at "
              f"{OUT.relative_to(ROOT)} (use --refresh to re-pull)")
        return

    req = urllib.request.Request(API, headers={"User-Agent": UA})
    raw = json.loads(urllib.request.urlopen(req, timeout=60).read())

    seen, out = set(), []
    for r in raw:
        try:
            lat, lng = float(r["gtfs_latitude"]), float(r["gtfs_longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        name = (r.get("stop_name") or "").strip()
        # "daytime_routes" is space separated, e.g. "4 5 6 6X" or "N W"
        routes = sorted({t for t in re.split(r"\s+", (r.get("daytime_routes") or "").strip())
                         if t and t != "-"})
        if not name or not routes:
            continue
        # One complex can appear once per platform; collapse on name+routes.
        key = (name, tuple(routes))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "lat": round(lat, 6), "lng": round(lng, 6),
                    "routes": routes, "borough": r.get("borough")})

    out.sort(key=lambda s: s["name"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / OUT.name
    tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.copyfile(tmp, OUT)

    routes = sorted({r for s in out for r in s["routes"]})
    print(f"{len(out)} stations -> {OUT.relative_to(ROOT)}")
    print(f"routes seen ({len(routes)}): {' '.join(routes)}")


if __name__ == "__main__":
    main()
