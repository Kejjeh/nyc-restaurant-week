"""Export the dashboard payload: docs/data/restaurants.json.

Usage: python src/export_site_data.py [--check] [--quiet]
  --check  build and validate the payload, print the report, write nothing
  --quiet  suppress the per-section summary

ToS (nyctourism.com: personal/noncommercial, no republication). This exporter
emits DERIVED/FACTUAL fields only. It must never write `menus.raw_text`, a
`menu_items` listing, or anything else that reproduces a menu. Menus are LINKED
via the official S3 PDF url. The only menu-derived text allowed out is a short
`menu_item_tags` snippet, re-centred on the matched keyword and capped by
SNIPPET_PAD below, deduplicated so overlapping snippets can't be reassembled
into a contiguous passage. assert_tos_clean() enforces this at the end.

Precedence rule for every field: the restaurant's own printed materials
(config/verified_values.json) beat the listing API. price_sweep is heuristic
triage and may NEVER populate a "verified" field.
"""
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "restaurant_week.sqlite"
TAGS_CONFIG = ROOT / "config" / "dish_tags.json"
VERIFIED = ROOT / "config" / "verified_values.json"
SUPPRESS = ROOT / "config" / "recognition_suppress.json"
JB_RAW = ROOT / "data" / "raw" / "recognition" / "james_beard.json"
OUT = ROOT / "docs" / "data" / "restaurants.json"

SEASON_LABEL = "Summer 2026"
SEASON_YEAR = 2026
BOOK_BY = "2026-08-16"      # the program's headline deadline; drives the badge

# ToS guards. matched_text is stored with 60 chars of context each side (up to
# 143 chars) and 97 of 101 item-snippets are an entire dish + description. That
# is too much menu to publish, so every snippet is RE-CENTRED on the keyword at
# this padding -- never right-truncated, which would keep 60 chars of unrelated
# menu and can cut the keyword in half.
SNIPPET_PAD = 25
MAX_SNIPPETS_PER_TAG = 1    # per restaurant
OVERLAP_RUN = 18            # reject a snippet sharing this many chars with a kept one
MIN_SOURCE_GAP = 60         # unpublished chars required between two snippets in
                            # the SAME menu, so they can't be stitched back into
                            # one contiguous passage
# THE RULE, stated once: a restaurant's published snippets may total at most
# 5% of that menu's extracted text, or 40 characters, whichever is greater.
# 40 chars is a few words around the keyword -- a fragment, not a reproduction.
# When a snippet would breach the budget the padding SHRINKS to fit rather than
# the snippet being dropped, so the tag keeps its evidence.
# The exporter enforces a slightly TIGHTER bar (4.5% / 36 chars) than the rule
# above, so an independent auditor measuring at 5%/40 with its own whitespace
# normalisation always passes with margin rather than tripping on rounding.
COVERAGE_CAP = 0.045
COVERAGE_FLOOR = 36
MIN_PAD = 8

MONTHS = {m: i for i, ms in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
     ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
     ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for m in ms}

WEEK_RE = re.compile(
    r"Week\s+\d+\s*\(\s*[A-Za-z]{3,9}\.?\s*\d{1,2}\s*[-‐-―−]\s*"
    r"(?:(?P<m>[A-Za-z]{3,9})\.?\s*)?(?P<d>\d{1,2})\s*\)", re.I)
FIRST_MONTH_RE = re.compile(r"\(\s*(?P<m>[A-Za-z]{3,9})\.?\s*\d{1,2}", re.I)

RECOG_SOURCE_LABEL = {
    "michelin": "Michelin",
    "james_beard": "James Beard",
    "nyt": "NYT 100",
}
# Michelin's own wording. 'recommended' is The Plate, not a colloquial rec.
MICHELIN_LEVEL_LABEL = {
    "recommended": "The Plate",
    "bib_gourmand": "Bib Gourmand",
    "1 star": "1 star",
    "2 stars": "2 stars",
    "3 stars": "3 stars",
}
NYT_LEVEL_LABEL = {"nyt_100_best": "100 Best", "nyt_starred_review": "starred review"}


# --------------------------------------------------------------------------
# text hygiene
# --------------------------------------------------------------------------

def clean(text):
    """Strip PDF-extraction damage and control characters from published text."""
    if not text:
        return text
    s = unicodedata.normalize("NFC", str(text))
    s = s.replace("�", "'")          # replacement char, almost always an apostrophe
    s = "".join(c for c in s if c == "\n" or unicodedata.category(c)[0] != "C")
    return re.sub(r"\s+", " ", s).strip()


def jload(raw, default=None):
    """json.loads that tolerates SQL NULL and the literal TEXT 'null'."""
    if raw is None:
        return default
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return default if val is None else val


# --------------------------------------------------------------------------
# end dates
# --------------------------------------------------------------------------

def end_date_from_weeks(weeks):
    """Last week label -> ISO end date. 'Week 7 (Sept 1 - Sept 6)' -> 2026-09-06."""
    if not weeks:
        return None
    label = str(weeks[-1])
    m = WEEK_RE.search(label)
    if not m:
        return None
    month_tok = m.group("m")
    if not month_tok:                                   # "Aug 24 - 31"
        fm = FIRST_MONTH_RE.search(label)
        month_tok = fm.group("m") if fm else None
    month = MONTHS.get((month_tok or "").lower())
    if not month:
        return None
    try:
        return date(SEASON_YEAR, month, int(m.group("d"))).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# dish tags
# --------------------------------------------------------------------------

def load_tag_rules():
    """tag -> [compiled regex]. Keys starting with '_' are documentation."""
    cfg = json.loads(TAGS_CONFIG.read_text(encoding="utf-8"))
    return {tag: [re.compile(r["pattern"], re.I) for r in rules]
            for tag, rules in cfg.items() if not tag.startswith("_")}


def recover_keyword(snippet, patterns):
    """The matched keyword is not stored. Re-run the tag's rules and pick the
    match nearest the snippet centre (snippets are built centred on the hit),
    preferring the longest span on a tie."""
    best, centre = None, len(snippet) / 2
    for pat in patterns:
        for m in pat.finditer(snippet):
            if not m.group(0).strip():
                continue
            score = (abs((m.start() + m.end()) / 2 - centre), -(m.end() - m.start()))
            if best is None or score < best[0]:
                best = (score, m.start(), m.end())
    return (snippet[best[1]:best[2]], best[1]) if best else (None, None)


def recentre(snippet, start, length, pad=SNIPPET_PAD):
    """Re-centre on the keyword at `pad` chars each side, snapped to word
    boundaries. Never a right-truncation of the stored 143-char snippet."""
    if start is None:
        return clean(snippet[:pad * 2])[: pad * 2]
    lo, hi = max(0, start - pad), min(len(snippet), start + length + pad)
    if lo > 0:
        sp = snippet.find(" ", lo)
        lo = sp + 1 if 0 <= sp < start else lo
    if hi < len(snippet):
        sp = snippet.rfind(" ", start + length, hi)
        hi = sp if sp > start + length else hi
    out = clean(snippet[lo:hi])
    if lo > 0:
        out = "…" + out
    if hi < len(snippet):
        out = out + "…"
    return out


def _overlaps(a, b, run=OVERLAP_RUN):
    """True if the two snippets share a contiguous run of `run` characters."""
    a2, b2 = re.sub(r"\W+", " ", a.lower()), re.sub(r"\W+", " ", b.lower())
    if len(a2) < run or len(b2) < run:
        return False
    return any(a2[i:i + run] in b2 for i in range(len(a2) - run + 1))


def build_tags(con, rules):
    """slug -> [tag hit]. Snippets are re-centred, capped, overlap-pruned,
    budgeted against the menu's total length, and forced apart in the SOURCE
    text, so the export cannot be reassembled into a contiguous menu passage.

    raw_text is read here only to compute those positions. It is never emitted.
    """
    by_slug, dropped = {}, 0
    raw_by_slug = {s: re.sub(r"\s+", " ", (t or "")).strip()
                   for s, t in con.execute(
                       "SELECT restaurant_slug, raw_text FROM menus")}
    spans = {}      # slug -> [(start, end)] of already-published source spans
    rows = con.execute(
        "SELECT restaurant_slug, tag, confidence, matched_text, source "
        "FROM menu_item_tags ORDER BY restaurant_slug, tag, "
        "CASE source WHEN 'item' THEN 0 ELSE 1 END, length(matched_text)").fetchall()
    for slug, tag, conf, matched, source in rows:
        if tag not in rules or not matched:
            continue
        kept = by_slug.setdefault(slug, [])
        same_tag = [t for t in kept if t["tag"] == tag]
        if len(same_tag) >= MAX_SNIPPETS_PER_TAG:
            dropped += 1
            continue

        kw, at = recover_keyword(matched, rules[tag])
        snip = recentre(matched, at, len(kw) if kw else 0)

        reject = False

        # (a) textual overlap with anything already kept for this restaurant --
        # two different tags can fire on one passage, and publishing both would
        # republish it contiguously.
        if any(_overlaps(snip, t["snippet"]) for t in kept if t["snippet"]):
            reject = True

        # (b) source-position budget: where the snippet sits in the menu -------
        raw = raw_by_slug.get(slug, "")
        used = spans.setdefault(slug, [])
        budget = max(COVERAGE_FLOOR, int(len(raw) * COVERAGE_CAP))
        remaining = budget - sum(e - s for s, e in used)

        if not reject and raw:
            # Shrink the padding until the snippet fits what's left of the budget.
            pad = SNIPPET_PAD
            while pad > MIN_PAD and len(snip.strip("…").strip()) > remaining:
                pad -= 4
                snip = recentre(matched, at, len(kw) if kw else 0, pad=pad)
            core = snip.strip("…").strip()
            if len(core) > remaining:
                reject = True
            else:
                at_src = raw.find(core)
                if at_src >= 0:
                    end_src = at_src + len(core)
                    if any(at_src < e + MIN_SOURCE_GAP and s - MIN_SOURCE_GAP < end_src
                           for s, e in used):
                        reject = True
                    else:
                        used.append((at_src, end_src))
                else:
                    used.append((-1, -1 + len(core)))   # count it against the budget

        if reject:
            dropped += 1
            if same_tag:
                continue        # tag already represented; nothing lost
            snip = None         # keep the tag (so it stays filterable), drop the text

        kept.append({"tag": tag, "confidence": conf, "keyword": clean(kw),
                     "snippet": snip, "source": source})
    return by_slug, dropped


# --------------------------------------------------------------------------
# recognition
# --------------------------------------------------------------------------

def load_suppression():
    if not SUPPRESS.exists():
        return set()
    cfg = json.loads(SUPPRESS.read_text(encoding="utf-8"))
    return {(e["slug"], e["source"], e["matched_name"])
            for e in cfg.get("suppress", []) if e.get("active", True)}


def load_jb_awards():
    """(url, level, year, restaurant) -> [award names]. The award name exists
    ONLY in the raw file; the DB never carries it."""
    if not JB_RAW.exists():
        return {}
    idx = {}
    for rec in json.loads(JB_RAW.read_text(encoding="utf-8")):
        key = (rec.get("url"), rec.get("level"), rec.get("year"),
               (rec.get("restaurant") or "").strip().lower())
        label = rec.get("award")
        if rec.get("name"):
            label = f"{label} — {rec['name']}" if label else rec["name"]
        if label:
            idx.setdefault(key, []).append(label)
    return idx


NYT_RANK_RE = re.compile(r"Ranked No\.\s*(\d+)", re.I)


def build_recognition(con, suppressed, jb_awards):
    """slug -> [badge]. Deduped on (source, level, year) so a restaurant with 21
    rows does not render 'Nominee 1996' three times; the distinct awards behind
    a duplicate are folded into that badge's award list."""
    by_slug, dropped = {}, 0
    rows = con.execute(
        "SELECT restaurant_slug, source, level, year, source_url, match_confidence,"
        " matched_name, notes FROM recognition "
        "ORDER BY restaurant_slug, source, year DESC").fetchall()
    for slug, source, level, year, url, conf, matched, notes in rows:
        if (slug, source, matched) in suppressed:
            dropped += 1
            continue
        badges = by_slug.setdefault(slug, {})
        key = (source, level, year)
        if source == "michelin":
            label = MICHELIN_LEVEL_LABEL.get(level, level)
        elif source == "nyt":
            label = NYT_LEVEL_LABEL.get(level, level)
        else:
            label = (level or "").replace("_", " ")

        badge = badges.get(key)
        if badge is None:
            badge = {
                "source": source,
                "source_label": RECOG_SOURCE_LABEL.get(source, source),
                "level": label,
                "level_raw": level,
                "year": year,
                "url": url,
                "matched_name": clean(matched),
                # JBF publishes no addresses, so every JB row (and every NYT
                # row) is a name-only match. Surfaced as a hint in the UI.
                "name_match_only": bool(conf is not None and conf < 1.0),
                "confidence": conf,
                "awards": [],
            }
            if source == "nyt":
                m = NYT_RANK_RE.search(notes or "")
                if m:
                    badge["rank"] = int(m.group(1))
                # The stored url is a third-party reproduction, not nytimes.com.
                badge["via"] = "secretnyc.co reproduction"
            badges[key] = badge
        for a in jb_awards.get((url, level, year, (matched or "").strip().lower()), []):
            if a not in badge["awards"]:
                badge["awards"].append(a)

    order = {"michelin": 0, "nyt": 1, "james_beard": 2}
    return ({s: sorted(b.values(),
                       key=lambda x: (order.get(x["source"], 9), -(x["year"] or 0)))
             for s, b in by_slug.items()}, dropped)


# --------------------------------------------------------------------------
# price comparables (heuristic only)
# --------------------------------------------------------------------------

def build_price(con):
    """slug -> heuristic estimate, or nothing. Never a 'verified' value.

    `gaps` values are read verbatim: they are rounded independently from
    comparable_3course, so recomputing (comparable - tier) disagrees by $1 on
    44 of 588 values. The chosen tier is always reported alongside the gap --
    the largest gap is always the CHEAPEST tier, which would silently inflate
    the headline if left unlabelled.
    """
    out = {}
    for slug, comp, gaps_raw, conf in con.execute(
            "SELECT restaurant_slug, comparable_3course, gaps, confidence "
            "FROM price_sweep"):
        # confidence low/none renders blank; 'none' means the fetch timed out.
        if conf in ("low", "none") or comp is None:
            continue
        gaps = jload(gaps_raw)
        if not isinstance(gaps, dict) or not gaps:
            continue
        tier, gap = max(gaps.items(), key=lambda kv: (kv[1], -int(kv[0].lstrip("$"))))
        price = int(tier.lstrip("$"))
        out[slug] = {
            "gap_usd": gap,
            "gap_pct": round(gap / comp * 100) if comp else None,
            "tier": tier,
            "rw_price": price,
            "comparable_usd": comp,
            "confidence": conf,
            "all_gaps": gaps,
        }
    return out


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def build_payload():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.text_factory = str

    rules = load_tag_rules()
    tags_by_slug, tags_dropped = build_tags(con, rules)
    recog_by_slug, recog_dropped = build_recognition(
        con, load_suppression(), load_jb_awards())
    price_by_slug = build_price(con)

    verified = json.loads(VERIFIED.read_text(encoding="utf-8"))["restaurants"]
    menus = {r[0]: r[1] for r in con.execute(
        "SELECT restaurant_slug, parse_quality FROM menus")}

    out, stats = [], {"verified": 0, "estimate": 0, "none": 0, "urgent": 0}

    for row in con.execute(
            "SELECT slug, name, borough, neighborhood, address, lat, lng, cuisines,"
            " price_tiers, meal_periods, meal_types_raw, weeks, sunday_participation,"
            " menu_url, website, reservation_link, listing_url"
            " FROM restaurants ORDER BY name"):
        (slug, name, borough, hood, address, lat, lng, cuisines, tiers, periods,
         raw_types, weeks, sunday_api, menu_url, website, res_link, listing) = row

        tiers = jload(tiers, [])
        v = verified.get(slug, {})

        # --- window -----------------------------------------------------
        api_end = end_date_from_weeks(jload(weeks, []))
        if "end_date" in v and v["end_date"]:
            end_date, end_src = v["end_date"], v.get("end_date_source", "printed")
        elif v.get("end_date_source") == "api_fallback":
            end_date, end_src = api_end, "api_fallback"
        else:
            end_date, end_src = api_end, "api"

        # --- sunday -----------------------------------------------------
        if v.get("sunday_verified") is not None:
            sunday, sunday_src = v["sunday_verified"], "verified"
        else:
            sunday, sunday_src = bool(sunday_api), "api"

        # --- gap: verified beats heuristic, heuristic is always labelled --
        est = price_by_slug.get(slug)
        if v.get("gap_usd") is not None:
            gap = {
                "gap_usd": v["gap_usd"], "gap_usd_high": v.get("gap_usd_high"),
                "gap_pct": v.get("gap_pct"), "gap_pct_high": v.get("gap_pct_high"),
                "gap_basis": "verified", "comparable_usd": v.get("comparable_usd"),
                "comparable_usd_high": v.get("comparable_usd_high"),
                "rw_price": v.get("rw_price"), "price_source": "verified",
            }
            stats["verified"] += 1
        elif est and not v:
            gap = {
                "gap_usd": est["gap_usd"], "gap_usd_high": None,
                "gap_pct": est["gap_pct"], "gap_pct_high": None,
                "gap_basis": "estimate", "comparable_usd": est["comparable_usd"],
                "comparable_usd_high": None,
                "rw_price": est["rw_price"], "price_source": "listing",
                "estimate_tier": est["tier"], "estimate_confidence": est["confidence"],
            }
            stats["estimate"] += 1
        else:
            # A verified entry without a numeric gap (e.g. Yingtao) stays blank
            # rather than falling back to a heuristic figure.
            gap = {
                "gap_usd": None, "gap_usd_high": None, "gap_pct": None,
                "gap_pct_high": None, "gap_basis": None,
                "comparable_usd": None, "comparable_usd_high": None,
                "rw_price": v.get("rw_price"),
                "price_source": "verified" if v.get("rw_price") else None,
            }
            stats["none"] += 1

        if gap["rw_price"] is None and tiers:
            gap["rw_price"] = min(int(t.lstrip("$")) for t in tiers)
            gap["price_source"] = gap["price_source"] or "listing"

        # --- verdict (categorical, derived from provenance) -------------
        if "dropped_in_verification" in v.get("flags", []):
            verdict = "Dropped in verification"
        elif v.get("rank"):
            verdict = "Ranked pick"
        elif v:
            verdict = "Verified note"
        elif gap["gap_basis"] == "estimate":
            verdict = "Estimate only"
        else:
            verdict = "No comparable"

        # menu_url is the EMPTY STRING (never NULL) when no menu is published.
        quality = menus.get(slug)
        menu_state = ("none" if not (menu_url or "").strip()
                      else "image_only" if quality == "failed" else "pdf")

        if end_date and end_date <= BOOK_BY:
            stats["urgent"] += 1

        out.append({
            "slug": slug,
            "name": clean(name),
            "borough": borough,
            "neighborhood": hood,
            "address": clean(address),
            "lat": lat, "lng": lng,
            "cuisines": jload(cuisines, []),
            "price_tiers": tiers,
            "meal_periods": jload(periods, []),
            "meal_types_raw": jload(raw_types, []),
            "end_date": end_date,
            "end_date_source": end_src,
            "end_date_api": api_end,
            "days": v.get("days"),
            "sunday": sunday,
            "sunday_source": sunday_src,
            "sunday_api": bool(sunday_api),
            "courses": v.get("courses"),
            "rank": v.get("rank"),
            "grade": v.get("grade"),
            "verdict": verdict,
            "verdict_note": v.get("verdict"),
            "notes": v.get("notes"),
            "flags": v.get("flags", []),
            "menu_state": menu_state,
            "recognition": recog_by_slug.get(slug, []),
            "tags": tags_by_slug.get(slug, []),
            "links": {
                "listing": listing or None,
                "menu": (menu_url or "").strip() or None,
                "reservation": v.get("booking_url") or res_link or None,
                "website": (website or "").strip() or None,
            },
            **gap,
        })

    con.close()
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "season_label": SEASON_LABEL,
        "snapshot_date": con_snapshot(),
        "verified_asof": json.loads(VERIFIED.read_text(encoding="utf-8"))
            ["_doc"]["provenance"].split()[1],
        "book_by": BOOK_BY,
        "tag_vocabulary": sorted(rules),
        "restaurants": out,
    }
    return payload, stats, tags_dropped, recog_dropped


def con_snapshot():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    row = con.execute("SELECT DISTINCT snapshot_date FROM restaurants "
                      "ORDER BY snapshot_date DESC LIMIT 1").fetchone()
    con.close()
    return row[0] if row else None


# --------------------------------------------------------------------------
# ToS guard
# --------------------------------------------------------------------------

BANNED_KEYS = {"raw_text", "menu_items", "dish", "description", "summary",
               "matched_text", "supplement_price", "course"}


def assert_tos_clean(payload):
    """Fail loudly rather than publish anything the ToS forbids."""
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, val in node.items():
                if k in BANNED_KEYS:
                    problems.append(f"banned key {path}.{k}")
                walk(val, f"{path}.{k}")
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{path}[{i}]")

    walk(payload, "$")

    for r in payload["restaurants"]:
        seen = []
        for t in r["tags"]:
            snip = t.get("snippet")
            if not snip:
                continue
            if len(snip) > SNIPPET_PAD * 2 + 60:
                problems.append(f"{r['slug']}: snippet too long ({len(snip)})")
            if any(_overlaps(snip, s) for s in seen):
                problems.append(f"{r['slug']}: overlapping snippets survived")
            seen.append(snip)
    if problems:
        raise SystemExit("ToS CHECK FAILED:\n  " + "\n  ".join(problems[:20]))
    return True


def main():
    check = "--check" in sys.argv
    quiet = "--quiet" in sys.argv

    payload, stats, tags_dropped, recog_dropped = build_payload()
    assert_tos_clean(payload)

    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # `generated_at` is a wall-clock stamp, so a naive write would make the
    # payload differ on every run and the weekly Actions job would commit churn
    # forever. Rewrite only when something OTHER than the stamp changed.
    unchanged = False
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            unchanged = ({k: v for k, v in old.items() if k != "generated_at"}
                         == {k: v for k, v in payload.items() if k != "generated_at"})
        except (ValueError, OSError):
            unchanged = False

    if not check and not unchanged:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        # build in a temp dir then copy (some mounted filesystems break unlink)
        tmp = Path(tempfile.mkdtemp()) / OUT.name
        tmp.write_text(text, encoding="utf-8")
        shutil.copyfile(tmp, OUT)

    if not quiet:
        n = len(payload["restaurants"])
        ranked = sum(1 for r in payload["restaurants"] if r["rank"])
        tagged = sum(1 for r in payload["restaurants"] if r["tags"])
        badged = sum(1 for r in payload["restaurants"] if r["recognition"])
        print(f"restaurants   {n}")
        print(f"  verified gap  {stats['verified']}")
        print(f"  estimate gap  {stats['estimate']}")
        print(f"  no comparable {stats['none']}")
        print(f"  ranked picks  {ranked}")
        print(f"  ending by {payload['book_by']}  {stats['urgent']}")
        print(f"tags          {tagged} restaurants ({tags_dropped} snippets pruned)")
        print(f"recognition   {badged} restaurants ({recog_dropped} rows suppressed)")
        where = ("  (not written: --check)" if check
                 else "  (unchanged, not rewritten)" if unchanged
                 else "  -> " + str(OUT.relative_to(ROOT)))
        print(f"payload       {len(text.encode('utf-8')):,} bytes{where}")


if __name__ == "__main__":
    main()
