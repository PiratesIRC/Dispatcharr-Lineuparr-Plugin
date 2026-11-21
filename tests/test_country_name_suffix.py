"""Issue #38: a full country name in parentheses tags the stream's country.

Public iptv-org playlists name the country at the end of the stream name, in
parentheses ("TNT (Russia) HD (1080p)"). Before this fix those streams read as
untagged and passed the country filter of a US lineup.
"""
import pytest

from Lineuparr.fuzzy_matcher import (
    _COUNTRY_NAME_TO_CODE,
    _KNOWN_COUNTRY_CODES,
    FuzzyMatcher,
    detect_stream_country,
)


@pytest.mark.parametrize("name, expected", [
    ("TNT (Russia) HD (1080p)", "RU"),
    ("TLC (Russia) (1080p)", "RU"),
    ("Discovery Channel (Russia) (1080p)", "RU"),
    ("Disney Channel (Romania)", "RO"),
    ("Comedy Central (Romania)", "RO"),
    ("Cinemax (Czech Republic)", "CZ"),
    ("Sundance TV (Spain)", "ES"),
    ("QVC (Japan) (1080p)", "JP"),
    ("ESPN (United States) HD (720p)", "US"),
    ("BBC One (United Kingdom)", "UK"),
    ("Arirang (South Korea)", "KR"),
    ("Sky News ( Ireland )", "IE"),
    ("rtl (germany)", "DE"),
])
def test_parenthesized_country_name_is_detected(name, expected):
    assert detect_stream_country(name) == expected


@pytest.mark.parametrize("name", [
    "Fox 5 (New York)",          # a city, not a country
    "PBS (East)",                # a feed region
    "ESPN (Pacific)",
    "Peachtree TV (Georgia)",    # US state; Georgia the country is not a lineup code
    "Al Jazeera (Qatar Edition)",  # extra words: not the whole bracket
    "Some Channel (Argentina)",  # not a lineup country code
    "Russia Today",              # a country word outside brackets
    "CNN [Russia]",              # square brackets are not the iptv-org form
])
def test_other_parenthesized_words_stay_untagged(name):
    assert detect_stream_country(name) is None


def test_a_leading_prefix_still_wins():
    assert detect_stream_country("US: Discovery (Russia)") == "US"


def test_every_mapped_name_is_a_lineup_country():
    assert set(_COUNTRY_NAME_TO_CODE.values()) <= _KNOWN_COUNTRY_CODES
    for name in _COUNTRY_NAME_TO_CODE:
        assert name == name.upper()


@pytest.mark.parametrize("channel, foreign, domestic", [
    ("TNT", "TNT (Russia) HD (1080p)", "US: TNT"),
    ("Disney Channel", "Disney Channel (Romania)", "US: Disney Channel"),
    ("QVC", "QVC (Japan) (1080p)", "QVC (United States)"),
])
def test_us_lineup_drops_the_reported_foreign_feeds(channel, foreign, domestic):
    matcher = FuzzyMatcher(match_threshold=80)
    results = matcher.match_all_streams(channel, [foreign, domestic], alias_map={},
                                        lineup_country="US")
    names = [r[0] for r in results]
    assert foreign not in names
    assert domestic in names
    assert matcher.country_filter_drops >= 1
