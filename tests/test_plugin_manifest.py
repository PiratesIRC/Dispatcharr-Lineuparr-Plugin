"""Manifest integrity and matcher invariant-sync tests.

Two classes of guard that have each caused a real regression before:

1. plugin.json manifest shape. Settings fields are defined by Plugin.fields in
   plugin.py (single source of truth); the manifest 'fields' array must stay
   empty or Dispatcharr would render a stale duplicate. The version string in
   plugin.json and plugin.py must match (bump_version.py keeps them in sync at
   release time; this catches manual drift). Field labels must use BMP-only
   emoji: a non-BMP glyph broke Dispatcharr's PluginFieldSerializer once.

2. Country-prefix detection sync. normalize_name() STRIPS a leading country
   prefix so a foreign stream matches; detect_stream_country() must DETECT the
   same prefix forms so the country filter can drop the wrong-country stream.
   If detection recognizes fewer forms than normalization strips, foreign
   streams match cleanly yet evade the filter and leak in (the bug-064
   asymmetry). These tests pin detection of every prefix form the strip side
   relies on.

Run with the unit suite: `python -m pytest tests/ -q`.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "Lineuparr"
PLUGIN_JSON = PLUGIN_DIR / "plugin.json"
PLUGIN_PY = PLUGIN_DIR / "plugin.py"

sys.path.insert(0, str(PLUGIN_DIR))
import fuzzy_matcher as fm  # noqa: E402  (after sys.path tweak; Django mocked in conftest)

EM_DASH = "\u2014"
PY_VERSION_RE = re.compile(r'PLUGIN_VERSION\s*=\s*"([^"]+)"')


@pytest.fixture(scope="module")
def manifest():
    return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))


# --- manifest shape -------------------------------------------------------

def test_manifest_fields_is_empty(manifest):
    """Settings fields live in Plugin.fields, not the manifest, to avoid drift."""
    assert manifest.get("fields") == [], (
        "plugin.json 'fields' must be [] - settings are defined by the "
        "Plugin.fields property in plugin.py"
    )


def test_manifest_has_actions(manifest):
    actions = manifest.get("actions")
    assert isinstance(actions, list) and actions, "plugin.json needs actions"
    for a in actions:
        assert a.get("id"), f"action missing id: {a}"
        assert a.get("label"), f"action {a.get('id')} missing label"


def test_manifest_field_labels_are_bmp_emoji(manifest):
    """Field labels must stay in the BMP (codepoint <= U+FFFF).

    Non-BMP glyphs break Dispatcharr's PluginFieldSerializer. fields is [] today,
    so this guards future additions if anyone re-introduces a manifest field.
    Action labels are intentionally NOT checked: they use a different serializer
    and the shipped actions use non-BMP emoji (rocket, chart) without issue.
    """
    for field in manifest.get("fields", []):
        for key in ("label", "button_label"):
            for ch in field.get(key, ""):
                assert ord(ch) <= 0xFFFF, (
                    f"field {field.get('id')!r} {key} has non-BMP char "
                    f"{ch!r} (U+{ord(ch):04X})"
                )


def test_manifest_version_matches_plugin_py(manifest):
    py_text = PLUGIN_PY.read_text(encoding="utf-8")
    m = PY_VERSION_RE.search(py_text)
    assert m, "PLUGIN_VERSION not found in plugin.py"
    assert manifest["version"] == m.group(1), (
        f"version drift: plugin.json={manifest['version']} "
        f"plugin.py={m.group(1)} (run bump_version.py)"
    )


def test_manifest_no_em_dashes():
    assert EM_DASH not in PLUGIN_JSON.read_text(encoding="utf-8"), (
        "plugin.json contains an em dash (U+2014)"
    )


# --- normalize/detect country-prefix sync ---------------------------------

@pytest.mark.parametrize("cc", sorted(fm._KNOWN_COUNTRY_CODES))
def test_colon_prefix_is_detected(cc):
    """normalize_name strips a 2-3 letter colon prefix; detect must recognize it."""
    assert fm.detect_stream_country(f"{cc}: Test Channel") == cc, (
        f"colon prefix {cc!r} is stripped by normalize_name but not detected; "
        f"foreign streams with this tag would evade the country filter"
    )


@pytest.mark.parametrize("iso3,iso2", sorted(fm._ISO3_TO_ISO2.items()))
def test_iso3_colon_prefix_maps_to_iso2(iso3, iso2):
    assert fm.detect_stream_country(f"{iso3}: Test Channel") == iso2


@pytest.mark.parametrize("cc,expected", [
    ("US", "US"), ("UK", "UK"), ("CA", "CA"), ("AU", "AU"), ("FR", "FR"),
    ("DE", "DE"), ("MX", "MX"), ("MEX", "MX"), ("FRA", "FR"), ("GER", "DE"),
])
def test_bare_space_prefix_is_detected(cc, expected):
    """The curated bare-space strip set must be matched by detection one-for-one."""
    assert fm.detect_stream_country(f"{cc} Racer Network") == expected


@pytest.mark.parametrize("name,expected", [
    ("UKSD: Sky Sports", "UK"),
    ("UKHD ESPN", "UK"),
    ("USFHD ESPN", "US"),
    ("USHD: TNT", "US"),
])
def test_country_glued_to_quality_is_detected(name, expected):
    assert fm.detect_stream_country(name) == expected


def test_usa_network_is_not_misread_as_country():
    """The bare 'USA ' rule must not eat the real channel 'USA Network'."""
    assert fm.detect_stream_country("USA Network") is None
