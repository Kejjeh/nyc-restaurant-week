"""Shared config for the NYC Restaurant Week pipeline."""
import datetime
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
MENUS_DIR = RAW / "menus"
LISTING_DIR = RAW / "listing"
DETAILS_DIR = RAW / "details"
CACHE_DIR = ROOT / "data" / "cache"
for d in (MENUS_DIR, LISTING_DIR, DETAILS_DIR, PROCESSED, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)


def load_season(path):
    """Season facts from config/season.json -- the one file a changeover edits."""
    s = json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("code", "label", "year", "start", "book_by", "end", "min_rows"):
        if k not in s:
            raise ValueError(f"season.json missing key: {k}")
    if not re.fullmatch(r"srw\d{2}", s["code"]):
        raise ValueError(f"season.json code {s['code']!r} does not match srwNN")
    for k in ("start", "book_by", "end"):
        try:
            datetime.date.fromisoformat(s[k])
        except (TypeError, ValueError):
            raise ValueError(f"season.json {k} {s[k]!r} is not an ISO date")
    if s["book_by"] > s["end"]:
        raise ValueError(f"season.json book_by {s['book_by']} is after end {s['end']}")
    return s


_season = load_season(ROOT / "config" / "season.json")
SEASON = _season["code"]
SEASON_LABEL = _season["label"]
SEASON_YEAR = _season["year"]
SEASON_START = _season["start"]
BOOK_BY = _season["book_by"]
PROGRAM_END = _season["end"]
MIN_ROWS = _season["min_rows"]

# Generous bounding box over all five boroughs. Three participants with plainly
# Manhattan addresses geocode to Oakland CA and San Angelo TX in the source
# detail pages; plotting those is worse than plotting nothing, and a single bad
# point also ruins any auto-fit of the map bounds.
#
# It lived in three files, and the roster -- added later -- had a fourth copy it
# only used for judging Google results, never for the coordinates it published.
# So the dashboard nulled Oakland out and the roster shipped it. One definition.
NYC_BOUNDS = (40.45, 41.02, -74.30, -73.65)   # lat_min, lat_max, lng_min, lng_max


def in_nyc(lat, lng):
    """Is this point plausibly in New York City?"""
    if lat is None or lng is None:
        return False
    lo_a, hi_a, lo_o, hi_o = NYC_BOUNDS
    return lo_a <= lat <= hi_a and lo_o <= lng <= hi_o


def sane_coords(lat, lng):
    """(lat, lng) if the point is plausibly in NYC, else (None, None)."""
    return (lat, lng) if in_nyc(lat, lng) else (None, None)


API_URL = "https://program-api.nyctourism.com/restaurant-week"
SITE = "https://www.nyctourism.com"
LISTING_PAGE = f"{SITE}/restaurant-week/"

# Public key embedded in the site's client-side JS (page chunk). If it rotates,
# discover_api_key() re-extracts it from the live JS bundles.
DEFAULT_API_KEY = "lTQSe929f34fohKaNq0OH53mdVL0yncvtqmuUG6i"
KEY_CACHE = CACHE_DIR / "api_key.txt"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

RATE_LIMIT_SECONDS = 1.0  # polite: <= 1 request/sec everywhere
_last_request = [0.0]


def throttle():
    wait = _last_request[0] + RATE_LIMIT_SECONDS - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.time()


def http_get(url, timeout=30):
    throttle()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout).read()


def api_key():
    if KEY_CACHE.exists():
        return KEY_CACHE.read_text().strip()
    return DEFAULT_API_KEY


def discover_api_key():
    """Re-extract the public x-api-key from the site's JS bundles."""
    html = http_get(LISTING_PAGE).decode("utf-8", "replace")
    chunks = sorted(set(re.findall(r'src="(/_next/static/chunks/[^"]+)"', html)))
    for u in chunks:
        js = http_get(SITE + u).decode("utf-8", "replace")
        if "program-api" not in js:
            continue
        m = re.search(r'"x-api-key"\s*:\s*"([A-Za-z0-9]{20,})"', js)
        if m:
            KEY_CACHE.write_text(m.group(1))
            return m.group(1)
    raise RuntimeError("Could not find x-api-key in site JS; inspect manually.")


def api_post(body, retry_on_403=True):
    throttle()
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key(),
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except urllib.error.HTTPError as e:
        if e.code == 403 and retry_on_403:
            discover_api_key()
            return api_post(body, retry_on_403=False)
        raise


# How many reviews of doubt a rating is shrunk by, before we mostly believe a
# restaurant's own average. Lives here rather than in export_site_data because
# THREE payloads publish a score computed with it -- restaurants.json,
# places.json and venues.json -- and the roster page had drifted to its own
# value of 300 while its comment said it used "the same treatment". Chosen
# against the actual review distribution; the reasoning is in the README and in
# the block above bayesian_score().
GOOGLE_PRIOR = 150
