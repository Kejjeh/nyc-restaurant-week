"""Track restaurants I want to try that are NOT in Restaurant Week.

Usage:
  python src/places_cli.py add "Le Bernardin" ["155 W 51st St"]
  python src/places_cli.py list
  python src/places_cli.py remove <slug>

`add` resolves the name through the same Text Search machinery the roster uses
(src/fetch_google_ratings.py) and prints the top candidate for a y/n before
anything is written. There is no coordinate of our own to corroborate against
here, so the human confirmation IS the verification -- the same rule that lets
a hand-pinned place_id skip the geometry test.

Key: env GOOGLE_PLACES_KEY, else config/secrets.py (both gitignored). It is
never printed, never cached, and never enters a tracked file.

Writes config/places.json (tracked: the list itself) and, per place,
data/cache/places/<slug>.json in exactly the record shape data/raw/google/ uses,
so the exporter reads a place and a participant through one code path.
"""
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from fetch_google_ratings import TEXT_URL, api_key, best_candidate, get, norm

ROOT = Path(__file__).resolve().parents[1]
PLACES = ROOT / "config" / "places.json"
CACHE = ROOT / "data" / "cache" / "places"
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"


def slugify(name):
    """Name -> the kebab shape a roster slug has. Apostrophes CLOSE UP
    ("Mark's" -> marks), which is what the program's own slugs do."""
    return "-".join(norm(re.sub(r"['’]", "", name or "")).split())


def load_places(path=PLACES):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    doc.setdefault("places", [])
    return doc


def save_places(doc, path=PLACES):
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def roster_slugs():
    """Slugs the program already owns, read live rather than from a copy."""
    if not DB.exists():
        return set()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("SELECT slug FROM restaurants")}
    finally:
        con.close()


def reject_reason(slug, places, roster):
    """Why this slug cannot be added, or None. Every dashboard row is keyed by
    slug, so a collision does not merge two rows -- it hides one."""
    if not slug:
        return "empty slug"
    if any(p.get("slug") == slug for p in places):
        return f"{slug} is already on the list"
    if slug in roster:
        return f"{slug} is a Restaurant Week slug; pick another"
    return None


def add_place(doc, entry, roster):
    bad = reject_reason(entry.get("slug"), doc.get("places", []), roster)
    if bad:
        raise SystemExit(f"not added: {bad}")
    return {**doc, "places": [*doc.get("places", []), entry]}


def remove_place(doc, slug):
    kept = [p for p in doc.get("places", []) if p.get("slug") != slug]
    if len(kept) == len(doc.get("places", [])):
        raise SystemExit(f"{slug} is not on the list")
    return {**doc, "places": kept}


def resolve(name, hint, key):
    """Top Text Search candidate for a name -> the flattened match block.

    best_candidate is handed no coordinates on purpose: with nothing to
    corroborate, it falls back to ranking by name similarity, which is the most
    a machine can honestly do here.
    """
    q = " ".join(x for x in (name, hint, "New York") if x)
    d = get(TEXT_URL, {"query": q, "key": key})
    if d.get("status") != "OK":
        raise SystemExit(f"search {d.get('status')}: "
                         f"{d.get('error_message', '')[:120]}")
    best = best_candidate(d["results"], name, None, None)
    if best is None:
        raise SystemExit(f"no usable result for {q!r}")
    return best[0]


def cache_record(slug, name, cand):
    """The confirmed match, in data/raw/google/'s record shape."""
    rec = {"slug": slug, "query_name": name, "matched": cand, "accepted": True,
           "reason": "confirmed by hand", "source": "confirmed", "error": None}
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / f"{slug}.json"
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.copyfile(tmp, CACHE / f"{slug}.json")
    return rec


def cmd_add(name, hint):
    slug = slugify(name)
    doc = load_places()
    bad = reject_reason(slug, doc["places"], roster_slugs())
    if bad:
        raise SystemExit(f"not added: {bad}")
    cand = resolve(name, hint, api_key())
    print(f"  {cand['name']}\n  {cand['address']}\n"
          f"  {cand['rating']}* ({cand['user_ratings_total']} reviews)"
          f" · {cand['business_status']}")
    if not input(f"add as {slug}? [y/N] ").strip().lower().startswith("y"):
        return print("not added")
    cache_record(slug, name, cand)
    save_places(add_place(doc, {"slug": slug, "name": name,
                                "address": cand["address"],
                                "place_id": cand["place_id"], "notes": None},
                          roster_slugs()))
    print(f"added {slug} -> config/places.json, cached data/cache/places/{slug}.json")


def cmd_list():
    places = load_places()["places"]
    print(f"{len(places)} place(s) on my list")
    for p in places:
        cached = "" if (CACHE / f"{p['slug']}.json").exists() else "  (no google match)"
        print(f"  {p['slug'][:34]:34} {(p.get('address') or '')[:40]}{cached}")


def cmd_remove(slug):
    save_places(remove_place(load_places(), slug))
    print(f"removed {slug} (data/cache/places/{slug}.json left alone)")


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        return cmd_list()
    if cmd == "add" and len(argv) >= 2:
        return cmd_add(argv[1], argv[2] if len(argv) > 2 else None)
    if cmd == "remove" and len(argv) == 2:
        return cmd_remove(argv[1])
    raise SystemExit(__doc__.split("\n\n")[1])


if __name__ == "__main__":
    main()
