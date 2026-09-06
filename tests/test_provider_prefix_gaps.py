"""Regression: provider-prefix gaps found by analyzing the stream names of two
real provider playlists (2026-06-07).

Four classes of name that the matcher mishandled, all confirmed against the
live provider exports:

  P1  Country code glued to a quality tag with no separator ("UKSD: Sky Sports",
      "UKHD ESPN"). normalize_name() stripped the prefix but
      detect_stream_country() returned None, so the stream matched a foreign
      lineup cleanly yet evaded the country filter (the bug-064 asymmetry).
  P2  "USA " space prefix as a US country tag ("USA  ABC"). Was undetected; the
      real channel "USA Network" must stay safe (it is tagged "US ..." in feeds).
  P4  "## ... ##" two-hash divider pseudo-channels slipped past _is_group_header
      (which only caught runs of 3+).
  P5  Colon country codes TR/GR/IR/AL (Turkey/Greece/Iran/Albania) were stripped
      by normalization but not detected, so they leaked as wildcards. "AR" stays
      undetected on purpose (Arabic-language, not Argentina) and "GOLD:" is a
      category prefix that must be stripped.
"""
import pytest

from Lineuparr.fuzzy_matcher import detect_stream_country, FuzzyMatcher


class TestGluedQualityCountry:
    @pytest.mark.parametrize("name,expected", [
        ("UKSD: Sky Sports Main Event", "UK"),
        ("UKHD: Sky Sports", "UK"),
        ("UKFHD Sky Sports", "UK"),
        ("UKSD Sky Sports", "UK"),
        ("USSD: ESPN", "US"),
        ("USHD ESPN", "US"),
        ("USFHD Fox", "US"),
    ])
    def test_detect_glued_quality_country(self, name, expected):
        assert detect_stream_country(name) == expected

    def test_glued_quality_prefix_is_stripped(self):
        m = FuzzyMatcher(match_threshold=80)
        assert m.normalize_name("UKSD: Sky Sports Main Event") == m.normalize_name("Sky Sports Main Event")
        assert m.normalize_name("USHD ESPN") == m.normalize_name("ESPN")

    def test_glued_detect_and_normalize_stay_symmetric(self):
        # The whole point: both must agree, or wrong-country streams leak.
        m = FuzzyMatcher(match_threshold=80)
        name = "UKHD: Sky Sports"
        assert detect_stream_country(name) == "UK"
        assert "uk" not in m.normalize_name(name).lower().split()


class TestUsaSpacePrefix:
    @pytest.mark.parametrize("name", [
        "USA  ABC",          # double space (the common form)
        "USA BET",
        "USA  ANIMAL PLANET",
    ])
    def test_usa_space_is_us(self, name):
        assert detect_stream_country(name) == "US"

    def test_usa_space_prefix_stripped(self):
        m = FuzzyMatcher(match_threshold=80)
        assert m.normalize_name("USA  ABC") == m.normalize_name("ABC")

    def test_usa_network_channel_not_misread_as_country(self):
        # "USA Network" the channel must NOT be read as country=US.
        assert detect_stream_country("USA Network") is None
        assert detect_stream_country("USA Network West") is None


class TestColonCountryCodes:
    @pytest.mark.parametrize("name,expected", [
        ("TR: 24 TV", "TR"),
        ("GR: Mega Channel", "GR"),
        ("IR: IRIB IRINN 1", "IR"),
        ("AL: DigitalB", "AL"),
    ])
    def test_new_colon_countries_detected(self, name, expected):
        assert detect_stream_country(name) == expected

    @pytest.mark.parametrize("name,expected", [
        ("RO: Acasa Gold", "RO"),
        ("RU: 2X2", "RU"),
        ("AZ: Baku TV", "AZ"),
        ("HR: N1 BH", "HR"),
        ("TH: 13Siam Thai", "TH"),
        ("MK: 24 Vesti", "MK"),
        ("IL: Kan 11", "IL"),
        ("CO: Cable Noticias", "CO"),
        ("CR: FUTV HD", "CR"),
        ("CY: Alpha CY", "CY"),
        ("BG: 24 Kitchen", "BG"),
        ("RS: AMC", "RS"),
        ("JP: Animax", "JP"),
        ("KR: Arirang", "KR"),
        ("CZ: Arena Sport 1", "CZ"),
        ("HU: Apostol TV", "HU"),
        ("NZ: BBC Earth", "NZ"),
        ("PH: GMA 7", "PH"),
        ("VN: BBC Earth HD", "VN"),
        ("PK: 92 News HD", "PK"),
        ("SI: Arena Sport 1", "SI"),
        ("ETH: Addis TV", "ET"),
    ])
    def test_second_batch_foreign_countries_detected(self, name, expected):
        # These were leaking as backup streams; detection now filters them.
        assert detect_stream_country(name) == expected

    def test_arabic_ar_is_not_a_country(self):
        # "AR" tags Arabic-language channels in these feeds, not Argentina.
        assert detect_stream_country("AR: Al Masalah") is None

    @pytest.mark.parametrize("name", [
        "HUB: A&E",           # provider tag carrying US channels - must stay wildcard
        "AMP: Adult Swim",
        "STC: AD Sports 1",   # Saudi telco provider tag, not a country
        "OSN: Action",
        "MEO: 1+1 International",
        "LA: AMC",            # Latin America region, not a single country
        "AFR: Erisat",
        "MT: Cooking 1 4K",   # theme-channel tag in these feeds, not Malta
    ])
    def test_provider_tags_and_regions_are_not_countries(self, name):
        assert detect_stream_country(name) is None

    def test_gold_is_category_not_country(self):
        assert detect_stream_country("GOLD: A Spor") is None

    def test_gold_prefix_is_stripped(self):
        m = FuzzyMatcher(match_threshold=80)
        assert m.normalize_name("GOLD: A Spor") == m.normalize_name("A Spor")


class TestTwoHashDividers:
    @pytest.mark.parametrize("name", [
        "## 24/7 COMEDY ##",
        "## BEIN SPORTS GOLD ##",
        "## ENTERTAINMENT HD/RAW ##",
        "### MATCHROOM PPV ###",     # 3-hash still caught
        "===== EUROSPORT =====",
        "** MOVIES **",
    ])
    def test_dividers_are_flagged(self, name):
        assert FuzzyMatcher._is_group_header(name) is True

    @pytest.mark.parametrize("name", [
        "001 | Montreal vs Toronto",   # single pipe is a real separator
        "beIN SPORTS 1",
        "Sky Sports Main Event",
        "C-SPAN 2",
    ])
    def test_real_channels_not_flagged(self, name):
        assert FuzzyMatcher._is_group_header(name) is False
