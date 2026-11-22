"""Built-in aliases for four channels that a run missed under the 95 threshold.

ESPN8: The Ocho, Yahoo Finance, ITV (ITV1) and Great! Movies. The ITV entry must
not pull the timeshift feeds: "ITV +1" and "ITV1 +1" are separate channels, so
the bare number forms ("ITV1", "ITV 1") may match but the +1 streams may not.
"""
import pytest

from Lineuparr.aliases import CHANNEL_ALIASES
from Lineuparr.fuzzy_matcher import FuzzyMatcher


@pytest.fixture
def matcher():
    return FuzzyMatcher(match_threshold=95)


@pytest.mark.parametrize("channel,stream", [
    ("ESPN8: The Ocho", "US: ESPN8 The Ocho"),
    ("ESPN8: The Ocho", "US: ESPN 8 The Ocho HD"),
    ("ESPN8: The Ocho", "US: ESPN Ocho"),
    ("Yahoo Finance", "US: Yahoo Finance Live"),
    ("Great! Movies", "US: Great Movies HD"),
    ("ITV", "UK: ITV1 HD"),
    ("ITV", "UK: ITV1"),
    ("ITV", "UK: ITV 1"),
])
def test_the_new_alias_reaches_its_stream(matcher, channel, stream):
    results = matcher.match_all_streams(channel, [stream], CHANNEL_ALIASES)
    assert [r[0] for r in results] == [stream]


@pytest.mark.parametrize("stream", [
    "UK: ITV +1",
    "UK: ITV1 +1",
    "UK: ITV +1 HD",
    "UK: ITV1 London +1",
    "UK: ITV2",
])
def test_the_itv_alias_does_not_pull_a_timeshift_or_another_channel(matcher, stream):
    results = matcher.match_all_streams("ITV", [stream], CHANNEL_ALIASES)
    assert results == []


def test_the_great_movies_timeshift_is_not_pulled(matcher):
    results = matcher.match_all_streams("Great! Movies", ["US: Great Movies +1"], CHANNEL_ALIASES)
    assert results == []


def test_the_new_entries_are_keyed_by_the_lineup_names():
    assert "ESPN8: The Ocho" in CHANNEL_ALIASES
    assert "Yahoo Finance" in CHANNEL_ALIASES
    assert "ITV" in CHANNEL_ALIASES
    assert "Great! Movies" in CHANNEL_ALIASES
    assert "ITV +1" not in CHANNEL_ALIASES
