"""Configurable dish tagging over the menu data.

Rules live in config/dish_tags.json (tag -> [{pattern, confidence, note}]).
Adding a new tag is a one-line config change + re-run; no code edits.

Scans BOTH:
  - menu_items.dish + description  (structured, from full parses)   -> source='item'
  - menus.raw_text                 (all menus incl. partial/failed) -> source='raw_text'
so partial parses are not invisible. Every hit stores the matched snippet
for auditing. Results go to the menu_item_tags table (menu_item_id is NULL
for raw_text-level hits).
"""
import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
CONFIG = ROOT / "config" / "dish_tags.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS menu_item_tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_slug TEXT,
  menu_id INTEGER REFERENCES menus(id),
  menu_item_id INTEGER REFERENCES menu_items(id),  -- NULL for raw_text hits
  tag TEXT NOT NULL,
  confidence TEXT CHECK (confidence IN ('high','low')),
  matched_text TEXT,   -- snippet around the match, for auditing
  source TEXT CHECK (source IN ('item','raw_text'))
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON menu_item_tags(tag);
CREATE INDEX IF NOT EXISTS idx_tags_rest ON menu_item_tags(restaurant_slug);
"""


def load_rules():
    cfg = json.loads(CONFIG.read_text())
    rules = {}
    for tag, rlist in cfg.items():
        if tag.startswith("_"):
            continue
        rules[tag] = [
            (re.compile(r["pattern"], re.I), r.get("confidence", "high"))
            for r in rlist
        ]
    return rules


def snippet(text, m, pad=60):
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def main():
    rules = load_rules()
    tmp = Path(tempfile.mkdtemp()) / DB.name  # sqlite needs a lockable fs
    shutil.copyfile(DB, tmp)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.execute("DELETE FROM menu_item_tags")  # idempotent re-run

    # 1) structured items
    for iid, mid, slug, dish, desc in con.execute(
        "SELECT mi.id, mi.menu_id, m.restaurant_slug, mi.dish, mi.description"
        " FROM menu_items mi JOIN menus m ON mi.menu_id = m.id"
    ).fetchall():
        text = " ".join(x for x in (dish, desc) if x)
        for tag, pats in rules.items():
            best = None
            for pat, conf in pats:
                m = pat.search(text)
                if m and (best is None or conf == "high"):
                    best = (conf, snippet(text, m))
            if best:
                con.execute(
                    "INSERT INTO menu_item_tags (restaurant_slug, menu_id,"
                    " menu_item_id, tag, confidence, matched_text, source)"
                    " VALUES (?,?,?,?,?,?,'item')",
                    (slug, mid, iid, tag, best[0], best[1]),
                )

    # 2) raw text (covers partial/failed parses; also catches items the
    #    structurer missed on full parses)
    for mid, slug, raw in con.execute(
        "SELECT id, restaurant_slug, raw_text FROM menus WHERE raw_text IS NOT NULL"
    ).fetchall():
        for tag, pats in rules.items():
            for pat, conf in pats:
                for m in pat.finditer(raw):
                    con.execute(
                        "INSERT INTO menu_item_tags (restaurant_slug, menu_id,"
                        " menu_item_id, tag, confidence, matched_text, source)"
                        " VALUES (?,?,NULL,?,?,?,'raw_text')",
                        (slug, mid, tag, conf, snippet(raw, m)),
                    )

    con.commit()
    print("== restaurants per tag (union of item + raw_text hits) ==")
    for tag, n_rest, n_hits in con.execute(
        "SELECT tag, COUNT(DISTINCT restaurant_slug), COUNT(*)"
        " FROM menu_item_tags GROUP BY tag ORDER BY 2 DESC"
    ):
        print(f"  {tag}: {n_rest} restaurants ({n_hits} hits)")
    print("== by confidence ==")
    for row in con.execute(
        "SELECT tag, confidence, COUNT(DISTINCT restaurant_slug)"
        " FROM menu_item_tags GROUP BY 1,2"
    ):
        print(" ", row)
    con.close()
    shutil.copyfile(tmp, DB)


if __name__ == "__main__":
    main()
