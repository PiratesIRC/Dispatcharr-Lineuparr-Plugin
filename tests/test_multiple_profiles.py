"""The Channel Profile setting accepts more than one profile.

A user asked to have Lineuparr enable its channels in two profiles at once
(2026-11-01). The resolver `_resolve_channel_profiles` already split the value on
commas, but the settings form declared the field as a single-choice `select`, so
only one name could ever reach it. Dispatcharr's plugin form has no multi-select
type (measured 2026-11-01 by reading the compiled PluginCard component in the
running container: it renders boolean, number, select, text and info, with a
plain text input as the default), so the field is now a free-text `string`, the
same shape as EPG Sources for Matching.

Values saved by the old dropdown were a single profile name or `_none`; both must
keep working after the change.
"""
import logging

import pytest

import Lineuparr.plugin as plugin_module
from Lineuparr.plugin import Plugin


LOGGER = logging.getLogger("test")

PROFILES = [
    {"id": 1, "name": "Living Room"},
    {"id": 2, "name": "Kids"},
    {"id": 3, "name": "Bedroom"},
]


class _FakeQuerySet:
    def __init__(self, rows):
        self._rows = rows

    def values(self, *fields):
        return [{k: r[k] for k in fields} for r in self._rows]


class _FakeManager:
    def all(self):
        return _FakeQuerySet(PROFILES)


class _FakeChannelProfile:
    objects = _FakeManager()


@pytest.fixture
def fake_profiles(monkeypatch):
    monkeypatch.setattr(plugin_module, "ChannelProfile", _FakeChannelProfile)


def _profile_field():
    fields = Plugin.__new__(Plugin).fields
    return next(f for f in fields if f.get("id") == "channel_profiles")


def test_profile_field_is_free_text():
    field = _profile_field()
    assert field["type"] == "string"
    assert "options" not in field


def test_profile_field_default_means_none():
    assert _profile_field()["default"] == ""


def test_profile_field_help_explains_commas():
    help_text = _profile_field()["help_text"]
    assert "comma" in help_text.lower()


def test_resolves_several_profiles_in_order(fake_profiles):
    p = Plugin()
    resolved = p._resolve_channel_profiles(
        {"channel_profiles": "Kids, living room"}, LOGGER)
    assert [r["id"] for r in resolved] == [2, 1]


def test_old_single_dropdown_value_still_resolves(fake_profiles):
    p = Plugin()
    resolved = p._resolve_channel_profiles({"channel_profiles": "Bedroom"}, LOGGER)
    assert [r["id"] for r in resolved] == [3]


@pytest.mark.parametrize("value", ["", "_none", None, "  ", " , "])
def test_blank_or_old_none_value_resolves_to_nothing(fake_profiles, value):
    p = Plugin()
    assert p._resolve_channel_profiles({"channel_profiles": value}, LOGGER) == []


def test_unknown_name_is_skipped_and_others_kept(fake_profiles):
    p = Plugin()
    resolved = p._resolve_channel_profiles(
        {"channel_profiles": "Kids, Garage"}, LOGGER)
    assert [r["id"] for r in resolved] == [2]


def test_duplicate_name_is_resolved_once(fake_profiles):
    p = Plugin()
    resolved = p._resolve_channel_profiles(
        {"channel_profiles": "Kids, kids"}, LOGGER)
    assert [r["id"] for r in resolved] == [2]


def test_enable_writes_to_every_resolved_profile(monkeypatch):
    """Each resolved profile gets its own membership rows."""
    created_for = []
    enabled_for = []

    class _Filter:
        def __init__(self, pid):
            self.pid = pid

        def values_list(self, *a, **k):
            return []

        def update(self, **kw):
            enabled_for.append(self.pid)
            return 0

    class _MembershipManager:
        def filter(self, channel_profile_id=None, **kw):
            return _Filter(channel_profile_id)

        def bulk_create(self, rows, **kw):
            created_for.extend({r.channel_profile_id for r in rows})

    class _FakeMembership:
        objects = _MembershipManager()

        def __init__(self, channel_profile_id, channel_id, enabled):
            self.channel_profile_id = channel_profile_id
            self.channel_id = channel_id
            self.enabled = enabled

    monkeypatch.setattr(plugin_module, "ChannelProfile", _FakeChannelProfile)
    monkeypatch.setattr(plugin_module, "ChannelProfileMembership", _FakeMembership)

    p = Plugin()
    p._enable_channels_in_profiles(
        [10, 11], {"channel_profiles": "Living Room, Kids"}, LOGGER)
    assert sorted(set(created_for)) == [1, 2]
    assert sorted(enabled_for) == [1, 2]
