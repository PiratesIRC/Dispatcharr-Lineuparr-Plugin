"""Regression: alias fuzzy matching must not merge ABC/BBC/CBC/NBC-style siblings.

Bug: alias "ABC News" (from ABC News Live's alias list) scored 93% against stream
"BBC News" - 1 Levenshtein edit over 16 chars - and passed the token-overlap
guard because "news" is shared and the 3-char call sign was below the guard's
4-char token floor. Fix: alias fuzzy matches require majority token overlap, so
the differing call sign forces rejection.
"""
import pytest

from Lineuparr.fuzzy_matcher import FuzzyMatcher


@pytest.fixture
def matcher():
    m = FuzzyMatcher(match_threshold=85)
    return m


@pytest.fixture
def alias_map():
    return {
        "ABC News Live": ["ABC News", "ABC News Live"],
        "BBC News": ["BBC News", "BBC World News"],
        "CBC News Network": ["CBC News", "CBC News Network"],
    }


class TestCallsignCrossMatchRejected:
    """ABC/BBC/CBC News variants must not cross-match via alias fuzzy."""

    def test_abc_query_does_not_match_bbc_stream(self, matcher, alias_map):
        results = matcher.match_all_streams(
            "ABC News Live", ["BBC News", "BBC News SD", "BBC News HD"], alias_map
        )
        assert results == [], f"Expected no matches, got {results}"

    def test_abc_query_does_not_match_cbc_stream(self, matcher, alias_map):
        results = matcher.match_all_streams(
            "ABC News Live", ["CBC News", "CBC News Network"], alias_map
        )
        assert results == [], f"Expected no matches, got {results}"

    def test_bbc_query_does_not_match_abc_stream(self, matcher, alias_map):
        results = matcher.match_all_streams(
            "BBC News", ["ABC News", "ABC News Live"], alias_map
        )
        assert results == [], f"Expected no matches, got {results}"


class TestValidAliasMatchesStillWork:
    """Fix must not regress legitimate alias matches."""

    def test_abc_matches_abc_variants(self, matcher, alias_map):
        results = matcher.match_all_streams(
            "ABC News Live", ["ABC News", "ABC News Live", "ABC News HD"], alias_map
        )
        matched_names = {r[0] for r in results}
        assert "ABC News" in matched_names
        assert "ABC News Live" in matched_names
        assert "ABC News HD" in matched_names

    def test_bbc_matches_bbc_world_news(self, matcher, alias_map):
        results = matcher.match_all_streams(
            "BBC News", ["BBC World News"], alias_map
        )
        assert len(results) == 1
        assert results[0][0] == "BBC World News"
