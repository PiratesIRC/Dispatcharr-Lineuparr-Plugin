"""Regression: country-scoped alias overrides (bug-063).

A channel NAME can exist in several markets with different stream-name
variants. CHANNEL_ALIASES is keyed only by name, so a France-only
"TLC" -> "Discovery Science" entry used to be a *duplicate key* that silently
clobbered the US "TLC" -> "TLC US" entry AND leaked into US/UK/NL/CA lineups,
where TLC and Discovery Science are SEPARATE channels.

Fix: such variants live in COUNTRY_ALIASES and are merged by _build_alias_map()
only for the lineup's own country, so they never cross markets.
"""
import ast
import collections

import pytest

from Lineuparr.aliases import CHANNEL_ALIASES, COUNTRY_ALIASES
from Lineuparr.fuzzy_matcher import FuzzyMatcher
from Lineuparr.plugin import Plugin


def _channel_aliases_key_list():
    """Parse aliases.py source and return the literal key list (to catch
    duplicate keys, which a runtime dict silently collapses)."""
    import Lineuparr.aliases as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "CHANNEL_ALIASES":
            return [k.value for k in node.value.keys]
    raise AssertionError("CHANNEL_ALIASES not found")


def _build(country):
    """Invoke the real _build_alias_map for a given lineup country."""
    plugin = Plugin.__new__(Plugin)  # skip heavy __init__; method is self-contained
    settings = {"lineup_file": f"{country}_Test_lineup.json", "custom_aliases": ""}
    return plugin._build_alias_map(settings, logger=__import__("logging").getLogger("t"))


@pytest.fixture
def matcher():
    return FuzzyMatcher(match_threshold=80)


def _matched(matcher, lineup, stream, country, alias_map):
    matcher.precompute_normalizations([stream])
    res = matcher.match_all_streams(lineup, [stream], alias_map, lineup_country=country)
    return stream in {r[0] for r in res}


class TestNoDuplicateAliasKeys:
    def test_channel_aliases_has_no_duplicate_keys(self):
        keys = _channel_aliases_key_list()
        dupes = [k for k, c in collections.Counter(keys).items() if c > 1]
        assert dupes == [], f"duplicate keys silently override each other: {dupes}"

    def test_global_tlc_mtv_are_the_us_entries(self):
        assert CHANNEL_ALIASES["TLC"] == ["TLC", "TLC US"]
        # MTV global entry must remain the US one (not clobbered by the France
        # override); US EPG guide-name variants are appended but the head stays US.
        assert CHANNEL_ALIASES["MTV"][:2] == ["MTV", "MTV US"]
        assert "MTV France" not in CHANNEL_ALIASES["MTV"]

    def test_fr_overrides_exist(self):
        assert "Discovery Science" in COUNTRY_ALIASES["FR"]["TLC"]
        assert "MTV France" in COUNTRY_ALIASES["FR"]["MTV"]


class TestBuildAliasMapIsCountryScoped:
    def test_us_map_excludes_fr_variants(self):
        am = _build("US")
        assert "Discovery Science" not in am["TLC"]
        assert "MTV France" not in am["MTV"]

    def test_fr_map_includes_fr_variants_on_top_of_global(self):
        am = _build("FR")
        assert "Discovery Science" in am["TLC"]
        assert "TLC" in am["TLC"]            # global entry preserved
        assert "MTV France" in am["MTV"]
        assert "MTV" in am["MTV"]

    def test_unknown_country_is_just_the_global_map(self):
        assert _build("ZZ")["TLC"] == CHANNEL_ALIASES["TLC"]


class TestMatcherBehaviourPerCountry:
    def test_us_tlc_does_not_steal_discovery_science(self, matcher):
        am = _build("US")
        assert not _matched(matcher, "TLC", "Discovery Science", "US", am)

    def test_us_science_still_matches_discovery_science(self, matcher):
        am = _build("US")
        assert _matched(matcher, "Science", "Discovery Science", "US", am)

    def test_fr_tlc_matches_discovery_science(self, matcher):
        am = _build("FR")
        assert _matched(matcher, "TLC", "Discovery Science", "FR", am)

    def test_fr_mtv_matches_mtv_france_but_us_does_not(self, matcher):
        assert _matched(matcher, "MTV", "MTV France", "FR", _build("FR"))
        assert not _matched(matcher, "MTV", "MTV France", "US", _build("US"))

    def test_plain_names_still_match_everywhere(self, matcher):
        for cc in ("US", "FR"):
            am = _build(cc)
            assert _matched(matcher, "TLC", "TLC", cc, am)
            assert _matched(matcher, "MTV", "MTV", cc, am)
