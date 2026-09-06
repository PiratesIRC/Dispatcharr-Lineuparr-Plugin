"""Data-integrity tests for the shipped Lineuparr/*_lineup.json files.

These guard invariants that the matcher logic assumes but never checks at
runtime, where a bad value fails silently instead of loudly:

* Two channels with the same name in the same category silently collapse into
  one via Channel.objects.get_or_create(name, channel_group_id) (later number
  wins, no error). That is a data defect, so we reject it here.
* The plugin discovers lineups with glob("*_lineup.json") and parses the
  country code from the filename, so the filename must keep the expected shape.
* Em dashes are banned project-wide (user rule); JSON strings are committed
  files, so they are in scope.

Run with the rest of the unit suite: `python -m pytest tests/ -q`.
"""
import json
import re
from pathlib import Path

import pytest

LINEUP_DIR = Path(__file__).resolve().parent.parent / "Lineuparr"
LINEUP_FILES = sorted(LINEUP_DIR.glob("*_lineup.json"))

# Plugin._parse_lineup_filename expects a leading country code. Every shipped
# file currently uses a 2-letter ISO code; the {2,} allows a future 3-letter
# code without loosening the "_lineup.json" tail.
FILENAME_RE = re.compile(r"^[A-Z]{2,}_.+_lineup\.json$")
EM_DASH = "\u2014"


def _ids(files):
    return [f.name for f in files]


def test_lineup_files_present():
    assert LINEUP_FILES, f"no *_lineup.json files found under {LINEUP_DIR}"


@pytest.mark.parametrize("path", LINEUP_FILES, ids=_ids(LINEUP_FILES))
def test_filename_matches_discovery_pattern(path):
    assert FILENAME_RE.match(path.name), (
        f"{path.name} will not parse as <CC>_<name>_lineup.json"
    )


@pytest.mark.parametrize("path", LINEUP_FILES, ids=_ids(LINEUP_FILES))
def test_parses_as_json_with_categories(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: top level must be an object"
    cats = data.get("categories")
    assert isinstance(cats, dict) and cats, (
        f"{path.name}: required 'categories' dict is missing or empty"
    )
    for cat_name, channels in cats.items():
        # Empty category lists are allowed (some lineups carry a placeholder
        # category that syncs nothing); they just must be lists.
        assert isinstance(channels, list), (
            f"{path.name}: category '{cat_name}' must be a list"
        )


@pytest.mark.parametrize("path", LINEUP_FILES, ids=_ids(LINEUP_FILES))
def test_channels_have_name_and_number(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for cat_name, channels in data["categories"].items():
        for i, ch in enumerate(channels):
            assert isinstance(ch, dict), (
                f"{path.name}: {cat_name}[{i}] is not an object"
            )
            assert ch.get("name"), (
                f"{path.name}: {cat_name}[{i}] missing non-empty 'name'"
            )
            assert "number" in ch, (
                f"{path.name}: channel '{ch.get('name')}' in '{cat_name}' "
                f"missing 'number'"
            )


@pytest.mark.parametrize("path", LINEUP_FILES, ids=_ids(LINEUP_FILES))
def test_no_duplicate_channel_names_within_a_category(path):
    """Duplicate names in one category collapse into a single channel."""
    data = json.loads(path.read_text(encoding="utf-8"))
    offenders = []
    for cat_name, channels in data["categories"].items():
        seen = {}
        for ch in channels:
            name = ch.get("name")
            seen[name] = seen.get(name, 0) + 1
        offenders += [
            f"{cat_name!r}: {name!r} x{n}" for name, n in seen.items() if n > 1
        ]
    assert not offenders, (
        f"{path.name}: duplicate channel names within a category "
        f"(they collapse on sync): " + "; ".join(offenders)
    )


@pytest.mark.parametrize("path", LINEUP_FILES, ids=_ids(LINEUP_FILES))
def test_no_em_dashes(path):
    text = path.read_text(encoding="utf-8")
    assert EM_DASH not in text, (
        f"{path.name}: contains an em dash (U+2014); use a hyphen, comma, "
        f"or colon instead"
    )
