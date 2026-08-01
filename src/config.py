"""Shared config for the NYC Restaurant Week pipeline."""
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

SEASON = "srw26"  # summer 2026; verified from live menuFileUrl values
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
