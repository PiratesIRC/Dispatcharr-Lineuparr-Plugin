"""Regression: lineup channel "Sportsnet Pacific" must not match a regionless
"Sportsnet" stream.

Bug: REGIONAL_PATTERNS strips the full word "pacific" during normalize_name
(while keeping east/west), so "Sportsnet Pacific" normalized to "Sportsnet" and
scored 100% against a plain "Sportsnet" stream. The regional filter's
query_has_pacific check was reading the already-stripped normalized query, so
it never fired. Fix: detect the word "pacific" from the original lineup name.
"""
import pytest

from Lineuparr.fuzzy_matcher import FuzzyMatcher


@pytest.fixture
def matcher():
    return FuzzyMatcher(match_threshold=85)


class TestPacificLineupRejectsRegionlessStream:
    def test_sportsnet_pacific_rejects_plain_sportsnet(self, matcher):
        results = matcher.match_all_streams(
            "Sportsnet Pacific",
            ["Sportsnet", "Sportsnet East", "Sportsnet Pacific", "Sportsnet West"],
            alias_map={},
        )
        matched = {r[0] for r in results}
        assert "Sportsnet" not in matched, f"Regionless stream leaked in: {results}"
        assert "Sportsnet East" not in matched
        assert "Sportsnet West" not in matched
        assert "Sportsnet Pacific" in matched

    def test_sportsnet_pacific_paren_abbrev_still_works(self, matcher):
        """(P) abbreviation path must still work after the fix."""
        results = matcher.match_all_streams(
            "Sportsnet (P)",
            ["Sportsnet", "Sportsnet Pacific"],
            alias_map={},
        )
        matched = {r[0] for r in results}
        assert "Sportsnet" not in matched
        assert "Sportsnet Pacific" in matched


class TestPacificDoesNotBreakOthers:
    def test_regionless_sportsnet_query_still_rejects_pacific(self, matcher):
        """Existing behavior: lineup "Sportsnet" should reject Pacific/West candidates."""
        results = matcher.match_all_streams(
            "Sportsnet",
            ["Sportsnet", "Sportsnet Pacific", "Sportsnet West", "Sportsnet East"],
            alias_map={},
        )
        matched = {r[0] for r in results}
        assert "Sportsnet Pacific" not in matched
        assert "Sportsnet West" not in matched
        assert "Sportsnet" in matched

    def test_east_query_unaffected(self, matcher):
        results = matcher.match_all_streams(
            "Sportsnet East",
            ["Sportsnet East", "Sportsnet Pacific", "Sportsnet West"],
            alias_map={},
        )
        matched = {r[0] for r in results}
        assert matched == {"Sportsnet East"}
