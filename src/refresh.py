"""Weekly refresh: re-pull listing, fetch new details/menus, rebuild DB, diff.

Usage: python src/refresh.py [--force-menus]
  --force-menus  re-download every PDF (detects in-place menu content changes;
                 otherwise only new/missing PDFs are fetched)
"""
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent


def run(script, *args):
    print(f"\n=== {script} {' '.join(args)} ===", flush=True)
    subprocess.run([sys.executable, str(SRC / script), *args], check=True)


def main():
    force = "--force-menus" in sys.argv
    if force:
        menus = SRC.parent / "data" / "raw" / "menus"
        for f in menus.glob("*.pdf"):
            f.unlink()
        # parse_menus.py skips any slug already in parsed-progress.json, so
        # without clearing it the re-downloaded PDFs are never re-parsed and
        # in-place menu edits never reach the DB or the site -- which is the
        # entire reason --force-menus exists.
        progress = menus / "parsed-progress.json"
        if progress.exists():
            progress.unlink()
            print("cleared parsed-progress.json (forcing a full re-parse)")
    run("fetch_listing.py")
    run("fetch_details.py")
    run("download_menus.py")
    run("parse_menus.py")
    run("build_db.py")
    run("tag_dishes.py")          # build_db recreates the DB, so re-tag
    run("enrich_recognition.py")  # re-match from cached raw recognition files
    # Outdoor licences are issued and expire continuously, so unlike the subway
    # stations this is re-pulled -- one request a week against NYC Open Data.
    run("fetch_outdoor_dining.py", "--refresh")
    # Ratings drift slowly and the cache is keyed per slug, so this only calls
    # the API for restaurants added since last week -- usually zero. Skipped
    # entirely without a key, which is the case in CI: the key is gitignored
    # and must never be committed to a public repo.
    if os.environ.get("GOOGLE_PLACES_KEY"):
        run("fetch_google_ratings.py")
    else:
        print("fetch_google_ratings.py skipped: no GOOGLE_PLACES_KEY in env")
    run("export_site_data.py")    # docs/ payload; must follow tag+recognition
    # The my-list payload shrinks its Google scores toward the ROSTER mean, so
    # it has to follow both the ratings fetch and the exporter it borrows from.
    run("export_places.py")
    run("diff_report.py")


if __name__ == "__main__":
    main()
