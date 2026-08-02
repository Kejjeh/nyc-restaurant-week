"""Weekly refresh: re-pull listing, fetch new details/menus, rebuild DB, diff.

Usage: python src/refresh.py [--force-menus]
  --force-menus  re-download every PDF (detects in-place menu content changes;
                 otherwise only new/missing PDFs are fetched)
"""
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
        for f in (SRC.parent / "data" / "raw" / "menus").glob("*.pdf"):
            f.unlink()
    run("fetch_listing.py")
    run("fetch_details.py")
    run("download_menus.py")
    run("parse_menus.py")
    run("build_db.py")
    run("tag_dishes.py")          # build_db recreates the DB, so re-tag
    run("enrich_recognition.py")  # re-match from cached raw recognition files
    run("export_site_data.py")    # docs/ payload; must follow tag+recognition
    run("diff_report.py")


if __name__ == "__main__":
    main()
