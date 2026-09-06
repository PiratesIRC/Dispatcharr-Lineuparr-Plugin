"""Embedded per-channel aliases in lineup JSON, plus alias-anchored callsign matching.

Two related behaviours, both requested in PR #18:

1. A lineup JSON channel entry may carry an "aliases" array. Those aliases are
   merged into the alias map alongside the built-in table, the country-scoped
   overrides and the user's Custom Channel Aliases setting (which still merges
   last).

2. When one of a channel's aliases IS a US broadcast callsign, a stream whose
   name carries that callsign matches even though the lineup channel name does
   not contain it. The core callsign anchor in match_all_streams only fires when
   the LINEUP name carries a callsign, so "My9 New York" could never reach a
   stream called "US: MY 9 WWOR NEW YORK" before this.

The alias side deliberately reuses the core callsign denylist, so ordinary
K/W words that happen to have callsign shape (KIDS, WORLD, WOMEN, WEST, KISS,
WWE) never become anchors.
"""
import logging

import pytest
from Lineuparr.fuzzy_matcher import FuzzyMatcher
from Lineuparr.plugin import Plugin

LOGGER = logging.getLogger("test_embedded_lineup_aliases")

WWOR_ALIASES = ["WWOR", "WWOR-TV", "WWOR-DT", "WWORDT", "MY9", "MY NETWORK TV NEW YORK"]


@pytest.fixture
def matcher():
    return FuzzyMatcher(match_threshold=80)


class TestAliasCallsignExtraction:
    """Only an alias that is entirely a callsign becomes an anchor."""

    @pytest.mark.parametrize("alias,expected", [
        ("WWOR", "WWOR"),
        ("WWOR-TV", "WWOR"),
        ("WWOR-DT", "WWOR"),
        ("WWORDT", "WWOR"),
        ("wwor", "WWOR"),
        ("WABC-TV", "WABC"),
        ("KTLA", "KTLA"),
    ])
    def test_real_callsigns_extract(self, matcher, alias, expected):
        assert matcher._alias_callsign(alias) == expected

    @pytest.mark.parametrize("alias", [
        # Denylisted common words that have callsign shape.
        "KIDS", "WORLD", "WOMEN", "WEST", "KISS", "WWE", "WITH", "KING",
        # Not callsign-shaped at all.
        "HBO", "MAX", "CNN", "MY9", "KUWAIT", "WLLOHD",
        # A phrase containing a K/W word is not an anchor, even when the phrase
        # STARTS with a real callsign: "WWOR New York" is a display name, and
        # anchoring on part of a phrase is how "WGN America" would start
        # claiming every stream carrying WGN.
        "MY NETWORK TV NEW YORK", "WORLD FISHING NETWORK",
        "WWOR NEW YORK", "WGN America", "KTLA 5 Los Angeles",
        # Junk.
        "", "   ", None,
    ])
    def test_non_callsigns_rejected(self, matcher, alias):
        assert matcher._alias_callsign(alias) is None


class TestAliasCallsignMatching:
    """A callsign alias reaches streams that embed the callsign as a whole token."""

    @pytest.mark.parametrize("stream", [
        "CITY: MNT WWOR NEW YORK",
        "GO: MNT-WWOR",
        "US| MY 9 SECAUCUS NY (WWOR)",
        "US: MY 9 WWOR NEW YORK",
        "US: WWOR-TV NEW YORK HD",
    ])
    def test_callsign_alias_matches_embedded_callsign(self, matcher, stream):
        results = matcher.alias_match("My9 New York", [stream], {"My9 New York": WWOR_ALIASES})
        assert [r[0] for r in results] == [stream], f"{stream} should match via callsign alias"
        assert results[0][2] == "alias-callsign"

    def test_stream_that_is_only_the_callsign_matches_as_a_plain_alias(self):
        """"US: WWORDT" normalizes to the alias itself, so the existing exact
        alias path claims it first at 100. The callsign rescue is for names the
        exact path cannot reach."""
        matcher = FuzzyMatcher(match_threshold=80)
        results = matcher.alias_match("My9 New York", ["US: WWORDT"], {"My9 New York": WWOR_ALIASES})
        assert results == [("US: WWORDT", 100, "alias")]

    @pytest.mark.parametrize("stream", [
        "WWORLD NEWS",            # partial word, not a whole token
        "US: SWWOR TV",           # partial word, leading letter
        "US: WPIX NEW YORK",      # a different station
        "US: MY 9 NEW JERSEY",    # no callsign at all
    ])
    def test_unrelated_streams_rejected(self, matcher, stream):
        results = matcher.alias_match("My9 New York", [stream], {"My9 New York": WWOR_ALIASES})
        assert results == [], f"{stream} must not match via callsign alias"

    def test_exact_alias_still_outranks_callsign_rescue(self, matcher):
        streams = ["US: MY 9 WWOR NEW YORK", "MY9"]
        results = matcher.alias_match("My9 New York", streams, {"My9 New York": WWOR_ALIASES})
        assert results[0] == ("MY9", 100, "alias")

    def test_denylisted_alias_does_not_anchor(self, matcher):
        """An alias of KIDS must not pull in every stream containing "kids"."""
        streams = ["US: KIDS ZONE HD", "US: DISNEY KIDS"]
        results = matcher.alias_match("Nick Jr", streams, {"Nick Jr": ["KIDS"]})
        assert results == []

    def test_wrong_country_still_filtered(self, matcher):
        results = matcher.match_all_streams(
            "My9 New York", ["UK: WWOR NEW YORK"], {"My9 New York": WWOR_ALIASES},
            lineup_country="US",
        )
        assert results == []

    def test_wrong_region_still_filtered(self, matcher):
        """A region-less lineup channel drops a West feed when an East-or-neutral
        alternative exists. The core keeps a sole West match rather than return
        nothing, so the candidate list has to hold both to exercise the filter."""
        results = matcher.match_all_streams(
            "My9 New York",
            ["US: WWOR NEW YORK WEST", "US: WWOR NEW YORK"],
            {"My9 New York": WWOR_ALIASES},
            lineup_country="US",
        )
        assert [r[0] for r in results] == ["US: WWOR NEW YORK"]

    def test_channel_without_callsign_alias_is_untouched(self, matcher):
        """No callsign alias means no extra work and no extra matches."""
        results = matcher.alias_match("Food Network", ["US: FOOD NETWORK WEST HD"],
                                      {"Food Network": ["Food", "FoodNetwork"]})
        assert all(r[2] != "alias-callsign" for r in results)


def _plugin_with_lineup(lineup):
    """A Plugin whose _load_lineup returns the supplied dict.

    __init__ is skipped: _build_alias_map is self-contained, and the real
    __init__ arms plugin machinery that has no place in a unit test.
    """
    plugin = Plugin.__new__(Plugin)
    plugin._load_lineup = lambda settings, logger: lineup
    return plugin


def _lineup(channels):
    return {"package": "Test", "categories": {"Local": channels}}


class TestEmbeddedAliasMerge:
    def test_embedded_aliases_are_merged(self):
        plugin = _plugin_with_lineup(_lineup([
            {"name": "My9 New York", "number": 509, "aliases": ["WWOR", "MY9"]},
        ]))
        alias_map = plugin._build_alias_map({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert alias_map["My9 New York"] == ["WWOR", "MY9"]

    def test_embedded_aliases_extend_builtin_entry_without_dropping_it(self):
        plugin = _plugin_with_lineup(_lineup([
            {"name": "CNN", "aliases": ["CNN USA"]},
        ]))
        alias_map = plugin._build_alias_map({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert "CNN USA" in alias_map["CNN"]
        assert len(alias_map["CNN"]) > 1, "built-in CNN aliases must survive the merge"

    def test_custom_aliases_still_merge_last(self):
        plugin = _plugin_with_lineup(_lineup([
            {"name": "My9 New York", "aliases": ["WWOR"]},
        ]))
        alias_map = plugin._build_alias_map(
            {"lineup_file": "US_Test_lineup.json",
             "custom_aliases": '{"My9 New York": ["My Nine"]}'},
            LOGGER,
        )
        assert alias_map["My9 New York"] == ["WWOR", "My Nine"]

    def test_duplicates_are_collapsed(self):
        plugin = _plugin_with_lineup(_lineup([
            {"name": "My9 New York", "aliases": ["WWOR", "WWOR", " WWOR "]},
        ]))
        alias_map = plugin._build_alias_map({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert alias_map["My9 New York"] == ["WWOR"]

    def test_string_alias_is_accepted(self):
        plugin = _plugin_with_lineup(_lineup([
            {"name": "My9 New York", "aliases": "WWOR"},
        ]))
        alias_map = plugin._build_alias_map({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert alias_map["My9 New York"] == ["WWOR"]

    @pytest.mark.parametrize("entry", [
        {"name": "My9 New York", "aliases": 5},
        {"name": "My9 New York", "aliases": {"a": "b"}},
        {"name": "My9 New York", "aliases": []},
        {"name": "My9 New York", "aliases": ["", "  "]},
        {"name": "", "aliases": ["WWOR"]},
        {"aliases": ["WWOR"]},
        {"name": "My9 New York"},
        "not a dict",
    ])
    def test_malformed_entries_are_ignored(self, entry):
        plugin = _plugin_with_lineup(_lineup([entry]))
        alias_map = plugin._build_alias_map({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert "My9 New York" not in alias_map

    def test_unreadable_lineup_does_not_break_the_alias_map(self):
        plugin = Plugin.__new__(Plugin)

        def _boom(settings, logger):
            raise FileNotFoundError("no such lineup")

        plugin._load_lineup = _boom
        alias_map = plugin._build_alias_map({"lineup_file": "US_Missing_lineup.json"}, LOGGER)
        assert "CNN" in alias_map, "built-in aliases must survive a lineup load failure"

    def test_shipped_lineup_without_aliases_is_unchanged(self):
        """Uses the real loader against a real shipped lineup file."""
        plugin = Plugin.__new__(Plugin)
        plugin._lineup_cache = None
        plugin._lineup_cache_file = None
        settings = {"lineup_file": "US_Verizon-FIOS_lineup.json", "category_detail": "normal"}
        with_lineup = plugin._build_alias_map(settings, LOGGER)

        bare = Plugin.__new__(Plugin)
        bare._load_lineup = lambda settings, logger: _lineup([])
        assert with_lineup == bare._build_alias_map(settings, LOGGER)
