"""Sweep restaurants' OWN websites for configured menu terms.

Usage: python src/menu_term_sweep.py [--shard i/k] [--report] [--refresh SLUG]

Why this exists separately from tag_dishes.py: that one reads menus.raw_text,
which is the Restaurant Week prix-fixe PDF. Some dishes are essentially never
on a $45-60 prix fixe -- a seafood tower is an a la carte raw-bar item -- so
searching the RW menus for them returns nothing. Terms configured in
config/offsite_tags.json are searched against the restaurant's own site
instead.

The distinction is load-bearing and must survive into the UI: a hit here means
"this restaurant serves it", NOT "it is included in Restaurant Week".

Only the restaurant's own domain (plus PDFs it links to) is fetched. Delivery
aggregators are never read -- see BLOCKED.

Resumable: one cache file per slug in data/raw/menusweep/{slug}.json.
Sharded:   --shard i/k processes slugs where hash(slug) %% k == i.
"""
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from price_sweep import fetch, visible_text, pdf_text, MENU_WORDS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "menusweep"
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
CONFIG = ROOT / "config" / "offsite_tags.json"

# HARD RULE from the project brief: never backfill from delivery aggregators.
BLOCKED = re.compile(
    r"(seamless|grubhub|doordash|ubereats|postmates|caviar|slice|chownow"
    r"|toasttab|clover\.com|yelp\.|tripadvisor|opentable\.com/r/|resy\.com)", re.I)

SNIPPET_PAD = 45          # trimmed again by the exporter's ToS budget
MAX_HITS_PER_TAG = 3      # a few candidates so the exporter can pick a clean one
PAUSE = 0.15               # politeness between requests to one host


def load_terms():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    return {tag: [(re.compile(r["pattern"], re.I), r.get("confidence", "high"))
                  for r in rules]
            for tag, rules in cfg.items() if not tag.startswith("_")}


MENU_HREF = re.compile(r"menu|dine|food|lunch|dinner|brunch|carte|prix|raw.?bar"
                       r"|shellfish|oyster", re.I)


def pick_links(hrefs, base):
    """Menu-ish links on the restaurant's own host, most menu-like first."""
    host = urllib.parse.urlparse(base).netloc
    seen, pages, pdfs = set(), [], []
    for u in hrefs:
        u = (u or "").rstrip("#")
        if not u.startswith("http") or u in seen or u == base or BLOCKED.search(u):
            continue
        if not MENU_HREF.search(u):
            continue
        if urllib.parse.urlparse(u).netloc != host and not u.lower().endswith(".pdf"):
            continue
        seen.add(u)
        (pdfs if u.lower().endswith(".pdf") else pages).append(u)
    # a page whose url actually says "menu" beats one that merely says "dine"
    pages.sort(key=lambda u: (0 if re.search(r"menu|raw.?bar", u, re.I) else 1, len(u)))
    return pages[:8], pdfs[:3]


def scan(text, terms, url, found):
    """Collect every match. Capping happens later, in prune() -- capping here
    let three "raw bar" (low) hits fill the bucket and lock out the
    "SEAFOOD TOWER" (high) further down the same page."""
    text = re.sub(r"\s+", " ", text)
    for tag, pats in terms.items():
        for pat, conf in pats:
            for m in pat.finditer(text):
                a = max(0, m.start() - SNIPPET_PAD)
                b = min(len(text), m.end() + SNIPPET_PAD)
                found.setdefault(tag, []).append(
                    {"tag": tag, "confidence": conf, "keyword": m.group(0)[:40],
                     "snippet": text[a:b].strip(), "url": url})


def prune(found):
    """Highest confidence first, deduped, capped per tag."""
    out = []
    for tag, hits in found.items():
        seen, kept = set(), []
        for h in sorted(hits, key=lambda h: 0 if h["confidence"] == "high" else 1):
            key = h["snippet"].lower()
            if key in seen:
                continue
            seen.add(key)
            kept.append(h)
            if len(kept) >= MAX_HITS_PER_TAG:
                break
        out += kept
    return out


def render(browser, url, timeout=30000):
    """-> (innerText, [hrefs]). Menus are routinely client-rendered: Smith &
    Wollensky's "SEAFOOD TOWER" is absent from the raw HTML and present only
    after scripts run, so a plain GET silently under-reports."""
    page = browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        page.wait_for_timeout(1800)
        text = page.evaluate("()=>document.body ? document.body.innerText : ''")
        hrefs = page.evaluate("()=>[...document.querySelectorAll('a')].map(a=>a.href)")
        return text, hrefs
    finally:
        page.close()


def sweep_one(browser, slug, website, terms):
    rec = {"slug": slug, "website": website, "pages_fetched": 0,
           "hits": [], "error": None}
    found = {}
    try:
        if BLOCKED.search(website):
            rec["error"] = "blocked host (aggregator)"
            return rec
        text, hrefs = render(browser, website)
        rec["pages_fetched"] += 1
        scan(text, terms, website, found)
        pages, pdfs = pick_links(hrefs, website)
        for u in pages:
            try:
                time.sleep(PAUSE)
                t2, _ = render(browser, u)
                scan(t2, terms, u, found)
                rec["pages_fetched"] += 1
            except Exception:
                pass
        for u in pdfs:
            try:
                time.sleep(PAUSE)
                d, _ = fetch(u, timeout=25)
                scan(pdf_text(d), terms, u, found)
                rec["pages_fetched"] += 1
            except Exception:
                pass
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    rec["hits"] = prune(found)
    return rec


def targets():
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    return con.execute(
        "SELECT slug, website FROM restaurants"
        " WHERE website IS NOT NULL AND website != '' ORDER BY slug").fetchall()


def write(rec):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp()) / f"{rec['slug']}.json"
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.copyfile(tmp, OUT / f"{rec['slug']}.json")


def report():
    files = sorted(OUT.glob("*.json"))
    by_tag, errs = {}, 0
    for f in files:
        r = json.loads(f.read_text(encoding="utf-8"))
        if r.get("error"):
            errs += 1
        for h in r["hits"]:
            by_tag.setdefault(h["tag"], {}).setdefault(h["confidence"], set()).add(r["slug"])
    print(f"swept {len(files)} restaurants · {errs} unreachable")
    for tag, byconf in by_tag.items():
        hi = byconf.get("high", set())
        lo = byconf.get("low", set()) - hi
        print(f"\n== {tag}: {len(hi)} high, {len(lo)} low-only")
        for s in sorted(hi):
            print(f"   HIGH {s}")
        for s in sorted(lo):
            print(f"   low  {s}")


def main():
    if "--report" in sys.argv:
        return report()
    terms = load_terms()
    shard_i, shard_k = 0, 1
    if "--shard" in sys.argv:
        shard_i, shard_k = (int(x) for x in sys.argv[sys.argv.index("--shard") + 1].split("/"))

    rows = targets()
    todo = [(s, w) for s, w in rows
            if int(hashlib.md5(s.encode()).hexdigest(), 16) % shard_k == shard_i
            and not (OUT / f"{s}.json").exists()]
    print(f"shard {shard_i}/{shard_k}: {len(todo)} to sweep "
          f"({len(rows)} total, {len(list(OUT.glob('*.json')))} cached)", flush=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for n, (slug, site) in enumerate(todo, 1):
                rec = sweep_one(browser, slug, site, terms)
                write(rec)
                tags = {h["tag"] for h in rec["hits"]}
                print(f"  [{n}/{len(todo)}] {slug}: {rec['pages_fetched']}p "
                      f"{'HIT ' + ','.join(tags) if tags else ''}{rec['error'] or ''}",
                      flush=True)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
