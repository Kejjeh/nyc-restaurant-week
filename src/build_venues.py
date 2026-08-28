"""Build the canonical VENUE roster: every NYC restaurant with award recognition.

Usage: python src/build_venues.py [--quiet]

The site used to be a Restaurant Week tool, so its universe was "the 636
restaurants in this season's listing" and an award was a badge one of them
might carry. That had the relationship backwards. An award is the durable
fact -- Michelin, the Beard Foundation and the Times keep recognising the same
restaurants for decades -- while Restaurant Week participation is a marketing
decision one restaurant makes in one summer.

So: VENUES is the roster. `rw_slug` is a nullable column on it. Being in
Restaurant Week is now a property a venue may or may not have, alongside
holding a star or being closed.

Inputs
  data/processed/restaurant_week.sqlite   (restaurants: this season's listing)
  data/raw/recognition/*.json             (the award lists, unfiltered)
  config/awards.json                      (sources, honor points, weights)

Outputs (same DB)
  venues        one row per real restaurant, RW or not, open or not
  venue_awards  one row per award record, attached to a venue

  data/processed/venue_merge_review.json  every merge this refused to make

MERGING IS THE WHOLE PROBLEM. Michelin and the Times give addresses; the Beard
Foundation gives none at all, for 1,363 records spanning 35 years. The rules
below are deliberately conservative in different ways per source, and anything
they will not decide is written to the review file rather than guessed. The
cost of a wrong merge is two restaurants silently becoming one row; the cost of
a missed merge is one restaurant appearing twice. Both are bad. Only the first
is invisible, so that is the one the thresholds are set against.
"""
import datetime as dt
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from config import SEASON_YEAR, sane_coords
from enrich_recognition import norm_name, street_key

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
RAW = ROOT / "data" / "raw" / "recognition"
AWARDS_CONFIG = ROOT / "config" / "awards.json"
ALIASES = ROOT / "config" / "venue_aliases.json"
REVIEW = ROOT / "data" / "processed" / "venue_merge_review.json"
SLUG_LEDGER = ROOT / "data" / "venue_slugs.json"

# How alike two tokens must be before one is treated as a misspelling of the
# other. 0.85 sits in the gap the data leaves: the real variants measured here
# score 0.889 and above, the east/west contrast pairs all score 0.75.
TOKEN_SIM = 0.85

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
  venue_slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  address TEXT, lat REAL, lng REAL, borough TEXT, neighborhood TEXT,
  rw_slug TEXT REFERENCES restaurants(slug),  -- NULL = not in Restaurant Week
  status TEXT CHECK (status IN ('open','closed','unknown')) NOT NULL DEFAULT 'unknown',
  status_source TEXT,        -- what established the status, never a guess
  place_id TEXT,
  rating REAL, user_ratings_total INTEGER,
  first_award_year INTEGER, last_award_year INTEGER,
  award_sources TEXT,        -- JSON array, e.g. ["michelin","james_beard"]
  award_count INTEGER NOT NULL DEFAULT 0,
  top_honor TEXT,            -- 'michelin:1 star' etc; the best single honor held
  top_honor_label TEXT,
  top_honor_year INTEGER,    -- the year that honor was given
  top_honor_is_latest INTEGER,  -- 1 if from that source's most recent selection
  prestige INTEGER,          -- 0-100, per config/awards.json
  seeded_from TEXT,          -- which source first created this row
  resolution TEXT            -- how identity was established, in words
);
CREATE TABLE IF NOT EXISTS venue_awards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  venue_slug TEXT REFERENCES venues(venue_slug),
  source TEXT NOT NULL, level TEXT, award TEXT, year INTEGER,
  rank INTEGER,              -- NYT Top 100 position, where the list is ranked
  person TEXT,               -- Beard awards are frequently to a chef, not a room
  source_url TEXT,
  matched_name TEXT, match_confidence REAL, how TEXT
);
CREATE INDEX IF NOT EXISTS idx_va_venue ON venue_awards(venue_slug);
CREATE INDEX IF NOT EXISTS idx_venues_rw ON venues(rw_slug);
CREATE INDEX IF NOT EXISTS idx_venues_prestige ON venues(prestige);
"""


def load_aliases(path=ALIASES):
    """Human rulings the matching rules will not make for themselves.

    Kept OUT of the rules on purpose. Every entry here is one edit away from a
    threshold that would also mis-handle a real name -- splitting "Zaab Zaab,
    Zaab Zaab Talay" automatically means splitting "Fifty Seven Fifty Seven,
    The Four Seasons Hotel" too, and that failure is invisible. A handful of
    hand rulings costs nothing and breaks nothing.
    """
    if not path.exists():
        return {}, {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    strip = lambda d: {k: v for k, v in (d or {}).items() if not k.startswith("_")}
    not_venues = {norm_name(k): v for k, v in strip(doc.get("not_venues")).items()}
    split_into = {norm_name(k): v for k, v in strip(doc.get("split_into")).items()}
    return not_venues, split_into


def slugify(name):
    """Name -> kebab slug, apostrophes closing up ("Mark's" -> marks).

    Deliberately the same shape src/places_cli.py produces, so a venue slug and
    a Restaurant Week slug are comparable strings rather than two dialects.
    """
    s = norm_name(re.sub(r"['’]", "", name or ""))
    return "-".join(s.split()) or "venue"


def unique_slug(base, taken):
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


class Ledger:
    """Venue slugs that outlive the build that minted them.

    build_venues rebuilds the roster from scratch every run, so without this a
    venue_slug is only ever a fact about the current inputs. 156 of the RW-seeded
    venues take their slug from the programme's listing; when one of those
    restaurants is absent from the next listing it is re-created from its award
    records under a different slug, and the weekly report reads that as a
    departure and an arrival rather than as the same restaurant.

    The ledger is the memory that makes a slug an identity: once a restaurant
    has one it keeps it, whichever source happens to seed it next season.

    Reuse is not on name alone. Two different restaurants share a name often
    enough (and the roster already carries "-2" slugs for them) that handing
    over a slug on a name match would hand over another restaurant's identity,
    and after issue #2 its paid Places lookup with it. An entry is eligible only
    if the incoming address corroborates it or neither side has an address to
    disagree with; a plain contradiction mints a new slug instead.
    """

    VERSION = 1

    def __init__(self, entries=None, today=None):
        self.entries = [dict(e) for e in (entries or [])]
        self.today = today or dt.date.today().isoformat()
        self.by_norm = {}
        for e in self.entries:
            self.by_norm.setdefault(e["norm"], []).append(e)
        self.order = {id(e): i for i, e in enumerate(self.entries)}
        # Slugs spoken for by a live entry and not yet claimed this build. A new
        # venue must not mint one of these, or the restaurant it belongs to
        # cannot have it back when it returns.
        self.free = {e["slug"] for e in self.entries if not e.get("merged_into")}
        self.claimed = {}         # slug -> entry, this build
        self.minted = []          # slugs with no prior entry
        self.reissued = []        # slugs an earlier build would have changed

    @classmethod
    def load(cls, path, today=None):
        if not Path(path).exists():
            return cls(today=today)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(doc.get("entries"), today=today)

    def _fit(self, entry, street, postal, has_address):
        """How well an entry corroborates an incoming venue. Lower is better."""
        # street_key is a SET of candidate numbers and corroborates by
        # intersection, not equality -- "385 Ninth Ave." and "440 W. 33rd St."
        # are one restaurant with two entrances.
        if street and entry.get("street") and set(entry["street"]) & street:
            return 0
        if postal and entry.get("zip") and entry["zip"] == postal:
            return 1
        if not has_address or not (entry.get("street") or entry.get("zip")):
            return 2      # nothing to disagree with
        return 3          # a real contradiction; not this restaurant

    def claim(self, name, address):
        """The slug this restaurant held in an earlier build, or None."""
        cands = [e for e in self.by_norm.get(norm_name(name), [])
                 if not e.get("merged_into") and e["slug"] in self.free]
        if not cands:
            return None
        street, postal = street_key(address), postal_key(address)
        scored = [(self._fit(e, street, postal, bool(address)), self.order[id(e)], e)
                  for e in cands]
        fit, _, entry = min(scored, key=lambda t: t[:2])
        if fit > 2:
            return None
        self.free.discard(entry["slug"])
        self.claimed[entry["slug"]] = entry
        return entry["slug"]

    def reserved(self, slug):
        return slug in self.free

    def record(self, venue):
        """Write this build's answer back, minting an entry for a new venue."""
        slug = venue["venue_slug"]
        entry = self.claimed.get(slug)
        if entry is None:
            entry = next((e for e in self.entries if e["slug"] == slug
                          and not e.get("merged_into")), None)
            if entry is None:
                entry = {"slug": slug, "norm": norm_name(venue["name"]),
                         "first_seen": self.today}
                self.entries.append(entry)
                self.order[id(entry)] = len(self.entries)
                self.by_norm.setdefault(entry["norm"], []).append(entry)
                self.minted.append(slug)
            self.free.discard(slug)
            self.claimed[slug] = entry
        entry["name"] = venue["name"]
        entry["norm"] = norm_name(venue["name"])
        entry["street"] = sorted(street_key(venue.get("address")) or ()) or None
        entry["zip"] = postal_key(venue.get("address"))
        entry["last_seen"] = self.today

    def retire(self, slug, into):
        """One venue folded into another: the losing slug is never reissued.

        Kept rather than deleted so a link to it can still be answered -- the
        restaurant did not stop existing, its row did.
        """
        for e in self.entries:
            if e["slug"] == slug and not e.get("merged_into"):
                e["merged_into"] = into
                e["last_seen"] = self.today
        self.free.discard(slug)
        self.claimed.pop(slug, None)

    def document(self):
        return {"_doc": "venue_slug is a durable identity, not a fact about one "
                        "build. An entry is never deleted and a slug is never "
                        "reissued to a different restaurant; a venue folded into "
                        "another carries merged_into. See issue 25.",
                "version": self.VERSION,
                "entries": sorted(self.entries, key=lambda e: e["slug"])}


class Roster:
    """The venue set under construction, with the indexes merging needs.

    Kept as a class only because every merge decision needs three views of the
    same data at once -- by slug, by normalised name, and by street number --
    and threading three dicts through every function was worse.
    """

    def __init__(self, ledger=None):
        self.venues = {}          # slug -> dict
        self.by_norm = {}         # norm_name -> [slug]
        self.awards = []          # pending venue_awards rows
        self.refused = []         # merges this would not make, for a human
        self.confirm = []         # merges MADE on weaker evidence, for a human
        self.ledger = ledger      # slugs held from earlier builds, or None
        self.from_ledger = set()  # slugs this build did not get to choose

    def taken(self):
        """Slugs a new venue may not mint: in use, or held for an absent one."""
        if self.ledger is None:
            return self.venues
        return set(self.venues) | self.ledger.free

    def add(self, name, seeded_from, **fields):
        slug = self.ledger.claim(name, fields.get("address")) if self.ledger else None
        if slug:
            self.from_ledger.add(slug)
        else:
            slug = unique_slug(slugify(name), self.taken())
        v = {"venue_slug": slug, "name": name, "seeded_from": seeded_from,
             "address": None, "lat": None, "lng": None, "borough": None,
             "neighborhood": None, "rw_slug": None, "status": "unknown",
             "status_source": None, "place_id": None, "rating": None,
             "user_ratings_total": None, "resolution": None}
        v.update({k: val for k, val in fields.items() if val is not None})
        self.venues[slug] = v
        self.by_norm.setdefault(norm_name(name), []).append(slug)
        return v

    def candidates(self, name):
        return [self.venues[s] for s in self.by_norm.get(norm_name(name), [])]


def postal_key(addr):
    """The 5-digit ZIP, which street_key deliberately throws away.

    Needed because a street number is not the identity of a building. Ci Siamo
    sits in Manhattan West and is published as both "385 Ninth Ave." and
    "440 W. 33rd St." -- one restaurant, two entrances, no shared digits. The
    ZIP is what those two strings still agree on.
    """
    m = re.findall(r"\b(\d{5})\b", addr or "")
    return m[-1] if m else None


def match_with_address(roster, name, address):
    """Source HAS an address. Name must agree and the location must corroborate.

    Returns (venue|None, how, decision) where decision is one of:
      'merge'    -> use the returned venue
      'create'   -> make a new venue; this is a genuinely different restaurant
      'confirm'  -> merged, but on weaker evidence; log it for a human
      'refuse'   -> decide nothing, write it to review

    The evidence ladder, strongest first: a shared street number, then our own
    row having no address to contradict with, then a shared ZIP. Nothing below
    a shared ZIP merges.
    """
    ek = street_key(address)
    cands = roster.candidates(name)
    if not cands:
        return None, "no name match; new venue", "create"
    if not ek:
        if len(cands) == 1:
            return cands[0], "exact name, no street number to check", "merge"
        return None, f"name matches {len(cands)} venues, no address to break the tie", "refuse"

    hits = [c for c in cands if (street_key(c.get("address")) or set()) & ek]
    if len(hits) == 1:
        return hits[0], "name + street number", "merge"
    if len(hits) > 1:
        return None, f"{len(hits)} venues share this name AND street number", "refuse"

    # No street number agreed. Our own row may simply not have an address --
    # 40-odd Restaurant Week listings do not publish one -- and an absent
    # address is not a contradiction. A unique name is enough there, and the
    # external address is then worth adopting.
    if len(cands) == 1 and not cands[0].get("address"):
        return cands[0], "exact name; our row had no address to check", "merge"

    same_zip = [c for c in cands
                if postal_key(c.get("address"))
                and postal_key(c.get("address")) == postal_key(address)]
    if len(same_zip) == 1:
        return same_zip[0], "exact name, same postal code, different street entrance", "confirm"

    if len(cands) == 1:
        return None, "same name, different address; new venue", "create"
    return None, f"name matches {len(cands)} venues, none at this address", "refuse"


def match_name_only(roster, name):
    """Source has NO address -- the entire James Beard file.

    A name is not an identifier, so the only safe merge is onto a name that is
    unique in the roster. Two candidates is not a coin flip to be won; it is a
    row for a human. And a name that matches nothing is a new venue, which for
    a 1994 Beard nominee is very often a restaurant that closed before the
    Restaurant Week listing this roster was seeded from ever existed.
    """
    cands = roster.candidates(name)
    if not cands:
        return None, "no name match; new venue", "create"
    if len(cands) == 1:
        return cands[0], "unique name, no address available", "merge"
    return None, f"name matches {len(cands)} venues and this source has no address", "refuse"


def spelling_variant(a, b):
    """Is `b` the same name as `a`, misspelled -- as opposed to a sibling branch?

    A plain similarity ratio cannot answer this, and reaching for one is how the
    roster grows a wrong merge. Measured on the real data:

        la pecora bianca upper EAST side / upper WEST side   ratio 0.969
        saint ambroeus                   / sant ambroeus     ratio 0.963

    The pair that must NOT merge scores HIGHER than the pair that must. Ratio
    is measuring the wrong thing: what separates them is not how much of the
    string differs but WHICH token does, and whether that token is a spelling
    of the other or a word chosen to contrast with it.

    So: exactly one token may differ on each side, and those two tokens must
    look like one word written twice -- similar, and starting with the same
    letter. "boon"/"boons" and "momfuku"/"momofuku" pass; "east"/"west" fails
    on both counts.

    That is still not enough on its own. These three pairs are all one edit
    apart and all clear the similarity bar, and all three are DIFFERENT
    restaurants:

        isa / insa            Isa in Williamsburg, Insa in Gowanus
        mam / mamo            Mắm on the Lower East Side, Mamo in SoHo
        sevilla / semilla     Sevilla in the West Village, Semilla in Williamsburg

    What they have in common is that the differing token is the ENTIRE name.
    There is no second word left to agree, so the similarity is the whole of
    the evidence and one letter decides a merge. The pairs that are genuinely
    one restaurant always keep something: "ambroeus", "verde", "ssam bar",
    "uncle". So a spelling variant must also share at least one other token --
    a single-word name is never folded into another single-word name.
    """
    A, B = norm_name(a).split(), norm_name(b).split()
    if A == B or not A or not B:
        return False
    ca, cb = Counter(A), Counter(B)
    only_a = list((ca - cb).elements())
    only_b = list((cb - ca).elements())
    # One extra token on one side only means an added qualifier -- "Tonchin"
    # against "Tonchin Brooklyn" -- which is a different restaurant, not a typo.
    if len(only_a) != 1 or len(only_b) != 1:
        return False
    if not (set(A) & set(B)):
        return False        # nothing but the suspect token to go on
    x, y = only_a[0], only_b[0]
    return x[0] == y[0] and SequenceMatcher(None, x, y).ratio() >= TOKEN_SIM


def addresses_compatible(a, b):
    """Could these two addresses be the same place? Missing is not a conflict."""
    if not a or not b:
        return True
    ka, kb = street_key(a), street_key(b)
    if ka and kb and ka & kb:
        return True
    pa, pb = postal_key(a), postal_key(b)
    if pa and pb:
        return pa == pb
    return not (ka and kb)


SOURCE_RANK = {"rw": 0, "michelin": 1, "nyt": 2, "james_beard": 3}


def _row_rank(v):
    """Which of two duplicate rows should survive. Restaurant Week first (it is
    the only source with coordinates), then the sources that carry addresses."""
    return (0 if v.get("rw_slug") else 1, SOURCE_RANK.get(v.get("seeded_from"), 9))


def _preferred_name(a, b, awards):
    """-> (name, was_it_a_tie). Frequency across award records decides; the
    shorter spelling breaks a true tie, which is arbitrary but deterministic
    and is recorded in the review file so a human can overrule it."""
    counts = {}
    for aw in awards:
        n = aw.get("matched_name")
        if n:
            counts[n] = counts.get(n, 0) + 1
    na, nb = a["name"], b["name"]
    ca, cb = counts.get(na, 0), counts.get(nb, 0)
    if ca != cb:
        return (na if ca > cb else nb), False
    ra, rb = SOURCE_RANK.get(a.get("seeded_from"), 9), SOURCE_RANK.get(b.get("seeded_from"), 9)
    if ra != rb:
        return (na if ra < rb else nb), False
    return (na if len(na) <= len(nb) else nb), True


def merge_spelling_variants(roster):
    """Second pass: fold venues that are one restaurant spelled two ways.

    Runs after every source is in, not during, because the duplicate is usually
    created by a LATER source than the row it duplicates -- and because a rule
    this delicate belongs somewhere it can be listed, tested and audited on its
    own rather than buried in the middle of a matching loop.
    """
    merges = []
    slugs = sorted(roster.venues)
    absorbed = set()
    for i, sa in enumerate(slugs):
        if sa in absorbed:
            continue
        for sb in slugs[i + 1:]:
            if sb in absorbed:
                continue
            va, vb = roster.venues[sa], roster.venues[sb]
            if not spelling_variant(va["name"], vb["name"]):
                continue
            if not addresses_compatible(va.get("address"), vb.get("address")):
                continue
            # Keep the row with more to lose: a Restaurant Week seed carries
            # coordinates, a neighborhood and a live listing; an award-created
            # row carries a name and maybe an address.
            keep, drop = (va, vb) if _row_rank(va) <= _row_rank(vb) else (vb, va)
            for field in ("address", "lat", "lng", "borough", "neighborhood"):
                if keep.get(field) is None and drop.get(field) is not None:
                    keep[field] = drop[field]
            for aw in roster.awards:
                if aw["venue_slug"] == drop["venue_slug"]:
                    aw["venue_slug"] = keep["venue_slug"]
                    aw["how"] = f"{aw.get('how') or ''}; spelling variant folded"
            # Which row survives and which SPELLING is displayed are separate
            # questions. The surviving row is the one carrying the data; the
            # displayed name is whichever spelling the sources use most, because
            # the misspelling is by definition the rare one. Both Momofuku Ssam
            # Bar (7 records to 1) and Uncle Boons (6 to 1) are decided here,
            # and were shown under their typo before this existed.
            display, tie = _preferred_name(keep, drop, roster.awards)
            was = keep["name"]
            keep["name"] = display
            merges.append({"kept": keep["venue_slug"], "kept_name": display,
                           "folded": drop["venue_slug"],
                           "folded_name": drop["name"] if display != drop["name"] else was,
                           "name_was_a_tie": tie,
                           "reason": "one differing token, same first letter, "
                                     f"similarity >= {TOKEN_SIM}"})
            absorbed.add(drop["venue_slug"])
            if roster.ledger is not None:
                roster.ledger.retire(drop["venue_slug"], keep["venue_slug"])
            roster.by_norm[norm_name(drop["name"])].remove(drop["venue_slug"])
            del roster.venues[drop["venue_slug"]]
            break
    return merges


# Borough from a postal code, which is the only part of a US address that
# actually encodes one. The city token lies constantly here: every Michelin
# address in Manhattan says "New York", and a Queens address is as likely to
# say "Astoria" or "Long Island City" as "Queens".
ZIP_BOROUGH = (
    ("100", "Manhattan"), ("101", "Manhattan"), ("102", "Manhattan"),
    ("103", "Staten Island"),
    ("104", "The Bronx"),
    ("112", "Brooklyn"),
    ("110", "Queens"), ("111", "Queens"), ("113", "Queens"), ("114", "Queens"),
    ("116", "Queens"),
)
# Named places that resolve a borough on their own, for the addresses and the
# James Beard `city` hints that carry no usable ZIP.
CITY_BOROUGH = {
    "manhattan": "Manhattan", "new york": "Manhattan", "new york city": "Manhattan",
    "brooklyn": "Brooklyn", "queens": "Queens", "bronx": "The Bronx",
    "the bronx": "The Bronx", "staten island": "Staten Island",
    "long island city": "Queens", "astoria": "Queens", "flushing": "Queens",
    "forest hills": "Queens", "woodside": "Queens", "jackson heights": "Queens",
    "ridgewood": "Queens", "rego park": "Queens", "elmhurst": "Queens",
    "sunnyside": "Queens", "corona": "Queens", "jamaica": "Queens",
}


def borough_from(address, city_hint=None):
    """Borough, or None. ZIP first because the city token is unreliable.

    Spellings match the Restaurant Week listing's own ("The Bronx", not
    "Bronx") so one borough is one filter value on the site rather than two.

    Never guesses "Manhattan" from an absent address: an unplaced venue is a
    fact about our data, and pretending otherwise puts pins in the wrong borough
    on the map and silently skews every by-borough count on the page.
    """
    z = postal_key(address)
    if z:
        for prefix, boro in ZIP_BOROUGH:
            if z.startswith(prefix):
                return boro
    for text in (address, city_hint):
        if not text:
            continue
        low = text.lower()
        for token, boro in CITY_BOROUGH.items():
            if re.search(rf"\b{re.escape(token)}\b", low):
                return boro
    return None


GROUP_NOISE = {"others", "and others", "inc", "co", "llc"}


def split_group(name):
    """A restaurateur award names a PORTFOLIO, not a restaurant. -> parts.

    The James Beard file records the Outstanding Restaurateur category as one
    string listing every room the winner runs:

        "Frenchette, Le Veau d' Or, and Le Rock"
        "Gracious Hospitality (COTE, Undercote, and COQODAQ)"

    Left alone each of those becomes a venue in its own right -- a restaurant
    called "Frenchette, Le Veau d' Or, and Le Rock" appeared on the roster --
    and the actual restaurants never receive the award.

    Splitting is the easy half. The hard half is that plenty of real names
    contain the same punctuation: Gage & Tollner, Milk & Honey, Grand Central
    Oyster Bar and Restaurant, Simon & The Whale, Fifty Seven Fifty Seven, The
    Four Seasons Hotel. This function only proposes the parts; the caller
    decides, and the bar it has to clear depends on group_marker() below.
    """
    inner = re.search(r"\(([^)]*,[^)]*)\)", name or "")
    text = inner.group(1) if inner else (name or "")
    # The slash is a separator too. Five Beard records pack a portfolio into one
    # with no comma anywhere -- "Babbo/Lupa/Esca" -- and without this those three
    # restaurants never receive the award the Foundation just gave them, while a
    # venue named after all three sits on the roster instead.
    parts = [p.strip(" .\"") for p in re.split(r",|\band\b|/", text)]
    # "Le Veau d' Or" is written with a stray space in the Beard file. Left
    # alone it becomes a venue under that spelling and never meets the real
    # name again -- norm_name keeps "d or" as two tokens and "dor" as one, so
    # even the spelling-variant pass cannot reunite them.
    parts = [re.sub(r"(?<=\b\w)['’]\s+", "'", p) for p in parts]
    return [p for p in parts if p and p.lower() not in GROUP_NOISE]


# Parts that are not restaurant names. The award files pack extra information
# into these strings -- a second city, a sister business -- and a part shaped
# like that must never become a venue. Everything else in a confirmed list IS a
# restaurant name, because the awarding body just said so.
NOT_A_NAME = re.compile(r"^(?:\W*|.{,2}|[A-Z]{2,3})$")


def plausible_venue_name(part):
    """Is this part of a confirmed list a restaurant name?

    "Nobu, NY/Matsuhisa, LA" is the case this exists for: a real restaurant, a
    second location written with a slash, and a city abbreviation. Splitting it
    once put a venue called "LA" on the roster.

    split_group() now splits on the slash as well, so a part reaching here will
    rarely contain one. The guard stays for the parts that do not come from
    split_group -- a hand-ruled split in venue_aliases.json is taken verbatim.

    Deliberately a test on the STRING, not on whether we have heard of it.
    Whether the Beard Foundation named Morandi in a 2010 award is a fact about
    the award, and making it depend on our own roster is what made this
    season-dependent in the first place.
    """
    part = (part or "").strip()
    if NOT_A_NAME.match(part):
        return False
    # A slash means the source packed two things into one part. We do not know
    # which half is the restaurant, and guessing is how "NY/Matsuhisa" happens.
    return "/" not in part and any(ch.isalpha() for ch in part)


def group_marker(name):
    """Is this string unambiguously a LIST of restaurants? -> the marker, or None.

    These three shapes never occur inside a real restaurant name, so a string
    carrying one can be split on much weaker evidence than an ambiguous
    "X and Y" -- a single matching part is enough, and the leftovers are
    recorded rather than turned into venues.
    """
    low = (name or "").lower()
    if "and others" in low or "others)" in low:
        return "names 'and others'"
    if re.search(r"\([^)]*(,|\band\b)[^)]*\)", name or ""):
        return "parenthesised list"
    if (name or "").count(",") >= 2:
        return "comma list"
    return None


def awarded_slugs(roster):
    """Venue slugs holding at least one award, as of right now.

    Recomputed rather than cached because resolve_group_awards() adds awards as
    it goes, and a part vouched in by one portfolio string is legitimate
    evidence for the next one.
    """
    return {a["venue_slug"] for a in roster.awards}


def resolve_group_awards(roster, deferred, split_into=None, not_venues=None):
    """Attach each deferred portfolio award to the restaurants it names.

    A part that does not resolve is recorded rather than created: "Le Veau d'
    Or" is written with a stray space in this file and will not match, and
    inventing a venue for it is how the junk row got there in the first place.

    Resolution is iterated to a fixed point, because one portfolio string can
    supply the evidence another one needs. "Babbo/Lupa/Esca" vouches Esca onto
    the roster; only then can "Po/Lupa/Babbo" show two award-holding parts and
    prove it is a list too. Deciding in a single pass makes the answer depend on
    the order the records happen to sit in the file, which is exactly the
    dependency #7 was written to remove. The loop cannot run away: a pass that
    resolves nothing ends it, so it runs at most once per deferred string.
    """
    split_into = split_into or {}
    not_venues = not_venues or {}
    attached, kept_whole, unresolved = [], [], []
    created_from_groups = []

    pending = list(deferred)
    while True:
        still = [item for item in pending
                 if not _resolve_group(roster, item, split_into, not_venues,
                                       attached, unresolved, created_from_groups)]
        if len(still) == len(pending):
            break
        pending = still

    # Everything still here failed on the last pass with the whole roster built,
    # so its verdict will not change however many more passes it gets.
    for item in pending:
        marker = ("hand-ruled split" if norm_name(item["name"]) in split_into
                  else group_marker(item["name"]))
        if marker:
            roster.refused.append({
                "source": item["source"], "name": item["name"], "address": None,
                "year": item["award"].get("year"),
                "level": item["award"].get("level"),
                "reason": f"{marker}, but none of its parts match a venue",
                "candidates": split_into.get(norm_name(item["name"]))
                              or split_group(item["name"])})
        else:
            kept_whole.append(item)
    return attached, kept_whole, unresolved, created_from_groups


def _resolve_group(roster, item, split_into, not_venues,
                   attached, unresolved, created_from_groups):
    """One attempt at one portfolio string. -> did it resolve?

    Returns False without touching the roster when the evidence is not there
    yet, so the caller can try again once another string has added venues.
    """
    ruled = split_into.get(norm_name(item["name"]))
    parts = ruled or split_group(item["name"])
    # A human confirmed this one is a list, so the parts do not have to
    # prove it by already existing -- they are created if they are new.
    marker = "hand-ruled split" if ruled else group_marker(item["name"])
    if ruled:
        for part in parts:
            if match_name_only(roster, part)[2] == "create":
                v = roster.add(part, item["source"])
                v["resolution"] = "created from a hand-ruled split in venue_aliases.json"
    hits, misses = [], []
    for part in parts:
        venue, _, decision = match_name_only(roster, part)
        (hits if decision == "merge" else misses).append((part, venue))
    # Evidence that this string is a LIST has to come from somewhere that
    # does not change every summer. A venue holding an award of its own is
    # in these files whatever Restaurant Week does; a venue that exists only
    # because it joined this season's programme is not, and counting it made
    # three of Keith McNally's restaurateur awards silently disappear when
    # Morandi left the listing.
    vouching = [pair for pair in hits
                if pair[1]["venue_slug"] in awarded_slugs(roster)]
    # Without a marker the string might be one restaurant, and only two
    # independent hits can rule that out. With one, it is a list either way,
    # so a single hit is enough and zero hits still must not become a venue.
    if len(vouching) < (1 if marker else 2):
        return False
    # The string is a confirmed list, so every part of it is a restaurant
    # name unless its shape says otherwise. Creating the ones we have not
    # met is the point: Pastis, Pravda, Lucky Strike, Reynard, Undercote,
    # Cafe Zaffri and Le Veau d'Or are real restaurants that appear in these
    # files ONLY inside a portfolio string, and never reached the roster.
    for part, _ in misses:
        # A part a human has ruled is not a restaurant is dropped here as well
        # as at the top of the source loop. Splitting on the slash is what makes
        # this reachable: "Nobu, NY/Matsuhisa, LA" used to be refused whole
        # because its middle part contained a slash, and now yields Matsuhisa --
        # a real restaurant, in Los Angeles.
        if norm_name(part) in not_venues:
            unresolved.append({"group": item["name"], "part": part,
                               "marker": marker,
                               "year": item["award"].get("year"),
                               "reason": not_venues[norm_name(part)]})
        elif plausible_venue_name(part):
            venue = roster.add(part, item["source"])
            venue["resolution"] = (
                f"named in a group award alongside "
                f"{len(vouching)} award-holding restaurants: {item['name']!r}")
            hits.append((part, venue))
            created_from_groups.append(part)
        else:
            unresolved.append({"group": item["name"], "part": part,
                               "marker": marker,
                               "year": item["award"].get("year"),
                               "reason": "named in a group award but does not "
                                         "have the shape of a restaurant name"})
    for part, venue in hits:
        a = dict(item["award"])
        a.update(venue_slug=venue["venue_slug"], matched_name=part,
                 match_confidence=0.9,
                 how=f"named in a group award: {item['name']!r}")
        roster.awards.append(a)
        attached.append((venue["venue_slug"], part))
    return True


def nyt_rank(notes, pattern):
    m = re.search(pattern, notes or "")
    return int(m.group(1)) if m else None


def load_awards_config(path=AWARDS_CONFIG):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("sources", "honors", "breadth_bonus", "recency", "closed_penalty"):
        if key not in cfg:
            raise ValueError(f"awards.json missing key: {key}")
    for k in cfg["honors"]:
        if k.startswith("_"):
            continue
        if ":" not in k:
            raise ValueError(f"honor key {k!r} must be 'source:level'")
    pinned = cfg["recency"].get("reference_year")
    if pinned is not None and not isinstance(pinned, int):
        raise ValueError(
            f"awards.json recency.reference_year must be an integer or null, "
            f"got {pinned!r}")
    return cfg


def honor_key(source, level):
    return f"{source}:{level}"


def reference_year(cfg):
    """The year recency is measured from.

    `config/season.json` is the only file in this repo allowed to carry a year --
    the README states it as an invariant and every other module honours it. This
    one quietly did not: `reference_year` sat hard-coded in awards.json, so the
    first changeover would have decayed every venue's standing against a stale
    year with nothing failing to say so.

    An explicit value still wins, because pinning a rebuild to a past scoring
    run is a real thing to want. Absent or null, the season file decides.
    """
    return cfg.get("recency", {}).get("reference_year") or SEASON_YEAR


def prestige_for(venue_awards, cfg, closed):
    """0-100 composite. Documented in config/awards.json; no magic here.

    base   = the single best honor held
    + NYT rank bonus, linear from No. 1 down to No. 100
    + breadth, for each independent jury beyond the first
    x recency, on the most recent honor of any kind
    x closed penalty, applied last
    """
    honors = cfg["honors"]
    scored = [(honors[honor_key(a["source"], a["level"])]["points"], a)
              for a in venue_awards
              if honor_key(a["source"], a["level"]) in honors]
    if not scored:
        return 0, None, None, None, None
    base, best = max(scored, key=lambda p: p[0])
    total = float(base)

    ranks = [a["rank"] for a in venue_awards if a.get("rank")]
    if ranks:
        rmax = cfg.get("nyt_rank_bonus", {}).get("max", 0)
        total += rmax * max(0.0, (100 - min(ranks)) / 99.0)

    sources = {a["source"] for a in venue_awards}
    b = cfg["breadth_bonus"]
    total += min(b["max"], b["per_extra_source"] * (len(sources) - 1))

    years = [a["year"] for a in venue_awards if a.get("year")]
    if years:
        age = reference_year(cfg) - max(years)
        for step in cfg["recency"]["steps"]:
            if age <= step["within_years"]:
                total *= step["factor"]
                break
    if closed:
        total *= cfg["closed_penalty"]["factor"]

    key = honor_key(best["source"], best["level"])
    return (int(round(min(100.0, max(0.0, total)))), key, honors[key]["label"],
            best.get("year"), best["source"])


def build(con, cfg, quiet=False, ledger=None):
    roster = Roster(ledger)
    not_venues, split_into = load_aliases()
    ruled_out = []

    # --- seed: this season's Restaurant Week listing -------------------------
    # Seeded first on purpose. These rows are the only ones that arrive with a
    # verified address, coordinates and a neighborhood, so every later source
    # gets to merge ONTO them rather than the other way round.
    for row in con.execute(
        "SELECT slug, name, address, lat, lng, borough, neighborhood"
        " FROM restaurants ORDER BY slug"
    ):
        slug, name, address, lat, lng, boro, hood = row
        # The listing geocodes three plainly Manhattan restaurants to Oakland
        # and San Angelo. The dashboard has always dropped those; the roster
        # published them, because it took its coordinates straight from the
        # table. Dropped at the seed rather than at export, so the venue also
        # becomes a candidate for a Places lookup that could supply real ones.
        lat, lng = sane_coords(lat, lng)
        v = roster.add(name, "rw", address=address, lat=lat, lng=lng,
                       borough=boro, neighborhood=hood)
        v["rw_slug"] = slug
        # Participating in a season that is running is direct evidence of trading.
        v["status"], v["status_source"] = "open", "restaurant week listing"
        v["resolution"] = "Restaurant Week participant"
        # A venue slug and its RW slug should be the same string wherever the
        # name allows it; when slugify disagrees with the program's own slug,
        # the program's wins, because every cached artefact on disk is keyed by it.
        #
        # Except over a slug this venue already holds. That is the whole of
        # issue 25: the programme's slug is a fact about one season, and letting
        # it overwrite a durable identity is what made a restaurant leaving the
        # programme look like a different restaurant arriving. On the first build
        # the ledger is empty, so every RW venue still takes the programme's slug
        # -- and then keeps it.
        held = v["venue_slug"] in roster.from_ledger
        reserved = roster.ledger.reserved(slug) if roster.ledger else False
        if not held and not reserved and slug != v["venue_slug"] and slug not in roster.venues:
            roster.venues.pop(v["venue_slug"])
            roster.by_norm[norm_name(name)].remove(v["venue_slug"])
            v["venue_slug"] = slug
            roster.venues[slug] = v
            roster.by_norm[norm_name(name)].append(slug)

    seeded = len(roster.venues)

    # --- fold in the award sources, addresses first --------------------------
    order = ("michelin", "nyt", "james_beard")
    stats = {}
    deferred = []      # portfolio awards, resolved once every venue exists
    person_only = []   # awards to a person with no room, dropped with a reason
    for source in order:
        spec = cfg["sources"][source]
        f = RAW / spec["file"]
        if not f.exists():
            print(f"{source}: no raw file, skipped")
            continue
        records = json.loads(f.read_text(encoding="utf-8"))
        merged = created = refused = confirmed = skipped = 0
        for e in records:
            # No fallback to e["name"]. For michelin and nyt that field IS
            # name_field, so the fallback was dead; for James Beard it is the
            # HONOREE, and falling back to it put 66 people on the roster as
            # restaurants -- Anthony Bourdain, Craig Claiborne, Gael Greene --
            # scoring 53-77 and outranking most of the real ones. All 77 Beard
            # records with no `restaurant` are a person: Who's Who inductees,
            # Lifetime Achievement, Humanitarian of the Year, and a few
            # restaurateur and wine awards recorded without their room.
            name = e.get(spec["name_field"])
            if not name:
                skipped += 1
                person_only.append({
                    "source": source, "person": e.get("name"),
                    "award": e.get("award"), "year": e.get("year"),
                    "level": e.get("level"),
                    "reason": f"honours a person, and the record names no "
                              f"{spec['name_field']}"})
                continue
            address = e.get("address")
            rank_pat = spec.get("rank_from_notes")
            award_row = {
                "source": source, "level": e.get("level"), "award": e.get("award"),
                "year": e.get("year"),
                "rank": nyt_rank(e.get("notes"), rank_pat) if rank_pat else None,
                "person": e.get(spec["person_field"]) if spec.get("person_field") else None,
                "source_url": e.get("url"),
            }
            # A name a human has ruled is not a restaurant: drop the record and
            # say so. Never create a venue, and never quietly attach it to some
            # other restaurant instead.
            if norm_name(name) in not_venues:
                ruled_out.append({"source": source, "name": name,
                                  "year": e.get("year"), "level": e.get("level"),
                                  "reason": not_venues[norm_name(name)]})
                continue
            # A name that splits into several is a portfolio award until proved
            # otherwise, and proving it needs the whole roster, so it waits.
            if norm_name(name) in split_into or len(split_group(name)) > 1:
                deferred.append({"name": name, "award": award_row, "source": source})
                continue
            if spec["has_addresses"] and address:
                venue, how, decision = match_with_address(roster, name, address)
            else:
                venue, how, decision = match_name_only(roster, name)

            if decision == "refuse":
                roster.refused.append({
                    "source": source, "name": name, "address": address,
                    "year": e.get("year"), "level": e.get("level"),
                    "reason": how,
                    "candidates": [v["venue_slug"] for v in roster.candidates(name)],
                })
                refused += 1
                continue
            if decision == "create":
                venue = roster.add(
                    name, source, address=address,
                    borough=borough_from(address, e.get("city")),
                )
                venue["resolution"] = f"created from {spec['label']}: {how}"
                created += 1
            else:
                merged += 1
                if decision == "confirm":
                    confirmed += 1
                    roster.confirm.append({
                        "source": source, "name": name,
                        "external_address": address,
                        "merged_into": venue["venue_slug"],
                        "our_address": venue.get("address"),
                        "year": e.get("year"), "level": e.get("level"),
                        "reason": how,
                    })
                if not venue.get("address") and address:
                    venue["address"] = address
                if not venue.get("borough"):
                    venue["borough"] = borough_from(venue.get("address"), e.get("city"))
            award_row.update(venue_slug=venue["venue_slug"], matched_name=name,
                             match_confidence=1.0 if "street number" in how else 0.9,
                             how=how)
            roster.awards.append(award_row)
        stats[source] = dict(records=len(records), merged=merged, created=created,
                             refused=refused, confirmed=confirmed, skipped=skipped)
        if not quiet:
            print(f"{source}: {len(records)} records -> {merged} merged "
                  f"({confirmed} on weaker evidence), {created} new venues, "
                  f"{refused} refused, {skipped} without a venue name")

    # Portfolio awards, now that every source has been folded in.
    attached, kept_whole, unresolved, from_groups = resolve_group_awards(
        roster, deferred, split_into, not_venues)
    for item in kept_whole:
        # Not a portfolio after all -- "Gage & Tollner" is one restaurant. Put
        # it back through the ordinary path.
        venue, how, decision = match_name_only(roster, item["name"])
        if decision == "refuse":
            roster.refused.append({"source": item["source"], "name": item["name"],
                                   "address": None,
                                   "year": item["award"].get("year"),
                                   "level": item["award"].get("level"),
                                   "reason": how, "candidates": []})
            continue
        if decision == "create":
            venue = roster.add(item["name"], item["source"])
            venue["resolution"] = f"created from a single name: {how}"
        a = dict(item["award"])
        a.update(venue_slug=venue["venue_slug"], matched_name=item["name"],
                 match_confidence=0.9, how=how)
        roster.awards.append(a)
    roster.group_unresolved = unresolved
    roster.ruled_out = ruled_out
    roster.person_only = person_only
    if person_only and not quiet:
        print(f"awards to a person with no room: {len(person_only)} records "
              f"across {len({r['person'] for r in person_only})} people, dropped")
    if ruled_out and not quiet:
        print(f"ruled out by config/venue_aliases.json: {len(ruled_out)} records "
              f"across {len({r['name'] for r in ruled_out})} names that are not restaurants")
    if not quiet:
        print(f"\nportfolio awards: {len(deferred)} list-shaped names -> "
              f"{len(attached)} attachments across the restaurants they name, "
              f"{len(kept_whole)} were single names after all, "
              f"{len(set(from_groups))} restaurants vouched into existence by "
              f"the parts around them, {len(unresolved)} parts still unmatched")

    folded = merge_spelling_variants(roster)
    if folded and not quiet:
        print(f"\nfolded {len(folded)} spelling variants:")
        for m in folded:
            print(f"  {m['folded_name']!r} -> {m['kept_name']!r} ({m['kept']})")
    roster.folded = folded

    # --- derive ---------------------------------------------------------------
    # The most recent selection each source has published, taken from the data
    # rather than pinned, so a back-filled Michelin history (issue 3) moves it
    # without a config edit.
    latest_year = {}
    for a in roster.awards:
        if a.get("year"):
            latest_year[a["source"]] = max(latest_year.get(a["source"], 0), a["year"])
    by_venue = {}
    for a in roster.awards:
        by_venue.setdefault(a["venue_slug"], []).append(a)
    for slug, v in roster.venues.items():
        aw = by_venue.get(slug, [])
        years = [a["year"] for a in aw if a.get("year")]
        v["award_count"] = len(aw)
        v["award_sources"] = json.dumps(sorted({a["source"] for a in aw}))
        v["first_award_year"] = min(years) if years else None
        v["last_award_year"] = max(years) if years else None
        (v["prestige"], v["top_honor"], v["top_honor_label"],
         v["top_honor_year"], honor_source) = prestige_for(
            aw, cfg, closed=(v["status"] == "closed"))
        # Whether the badge should carry its year. NOT a claim that the honour
        # lapsed: a Michelin star is a standing selection and does lapse, while
        # a James Beard win is an event and never does. What both share is that
        # a bare "James Beard winner" on a 1993 award reads as news, and 386 of
        # the 782 badges on the roster were doing exactly that.
        v["top_honor_is_latest"] = int(
            v["top_honor_year"] is not None
            and v["top_honor_year"] == latest_year.get(honor_source))

    if ledger is not None:
        for v in roster.venues.values():
            ledger.record(v)
        if not quiet:
            reissued = sorted(roster.from_ledger)
            print(f"\nslug ledger: {len(reissued)} venues kept an identity from an "
                  f"earlier build, {len(ledger.minted)} minted a new one")

    # --- write ----------------------------------------------------------------
    # Dropped, not DELETEd. Both tables are rebuilt in full every run, so the
    # rows were never the problem -- the SCHEMA below is CREATE TABLE IF NOT
    # EXISTS, so against a database that already had these tables a new column
    # was silently never added, and the next INSERT failed with "table venues
    # has no column named ...". Dropping first makes the schema in this file the
    # schema on disk, which is what every reader already assumed.
    con.execute("DROP TABLE IF EXISTS venue_awards")
    con.execute("DROP TABLE IF EXISTS venues")
    con.executescript(SCHEMA)
    cols = ("venue_slug", "name", "address", "lat", "lng", "borough", "neighborhood",
            "rw_slug", "status", "status_source", "place_id", "rating",
            "user_ratings_total", "first_award_year", "last_award_year",
            "award_sources", "award_count", "top_honor", "top_honor_label",
            "top_honor_year", "top_honor_is_latest",
            "prestige", "seeded_from", "resolution")
    con.executemany(
        f"INSERT INTO venues ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [tuple(v.get(c) for c in cols) for v in roster.venues.values()])
    acols = ("venue_slug", "source", "level", "award", "year", "rank", "person",
             "source_url", "matched_name", "match_confidence", "how")
    con.executemany(
        f"INSERT INTO venue_awards ({','.join(acols)}) VALUES ({','.join('?' * len(acols))})",
        [tuple(a.get(c) for c in acols) for a in roster.awards])
    # NOT written here. build() takes whatever connection it is handed --
    # including a temp copy in a test or a changeover simulation -- and a
    # module-level path does not care which. It wrote the committed review file
    # from a mutated database and nobody noticed until the Ci Siamo entry it
    # should have contained had quietly become an empty list. main() writes it;
    # build() only says what it would contain.
    return roster, seeded, stats, review_payload(roster)


def review_payload(roster):
    """Everything a human still has to rule on, as data rather than a file."""
    return {
        "_doc": "refused: no venue was touched and the award was DROPPED -- rule "
                "on these or they stay missing. confirm: the merge WAS made on "
                "weaker-than-usual evidence (same name and postal code, "
                "different street number) -- check that it is really one "
                "restaurant and not two.",
        "refused": roster.refused,
        "confirm": roster.confirm,
        "folded_spelling_variants": getattr(roster, "folded", []),
        "group_award_parts_unmatched": getattr(roster, "group_unresolved", []),
        "ruled_out_by_venue_aliases": getattr(roster, "ruled_out", []),
        "awards_to_a_person_with_no_room": getattr(roster, "person_only", []),
    }


def main():
    quiet = "--quiet" in sys.argv
    cfg = load_awards_config()
    tmp = Path(tempfile.mkdtemp()) / DB.name
    shutil.copyfile(DB, tmp)
    con = sqlite3.connect(tmp)
    ledger = Ledger.load(SLUG_LEDGER)
    roster, seeded, _, review = build(con, cfg, quiet=quiet, ledger=ledger)
    con.commit()
    REVIEW.write_text(json.dumps(review, indent=1, ensure_ascii=False),
                      encoding="utf-8")
    # Written here rather than in build() for the same reason the review file is:
    # build() takes whatever connection it is handed, including a temp copy in a
    # test or a changeover simulation, and must not commit one of those to disk.
    SLUG_LEDGER.write_text(
        json.dumps(ledger.document(), indent=1, ensure_ascii=False), encoding="utf-8")

    awarded = con.execute(
        "SELECT COUNT(*) FROM venues WHERE award_count > 0").fetchone()[0]
    rw = con.execute("SELECT COUNT(*) FROM venues WHERE rw_slug IS NOT NULL").fetchone()[0]
    both = con.execute(
        "SELECT COUNT(*) FROM venues WHERE rw_slug IS NOT NULL AND award_count > 0"
    ).fetchone()[0]
    print(f"\nvenues {len(roster.venues)}  "
          f"(seeded {seeded} from Restaurant Week, "
          f"{len(roster.venues) - seeded} added by award sources)")
    print(f"  with recognition: {awarded}   in Restaurant Week: {rw}   both: {both}")
    print(f"  awards recorded: {len(roster.awards)}   "
          f"refused: {len(roster.refused)}   to confirm: {len(roster.confirm)}"
          f" -> {REVIEW.relative_to(ROOT)}")
    print("\ntop honor distribution:")
    for row in con.execute(
        "SELECT top_honor_label, COUNT(*) FROM venues WHERE top_honor IS NOT NULL"
        " GROUP BY 1 ORDER BY 2 DESC"
    ):
        print(f"  {row[1]:5d}  {row[0]}")
    con.close()
    shutil.copyfile(tmp, DB)


if __name__ == "__main__":
    main()
