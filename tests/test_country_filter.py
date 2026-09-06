"""Tests for country-aware stream matching - prevents cross-country contamination.

Bug being fixed: a US lineup (e.g. Verizon FiOS) was attaching UK/IN/PLUTO-country
streams to US channels because match_all_streams had no country filter. Streams with
obvious country-prefix markers like `UK: Discovery Channel [vip]` or `(IN) Bloomberg TV`
normalize to the bare channel name and win exact/alias matches.
"""
import pytest

from Lineuparr.fuzzy_matcher import FuzzyMatcher, detect_stream_country


# ---------- detect_stream_country ----------

class TestDetectCountryParenPrefix:
    def test_iso2_paren(self):
        assert detect_stream_country("(US) ESPN") == "US"

    def test_iso2_paren_uk(self):
        assert detect_stream_country("(UK) BBC One") == "UK"

    def test_iso2_paren_in(self):
        assert detect_stream_country("(IN) (STV) Bloomberg TV") == "IN"

    def test_iso3_paren_normalizes_to_iso2(self):
        assert detect_stream_country("(USA) CNN") == "US"

    def test_iso3_mex(self):
        assert detect_stream_country("(MEX) Telemundo") == "MX"


class TestDetectCountryColonPrefix:
    def test_us_colon(self):
        assert detect_stream_country("US: CNN") == "US"

    def test_uk_colon(self):
        assert detect_stream_country("UK: Discovery Channel [vip]") == "UK"

    def test_mex_colon(self):
        assert detect_stream_country("MEX: Telemundo") == "MX"

    def test_ire_colon(self):
        assert detect_stream_country("IRE: RTE One") == "IE"


class TestDetectCountryPluto:
    def test_pluto_usa(self):
        assert detect_stream_country("(PLUTO USA) ESPN") == "US"

    def test_pluto_uk(self):
        assert detect_stream_country("(PLUTO UK) POP") == "UK"

    def test_pluto_brazil(self):
        assert detect_stream_country("(PLUTO Brazil) Babyfirst") == "BR"

    def test_pluto_sweden(self):
        assert detect_stream_country("(PLUTO Sweden) Comedy Central") == "SE"

    def test_pluto_germany(self):
        assert detect_stream_country("(PLUTO Germany) MTV") == "DE"

    def test_pluto_italy(self):
        assert detect_stream_country("(PLUTO Italy) Comedy") == "IT"

    def test_pluto_spain(self):
        assert detect_stream_country("(PLUTO Spain) Comedy") == "ES"

    def test_pluto_france(self):
        assert detect_stream_country("(PLUTO France) Comedy") == "FR"

    def test_pluto_denmark(self):
        assert detect_stream_country("(PLUTO Denmark) Comedy") == "DK"

    def test_pluto_norway(self):
        assert detect_stream_country("(PLUTO Norway) BET") == "NO"


class TestDetectCountryCaseInsensitive:
    def test_lowercase_paren(self):
        assert detect_stream_country("(us) ESPN") == "US"

    def test_lowercase_colon(self):
        assert detect_stream_country("us: CNN") == "US"

    def test_mixed_case_pluto(self):
        assert detect_stream_country("(Pluto UK) POP") == "UK"


class TestDetectCountryIso3Forms:
    def test_usa_colon_prefix(self):
        assert detect_stream_country("USA: CNN") == "US"

    def test_mex_paren(self):
        assert detect_stream_country("(MEX) Telemundo") == "MX"


class TestDetectCountryNoFalsePositives:
    """Must not treat US network names or other tags as country codes."""
    def test_nbc_is_not_country(self):
        assert detect_stream_country("NBC Sports Network") is None

    def test_fox_is_not_country(self):
        assert detect_stream_country("FOX News") is None

    def test_cbs_is_not_country(self):
        assert detect_stream_country("CBS Sports") is None

    def test_abc_is_not_country(self):
        assert detect_stream_country("ABC News") is None

    def test_nfl_is_not_country(self):
        assert detect_stream_country("NFL Network") is None

    def test_hd_bracket_tag_is_not_country(self):
        assert detect_stream_country("ESPN [HD]") is None

    def test_no_prefix(self):
        assert detect_stream_country("Discovery Channel") is None

    def test_empty_string(self):
        assert detect_stream_country("") is None

    def test_pluto_latin_unknown_returns_none(self):
        # "LATIN" isn't a single country - return None rather than guess
        assert detect_stream_country("(PLUTO Latin) Comedy") is None


# ---------- match_all_streams country filter ----------

@pytest.fixture
def matcher():
    return FuzzyMatcher(match_threshold=80)


class TestMatchAllStreamsCountryFilter:
    """lineup_country kwarg filters out candidates detected as different countries."""

    def test_us_lineup_rejects_uk_stream(self, matcher):
        candidates = [
            "US: Discovery Channel",
            "UK: Discovery Channel [vip]",
        ]
        results = matcher.match_all_streams(
            "Discovery Channel", candidates, alias_map={},
            lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "US: Discovery Channel" in names
        assert "UK: Discovery Channel [vip]" not in names

    def test_us_lineup_rejects_pluto_uk(self, matcher):
        candidates = ["(PLUTO UK) POP", "US: POP"]
        results = matcher.match_all_streams(
            "POP", candidates, alias_map={},
            lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "(PLUTO UK) POP" not in names
        assert "US: POP" in names

    def test_us_lineup_rejects_indian_stream(self, matcher):
        candidates = ["(IN) (STV) Bloomberg TV", "US: Bloomberg"]
        results = matcher.match_all_streams(
            "Bloomberg", candidates, alias_map={},
            lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "(IN) (STV) Bloomberg TV" not in names
        assert "US: Bloomberg" in names

    def test_uk_lineup_rejects_us_stream(self, matcher):
        candidates = ["UK: BBC News", "US: BBC News"]
        results = matcher.match_all_streams(
            "BBC News", candidates, alias_map={},
            lineup_country="UK",
        )
        names = [r[0] for r in results]
        assert "UK: BBC News" in names
        assert "US: BBC News" not in names

    def test_streams_without_country_marker_are_accepted(self, matcher):
        # No country prefix → can't rule out, so accept (avoid over-filtering)
        candidates = ["Discovery Channel", "UK: Discovery Channel"]
        results = matcher.match_all_streams(
            "Discovery Channel", candidates, alias_map={},
            lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "Discovery Channel" in names
        assert "UK: Discovery Channel" not in names

    def test_lineup_country_none_disables_filter(self, matcher):
        # Backward compat: when lineup_country is not supplied, behave as before.
        candidates = ["US: ESPN", "UK: ESPN"]
        results = matcher.match_all_streams(
            "ESPN", candidates, alias_map={},
            lineup_country=None,
        )
        names = [r[0] for r in results]
        assert "US: ESPN" in names
        assert "UK: ESPN" in names

    def test_matching_country_stream_accepted(self, matcher):
        candidates = ["(US) ESPN"]
        results = matcher.match_all_streams(
            "ESPN", candidates, alias_map={},
            lineup_country="US",
        )
        assert len(results) == 1
        assert results[0][0] == "(US) ESPN"

    def test_us_lineup_rejects_ca_stream(self, matcher):
        # Country matching is STRICT: CA-tagged streams are NOT the same channel
        # as their US namesake (Food Network US != Food Network CA, ESPN US has
        # no CA equivalent). A US lineup must reject CA-marked streams.
        candidates = ["(CA) ESPN", "CA: Food Network"]
        results = matcher.match_all_streams(
            "ESPN", candidates, alias_map={},
            lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "(CA) ESPN" not in names

    def test_us_lineup_rejects_ca_food_network(self, matcher):
        # The exact user-reported false positive: Food Network US != CA.
        candidates = ["(CA) Food Network", "Food Network"]
        results = matcher.match_all_streams(
            "Food Network", candidates, alias_map={},
            lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "(CA) Food Network" not in names
        assert "Food Network" in names  # untagged still matches

    def test_ca_lineup_rejects_us_stream(self, matcher):
        # Reverse direction is strict too (except shared channels, tested below).
        candidates = ["(US) ESPN", "US: Food Network"]
        results = matcher.match_all_streams(
            "ESPN", candidates, alias_map={},
            lineup_country="CA",
        )
        names = [r[0] for r in results]
        assert "(US) ESPN" not in names

    def test_regionless_candidate_with_country_set(self, matcher):
        # Plain "Discovery Channel" (no country marker) must still match
        # even when lineup_country is set - matches the "accept unlabeled" rule.
        candidates = ["Discovery Channel"]
        results = matcher.match_all_streams(
            "Discovery Channel", candidates, alias_map={},
            lineup_country="US",
        )
        assert len(results) == 1
        assert results[0][0] == "Discovery Channel"

    def test_country_filter_composes_with_east_west(self, matcher):
        # Both filters must run: drop wrong-country AND wrong-region candidates.
        # Query "HBO East" + US lineup → keep US East, drop UK East, drop US West.
        candidates = [
            "US: HBO East",
            "UK: HBO East",
            "US: HBO West",
        ]
        results = matcher.match_all_streams(
            "HBO East", candidates, alias_map={},
            lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "US: HBO East" in names
        assert "UK: HBO East" not in names
        assert "US: HBO West" not in names

    def test_country_filter_drops_counter(self, matcher):
        # matcher.country_filter_drops must reflect cross-country rejections
        # so plugin.py can log a summary after the per-channel loop.
        matcher.country_filter_drops = 0
        candidates = ["US: ESPN", "UK: ESPN", "(IN) ESPN"]
        matcher.match_all_streams(
            "ESPN", candidates, alias_map={},
            lineup_country="US",
        )
        assert matcher.country_filter_drops == 2

    def test_unknown_lineup_country_is_ignored(self, matcher):
        # Garbage country codes ("XX", lowercase typos, empty string) must not
        # cause the filter to drop everything - fall through to no-filter.
        candidates = ["US: ESPN", "UK: ESPN"]
        for bogus in ("XX", "usa", ""):
            results = matcher.match_all_streams(
                "ESPN", candidates, alias_map={},
                lineup_country=bogus,
            )
            names = [r[0] for r in results]
            assert "US: ESPN" in names, f"bogus={bogus!r} dropped valid US stream"
            assert "UK: ESPN" in names, f"bogus={bogus!r} dropped valid UK stream"

    def test_alias_match_also_filtered(self, matcher):
        # Alias stage (stage 0) must also respect the country filter - otherwise
        # an alias hit would still contaminate the result set.
        alias_map = {"discovery": ["discovery channel"]}
        candidates = ["UK: Discovery Channel [vip]"]
        results = matcher.match_all_streams(
            "Discovery", candidates, alias_map=alias_map,
            lineup_country="US",
        )
        assert results == []


class TestCrossBorderSharedChannels:
    """Strict-by-default country matching, with a curated allowlist of channels
    that are genuinely the same feed across the US<->MX border."""

    def test_us_lineup_rejects_mx_espn(self, matcher):
        # User-reported: ESPN MX feeds are NOT the same as US feeds.
        candidates = ["(MX) ESPN", "MEX: ESPN", "ESPN"]
        results = matcher.match_all_streams(
            "ESPN", candidates, alias_map={}, lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "(MX) ESPN" not in names
        assert "MEX: ESPN" not in names
        assert "ESPN" in names  # untagged still matches

    def test_us_lineup_accepts_mx_univision(self, matcher):
        # Univision IS a US Spanish network whose feed is often tagged MEX -
        # it stays on the shared allowlist and must still match.
        candidates = ["(MX) Univision", "MEX: Telemundo", "Univision"]
        results = matcher.match_all_streams(
            "Univision", candidates, alias_map={}, lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "(MX) Univision" in names

    def test_us_lineup_accepts_mx_telemundo(self, matcher):
        candidates = ["MEX: Telemundo", "Telemundo"]
        results = matcher.match_all_streams(
            "Telemundo", candidates, alias_map={}, lineup_country="US",
        )
        names = [r[0] for r in results]
        assert "MEX: Telemundo" in names

    def test_mx_lineup_accepts_us_univision(self, matcher):
        # Shared allowlist is symmetric (keyed by frozenset).
        candidates = ["(US) Univision", "Univision"]
        results = matcher.match_all_streams(
            "Univision", candidates, alias_map={}, lineup_country="MX",
        )
        names = [r[0] for r in results]
        assert "(US) Univision" in names

    def test_shared_allowlist_does_not_leak_other_channels(self, matcher):
        # Being on a US Spanish lineup must NOT make every MX stream match -
        # only the specific shared channel name passes.
        candidates = ["(MX) Las Estrellas"]
        results = matcher.match_all_streams(
            "Las Estrellas", candidates, alias_map={}, lineup_country="US",
        )
        assert results == []

    def test_drops_counter_strict(self, matcher):
        # US lineup, ESPN: US kept, CA + MX + UK dropped = 3 drops (ESPN is not
        # on any shared allowlist).
        matcher.country_filter_drops = 0
        candidates = ["US: ESPN", "(CA) ESPN", "(MX) ESPN", "UK: ESPN"]
        matcher.match_all_streams(
            "ESPN", candidates, alias_map={}, lineup_country="US",
        )
        assert matcher.country_filter_drops == 3
