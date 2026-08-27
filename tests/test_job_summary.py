"""What the Monday notification actually leads with.

The summary is what arrives; the log is tens of thousands of characters that
nobody reads. So what gets promoted out of the fold is the whole question, and
until this file existed the logic could not be run without pushing a commit and
waiting a week -- which is exactly how a wrong index came to silently skip the
entire SHORTLIST ALERTS block on every run.
"""
import pytest

from job_summary import block, closures, render, split_sections

LOG = """\
=== fetch_listing.py ===
snapshot written
=== build_venues.py --quiet ===
venues 1414
=== export_venues.py ===
wrote docs/data/venues.json (1250 KB)
=== diff_report.py ===
############################################################
## SHORTLIST ALERTS (booking-relevant changes)
  !! frenchette mealTypes: ['$60 Dinner'] -> ['$45 Dinner']
############################################################
# Diff: snapshot-2026-08-20.json -> snapshot-2026-08-27.json
added (1):
  + Somewhere New (somewhere-new)
############################################################
## ROSTER
  CLOSED since last week (2):
    x Estela (estela) — google: CLOSED_PERMANENTLY
    x Lilia (lilia) — google: CLOSED_PERMANENTLY
  gained recognition (1):
    + Frenchette (frenchette) +2 records
  unverified: 778 -> 700
  --
  recognition: 14 waiting (james_beard 8) -> recognition_review.json
############################################################
"""


def test_sections_split_on_the_step_banner():
    s = split_sections(LOG)
    assert set(s) >= {"fetch_listing", "build_venues", "export_venues", "diff_report"}
    assert "wrote docs/data/venues.json" in s["export_venues"]


def test_a_closure_leads_the_summary():
    """The single most booking-relevant fact this repo holds, and the only one
    in the report not recoverable from anywhere else -- a closure is not a
    listing change. It used to land inside a collapsed <details> blob."""
    out = render(LOG)
    head = out.index("## 🔴 Closed since last week")
    assert head < out.index("## ⚠️ Shortlist changes")
    assert "Estela (estela)" in out and "Lilia (lilia)" in out
    # and above the fold, not inside it
    assert head < out.index("<details>")


def test_closures_are_extracted_without_their_marker_or_indent():
    lines = block(LOG.split("## ROSTER", 1)[1], "")
    got = closures(["  CLOSED since last week (1):",
                    "    x Estela (estela) — google: CLOSED_PERMANENTLY",
                    "  gained recognition (1):"])
    assert got == ["Estela (estela) — google: CLOSED_PERMANENTLY"]


def test_no_closures_means_no_closure_heading():
    quiet = LOG.replace("""  CLOSED since last week (2):
    x Estela (estela) — google: CLOSED_PERMANENTLY
    x Lilia (lilia) — google: CLOSED_PERMANENTLY
""", "")
    out = render(quiet)
    assert "Closed since last week" not in out
    assert "## Shortlist changes" in out or "Shortlist" in out


def test_shortlist_alerts_still_lead_when_nothing_closed():
    quiet = LOG.replace("""  CLOSED since last week (2):
    x Estela (estela) — google: CLOSED_PERMANENTLY
    x Lilia (lilia) — google: CLOSED_PERMANENTLY
""", "")
    out = render(quiet)
    assert "⚠️ Shortlist changes" in out
    assert "frenchette mealTypes" in out


def test_an_unchanged_shortlist_says_so_rather_than_going_quiet():
    out = render(LOG.replace("  !! frenchette mealTypes: ['$60 Dinner'] -> ['$45 Dinner']", ""))
    assert "Shortlist: unchanged" in out


def test_the_roster_block_and_pending_rulings_survive_into_the_summary():
    out = render(LOG)
    assert "gained recognition" in out
    assert "unverified: 778 -> 700" in out
    assert "recognition: 14 waiting" in out


def test_an_empty_log_does_not_crash_the_step():
    """The summary step runs `if: always()`, so it must survive the refresh
    having died before writing anything."""
    out = render("")
    assert out.startswith("# NYC Restaurant Week")
    assert "Shortlist: unchanged" in out


@pytest.mark.parametrize("text", ["", None, "no banners at all\njust lines"])
def test_split_sections_is_total(text):
    assert isinstance(split_sections(text), dict)


def test_a_closure_is_named_once_above_the_fold():
    """It is promoted to the top, so repeating it in the roster block would
    teach people to skim. It DOES appear again inside the full-diff fold, which
    is the raw log verbatim and is meant to be complete -- so this asserts on
    the part of the summary somebody actually reads."""
    out = render(LOG)
    above = out[:out.index("<details>")]
    assert above.count("Estela (estela)") == 1
    assert out.count("Estela (estela)") == 2   # the fold keeps the raw copy
    # the rest of the block survives the removal
    assert "gained recognition" in out
    assert "unverified: 778 -> 700" in out


def test_dropping_the_closure_block_keeps_everything_after_it():
    from job_summary import drop_closure_block
    kept = drop_closure_block([
        "  CLOSED since last week (2):",
        "    x Estela (estela)",
        "    x Lilia (lilia)",
        "  gained recognition (1):",
        "    + Frenchette (frenchette) +2 records",
    ])
    assert kept == ["  gained recognition (1):",
                    "    + Frenchette (frenchette) +2 records"]
