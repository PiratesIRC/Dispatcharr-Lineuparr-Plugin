"""Alias maps must follow the channel's country, not the lineup filename's.

Lineuparr already lets a category name carry a country code ("UK| Sports" inside
an AU-NZ-UK lineup), and the matcher receives that per-category code. The alias
map did not follow: `_build_alias_map` merged COUNTRY_ALIASES for the country in
the lineup FILENAME only, so a UK channel sitting in a US lineup was matched with
US aliases.

This is a defect against the shipped per-category feature, so it is fixed on its
own, ahead of the per-channel country prefix that will also depend on it.

The provider returned by `_alias_map_provider` is memoized for one run only, and
these tests pin that: the map depends on the custom_aliases setting and on the
selected lineup's embedded aliases, and the plugin object survives between runs
in a long-lived worker process.
"""
import logging

import pytest
from Lineuparr.aliases import CHANNEL_ALIASES, COUNTRY_ALIASES
from Lineuparr.plugin import Plugin

LOGGER = logging.getLogger("test_country_scoped_alias_map")


def _plugin(lineup=None):
    """A Plugin with __init__ skipped; the alias methods are self-contained."""
    plugin = Plugin.__new__(Plugin)
    plugin._load_lineup = lambda settings, logger: (
        lineup if lineup is not None else {"package": "Test", "categories": {}}
    )
    return plugin


def _country_with_overrides():
    """A country code whose COUNTRY_ALIASES entry overrides a built-in name."""
    for cc, table in COUNTRY_ALIASES.items():
        for name in table:
            if name in CHANNEL_ALIASES:
                return cc, name
    pytest.skip("no COUNTRY_ALIASES entry overrides a built-in name")


class TestExplicitCountry:
    def test_requested_country_aliases_are_merged(self):
        cc, name = _country_with_overrides()
        alias_map = _plugin()._build_alias_map(
            {"lineup_file": "US_Test_lineup.json"}, LOGGER, country=cc)
        for alias in COUNTRY_ALIASES[cc][name]:
            assert alias in alias_map[name]

    def test_other_countries_aliases_are_not_merged(self):
        """The bug-063 guard: one market's variants must not leak into another."""
        cc, name = _country_with_overrides()
        other = next(c for c in COUNTRY_ALIASES if c != cc)
        alias_map = _plugin()._build_alias_map(
            {"lineup_file": "US_Test_lineup.json"}, LOGGER, country=other)
        leaked = [a for a in COUNTRY_ALIASES[cc][name] if a in alias_map.get(name, [])]
        assert not leaked or name in COUNTRY_ALIASES[other]

    def test_no_country_falls_back_to_the_lineup_filename(self):
        cc, name = _country_with_overrides()
        explicit = _plugin()._build_alias_map(
            {"lineup_file": f"{cc}_Test_lineup.json"}, LOGGER, country=cc)
        implied = _plugin()._build_alias_map(
            {"lineup_file": f"{cc}_Test_lineup.json"}, LOGGER)
        assert explicit == implied

    def test_custom_aliases_still_merge_last_for_every_country(self):
        alias_map = _plugin()._build_alias_map(
            {"lineup_file": "US_Test_lineup.json",
             "custom_aliases": '{"CNN": ["My CNN"]}'},
            LOGGER, country="UK")
        assert alias_map["CNN"][-1] == "My CNN"

    def test_embedded_lineup_aliases_apply_for_every_country(self):
        lineup = {"package": "T", "categories": {
            "Local": [{"name": "My9 New York", "aliases": ["WWOR"]}]}}
        alias_map = _plugin(lineup)._build_alias_map(
            {"lineup_file": "US_Test_lineup.json"}, LOGGER, country="UK")
        assert alias_map["My9 New York"] == ["WWOR"]


class TestAliasMapProvider:
    def test_provider_returns_a_distinct_map_per_country(self):
        cc, name = _country_with_overrides()
        other = next(c for c in COUNTRY_ALIASES if c != cc)
        provider = _plugin()._alias_map_provider({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert provider(cc)[name] != provider(other).get(name, [])

    def test_provider_memoizes_within_one_run(self):
        provider = _plugin()._alias_map_provider({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert provider("UK") is provider("UK")

    def test_provider_normalizes_the_country_code(self):
        provider = _plugin()._alias_map_provider({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert provider("uk") is provider("UK")

    def test_provider_accepts_no_country(self):
        """A lineup with plain theme categories passes None for every channel."""
        provider = _plugin()._alias_map_provider({"lineup_file": "US_Test_lineup.json"}, LOGGER)
        assert "CNN" in provider(None)

    def test_a_new_provider_reflects_edited_custom_aliases(self):
        """The plugin object survives between runs, so nothing may be memoized
        across them: an edited custom_aliases setting must take effect."""
        plugin = _plugin()
        first = plugin._alias_map_provider(
            {"lineup_file": "US_Test_lineup.json", "custom_aliases": ""}, LOGGER)
        assert "Edited" not in first("US").get("CNN", [])

        second = plugin._alias_map_provider(
            {"lineup_file": "US_Test_lineup.json",
             "custom_aliases": '{"CNN": ["Edited"]}'}, LOGGER)
        assert "Edited" in second("US")["CNN"]

    def test_every_matching_call_site_uses_the_provider(self):
        """The producer, not just the helper: a matching loop that kept a single
        alias map would pass this file's other tests while shipping the bug."""
        import ast
        import inspect

        import Lineuparr.plugin as plugin_module

        source = inspect.getsource(plugin_module)
        tree = ast.parse(source)

        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "match_all_streams"]
        assert len(calls) == 3, f"expected 3 match_all_streams call sites, found {len(calls)}"
        for call in calls:
            alias_arg = call.args[2]
            assert isinstance(alias_arg, ast.Call), (
                f"line {call.lineno}: alias argument must come from the provider, "
                f"got {ast.dump(alias_arg)[:60]}"
            )
            assert alias_arg.func.id == "alias_map_for", f"line {call.lineno}"
            # The provider must be handed the RESOLVED country variable. A
            # literal (None, "US") would compile and pass every other test in
            # this file while shipping the bug it exists to prevent.
            assert len(alias_arg.args) == 1, f"line {call.lineno}"
            assert isinstance(alias_arg.args[0], ast.Name), (
                f"line {call.lineno}: alias_map_for must receive the resolved "
                f"country variable, not a literal"
            )

        direct = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "_build_alias_map"]
        assert len(direct) == 1, (
            "_build_alias_map must be reached through _alias_map_provider only; "
            f"found {len(direct)} direct calls"
        )

    def test_a_new_provider_reflects_a_changed_lineup_file(self):
        """Two lineups of the same country carry different embedded aliases."""
        plugin = Plugin.__new__(Plugin)
        plugin._load_lineup = lambda settings, logger: {
            "package": "T",
            "categories": {"Local": [{"name": "My9 New York",
                                      "aliases": [settings["lineup_file"]]}]},
        }
        first = plugin._alias_map_provider({"lineup_file": "US_A_lineup.json"}, LOGGER)
        second = plugin._alias_map_provider({"lineup_file": "US_B_lineup.json"}, LOGGER)
        assert first("US")["My9 New York"] == ["US_A_lineup.json"]
        assert second("US")["My9 New York"] == ["US_B_lineup.json"]
