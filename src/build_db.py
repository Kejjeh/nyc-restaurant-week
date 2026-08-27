"""Build the SQLite database + flat CSV export from raw snapshots."""
import csv
import json
import re
import shutil
import sqlite3
import tempfile

from pathlib import Path

import parse_menus
from price_sweep import reconciled_gaps
from config import DETAILS_DIR, LISTING_DIR, MENUS_DIR, PROCESSED, SITE

DB = PROCESSED / "restaurant_week.sqlite"
CSV_OUT = PROCESSED / "restaurants.csv"

SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  borough TEXT, neighborhood TEXT, address TEXT, lat REAL, lng REAL,
  cuisines TEXT,              -- JSON array (tags minus neighborhood)
  price_tiers TEXT,           -- JSON array e.g. ["$30","$45"]
  meal_periods TEXT,          -- JSON array e.g. ["lunch","dinner","brunch"]
  meal_types_raw TEXT,        -- JSON array, verbatim API values
  weeks TEXT,                 -- JSON array, verbatim week labels
  sunday_participation INTEGER,  -- 1 if any Sunday meal type
  menu_url TEXT, website TEXT,
  reservation_partner TEXT, reservation_partner_id TEXT, reservation_link TEXT,
  listing_url TEXT, summary TEXT, collections TEXT,
  snapshot_date TEXT
);
CREATE TABLE IF NOT EXISTS menus (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_slug TEXT REFERENCES restaurants(slug),
  menu_url TEXT, pdf_file TEXT, sha256 TEXT,
  parse_quality TEXT CHECK (parse_quality IN ('full','partial','failed')),
  raw_text TEXT
);
CREATE TABLE IF NOT EXISTS menu_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  menu_id INTEGER REFERENCES menus(id),
  course TEXT, dish TEXT, description TEXT, supplement_price REAL, position INTEGER
);
CREATE INDEX IF NOT EXISTS idx_menu_rest ON menus(restaurant_slug);
CREATE INDEX IF NOT EXISTS idx_items_menu ON menu_items(menu_id);
"""


def derive(meal_types):
    tiers, periods, sunday = set(), set(), False
    for mt in meal_types:
        m = re.search(r"\$(\d+)", mt)
        if m:
            tiers.add(f"${m.group(1)}")
        low = mt.lower()
        if "sunday" in low:
            sunday = True
        if "brunch" in low:
            periods.add("brunch")
        elif "lunch" in low:
            periods.add("lunch")
        if "dinner" in low:
            periods.add("dinner")
    return sorted(tiers, key=lambda t: int(t[1:])), sorted(periods), sunday


def reservation(ec):
    if not ec:
        return None, None, None
    partner, pid = ec.get("partnerName"), ec.get("partnerId")
    link = None
    if partner == "OpenTable" and pid:
        link = f"https://www.opentable.com/restref/client/?rid={pid}"
    return partner, pid, link


def main():
    snap = json.loads((LISTING_DIR / "latest.json").read_text())
    items, stamp = snap["items"], snap["fetched"]
    parsed_path = MENUS_DIR / "parsed.json"
    parsed = json.loads(parsed_path.read_text()) if parsed_path.exists() else {}
    manifest_path = MENUS_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    # build in a temp dir (some mounted filesystems break sqlite locking),
    # then move into place
    tmp_db = Path(tempfile.mkdtemp()) / DB.name
    con = sqlite3.connect(tmp_db)
    con.executescript(SCHEMA)

    rows = []
    for it in items:
        slug = it["slug"]
        det_f = DETAILS_DIR / f"{slug}.json"
        det = json.loads(det_f.read_text()) if det_f.exists() else {}
        tiers, periods, sunday = derive(it.get("mealTypes", []))
        cuisines = [t for t in it.get("tags", []) if t != it.get("neighborhood")]
        partner, pid, link = reservation(it.get("ecommerce"))
        rows.append(
            (
                slug, it.get("shortTitle"), it.get("borough"), it.get("neighborhood"),
                det.get("address"), det.get("lat"), det.get("lng"),
                json.dumps(cuisines), json.dumps(tiers), json.dumps(periods),
                json.dumps(it.get("mealTypes", [])),
                json.dumps(it.get("restaurantInclusionWeek", [])),
                int(sunday), it.get("menuFileUrl"), it.get("website"),
                partner, pid, link,
                f"{SITE}/restaurant-week/{slug}/",
                it.get("summary"), json.dumps(it.get("collections", [])), stamp,
            )
        )
    con.executemany(
        f"INSERT INTO restaurants VALUES ({','.join('?' * 22)})", rows
    )

    for slug, meta in manifest.items():
        p = parsed.get(slug, {})
        # Re-derived on the way in, for the same reason the price sweeps are:
        # parse_menus.py is fixed at the source, but regenerating parsed.json
        # means re-downloading 473 PDFs at the mandatory 1 req/sec, and this
        # costs nothing. See dedupe()/grade() in src/parse_menus.py.
        items = parse_menus.dedupe(p.get("items", []))
        quality = (parse_menus.grade(p.get("raw_text") or "",
                                     p.get("courses") or [], items)
                   if "raw_text" in p else p.get("parse_quality", "failed"))
        cur = con.execute(
            "INSERT INTO menus (restaurant_slug, menu_url, pdf_file, sha256,"
            " parse_quality, raw_text) VALUES (?,?,?,?,?,?)",
            (
                slug, meta.get("url"), meta.get("file"), meta.get("sha256"),
                quality, p.get("raw_text"),
            ),
        )
        mid = cur.lastrowid
        con.executemany(
            "INSERT INTO menu_items (menu_id, course, dish, description,"
            " supplement_price, position) VALUES (?,?,?,?,?,?)",
            [
                (mid, x["course"], x["dish"], x["description"],
                 x["supplement_price"], i)
                for i, x in enumerate(items)
            ],
        )
    # price_sweep triage table (heuristic comparables; see README) — reload
    # from cached sweep results so DB rebuilds don't lose it
    sweep_dir = DB.parents[1] / "raw" / "pricesweep"
    if sweep_dir.exists():
        con.executescript("""
        CREATE TABLE IF NOT EXISTS price_sweep (
          restaurant_slug TEXT PRIMARY KEY, comparable_3course INTEGER,
          gaps TEXT, confidence TEXT, pages_fetched INTEGER, n_prices INTEGER,
          error TEXT, swept_date TEXT, comparable_basis TEXT);
        """)
        for f in sweep_dir.glob("*.json"):
            r = json.loads(f.read_text())
            con.execute(
                "INSERT OR REPLACE INTO price_sweep VALUES (?,?,?,?,?,?,?,?,?)",
                (r["slug"], r.get("comparable_3course"),
                 json.dumps(reconciled_gaps(r)),
                 r.get("confidence"), r.get("pages_fetched", 0),
                 len(r.get("prices", [])), r.get("error"), "2026-08-01",
                 "heuristic"),
            )
    con.commit()

    # flat CSV
    cur = con.execute(
        """SELECT slug, name, borough, neighborhood, address, cuisines, price_tiers,
                  meal_periods, weeks, sunday_participation, menu_url,
                  reservation_link, listing_url, website,
                  (SELECT parse_quality FROM menus m WHERE m.restaurant_slug =
                   restaurants.slug) AS menu_parse_quality
           FROM restaurants ORDER BY name"""
    )
    with open(CSV_OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        for r in cur:
            w.writerow(r)

    for t in ("restaurants", "menus", "menu_items"):
        print(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    print("parse_quality:", con.execute(
        "SELECT parse_quality, COUNT(*) FROM menus GROUP BY 1").fetchall())
    con.close()
    shutil.copyfile(tmp_db, DB)


if __name__ == "__main__":
    main()
