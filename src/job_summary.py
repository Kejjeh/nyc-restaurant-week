"""Turn a refresh log into the weekly GitHub job summary.

Usage: python src/job_summary.py [refresh.log] >> "$GITHUB_STEP_SUMMARY"

This lived inline in .github/workflows/refresh.yml, where it could not be run
or tested without pushing a commit and waiting for Monday. That is the same
condition that let a wrong `parents[]` index silently skip the entire SHORTLIST
ALERTS block on every run for weeks -- the report printed everything else and
nobody could tell. So it is a module now, and tests/test_job_summary.py feeds it
real log text.

What leads the summary is the whole point of the file. The log is tens of
thousands of characters and nobody reads it; the summary is what arrives in the
notification. So the two things that change what somebody should DO -- a
shortlist restaurant's booking details moving, and a restaurant closing -- go at
the top, and everything else goes in a fold.
"""
import pathlib
import re
import sys

SECTION_RE = re.compile(r"^=== (\S+?)\.py.*===$")
FENCE = "#" * 10
MAX_DIFF = 50000


def split_sections(text):
    """refresh.py prints '=== <script>.py <args> ===' before each step."""
    sections, cur = {}, "start"
    for line in (text or "").splitlines():
        m = SECTION_RE.match(line)
        if m:
            cur = m.group(1)
            sections[cur] = []
        else:
            sections.setdefault(cur, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def block(diff, header):
    """The lines of one ##-headed block in the diff, without its fences."""
    out, grab = [], False
    for line in (diff or "").splitlines():
        if header in line:
            grab = True
            continue
        if grab:
            if line.startswith(FENCE):
                break
            if line.strip():
                out.append(line.rstrip())
    return out


def closures(roster_lines):
    """Restaurants that closed, pulled out of the roster block.

    The single most booking-relevant fact this repo holds, and the one thing in
    the whole report that is not recoverable from anywhere else -- a closure is
    not a listing change, so nothing else in the summary would ever mention it.
    """
    out, grab = [], False
    for line in roster_lines:
        if line.strip().startswith("CLOSED since last week"):
            grab = True
            continue
        if grab:
            if not line.startswith("    "):
                break
            out.append(line.strip().lstrip("x ").strip())
    return out


def drop_closure_block(roster_lines):
    """The roster block with the CLOSED section removed, having promoted it."""
    out, skipping = [], False
    for line in roster_lines:
        if line.strip().startswith("CLOSED since last week"):
            skipping = True
            continue
        if skipping:
            if line.startswith("    "):
                continue
            skipping = False
        if line.strip():
            out.append(line)
    return out


def render(text):
    sections = split_sections(text)
    diff = sections.get("diff_report", "")
    export = sections.get("export_site_data", "")
    venues = sections.get("export_venues", "")
    roster = block(diff, "## ROSTER")
    alerts = [a.strip() for a in block(diff, "SHORTLIST ALERTS")]

    lines = ["# NYC Restaurant Week — weekly refresh", ""]

    # 1. Closures. Nothing else in this report can tell you a restaurant shut.
    shut = closures(roster)
    if shut:
        lines += ["## 🔴 Closed since last week", ""]
        lines += [f"- **{s}**" for s in shut] + [""]

    # 2. Shortlist changes.
    changed = [a for a in alerts if a.startswith("!!")]
    if changed:
        lines += ["## ⚠️ Shortlist changes — booking-relevant", ""]
        lines += [f"- **{a.lstrip('! ').strip()}**" for a in changed] + [""]
    else:
        lines += ["## Shortlist: unchanged", ""]

    # 3. The rest of the roster block. The closures were promoted above, so
    # they are dropped here rather than printed a second time -- a summary that
    # says the same thing twice teaches people to skim it.
    rest = drop_closure_block(roster) if shut else [r for r in roster if r.strip()]
    if rest:
        lines += ["## Roster", "", "```", *rest, "```", ""]

    for title, body in (("Roster payload", venues), ("Dashboard payload", export)):
        if body:
            lines += [f"## {title}", "", "```", body, "```", ""]
    if diff:
        lines += ["<details><summary>Full program diff</summary>", "",
                  "```", diff[:MAX_DIFF], "```", "", "</details>", ""]
    return "\n".join(lines)


def main():
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "refresh.log")
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    print(render(text))


if __name__ == "__main__":
    main()
