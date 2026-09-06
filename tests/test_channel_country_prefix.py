"""A channel name may carry its own country: "UK_CNN" inside a US lineup.

Lineuparr resolves a channel's country from the category name, falling back to
the lineup filename. Neither can mark an individual channel, so a US lineup
carrying a handful of foreign channels had to give up country filtering
entirely by selecting a combined multi-country lineup.

A name of the form CC_<name>, where CC is a recognized country code, is rewritten
once at lineup load time into a stripped name plus an internal country key. The
name Dispatcharr creates and the string the matcher normalizes are both the
stripped form, so no other code has to know the prefix exists.
"""
import json
import logging
import os

import pytest
from Lineuparr.fuzzy_matcher import detect_name_country_prefix
from Lineuparr.plugin import Plugin

LOGGER = logging.getLogger("test_channel_country_prefix")

import Lineuparr.plugin as _plugin_module

PLUGIN_DIR = os.path.dirname(os.path.abspath(_plugin_module.__file__))


class TestPrefixDetection:
    @pytest.mark.parametrize("name,expected", [
        ("UK_CNN", ("UK", "CNN")),
        ("uk_cnn", ("UK", "cnn")),
        ("Uk_CNN", ("UK", "CNN")),
        ("CA_CBC News", ("CA", "CBC News")),
        ("FR_TF1", ("FR", "TF1")),
        ("UK_ _CNN", ("UK", "_CNN")),
    ])
    def test_recognized_prefix_is_split_off(self, name, expected):
        assert detect_name_country_prefix(name) == expected

    @pytest.mark.parametrize("name", [
        "CNN",                # no prefix at all
        "XX_Foo",             # two letters, not a country code
        "My_Channel",         # ordinary name containing an underscore
        "MTV_Live",           # ditto
        "U_CNN",              # one letter
        "USA_Network",        # three letters
        "UK-CNN",             # hyphen is not the prefix form
        "UK CNN",             # space is not the prefix form
        "UK_",                # nothing left after the prefix
        "UK_   ",             # nothing but whitespace left
        "",
        None,
    ])
    def test_everything_else_is_left_alone(self, name):
        assert detect_name_country_prefix(name) == (None, name)


class TestEntryCountryResolution:
    """Channel country beats category country beats lineup filename country."""

    def test_channel_prefix_wins_over_category(self):
        entry = {"name": "CNN", "_country": "UK"}
        assert Plugin._resolve_entry_country(entry, "US", LOGGER) == "UK"

    def test_category_used_when_the_channel_has_none(self):
        assert Plugin._resolve_entry_country({"name": "CNN"}, "US", LOGGER) == "US"

    def test_none_when_neither_is_present(self):
        assert Plugin._resolve_entry_country({"name": "CNN"}, None, LOGGER) is None

    def test_channel_country_used_when_the_category_has_none(self):
        entry = {"name": "CNN", "_country": "UK"}
        assert Plugin._resolve_entry_country(entry, None, LOGGER) == "UK"

    def test_the_override_is_logged(self, caplog):
        """The category mechanism logs its own override; a stronger one must
        too, or a non-matching channel becomes guesswork."""
        entry = {"name": "CNN", "_country": "UK"}
        with caplog.at_level(logging.INFO):
            Plugin._resolve_entry_country(entry, "US", LOGGER)
        assert any("UK" in r.message and "CNN" in r.message for r in caplog.records)

    def test_no_log_when_nothing_is_overridden(self, caplog):
        with caplog.at_level(logging.INFO):
            Plugin._resolve_entry_country({"name": "CNN"}, "US", LOGGER)
        assert not caplog.records


@pytest.fixture
def lineup_file():
    """Write a lineup into the plugin directory and remove it afterwards.

    _load_lineup resolves paths against the package directory and refuses
    anything outside it, so the file has to live there to test the real loader.
    """
    path = os.path.join(PLUGIN_DIR, "US_PrefixTest_lineup.json")
    data = {
        "package": "Prefix Test",
        "categories": {
            "News": [
                {"name": "UK_CNN", "number": 501},
                {"name": "CNN", "number": 202},
                {"name": "XX_Foo", "number": 503},
            ],
        },
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    try:
        yield "US_PrefixTest_lineup.json"
    finally:
        os.remove(path)


class TestLoadTimeRewrite:
    def _load(self, lineup_file, detail="normal"):
        plugin = Plugin.__new__(Plugin)
        plugin._lineup_cache = None
        plugin._lineup_cache_file = None
        return plugin._load_lineup(
            {"lineup_file": lineup_file, "category_detail": detail}, LOGGER)

    def test_prefixed_channel_is_stripped_and_marked(self, lineup_file):
        news = self._load(lineup_file)["categories"]["News"]
        entry = next(e for e in news if e["number"] == 501)
        assert entry["name"] == "CNN"
        assert entry["_country"] == "UK"

    def test_unprefixed_channel_is_untouched(self, lineup_file):
        news = self._load(lineup_file)["categories"]["News"]
        entry = next(e for e in news if e["number"] == 202)
        assert entry["name"] == "CNN"
        assert "_country" not in entry

    def test_unrecognized_prefix_stays_part_of_the_name(self, lineup_file):
        news = self._load(lineup_file)["categories"]["News"]
        entry = next(e for e in news if e["number"] == 503)
        assert entry["name"] == "XX_Foo"
        assert "_country" not in entry

    @pytest.mark.parametrize("detail", ["normal", "none", "simple", "refined"])
    def test_rewrite_survives_every_category_detail_level(self, lineup_file, detail):
        data = self._load(lineup_file, detail)
        names = {e["name"] for cat in data["categories"].values() for e in cat}
        assert "UK_CNN" not in names
        assert "CNN" in names

    def test_shipped_lineups_are_unaffected(self):
        """Measured before implementing: no shipped channel name contains an
        underscore at all, so nothing shipped may be rewritten."""
        data = self._load("US_Verizon-FIOS_lineup.json")
        assert not [e for cat in data["categories"].values()
                    for e in cat if "_country" in e]


class TestMatchingLoopWiring:
    """The producer, not just the helper. A loop still resolving country once
    per category would pass every other test in this file."""

    def _tree(self):
        import ast
        import inspect

        import Lineuparr.plugin as plugin_module
        return ast, ast.parse(inspect.getsource(plugin_module))

    def test_every_match_call_site_uses_the_entry_level_country(self):
        ast, tree = self._tree()
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "match_all_streams"]
        assert len(calls) == 3
        for call in calls:
            alias_arg = call.args[2]
            assert isinstance(alias_arg, ast.Call)
            assert alias_arg.func.id == "alias_map_for", f"line {call.lineno}"
            assert alias_arg.args[0].id == "entry_cc", (
                f"line {call.lineno}: aliases must follow the channel's own "
                f"country, not the category's"
            )

    def test_epg_source_preference_uses_the_entry_level_country(self):
        """_pick_epg_by_country sits inside the channel loop but read the
        category variable, so it is the one that gets missed."""
        ast, tree = self._tree()
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_pick_epg_by_country"]
        assert calls, "no _pick_epg_by_country call found"
        for call in calls:
            assert call.args[1].id == "entry_cc", f"line {call.lineno}"

    def test_resolution_runs_inside_the_channel_loop(self):
        ast, tree = self._tree()
        assigns = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Assign)
                   and isinstance(n.targets[0], ast.Name)
                   and n.targets[0].id == "entry_cc"
                   and isinstance(n.value, ast.Call)
                   and getattr(n.value.func, "attr", None) == "_resolve_entry_country"]
        assert len(assigns) == 3, (
            f"expected one per-entry country resolution in each of the three "
            f"matching loops, found {len(assigns)}"
        )
