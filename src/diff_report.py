"""Diff the two most recent listing snapshots (+ menu hash changes)."""
import json
import sys

from config import LISTING_DIR, MENUS_DIR


def index(snap):
    return {it["slug"]: it for it in snap["items"]}


def season_boundary(old, new):
    """A roster replaced in both directions is a changeover, not a diff: every
    slug would read DROPPED/added and every shortlist alert would be a lie."""
    return len(old - new) > len(old) / 2 and len(new - old) > len(new) / 2


def main():
    snaps = sorted(LISTING_DIR.glob("snapshot-*.json"))
    if len(snaps) < 2:
        print("Only one snapshot exists; nothing to diff yet.")
        return 0
    old_p, new_p = snaps[-2], snaps[-1]
    old, new = index(json.loads(old_p.read_text())), index(json.loads(new_p.read_text()))
    added = sorted(set(new) - set(old))
    dropped = sorted(set(old) - set(new))
    if season_boundary(set(old), set(new)):
        print(f"season boundary — diff suppressed "
              f"(roster replaced: {len(dropped)} dropped, {len(added)} added)")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
