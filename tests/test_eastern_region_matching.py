"""Regression: streams tagged "Eastern"/"East" must attach to a regionless
lineup channel.

Bug (user-reported): a regionless lineup channel like "Food Network" or "ESPN"
failed to match streams named "Food Network Eastern" / "ESPN East" because
normalize_name preserved the bare region word, dragging the fuzzy score below
threshold. The region post-filter would have accepted the stream (regionless
defaults to East) but no candidate ever reached it.

Fix: strip East/Eastern/West (not the "Western"/"Westerns" genre) during
normalization for SCORING; region correctness is still enforced by the
post-match region filter, which reads the original un-normalized names.
"""
import pytest

from Lineuparr.fuzzy_matcher import FuzzyMatcher


@pytest.fixture
def matcher():
    return FuzzyMatcher(match_threshold=80)


def _matched(matcher, lineup, streams, country=None):
    matcher.precompute_normalizations(streams)
    return {r[0] for r in matcher.match_all_streams(
        lineup, streams, alias_map={}, lineup_country=country)}


class TestRegionlessMatchesEastern:
    def test_food_network_matches_eastern(self, matcher):
        assert "Food Network Eastern" in _matched(
            matcher, "Food Network", ["Food Network Eastern"], "US")

    def test_food_network_matches_east(self, matcher):
        assert "Food Network East" in _matched(
            matcher, "Food Network", ["Food Network East"], "US")

    def test_food_network_matches_eastern_paren(self, matcher):
        assert "Food Network (Eastern)" in _matched(
            matcher, "Food Network", ["Food Network (Eastern)"], "US")

    def test_espn_matches_eastern_with_provider_prefix(self, matcher):
        # "US Food Network Eastern" - bare provider prefix + region word.
        assert "US ESPN Eastern" in _matched(
            matcher, "ESPN", ["US ESPN Eastern"], "US")

    def test_regionless_still_rejects_west_and_pacific(self, matcher):
        # Eastern/East accepted, but West/Pacific still rejected for regionless.
        m = _matched(matcher, "Food Network",
                     ["Food Network East", "Food Network West", "Food Network Pacific"], "US")
        assert "Food Network East" in m
        assert "Food Network West" not in m
        assert "Food Network Pacific" not in m


class TestWesternGenreNotTreatedAsRegion:
    def test_western_channel_not_stripped_as_region(self, matcher):
        # "The Western Channel" is a genre, not a West-region feed. It must NOT
        # be treated as a West channel (which would reject regionless streams),
        # and must still match its own stream.
        assert "Western Channel" in _matched(
            matcher, "Western Channel", ["Western Channel"], "US")

    def test_westerns_stream_matches_western_lineup(self, matcher):
        assert "Grit Westerns" in _matched(
            matcher, "Grit Westerns", ["Grit Westerns"], "US")


class TestEastWestChannelsStillDistinguished:
    """The whole point of the original East/West design must still hold:
    an East channel rejects West streams and vice-versa, via the post-filter."""

    def test_east_channel_rejects_west_stream(self, matcher):
        m = _matched(matcher, "HBO East", ["HBO East", "HBO West"], "US")
        assert "HBO East" in m
        assert "HBO West" not in m

    def test_west_channel_rejects_east_keeps_pacific(self, matcher):
        m = _matched(matcher, "HBO West",
                     ["HBO East", "HBO West", "HBO Pacific"], "US")
        assert "HBO West" in m
        assert "HBO Pacific" in m   # Pacific is the West-coast feed
        assert "HBO East" not in m

    def test_east_channel_matches_regionless_stream(self, matcher):
        # Regionless stream defaults to East, so an East channel accepts it.
        m = _matched(matcher, "HBO East", ["HBO"], "US")
        assert "HBO" in m

    def test_two_zoned_siblings_resolve_to_their_own_region(self, matcher):
        # A lineup with both (E) and (W) variants: each must take its own feed.
        streams = ["Disney Channel East", "Disney Channel West"]
        east = _matched(matcher, "Disney Channel (E)", streams, "US")
        west = _matched(matcher, "Disney Channel (W)", streams, "US")
        assert "Disney Channel East" in east and "Disney Channel West" not in east
        assert "Disney Channel West" in west and "Disney Channel East" not in west
