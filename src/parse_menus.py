"""Parse downloaded menu PDFs into structured courses/dishes/supplements.

PDF formats vary wildly, so parses are graded rather than forced:
  full    - >=2 course sections detected, >=4 dishes total
  partial - text extracted but course structure unclear (raw text still stored)
  failed  - no usable text (image-only scan or corrupt file)
Raw extracted text is always kept alongside the structured rows.
Output: data/raw/menus/parsed.json
"""
import json
import re

from config import MENUS_DIR


def atomic_write(path, text):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def prune(parsed, manifest_slugs):
    """Progress is a skip list, so a slug the downloader dropped has to leave it —
    otherwise last season's parse is kept forever."""
    return {s: v for s, v in parsed.items() if s in manifest_slugs}


COURSE_PAT = re.compile(
    r"^\s*(first course|second course|third course|starters?|appetizers?|"
    r"to start|entr[ée]es?|mains?|main course|desserts?|dolci|postres|"
    r"antipasti|primi|secondi|salads?|small plates|snacks|sides|"
    r"lunch|dinner|brunch|supplements?|add[- ]?ons?)\s*:?\s*$",
    re.I,
)
PRICE_PAT = re.compile(r"\$\s?(\d{1,3}(?:\.\d{2})?)")
SUPP_PAT = re.compile(r"(\+\s*\$\s?\d+|supplement|sup\.)", re.I)
NOISE_PAT = re.compile(
    r"(restaurant week|prix[- ]fixe|per person|not includ|gratuit|beverage|tax(es)?\b|"
    r"choice of|choose one|www\.|https?://|@|follow us|\d{3}[-.]\d{3}[-.]\d{4})",
    re.I,
)


def extract_text(path):
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return ""


def parse_structure(text):
    courses, current, items = [], None, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 90:
            continue
        if COURSE_PAT.match(line):
            current = line.rstrip(":").title()
            courses.append(current)
            continue
        if NOISE_PAT.search(line):
            continue
        if current is None:
            continue
        # dish line: not all-caps boilerplate, has letters
        if not re.search(r"[a-zA-Z]{3}", line):
            continue
        supp = None
        m = SUPP_PAT.search(line)
        if m:
            pm = PRICE_PAT.search(line)
            supp = float(pm.group(1)) if pm else None
        # split "Dish — description" / "Dish: description"
        parts = re.split(r"\s+[–—-]\s+|:\s+", line, maxsplit=1)
        dish = parts[0].strip()
        desc = parts[1].strip() if len(parts) > 1 else None
        if len(dish) < 2:
            continue
        items.append(
            {"course": current, "dish": dish, "description": desc,
             "supplement_price": supp}
        )
    return courses, items


def grade(text, courses, items):
    if len(text.strip()) < 40:
        return "failed"
    if len(courses) >= 2 and len(items) >= 4:
        return "full"
    return "partial"


def main():
    manifest = json.loads((MENUS_DIR / "manifest.json").read_text())
    progress = MENUS_DIR / "parsed-progress.json"
    out = prune(load_json(progress, {}), set(manifest))
    n = 0
    for slug, meta in manifest.items():
        if slug in out:
            continue
        if "file" not in meta:
            out[slug] = {"parse_quality": "failed", "error": meta.get("error")}
            continue
        path = MENUS_DIR / meta["file"]
        if not path.exists():
            out[slug] = {"parse_quality": "failed", "error": "file missing"}
            continue
        text = extract_text(path)
        courses, items = parse_structure(text)
        out[slug] = {
            "parse_quality": grade(text, courses, items),
            "courses": courses,
            "items": items,
            "raw_text": text,
        }
        n += 1
        if n % 25 == 0:
            atomic_write(progress, json.dumps(out))
            print(f"  parsed {n} this run ({len(out)} total)", flush=True)
    atomic_write(progress, json.dumps(out))
    atomic_write(MENUS_DIR / "parsed.json", json.dumps(out, indent=1))
    dist = {}
    for v in out.values():
        dist[v["parse_quality"]] = dist.get(v["parse_quality"], 0) + 1
    print("parse_quality distribution:", dist)


if __name__ == "__main__":
    main()
