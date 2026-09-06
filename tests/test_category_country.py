"""Regression: mixed-country lineups must filter backup streams per category.

A lineup file like "AU-NZ-UK_Test_Mixed_lineup.json" does NOT match the single
"{CC}_..._lineup.json" pattern, so _parse_lineup_filename returns (None, None)
and lineup_cc is None. With no lineup-level country the stream country filter
was disabled entirely, and a Canadian/US beIN Sports feed attached to an
Australian "BEIN SPORTS 2" channel as a backup stream (real user report,
lineuparr_match_applied_20260608_004559.csv: AU channel -> "US: BEIN SPORTS 2 HD").

Mixed lineups encode each channel's country in the category name
("AU| AUSTRALIA VIP"). detect_category_country() recovers that code so the
filter can run per-category. Ordinary single-country lineups use plain theme
categories ("News", "Sports") which must return None so the caller falls back
to the lineup-level code.
"""
import pytest

from Lineuparr.fuzzy_matcher import detect_category_country, country_codes_in_text


class TestDetectCategoryCountry:
    @pytest.mark.parametrize("category,expected", [
        # Mixed-lineup category prefixes (the user's real format + variants).
        ("AU| AUSTRALIA VIP", "AU"),
        ("UK: Sports", "UK"),
        ("US-News", "US"),
        ("NZ Movies", "NZ"),
        ("US News", "US"),
        ("CA| Canada", "CA"),
        ("  AU |  Sports  ", "AU"),
        ("MEX: Deportes", "MX"),   # ISO-3 folded to ISO-2
        ("FRA| Sport", "FR"),
    ])
    def test_country_prefixed_categories(self, category, expected):
        assert detect_category_country(category) == expected

    @pytest.mark.parametrize("category", [
        # Ordinary theme categories must NOT be misread as a country.
        "News",
        "Sports",
        "Movies",
        "Entertainment",
        "Foreign",
        "Kids",
        "Sci-Fi",          # token "SCI" - not a country
        "On-Demand",       # token "ON" - not a country
        "Pay-Per-View",    # token "PAY" - not a country
        "Music & More",
        "4K Movies",       # leading digit
        "E! Entertainment",  # single leading letter
        "",
        None,
    ])
    def test_theme_categories_return_none(self, category):
        assert detect_category_country(category) is None


class TestCountryCodesInText:
    """Backs the Validate Settings country-mismatch warning: a group prefix or
    EPG source filter that targets a different country than the lineup.
    """
    @pytest.mark.parametrize("text,expected", [
        # EPG source filter patterns the user listed.
        ("UK*", {"UK"}),
        ("UK-*", {"UK"}),
        ("UK *", {"UK"}),
        ("*-UK", {"UK"}),
        ("*UK", {"UK"}),
        ("UK Jesmann", {"UK"}),
        ("UK*, EPG Share", {"UK"}),
        ("UK*, AU*", {"UK", "AU"}),
        # Group prefix forms.
        ("AU:", {"AU"}),
        ("AU ", {"AU"}),
        ("AU-", {"AU"}),
        ("US ", {"US"}),
        ("USA Guide", {"US"}),   # ISO-3 folded to ISO-2
    ])
    def test_detects_country_tokens(self, text, expected):
        assert country_codes_in_text(text) == expected

    @pytest.mark.parametrize("text", [
        "",
        None,
        "EPG Share",
        "Jessman",
        "Malasia",
        "DTV-",            # provider prefix, not a country
        "DIRECTV ",
        "Indian Channels",  # "IN" must not be matched inside "Indian"
        "none",
    ])
    def test_ignores_non_country_text(self, text):
        assert country_codes_in_text(text) == set()
