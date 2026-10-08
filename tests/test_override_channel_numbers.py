"""Channel numbers held by a ChannelOverride row must count as taken.

Dispatcharr 0.32.0 stores the number an operator assigns to an AUTO-SYNCED
channel in ChannelOverride.channel_number and leaves Channel.channel_number at
the provider value (apps/channels/managers.py apply_effective_channel_numbers).
Dispatcharr's own allocator treats both columns as taken
(managers.py max_reserved_channel_number, channel_number_is_reserved). If
Lineuparr reads only Channel.channel_number, it can hand a new channel the
number an auto-synced channel already shows.

The override column exists on 0.31.0 as well, so reading it is safe there. On
a Dispatcharr old enough to have no ChannelOverride model, the import fails and
numbering must behave exactly as before.
"""
import logging
import sys
from unittest.mock import MagicMock

import pytest

import Lineuparr.plugin as plugin_module
from Lineuparr.plugin import Plugin


def _values_list_model(numbers):
    """A stand-in model whose objects chain returns the given numbers.

    filter() honours channel_number__isnull the way the ORM does, so a query
    that asks for the wrong rows gets the wrong rows.
    """
    model = MagicMock()
    model.objects.values_list.return_value = list(numbers)

    def _filter(**kwargs):
        rows = list(numbers)
        if "channel_number__isnull" in kwargs:
            want_null = kwargs["channel_number__isnull"]
            rows = [n for n in rows if (n is None) == want_null]
        qs = MagicMock()
        qs.values_list.return_value = rows
        return qs

    model.objects.filter.side_effect = _filter
    return model


@pytest.fixture
def numbers(monkeypatch):
    """Install Channel and ChannelOverride stand-ins; returns a setter."""
    models_mod = sys.modules["apps.channels.models"]

    def install(raw, pinned):
        monkeypatch.setattr(plugin_module, "Channel", _values_list_model(raw))
        monkeypatch.setattr(models_mod, "ChannelOverride", _values_list_model(pinned),
                            raising=False)
    return install


def _assign(settings, count):
    p = Plugin()
    state = Plugin._init_assigner_state(settings)
    return [p._get_channel_number(settings, {"number": None}, state) for _ in range(count)]


def test_auto_next_skips_a_number_pinned_by_an_override(numbers):
    numbers(raw=[1, 2], pinned=[3])
    assert _assign({"channel_numbering": "auto_next"}, 2) == [4, 5]


def test_auto_highest_starts_above_a_pinned_number(numbers):
    numbers(raw=[1, 2], pinned=[500])
    state = Plugin._init_assigner_state({"channel_numbering": "auto_highest"})
    assert state["next"] == 501


def test_specific_mode_skips_a_pinned_number(numbers):
    numbers(raw=[], pinned=[100.0])
    settings = {"channel_numbering": "specific", "starting_channel_number": "100"}
    assert _assign(settings, 1) == [101]


def test_lineup_mode_reads_no_numbers(numbers):
    numbers(raw=[1], pinned=[3])
    state = Plugin._init_assigner_state({"channel_numbering": "lineup"})
    assert state["used"] == set()


def test_none_pins_are_ignored(numbers):
    numbers(raw=[1], pinned=[None, 2])
    state = Plugin._init_assigner_state({"channel_numbering": "auto_next"})
    assert state["used"] == {1, 2}


def test_missing_override_model_keeps_the_old_behaviour(numbers, monkeypatch):
    """A Dispatcharr without ChannelOverride: numbering reads raw numbers only."""
    numbers(raw=[1, 2], pinned=[])
    stub = MagicMock(spec=["Channel"])  # attribute access for ChannelOverride raises
    monkeypatch.setitem(sys.modules, "apps.channels.models", stub)
    state = Plugin._init_assigner_state({"channel_numbering": "auto_next"})
    assert state["used"] == {1, 2}


def test_failing_override_query_degrades_and_warns(numbers, monkeypatch, caplog):
    """A backstop must not stop a sync: fall back to raw numbers, loudly."""
    numbers(raw=[1, 2], pinned=[])
    broken = MagicMock()
    broken.objects.filter.side_effect = RuntimeError("database gone")
    monkeypatch.setattr(sys.modules["apps.channels.models"], "ChannelOverride", broken,
                        raising=False)
    with caplog.at_level(logging.WARNING, logger="plugins.lineuparr"):
        state = Plugin._init_assigner_state({"channel_numbering": "auto_next"})
    assert state["used"] == {1, 2}
    assert any("ChannelOverride" in r.getMessage() for r in caplog.records
               if r.levelno >= logging.WARNING)


def test_import_error_other_than_importerror_degrades_and_warns(numbers, monkeypatch, caplog):
    """An import that fails for another reason (for example Django's
    AppRegistryNotReady) must not stop a sync either."""
    import types

    class _Broken(types.ModuleType):
        def __getattr__(self, name):
            raise RuntimeError("apps not ready")

    numbers(raw=[1, 2], pinned=[])
    monkeypatch.setitem(sys.modules, "apps.channels.models", _Broken("apps.channels.models"))
    with caplog.at_level(logging.WARNING, logger="plugins.lineuparr"):
        state = Plugin._init_assigner_state({"channel_numbering": "auto_next"})
    assert state["used"] == {1, 2}
    assert any("ChannelOverride" in r.getMessage() for r in caplog.records
               if r.levelno >= logging.WARNING)
