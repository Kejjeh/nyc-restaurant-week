"""Match external recognition data (Michelin / James Beard / NYT) to restaurants.

Inputs (data/raw/recognition/):
  michelin.json     [{name, address?, level, year, url}]
  james_beard.json  [{name, restaurant?, award, level, year, url, city?}]
  nyt100.json       [{name, address?, level, year, url, verified}]

Matching is on NAME + NORMALIZED ADDRESS, never name alone, because of
chains/duplicates (e.g. two Empire Steak House locations). Ambiguous matches
go to data/processed/recognition_review.json for human review instead of
being auto-accepted.

match_confidence:
  1.0  exact normalized name + address street-number match
  0.9  exact normalized name, unique in both sets, no address available
  0.8  fuzzy name >= 0.92 + address street-number match
  <0.8 -> review file, not inserted
"""
import json
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
RAW = ROOT / "data" / "raw" / "recognition"
REVIEW = ROOT / "data" / "processed" / "recognition_review.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS recognition (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_slug TEXT REFERENCES restaurants(slug),
  source TEXT CHECK (source IN ('michelin','james_beard','nyt')),
  level TEXT,          -- e.g. '1 star', 'bib_gourmand', 'recommended',
                       -- 'winner: Best Chef NY', 'nominee: ...', 'nyt_100_best'
  year INTEGER,
  source_url TEXT,
  match_confidence REAL,
  matched_name TEXT,   -- the external name that matched
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_recog_rest ON recognition(restaurant_slug);
"""

STOP = re.compile(
    r"\b(the|restaurant|ristorante|nyc|new york(?: city)?|manhattan|brooklyn"
    r"|cafe|café|bar|bistro|brasserie|trattoria|osteria)\b"
)


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[’'`´]", "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = STOP.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def street_key(addr):
    """Set of street-number candidates. Hyphenated Queens numbers are joined
    ('10-07' -> '1007'); 5-digit zips and ordinal suffixes dropped. Two
    addresses corroborate if their sets intersect."""
    if not addr:
        return None
    a = addr.lower().replace("-", "")
    nums = {n for n in re.findall(r"\b(\d{1,4})\b", a)}
    nums -= {n for n in nums if re.search(rf"\b{n}(st|nd|rd|th)\b", a)}  # ordinals
    return nums or None


def match(externals, restaurants, source):
    """restaurants: list of dicts with slug, name, address."""
    by_norm = {}
    for r in restaurants:
        by_norm.setdefault(norm_name(r["name"]), []).append(r)
    accepted, review = [], []
    for e in externals:
        en = norm_name(e.get("restaurant") or e["name"])
        ek = street_key(e.get("address"))
        cands = by_norm.get(en, [])
        if len(cands) == 1:
            r = cands[0]
            rk = street_key(r.get("address"))
            if ek and rk:
                conf = 1.0 if ek & rk else 0.0
                if conf == 0.0:
                    review.append({**e, "reason": "name match, address mismatch",
                                   "candidate": r["slug"]})
                    continue
            else:
                conf = 0.9
            accepted.append((r["slug"], e, conf, "exact name"))
            continue
        if len(cands) > 1:
            hit = None
            if ek:
                for r in cands:
                    rk = street_key(r.get("address"))
                    if rk and ek & rk:
                        hit = r
                        break
            if hit:
                accepted.append((hit["slug"], e, 1.0, "name+address among chain"))
            else:
                review.append({**e, "reason": "multiple name matches, no address tiebreak",
                               "candidates": [r["slug"] for r in cands]})
            continue
        # fuzzy
        best, score = None, 0.0
        for nn, rs in by_norm.items():
            s = SequenceMatcher(None, en, nn).ratio()
            if s > score:
                best, score = rs, s
        if score >= 0.92 and len(best) == 1:
            r = best[0]
            rk, conf = street_key(r.get("address")), None
            if ek and rk and ek & rk:
                accepted.append((r["slug"], e, 0.8, f"fuzzy {score:.2f}+address"))
            else:
                review.append({**e, "reason": f"fuzzy {score:.2f}, no address confirm",
                               "candidate": r["slug"]})
        elif score >= 0.85:
            review.append({**e, "reason": f"fuzzy {score:.2f} below threshold",
                           "candidates": [r["slug"] for r in (best or [])]})
        # score < 0.85: external entity simply isn't a RW participant; drop
    return accepted, review


def main():
    tmp = Path(tempfile.mkdtemp()) / DB.name
    shutil.copyfile(DB, tmp)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM recognition")
    restaurants = [
        dict(zip(("slug", "name", "address"), row))
        for row in con.execute("SELECT slug, name, address FROM restaurants")
    ]
    all_review = {}
    for source, fname in (("michelin", "michelin.json"),
                          ("james_beard", "james_beard.json"),
                          ("nyt", "nyt100.json")):
        f = RAW / fname
        if not f.exists():
            print(f"{source}: no raw file, skipped")
            continue
        externals = json.loads(f.read_text())
        accepted, review = match(externals, restaurants, source)
        for slug, e, conf, how in accepted:
            con.execute(
                "INSERT INTO recognition (restaurant_slug, source, level, year,"
                " source_url, match_confidence, matched_name, notes)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (slug, source, e.get("level"), e.get("year"), e.get("url"),
                 conf, e.get("restaurant") or e["name"],
                 (e.get("notes") or "") + f" [{how}]"),
            )
        all_review[source] = review
        print(f"{source}: {len(externals)} external, {len(accepted)} matched, "
              f"{len(review)} to review")
    REVIEW.write_text(json.dumps(all_review, indent=1))
    print("\n== match_confidence distribution ==")
    for row in con.execute(
        "SELECT source, match_confidence, COUNT(*) FROM recognition GROUP BY 1,2"
    ):
        print(" ", row)
    con.commit()
    con.close()
    shutil.copyfile(tmp, DB)


if __name__ == "__main__":
    main()
