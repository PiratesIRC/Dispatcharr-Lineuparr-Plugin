"""Regression: space-separated country prefixes must be detected (bug: cross-
country backup streams).

A stream named "US beIN SPORTS (S)" carries a clear US country marker, but
detect_stream_country() only recognized markers wrapped in () or followed by a
`: - |` separator. The space-separated form returned None, so the country
filter in match_all_streams() could not drop it. On a non-US lineup (e.g. AU)
these US/UK/CA streams survived as candidates and were assigned as BACKUP
streams: a foreign-country feed attached to a local channel.

normalize_name() already strips the bare-space "US|UK|CA|AU " prefix
(PROVIDER_PREFIX_PATTERNS), so the two functions were inconsistent: matching
treated the stream as countryless (good fuzzy score) while the filter could not
prove its country (so it was kept). This test pins the symmetry.
"""
import pytest

from Lineuparr.fuzzy_matcher import detect_stream_country, FuzzyMatcher


class TestSpacePrefixDetection:
    @pytest.mark.parametrize("name,expected", [
        ("US beIN SPORTS (S)", "US"),
        ("US (English) beIN SPORTS (S)", "US"),
        ("US BeIN Sports LaLiga (S)", "US"),
        ("UK Sky Sports Main Event", "UK"),
        ("CA TSN 1 HD", "CA"),
        ("AU beIN SPORTS 1 HD", "AU"),
        ("FR beIN SPORTS MAX 4 HD (P)", "FR"),
        ("DE Sky Sport Bundesliga", "DE"),
        ("MX beIN Sports", "MX"),
        ("MEX Bein Sports", "MX"),   # 3-letter alias folds to MX
        ("FRA Canal+ Sport", "FR"),  # 3-letter alias folds to FR
        ("GER Sport1", "DE"),        # 3-letter alias folds to DE
    ])
    def test_bare_space_country_is_detected(self, name, expected):
        assert detect_stream_country(name) == expected

    @pytest.mark.parametrize("name", [
        "USA Network",            # USA != US + space, must NOT be detected as US
        "IN Country Television",  # 'IN' is a country code but is NOT in the
                                  # bare-space set, so this stays safe
        "IT Crowd",               # 'IT' is a country code but not in the set
        "ID Investigation",       # 'ID' (Indonesia) not in the set
        "FOX Sports 1",           # look-alike prefix, not a country
        "beIN SPORTS 1",          # no prefix at all
    ])
    def test_lookalikes_are_not_misdetected(self, name):
        assert detect_stream_country(name) is None

    def test_existing_separator_and_paren_forms_still_work(self):
        assert detect_stream_country("US: beIN Sports") == "US"
        assert detect_stream_country("(US) beIN Sports") == "US"
        assert detect_stream_country("MEX: Bein Sports") == "MX"


class TestFilterDropsForeignSpacePrefixedStreams:
    """End-to-end: an AU lineup must not keep space-prefixed US streams."""

    def test_au_lineup_drops_us_space_prefixed_bein(self):
        matcher = FuzzyMatcher(match_threshold=80)
        streams = ["US beIN SPORTS (S)", "AU beIN SPORTS 1 HD"]
        matcher.precompute_normalizations(streams)
        matcher.country_filter_drops = 0
        res = matcher.match_all_streams(
            "beIN Sports 1", streams, {}, lineup_country="AU"
        )
        kept = {r[0] for r in res}
        assert "US beIN SPORTS (S)" not in kept, "US stream leaked into AU lineup"
        assert "AU beIN SPORTS 1 HD" in kept, "AU stream should be kept"
        assert matcher.country_filter_drops >= 1


class TestFastPlatformPrefixStripped:
    """FAST streaming-platform source tags (Roku, Tubi, Pluto, etc.) are
    stripped during normalization so they don't drag down the match score.
    They are NOT country signals."""

    @pytest.fixture
    def matcher(self):
        return FuzzyMatcher(match_threshold=80)

    @pytest.mark.parametrize("tagged", [
        "RK: BEIN SPORTS XTRA",
        "GO: BEIN SPORTS XTRA",
        "TUBI: BEIN SPORTS XTRA",
        "PLUTO: BEIN SPORTS XTRA",
        "XUMO: BEIN SPORTS XTRA",
        "PLEX - BEIN SPORTS XTRA",
        "STIRR | BEIN SPORTS XTRA",
    ])
    def test_platform_prefix_is_removed(self, matcher, tagged):
        plain = matcher.normalize_name("BEIN SPORTS XTRA")
        assert matcher.normalize_name(tagged) == plain

    def test_platform_tag_is_not_a_country(self, tagged="RK: BEIN SPORTS XTRA"):
        # platform tags must never be read as a country marker
        assert detect_stream_country("RK: BEIN SPORTS XTRA") is None
        assert detect_stream_country("TUBI: BEIN SPORTS XTRA") is None

    def test_separator_required_so_real_words_survive(self, matcher):
        # "GOLF" / "PLEXUS" must not lose their leading letters (no separator)
        assert matcher.normalize_name("GOLF Channel") == matcher.normalize_name("GOLF Channel")
        assert "golf" in matcher.normalize_name("GOLF Channel").lower()
