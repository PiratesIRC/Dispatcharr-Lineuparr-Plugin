"""Regression: settings values stored as None must not crash string-processing helpers.

Bug reported from the UI: clicking the Validate button produced
`Internal error: 'NoneType' object has no attribute 'strip'`.
Root cause: `settings.get("key", "")` returns None when the value is explicitly
None in the stored config (not missing), because .get() only applies the default
for absent keys. Subsequent .strip()/.lstrip() then crashes.
Fix pattern: `(settings.get("key") or "").strip()`.
"""
import pytest

from Lineuparr.plugin import Plugin


NONE_SETTINGS = {
    "lineup_file": "US_Verizon-FIOS_lineup.json",
    "m3u_sources": None,
    "channel_profiles": None,
    "group_prefix": None,
    "channel_numbering": "specific",
    "starting_channel_number": None,
    "custom_aliases": None,
    "epg_sources": None,
    "match_sensitivity": "normal",
    "category_detail": "refined",
}


def test_get_group_prefix_handles_none():
    # group_prefix stored as None must not crash .lstrip().
    p = Plugin()
    result = p._get_group_prefix({"group_prefix": None}, {"package": "DIRECTV Premier"})
    assert result == "DIRECTV"


def test_init_assigner_state_handles_none_starting_number():
    # starting_channel_number=None must not crash .strip().
    state = Plugin._init_assigner_state(
        {"channel_numbering": "specific", "starting_channel_number": None}
    )
    assert state == {"next": 1, "used": set()}


@pytest.mark.parametrize(
    "method_name,extra_args",
    [
        ("_resolve_m3u_sources", ("logger",)),
        ("_build_alias_map", ("logger",)),
        ("_resolve_channel_profiles", ("logger",)),
        ("_get_filtered_epg_data", ("logger",)),
    ],
)
def test_settings_helpers_handle_none_values(method_name, extra_args):
    """These helpers all read settings keys that could be None. They must not
    crash with AttributeError on NoneType."""
    import logging
    p = Plugin()
    logger = logging.getLogger("test")
    method = getattr(p, method_name)
    # Must not raise AttributeError on None
    try:
        method(NONE_SETTINGS, logger)
    except AttributeError as e:
        if "NoneType" in str(e) and "strip" in str(e):
            pytest.fail(f"{method_name} crashed on None settings value: {e}")
        # Other AttributeErrors (mocked DB quirks) are fine - just not the strip bug
