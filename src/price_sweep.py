"""Automated first-pass price sweep of restaurant websites.

For each unverified restaurant: fetch its site, discover menu pages/PDFs,
extract $-prices heuristically, estimate a typical 3-course a la carte cost,
and compare to its RW tiers. This is TRIAGE, not verification: results are
grade [B/C] at best and exist to flag outliers for human/agent follow-up.

Resumable: results cached per slug in data/raw/pricesweep/{slug}.json.
Sharded:  --shard i/k  processes slugs where hash(slug) % k == i.
Report:   --report     prints ranked gaps from cached results (no fetching).

Heuristic comparable (3 courses):
  app     = median of prices in $8-28
  main    = median of prices in $22-70
  dessert = median of prices in $6-20 (fallback $14 if none seen)
Confidence: high >= 12 prices from >= 2 pages; medium >= 6 prices; low else.
"""
import hashlib
import io
import json
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "pricesweep"
OUT.mkdir(parents=True, exist_ok=True)
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
MENU_WORDS = re.compile(r"(menu|dine|food|lunch|dinner|brunch|carte|prix)", re.I)
PRICE = re.compile(r"\$\s?(\d{1,3})(?:\.\d{2})?\b")
ALREADY_ANALYZED = {  # covered by the manual value research; skip
 'manhatta','the-dining-room-at-gramercy-tavern','crown-shy','union-square-cafe',
 'nougatine-at-jean-georges','cafe-boulud','hawksmoor','quality-meats','quality-italian',
 'quality-bistro','smith-wollensky','frenchette','ai-fiori','gage-tollner','estiatorio-milos',
 'the-bar-room-at-the-modern','perry-street','oceana','zuma','benoit-restaurant-wine-bar',
 'scarpetta','casa-lever','tao-uptown','catch-nyc','mr-chow-new-york-57th-street',
 'empire-steak-house-east-50th-street','benjamin-steakhouse','mortons-the-steakhouse-manhattan',
 'ocean-prime','riverpark','lure-fishbar','naro','meadowsweet-williamsburg','mito-fort-greene',
 'mito-forest-hills','park-ave-kitchen-by-david-burke','kubeh','pera-soho','sant-ambroeus-brookfield',
 'french-louie','jacobs-pickles-upper-west-side','industry-kitchen','the-terrace-and-outdoor-gardens',
 'la-baraka','anassa-taverna-astoria','jade-eatery-and-lounge','flava-of-the-bronx',
 'the-bronx-beer-hall','mae-mae-cafe-plant-shop','code-red-restaurant-lounge'}



# --------------------------------------------------------------------------
# Gap arithmetic. ONE definition, because there are three places that need it
# and they used to disagree.
#
# The comparable and every gap are published side by side, so a gap must be the
# difference between the two numbers a reader can see. Rounding each of them
# independently from the same unrounded comparable does not do that: comp=60.5
# gives a comparable of 60 and a $45 gap of 16, and 60 - 45 is not 16.
#
# price_sweep was fixed for this; price_rescue -- a SECOND writer to the same
# cache -- was not, and neither was report(), which reads the cache raw. Both
# now go through here.
# --------------------------------------------------------------------------

def gaps_for(comparable, tiers):
    """Tier -> gap, derived from the comparable AS PUBLISHED."""
    return {t: comparable - int(str(t).strip("$")) for t in tiers}


def reconciled_gaps(rec):
    """The gaps in a cached sweep, re-derived so they subtract from its
    comparable.

    Records written before the fix hold the old numbers, and re-deriving them
    on read costs nothing where re-crawling ~600 restaurant websites to
    regenerate them would cost ten minutes of somebody else's bandwidth.

    A cache without a usable comparable is passed through untouched: there is
    nothing to reconcile against, and inventing one would be worse. So is a
    tier this cannot parse -- it keeps whatever it already had.
    """
    gaps, comp = rec.get("gaps"), rec.get("comparable_3course")
    if not isinstance(gaps, dict) or comp is None:
        return gaps
    out = {}
    for tier in gaps:
        try:
            out[tier] = comp - int(str(tier).strip("$"))
        except ValueError:
            out[tier] = gaps[tier]
    return out


def fetch(url, timeout=15, max_bytes=2_000_000, _retry=True):
    import time
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(max_bytes), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code == 308 and e.headers.get("Location"):
            return fetch(urllib.parse.urljoin(url, e.headers["Location"]),
                         timeout, max_bytes, _retry)
        if e.code == 429 and _retry:
            time.sleep(5)
            return fetch(url, timeout, max_bytes, _retry=False)
        raise


def pdf_text(data):
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages[:6])
    except Exception:
        return ""


def visible_text(html):
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", html)


def menu_links(html, base):
    links = set()
    for m in re.finditer(r'href=["\']([^"\'#]+)["\']', html, re.I):
        href = m.group(1)
        if not MENU_WORDS.search(href):
            continue
        u = urllib.parse.urljoin(base, href)
        if urllib.parse.urlparse(u).netloc == urllib.parse.urlparse(base).netloc \
           or u.lower().endswith(".pdf"):
            links.add(u)
    pdfs = [u for u in links if u.lower().endswith(".pdf")]
    pages = [u for u in links if not u.lower().endswith(".pdf")]
    return pages[:3], pdfs[:2]


def sweep_one(slug, website, tiers):
    rec = {"slug": slug, "website": website, "pages_fetched": 0, "prices": [],
           "error": None}
    try:
        data, ctype = fetch(website)
        html = data.decode("utf-8", "replace")
        rec["pages_fetched"] += 1
        texts = [visible_text(html)]
        pages, pdfs = menu_links(html, website)
        for u in pages:
            try:
                d, _ = fetch(u)
                texts.append(visible_text(d.decode("utf-8", "replace")))
                rec["pages_fetched"] += 1
            except Exception:
                pass
        for u in pdfs:
            try:
                d, _ = fetch(u, timeout=25)
                texts.append(pdf_text(d))
                rec["pages_fetched"] += 1
            except Exception:
                pass
        prices = []
        for t in texts:
            prices += [int(p) for p in PRICE.findall(t)]
        # drop obvious non-food numbers
        prices = [p for p in prices if 4 <= p <= 120]
        rec["prices"] = prices
        apps = [p for p in prices if 8 <= p <= 28]
        mains = [p for p in prices if 22 <= p <= 70]
        desserts = [p for p in prices if 6 <= p <= 20]
        if mains and apps:
            comp = (statistics.median(apps) + statistics.median(mains)
                    + (statistics.median(desserts) if desserts else 14))
            comp_r = round(comp)
            rec["comparable_3course"] = comp_r
            rec["gaps"] = gaps_for(comp_r, tiers)
        n = len(prices)
        rec["confidence"] = ("high" if n >= 12 and rec["pages_fetched"] >= 2
                             else "medium" if n >= 6 else "low")
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["confidence"] = "none"
    return rec


def targets():
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT slug, website, price_tiers FROM restaurants"
        " WHERE website IS NOT NULL AND website != ''").fetchall()
    return [(s, w, json.loads(t)) for s, w, t in rows if s not in ALREADY_ANALYZED]


def report():
    rows = []
    for f in OUT.glob("*.json"):
        r = json.loads(f.read_text())
        if r.get("gaps"):
            # Same re-derivation the database build does. A report that prints
            # a comparable and a gap on one line has to have them subtract.
            for tier, gap in reconciled_gaps(r).items():
                rows.append((gap, r["slug"], tier, r.get("comparable_3course"),
                             r["confidence"]))
    rows.sort(reverse=True)
    done = len(list(OUT.glob("*.json")))
    ok = sum(1 for f in OUT.glob("*.json") if json.loads(f.read_text()).get("gaps"))
    print(f"swept {done}, usable comparables {ok}")
    print("\n== best apparent gaps (top 30) ==")
    for gap, slug, tier, comp, conf in rows[:30]:
        print(f"  +${gap:3} {slug} {tier} (comp ~${comp}, {conf})")
    print("\n== worst (RW >= comparable, top 20) ==")
    for gap, slug, tier, comp, conf in [r for r in rows if r[0] <= 0][-20:]:
        print(f"  ${gap:4} {slug} {tier} (comp ~${comp}, {conf})")


def main():
    if "--report" in sys.argv:
        return report()
    shard_i, shard_k = 0, 1
    for a in sys.argv[1:]:
        if a.startswith("--shard"):
            shard_i, shard_k = map(int, a.split("=")[1].split("/"))
    todo = []
    for slug, website, tiers in targets():
        if int(hashlib.md5(slug.encode()).hexdigest(), 16) % shard_k != shard_i:
            continue
        if (OUT / f"{slug}.json").exists():
            continue
        todo.append((slug, website, tiers))
    print(f"shard {shard_i}/{shard_k}: {len(todo)} to sweep", flush=True)
    for i, (slug, website, tiers) in enumerate(todo, 1):
        rec = sweep_one(slug, website, tiers)
        (OUT / f"{slug}.json").write_text(json.dumps(rec))
        if i % 10 == 0:
            print(f"  {i}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
