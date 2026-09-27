"""Regression for GitHub issue #30: Category Detail "None - single group
(prefix only, no categories)" created "<prefix>: All" instead of a
prefix-only group name.

Root cause: _apply_category_detail("none") merges every channel under the
sentinel category "All", and _make_group_name treated "All" like any other
category, appending it after the prefix the same way it appends "News" or
"Movies". The setting's own description promises "prefix only, no
categories", so the "All" suffix was a bug.

The fix (in _make_group_name, the single place every caller goes through)
special-cases the sentinel category so the result is the prefix alone. It
also has to handle two things without a setting or a code change from the
maintainer's review of issue #30:

  1. Someone already running this mode has a "<prefix>: All" group in
     Dispatcharr. _migrate_single_group_name renames that existing group
     (preserving its id and channel assignments) instead of leaving it as an
     orphaned duplicate next to a freshly created "<prefix>" group - unless a
     "<prefix>" group already exists too, in which case both are left alone
     and a warning is logged (merging channels between groups is out of
     scope for a silent migration).
  2. A prefix that already ends in a separator (e.g. "DTV-") must not leave
     that separator dangling ("DTV-" not "DTV-All" -> "DTV", not "DTV-").
"""
import logging

import pytest

from Lineuparr import plugin as plugin_module
from Lineuparr.plugin import Plugin, PluginConfig


@pytest.fixture
def plugin():
    return Plugin.__new__(Plugin)


@pytest.fixture
def logger():
    return logging.getLogger("test")


class TestApplyCategoryDetailUsesSharedSentinel:
    def test_none_detail_merges_under_the_shared_sentinel(self, plugin):
        data = {"categories": {"News": [{"name": "CNN"}], "Sports": [{"name": "ESPN"}]}}
        result = plugin._apply_category_detail(data, "none")
        assert list(result["categories"].keys()) == [PluginConfig.SINGLE_GROUP_CATEGORY]
        assert len(result["categories"][PluginConfig.SINGLE_GROUP_CATEGORY]) == 2


class TestMakeGroupNameSingleGroupMode:
    @pytest.mark.parametrize("prefix,expected", [
        ("Spectrum", "Spectrum"),
        ("DIRECTV", "DIRECTV"),
        ("", "All"),
        ("DTV-", "DTV"),
        ("DTV: ", "DTV"),
        ("DTV ", "DTV"),
        ("DTV_", "DTV"),
        ("DTV/", "DTV"),
        ("---", "All"),  # prefix is nothing but separators - falls back
    ])
    def test_single_group_category_is_prefix_only(self, plugin, prefix, expected):
        assert plugin._make_group_name(prefix, PluginConfig.SINGLE_GROUP_CATEGORY) == expected


class TestMakeGroupNameOtherDetailModesUnchanged:
    """The fix must not touch Normal/Simple/Refined category naming."""

    @pytest.mark.parametrize("prefix,category,expected", [
        ("Spectrum", "Movies", "Spectrum: Movies"),
        ("Spectrum", "News", "Spectrum: News"),
        ("DTV-", "News", "DTV-News"),
        ("", "News", "News"),
    ])
    def test_normal_categories_unchanged(self, plugin, prefix, category, expected):
        assert plugin._make_group_name(prefix, category) == expected

    def test_a_real_category_literally_named_all_also_collapses(self, plugin):
        # Accepted edge case, documented on _make_group_name: a genuine
        # lineup category named exactly "All" collapses the same way the
        # sentinel does. Harmless (it already reads as one merged group).
        assert plugin._make_group_name("Spectrum", "All") == "Spectrum"


# --- Migration: renaming a pre-fix "<prefix>: All" group -------------------

class _FakeGroup:
    def __init__(self, name, id):
        self.name = name
        self.id = id
        self.saved = False

    def save(self, update_fields=None):
        self.saved = True


class _FakeQuerySet:
    def __init__(self, items):
        self._items = items

    def first(self):
        return self._items[0] if self._items else None

    def exists(self):
        return bool(self._items)

    def values(self, *fields):
        return [{f: getattr(g, f) for f in fields} for g in self._items]


class _FakeManager:
    def __init__(self, groups):
        self._groups = groups

    def filter(self, name=None):
        return _FakeQuerySet([g for g in self._groups if g.name == name])

    def all(self):
        return _FakeQuerySet(list(self._groups))

    def get_or_create(self, name, defaults=None):
        for g in self._groups:
            if g.name == name:
                return g, False
        new_group = _FakeGroup(name, id=max((g.id for g in self._groups), default=0) + 1)
        self._groups.append(new_group)
        return new_group, True


class _FakeChannelGroup:
    def __init__(self, groups):
        self.objects = _FakeManager(groups)


class TestMigrateSingleGroupName:
    def test_renames_old_group_when_new_name_is_free(self, plugin, logger, monkeypatch):
        old = _FakeGroup("Spectrum: All", id=7)
        fake = _FakeChannelGroup([old])
        monkeypatch.setattr(plugin_module, "ChannelGroup", fake)

        plugin._migrate_single_group_name("Spectrum", dry_run=False, logger=logger)

        assert old.name == "Spectrum"
        assert old.saved is True

    def test_leaves_both_alone_and_warns_when_new_name_already_exists(self, plugin, caplog, monkeypatch):
        old = _FakeGroup("Spectrum: All", id=7)
        new = _FakeGroup("Spectrum", id=9)
        fake = _FakeChannelGroup([old, new])
        monkeypatch.setattr(plugin_module, "ChannelGroup", fake)

        with caplog.at_level(logging.WARNING):
            plugin._migrate_single_group_name("Spectrum", dry_run=False, logger=logging.getLogger("test"))

        assert old.name == "Spectrum: All"  # unchanged
        assert old.saved is False
        assert any("Spectrum: All" in r.message and "Spectrum" in r.message for r in caplog.records)

    def test_dry_run_does_not_rename(self, plugin, logger, monkeypatch):
        old = _FakeGroup("Spectrum: All", id=7)
        fake = _FakeChannelGroup([old])
        monkeypatch.setattr(plugin_module, "ChannelGroup", fake)

        plugin._migrate_single_group_name("Spectrum", dry_run=True, logger=logger)

        assert old.name == "Spectrum: All"
        assert old.saved is False

    def test_no_old_group_is_a_silent_no_op(self, plugin, logger, monkeypatch):
        fake = _FakeChannelGroup([])
        monkeypatch.setattr(plugin_module, "ChannelGroup", fake)

        # Must not raise.
        plugin._migrate_single_group_name("Spectrum", dry_run=False, logger=logger)

    def test_no_prefix_is_a_no_op_without_touching_the_database(self, plugin, logger, monkeypatch):
        # Both old and new names are "All" with no prefix - nothing to migrate,
        # and the method should return before ever calling ChannelGroup.objects.
        class _ExplodingManager:
            def filter(self, **kwargs):
                raise AssertionError("should not query the database")

        class _ExplodingChannelGroup:
            objects = _ExplodingManager()

        monkeypatch.setattr(plugin_module, "ChannelGroup", _ExplodingChannelGroup)

        plugin._migrate_single_group_name("", dry_run=False, logger=logger)

    def test_renames_with_a_separator_terminated_prefix(self, plugin, logger, monkeypatch):
        old = _FakeGroup("DTV-All", id=3)
        fake = _FakeChannelGroup([old])
        monkeypatch.setattr(plugin_module, "ChannelGroup", fake)

        plugin._migrate_single_group_name("DTV-", dry_run=False, logger=logger)

        assert old.name == "DTV"
        assert old.saved is True


class TestSyncChannelsAlsoMigrates:
    """Regression from code review on this PR: the migration must run from
    the Sync Channels button too, not only Full Sync. There is no Sync
    Groups button in the UI, so Sync Channels (_do_sync_channels) is, in
    practice, the only path some installs ever take. Before this fix it
    built its own group lookup independently of _sync_groups and never
    called the migration, so pressing Sync Channels against an existing
    "<prefix>: All" group created a second "<prefix>" group instead of
    renaming the first - leaving the old group's channels orphaned.
    """

    def test_sync_channels_renames_the_old_group_instead_of_duplicating_it(
        self, plugin, logger, monkeypatch
    ):
        old = _FakeGroup("Spectrum: All", id=7)
        fake = _FakeChannelGroup([old])
        monkeypatch.setattr(plugin_module, "ChannelGroup", fake)

        # No channels in the lineup: this test is about which group exists
        # afterward, not channel assignment, so Channel.objects is never
        # touched and does not need its own fake.
        monkeypatch.setattr(
            plugin, "_load_lineup",
            lambda settings, logger: {"categories": {PluginConfig.SINGLE_GROUP_CATEGORY: []}},
        )
        monkeypatch.setattr(plugin, "_get_group_prefix", lambda settings, lineup: "Spectrum")
        plugin._suppress_refresh = False  # Plugin.__new__ skips __init__

        plugin._do_sync_channels({}, logger)

        names = [g.name for g in fake.objects._groups]
        assert names == ["Spectrum"], f"expected the old group renamed in place, got {names}"
        assert old.saved is True
