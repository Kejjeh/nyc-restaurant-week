"""The README states an invariant. This is the test that makes it one.

    "Season facts live in exactly one file, config/season.json. Nothing else in
     the repo hard-codes a season, a year or a deadline."

It has been broken twice. `config/awards.json` carried `reference_year: 2026`,
so the first changeover would have decayed every venue's standing against a
stale year with nothing failing to say so. And `docs/app.js` carried
`DATA.program_end || '2026-09-06'` in two places -- in a later season that
countdown would have run to a date already in the past, and the planner's date
loop would have started after its own end and silently offered no dates at all.

Neither would have raised. Both were found by reading, which is not a strategy.

Scoped to QUOTED occurrences in shipped code: a date inside a string literal is
a value the program uses, while the same digits in a comment or a docstring
example are prose about the value. Test fixtures are exempt -- pinning a real
season is exactly what they are for.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEASON = json.loads((ROOT / "config" / "season.json").read_text(encoding="utf-8"))

# Shipped code only. `tests/` pins real dates on purpose, and the generated
# payloads under docs/data/ are meant to carry the season they describe.
SHIPPED = [p for d in ("src", "docs") for p in (ROOT / d).rglob("*")
           if p.suffix in (".py", ".js", ".html")
           and "__pycache__" not in p.parts and "data" not in p.parts]


def quoted(value):
    """Occurrences of `value` inside a string literal, single or double quoted."""
    v = re.escape(str(value))
    return re.compile(rf"""['"]{v}['"]""")


# The one deliberate hardcode, named rather than excused by a looser rule.
#
# LEGACY_SEASON is the season the un-namespaced localStorage keys belonged to,
# back when there was only one. It must NOT follow season.json: if it did, next
# season's code would migrate keys under a code that never existed and every
# saved list from srw26 would be orphaned. It names a moment in the past, which
# is exactly what a season fact must never do -- and exactly why this line is
# right and the rule still holds everywhere else.
ALLOWED = {"docs/app.js": ["LEGACY_SEASON"]}


def allowed(path, line):
    return any(marker in line for marker in ALLOWED.get(path, ()))


@pytest.mark.parametrize("key", ["start", "book_by", "end", "code"])
def test_no_shipped_file_quotes_a_season_fact(key):
    pattern = quoted(SEASON[key])
    hits = []
    for path in SHIPPED:
        rel = path.relative_to(ROOT).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace")
                                 .splitlines(), 1):
            if pattern.search(line) and not allowed(rel, line):
                hits.append(f"{rel}:{i}: {line.strip()[:90]}")
    assert not hits, (
        f"season {key}={SEASON[key]!r} is hard-coded outside config/season.json:\n  "
        + "\n  ".join(hits))


def test_the_allowlist_still_describes_something_real():
    """An allowlist entry that matches nothing is a rule quietly switched off."""
    for rel, markers in ALLOWED.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in text, f"{rel} no longer contains {marker!r}; drop the exemption"


def test_the_guard_would_actually_catch_one():
    """A test that cannot fail is decoration."""
    pattern = quoted(SEASON["end"])
    assert pattern.search(f"""const end = DATA.program_end || '{SEASON["end"]}';""")
    assert pattern.search(f'''x = "{SEASON["end"]}"''')
    # prose about the value is not the value
    assert not pattern.search(f"# falling back to {SEASON['end']} meant ...")


def test_the_shipped_set_is_not_accidentally_empty():
    """If the glob stops matching, every assertion above passes vacuously."""
    names = {p.name for p in SHIPPED}
    assert {"app.js", "venues.js", "config.py", "export_site_data.py"} <= names
    assert not any("tests" in p.parts for p in SHIPPED)


def test_the_season_year_is_not_quoted_in_shipped_code():
    """awards.json is data rather than code, but the same rule applies to it."""
    awards = (ROOT / "config" / "awards.json").read_text(encoding="utf-8")
    cfg = json.loads(awards)
    assert cfg["recency"]["reference_year"] is None, (
        "awards.json pins a year again; it must resolve from season.json")
