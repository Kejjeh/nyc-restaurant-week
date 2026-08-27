"""Rescue pass for restaurants the v1 sweep couldn't price.

Fixes v1's blind spots:
  1. Scans FULL raw HTML including <script> JSON (menu platforms like
     BentoBox/Popmenu/Squarespace embed prices there; v1 stripped scripts).
  2. Probes common menu paths even when unlinked (/menu, /menus, /food, ...).
  3. Follows menu links to known menu-platform hosts (getbento, popmenu,
     spothopper, etc.), not just same-domain.
  4. --render mode: headless Chromium (playwright) for sites that still
     yield nothing — executes JS, then scans the rendered DOM + JSON.

Only touches slugs with no usable comparable. Writes back to
data/raw/pricesweep/{slug}.json with "method": "v2"/"v2-render".
Shard: --shard=i/k. Report totals: price_sweep.py --report still works.
"""
import json
import hashlib
import os

# headless chromium needs libXdamage extracted to /tmp/libs in this sandbox;
# harmless elsewhere (path simply won't exist)
os.environ["LD_LIBRARY_PATH"] = (
    "/tmp/libs/extracted/usr/lib/x86_64-linux-gnu:"
    + os.environ.get("LD_LIBRARY_PATH", ""))
import re
import statistics
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from price_sweep import OUT, fetch, gaps_for, pdf_text, targets  # noqa: E402

GUESS_PATHS = ["/menu", "/menus", "/food", "/dinner-menu", "/menu/", "/menus/",
               "/dinner", "/our-menu", "/food-menu", "/lunch-menu"]
PLATFORM_HOSTS = re.compile(
    r"(getbento|popmenu|spothopper|singleplatform|menufy|toasttab|bentobox"
    r"|squarespace|heartland|clover)", re.I)
DOLLAR = re.compile(r"\$\s?(\d{1,3})(?:\.\d{2})?\b")
JSON_PRICE = re.compile(r'"(?:price|amount|cost)"\s*:\s*"?\$?(\d{1,3})(?:\.\d{2})?"?')
MENU_HREF = re.compile(r'href=["\']([^"\'#]*(?:menu|food|dine)[^"\'#]*)["\']', re.I)


def prices_from(text):
    ps = [int(p) for p in DOLLAR.findall(text)] + \
         [int(p) for p in JSON_PRICE.findall(text)]
    return [p for p in ps if 4 <= p <= 120]


def gather_urls(html, base):
    urls = []
    for m in MENU_HREF.finditer(html):
        u = urllib.parse.urljoin(base, m.group(1))
        host = urllib.parse.urlparse(u).netloc
        if host == urllib.parse.urlparse(base).netloc or PLATFORM_HOSTS.search(u):
            urls.append(u)
    for p in GUESS_PATHS:
        urls.append(urllib.parse.urljoin(base, p))
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:8]


def render_prices(url):
    """-> (prices, pages_seen).

    The page count is returned rather than assumed, because confidence is
    graded on prices AND pages ("high" needs twelve prices off two pages) and
    the caller used to hard-code 1. Render mode clicks through to a menu page
    when the landing page is thin, so the second page was real and simply not
    counted -- which made "high" unreachable for every rendered record, all
    254 of them.
    """
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        try:
            page = b.new_page(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X "
                              "10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/126.0 Safari/537.36")
            page.goto(url, timeout=20000, wait_until="networkidle")
            content = page.content()
            ps = prices_from(content)
            pages = 1
            # try one menu link in-page
            if len(ps) < 6:
                loc = page.locator("a[href*='menu' i]").first
                try:
                    loc.click(timeout=4000)
                    page.wait_for_timeout(2500)
                    ps += prices_from(page.content())
                    pages += 1
                except Exception:
                    pass
            return ps, pages
        finally:
            b.close()


def rescue_one(slug, website, tiers, render=False):
    rec = {"slug": slug, "website": website, "method": "v2-render" if render
           else "v2", "pages_fetched": 0, "prices": [], "error": None}
    try:
        if render:
            rec["prices"], rec["pages_fetched"] = render_prices(website)
        else:
            data, _ = fetch(website)
            html = data.decode("utf-8", "replace")
            rec["pages_fetched"] = 1
            texts = [html]
            for u in gather_urls(html, website):
                try:
                    d, ct = fetch(u, timeout=20)
                    if u.lower().endswith(".pdf") or "pdf" in ct:
                        texts.append(pdf_text(d))
                    else:
                        texts.append(d.decode("utf-8", "replace"))
                    rec["pages_fetched"] += 1
                except Exception:
                    continue
                if sum(len(prices_from(t)) for t in texts) >= 25:
                    break
            rec["prices"] = [p for t in texts for p in prices_from(t)]
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    ps = rec["prices"]
    apps = [p for p in ps if 8 <= p <= 28]
    mains = [p for p in ps if 22 <= p <= 70]
    desserts = [p for p in ps if 6 <= p <= 20]
    if apps and mains:
        comp = (statistics.median(apps) + statistics.median(mains)
                + (statistics.median(desserts) if desserts else 14))
        # Derived from the comparable AS PUBLISHED, not rounded independently
        # from the same unrounded figure -- see gaps_for() in price_sweep.
        # This module writes to the same cache price_sweep does, and only
        # price_sweep got the fix the first time round.
        comp_r = round(comp)
        rec["comparable_3course"] = comp_r
        rec["gaps"] = gaps_for(comp_r, tiers)
    n = len(ps)
    rec["confidence"] = ("high" if n >= 12 and rec["pages_fetched"] >= 2
                         else "medium" if n >= 6 else
                         "low" if not rec["error"] else "none")
    return rec


def todo_slugs(render=False):
    out = []
    for slug, website, tiers in targets():
        f = OUT / f"{slug}.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text())
        if r.get("gaps"):
            continue
        if not render and r.get("method") == "v2":
            continue
        if render and r.get("method") == "v2-render":
            continue
        out.append((slug, website, tiers))
    return out


def main():
    render = "--render" in sys.argv
    shard_i, shard_k = 0, 1
    for a in sys.argv[1:]:
        if a.startswith("--shard"):
            shard_i, shard_k = map(int, a.split("=")[1].split("/"))
    todo = [(s, w, t) for s, w, t in todo_slugs(render)
            if int(hashlib.md5(s.encode()).hexdigest(), 16) % shard_k == shard_i]
    print(f"{'render' if render else 'v2'} shard {shard_i}/{shard_k}: "
          f"{len(todo)} to rescue", flush=True)
    for i, (slug, website, tiers) in enumerate(todo, 1):
        rec = rescue_one(slug, website, tiers, render)
        (OUT / f"{slug}.json").write_text(json.dumps(rec))
        if i % 5 == 0:
            print(f"  {i}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
