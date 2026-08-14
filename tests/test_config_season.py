"""Season facts must come from config/season.json, nowhere else."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

GOOD = {
    "code": "srw26", "label": "Summer 2026", "year": 2026,
    "start": "2026-07-20", "book_by": "2026-08-16", "end": "2026-09-06",
    "min_rows": 400,
}


def test_season_constants_load_from_season_json():
    import config

    raw = json.loads((ROOT / "config" / "season.json").read_text(encoding="utf-8"))
    season = config.load_season(ROOT / "config" / "season.json")
    assert season["code"] == raw["code"]
    # week 2 in snapshot-2026-07-31.json runs Jul 27 - Aug 2 => week 1 = Jul 20
    assert config.SEASON == "srw26"
    assert config.SEASON_LABEL == "Summer 2026"
    assert config.SEASON_YEAR == 2026
    assert config.SEASON_START == "2026-07-20"
    assert config.BOOK_BY == "2026-08-16"
    assert config.PROGRAM_END == "2026-09-06"
    assert config.MIN_ROWS == 400


def test_load_season_rejects_garbage_naming_the_bad_field(tmp_path):
    import config

    def write(**overrides):
        d = {**GOOD, **overrides}
        for k, v in list(d.items()):
            if v is None:
                del d[k]
        p = tmp_path / "season.json"
        p.write_text(json.dumps(d), encoding="utf-8")
        return p

    with pytest.raises(ValueError, match="start"):
        config.load_season(write(start="July 20, 2026"))
    with pytest.raises(ValueError, match="book_by"):
        config.load_season(write(book_by="2026-09-07"))  # after end
    with pytest.raises(ValueError, match="code"):
        config.load_season(write(code="winter2027"))
    with pytest.raises(ValueError, match="min_rows"):
        config.load_season(write(min_rows=None))


def test_exporter_season_constants_flow_from_config_not_local_copies():
    import config
    import export_site_data

    assert export_site_data.SEASON_LABEL is config.SEASON_LABEL
    assert export_site_data.SEASON_YEAR == config.SEASON_YEAR
    assert export_site_data.BOOK_BY is config.BOOK_BY
    assert export_site_data.PROGRAM_END is config.PROGRAM_END
