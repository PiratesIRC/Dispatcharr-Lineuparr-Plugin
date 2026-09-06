"""Tests for lineup file rename: new naming convention and country code extraction."""
import os
import json
import pytest


def test_parse_lineup_filename_valid():
    """Country code and provider name are extracted from the new filename format."""
    from Lineuparr.plugin import Plugin

    p = Plugin()
    cc, provider = p._parse_lineup_filename("US_DirecTV-Premier_lineup.json")
    assert cc == "US"
    assert provider == "DirecTV-Premier"


def test_parse_lineup_filename_with_hyphens():
    """Provider names with hyphens are preserved."""
    from Lineuparr.plugin import Plugin

    p = Plugin()
    cc, provider = p._parse_lineup_filename("US_Verizon-FIOS_lineup.json")
    assert cc == "US"
    assert provider == "Verizon-FIOS"


def test_parse_lineup_filename_non_us():
    """Non-US country codes work."""
    from Lineuparr.plugin import Plugin

    p = Plugin()
    cc, provider = p._parse_lineup_filename("CA_Bell-Fibe_lineup.json")
    assert cc == "CA"
    assert provider == "Bell-Fibe"


def test_parse_lineup_filename_invalid():
    """Old-format filenames return None for both fields."""
    from Lineuparr.plugin import Plugin

    p = Plugin()
    cc, provider = p._parse_lineup_filename("premier-lineup.json")
    assert cc is None
    assert provider is None


def test_lineup_display_name():
    """Display name is formatted as 'Provider (CC)' with hyphens replaced by spaces."""
    from Lineuparr.plugin import Plugin

    p = Plugin()
    cc, provider = p._parse_lineup_filename("US_DirecTV-Premier_lineup.json")
    display = f"{provider.replace('-', ' ')} ({cc})" if cc else "unknown"
    assert display == "DirecTV Premier (US)"
