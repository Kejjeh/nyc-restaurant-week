"""Diff the two most recent listing snapshots (+ menu hashes, + the roster)."""
import json
import subprocess
import sys

from config import LISTING_DIR, MENUS_DIR

ROOT = LISTING_DIR.parents[2]
VENUES = ROOT / "docs" / "data" / "venues.json"
REVIEWS = (("recognition", ROOT / "data" / "processed" / "recognition_review.json"),
           ("roster merges", ROOT / "data" / "processed" / "venue_merge_review.json"))
LIST_CAP = 25   # rows printed per section before the report says how many it hid


def index(snap):
    return {it["slug"]: it for it in snap["items"]}


def season_boundary(old, new):
    """A roster replaced in both directions is a changeover, not a diff: every
    slug would read DROPPED/added and every shortlist alert would be a lie."""
    return len(old - new) > len(old) / 2 and len(new - old) > len(new) / 2


def previous_payload(path=VENUES):
    """Last week's roster, read from git rather than from a sidecar file.

    The menu-hash section above keeps its own history file and pays for it with
    a documented wart: the report writes state, so running it twice always shows
    zero changes the second time. There is no need to repeat that here. The
    previous payload is already stored, versioned and immutable in HEAD, because
    the weekly workflow commits it -- and `export_venues.py` declines to rewrite
    the file when nothing but the clock moved, so HEAD is the last roster that
    actually differed, not merely the last run.

    Returns None when there is nothing to compare against: a first run, a
    shallow checkout, or a tree with no commits. That is not an error.
    """
    try:
        raw = subprocess.run(
            ["git", "show", f"HEAD:{path.relative_to(ROOT).as_posix()}"],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=True).stdout
        return json.loads(raw)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def count_rulings(doc):
    """-> {section: n} for a review file, whatever shape it happens to be.

    The two files disagree: recognition_review.json is {source: [...]} and
    venue_merge_review.json is {section: [...]} with a _doc string mixed in.
    Counting is the same question either way, so it is asked once here.
    """
    if not isinstance(doc, dict):
        return {"items": len(doc or [])}
    return {k: len(v) for k, v in doc.items() if isinstance(v, list) and v}


def pending_rulings():
    """Say out loud that a human owes the pipeline some answers.

    Both review files have been accumulating decisions nobody was ever told
    about. A refused merge is not a curiosity -- it is an award that is NOT on
    the roster until someone rules on it, and a file nothing points at is a file
    nobody opens. The README carried a hand-written "14 pending" that was
    already drifting.
    """
    lines = []
    for label, path in REVIEWS:
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            lines.append(f"  {label}: {path.name} is unreadable")
            continue
        counts = count_rulings(doc)
        # Folds and rulings already applied are recorded for auditing, not
        # waiting on anyone. Saying "18 waiting" when 8 are already settled
        # trains people to ignore the number.
        settled = {"folded_spelling_variants", "ruled_out_by_venue_aliases", "_doc"}
        waiting = {k: n for k, n in counts.items() if k not in settled}
        total = sum(waiting.values())
        if not total:
            lines.append(f"  {label}: nothing waiting")
            continue
        detail = ", ".join(f"{k} {n}" for k, n in sorted(waiting.items()))
        lines.append(f"  {label}: {total} waiting ({detail}) -> {path.name}")
    return lines


def roster_changes(now, was):
    """-> what moved between two roster payloads. Pure; no files, no printing.

    Split out from the reporting so it can be tested against two hand-built
    payloads. The listing diff above cannot be, and the bug in its shortlist
    path -- a wrong `parents[]` index that silently skipped the entire SHORTLIST
    ALERTS block on every run for weeks -- is exactly what that costs.
    """
    a = {v["slug"]: v for v in now.get("venues", [])}
    b = {v["slug"]: v for v in was.get("venues", [])}
    both = sorted(set(a) & set(b))
    counts = {}
    for key in ("unverified", "mappable"):
        before, after = was.get("counts", {}).get(key), now.get("counts", {}).get(key)
        if before is not None and after is not None and before != after:
            counts[key] = (before, after)
    return {
        "added": sorted(set(a) - set(b)),
        "removed": sorted(set(b) - set(a)),
        # Closures first: the only line here that changes what someone should
        # do tonight.
        "closed": [s for s in both
                   if a[s]["status"] == "closed" and b[s]["status"] != "closed"],
        "reopened": [s for s in both
                     if b[s]["status"] == "closed" and a[s]["status"] == "open"],
        "gained": [s for s in both if a[s]["award_count"] > b[s]["award_count"]],
        "lost": [s for s in both if a[s]["award_count"] < b[s]["award_count"]],
        "counts": counts,
        "now": a, "was": b,
    }


def roster_diff():
    """What changed about the restaurants themselves, not about the programme.

    The listing diff above answers "who is in Restaurant Week this week". Now
    that the roster is the spine, the weekly questions are different ones: who
    got recognised, who closed, and who arrived. A closure in particular is the
    most booking-relevant fact this repo holds, and it would otherwise reach
    nobody -- it is not a listing change, so nothing above would print it.
    """
    if not VENUES.exists():
        return
    now = json.loads(VENUES.read_text(encoding="utf-8"))
    was = previous_payload()
    print("#" * 60)
    print("## ROSTER")
    if was is None:
        c = now["counts"]
        print(f"  no previous payload in HEAD to compare against — "
              f"{c['venues']} venues, {c['with_recognition']} recognised")
        for line in pending_rulings():
            print(line)
        print("#" * 60)
        return

    d = roster_changes(now, was)
    a, b = d["now"], d["was"]

    if d["closed"]:
        print(f"  CLOSED since last week ({len(d['closed'])}):")
        for s in d["closed"]:
            print(f"    x {a[s]['name']} ({s}) — {a[s].get('status_source')}")
    if d["reopened"]:
        print(f"  reopened ({len(d['reopened'])}):")
        for s in d["reopened"]:
            print(f"    o {a[s]['name']} ({s})")
    if d["gained"]:
        print(f"  gained recognition ({len(d['gained'])}):")
        for s in d["gained"]:
            n = a[s]["award_count"] - b[s]["award_count"]
            honour = ("" if a[s]["top_honor_label"] == b[s]["top_honor_label"]
                      else f", now {a[s]['top_honor_label']}")
            print(f"    + {a[s]['name']} ({s}) +{n} record"
                  f"{'' if n == 1 else 's'}{honour}")
    if d["lost"]:
        print(f"  lost award records ({len(d['lost'])}) — usually a merge rule"
              f" change, worth a look:")
        for s in d["lost"]:
            print(f"    - {a[s]['name']} ({s}) "
                  f"{b[s]['award_count']} -> {a[s]['award_count']}")
    for key, rows, sign, src in (("added", d["added"], "+", a),
                                 ("removed", d["removed"], "-", b)):
        if not rows:
            continue
        label = "new venues" if key == "added" else "venues gone"
        print(f"  {label} ({len(rows)}):")
        for s in rows[:LIST_CAP]:
            v = src[s]
            extra = (f" {v['top_honor_label'] or 'no honour'} · from {v.get('seeded_from')}"
                     if key == "added" else "")
            print(f"    {sign} {v['name']} ({s}){extra}")
        # Never let a cap read as "that was all of them".
        if len(rows) > LIST_CAP:
            print(f"    … and {len(rows) - LIST_CAP} more not listed")

    # Resolution progress, so 769 unverified rows do not quietly stay 769.
    for key, (before, after) in d["counts"].items():
        print(f"  {key}: {before} -> {after}")

    if not any(d[k] for k in ("closed", "reopened", "gained", "lost",
                              "added", "removed")):
        print("  no change to the roster")
    rulings = pending_rulings()
    if rulings:
        print("  --")
        for line in rulings:
            print(line)
    print("#" * 60)


def main():
    snaps = sorted(LISTING_DIR.glob("snapshot-*.json"))
    if len(snaps) < 2:
        print("Only one snapshot exists; nothing to diff yet.")
        roster_diff()
        return 0
    old_p, new_p = snaps[-2], snaps[-1]
    old, new = index(json.loads(old_p.read_text())), index(json.loads(new_p.read_text()))
    added = sorted(set(new) - set(old))
    dropped = sorted(set(old) - set(new))
    if season_boundary(set(old), set(new)):
        print(f"season boundary — diff suppressed "
              f"(roster replaced: {len(dropped)} dropped, {len(added)} added)")
        # The roster is NOT season-scoped, so a changeover is exactly when its
        # diff is most worth reading: the listing churns completely and the
        # award side should barely move.
        roster_diff()
        return 0
    # shortlist call-out first: any change touching config/shortlist.json slugs
    # parents: [0]=data/raw, [1]=data, [2]=repo root. parents[1] resolved to
    # data/config/shortlist.json, which never exists, so the whole SHORTLIST
    # ALERTS block was silently skipped on every run.
    sl_file = LISTING_DIR.parents[2] / "config" / "shortlist.json"
    if not sl_file.exists():
        print(f"WARNING: shortlist not found at {sl_file} — no shortlist alerts")
    if sl_file.exists():
        sl = set(json.loads(sl_file.read_text())["slugs"])
        alerts = []
        for s_ in sorted(sl):
            if s_ in old and s_ not in new:
                alerts.append(f"DROPPED from program: {s_}")
            elif s_ in old and s_ in new:
                for field in ("mealTypes", "restaurantInclusionWeek", "menuFileUrl"):
                    if old[s_].get(field) != new[s_].get(field):
                        alerts.append(
                            f"{s_} {field}: {old[s_].get(field)} -> {new[s_].get(field)}")
        print("#" * 60)
        print("## SHORTLIST ALERTS (booking-relevant changes)")
        if alerts:
            for a in alerts:
                print("  !!", a)
        else:
            print("  none — shortlist unchanged")
        print("#" * 60)
    print(f"# Diff: {old_p.name} -> {new_p.name}")
    print(f"added ({len(added)}):")
    for s in added:
        print(f"  + {new[s]['shortTitle']} ({s})")
    print(f"dropped ({len(dropped)}):")
    for s in dropped:
        print(f"  - {old[s]['shortTitle']} ({s})")
    print("changed:")
    n = 0
    for s in sorted(set(new) & set(old)):
        for field in ("mealTypes", "restaurantInclusionWeek", "menuFileUrl", "website"):
            a, b = old[s].get(field), new[s].get(field)
            if a != b:
                print(f"  ~ {new[s]['shortTitle']} ({s}) {field}: {a} -> {b}")
                n += 1
    print(f"({n} field changes)")
    # menu content changes via manifest hashes
    hist = MENUS_DIR / "manifest_history.json"
    manifest_p = MENUS_DIR / "manifest.json"
    if manifest_p.exists():
        cur = json.loads(manifest_p.read_text())
        prev = json.loads(hist.read_text()) if hist.exists() else {}
        changed = [
            s for s in cur
            if s in prev and prev[s].get("sha256") and cur[s].get("sha256")
            and prev[s]["sha256"] != cur[s]["sha256"]
        ]
        print(f"menu PDFs changed content ({len(changed)}):")
        for s in changed:
            print(f"  ~ {s}")
        # quirk: a read-only report writes state, so a second run always shows 0 changed
        hist.write_text(json.dumps(cur, indent=1))
    roster_diff()
    return 0


if __name__ == "__main__":
    sys.exit(main())
