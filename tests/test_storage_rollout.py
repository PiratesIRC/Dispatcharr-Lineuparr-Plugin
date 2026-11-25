"""The operation lock during the rollout: old-code and new-code workers stop each other.

Old code (the previous release) takes only the lock at its /data path, and it checks only for a
fresh file there (the timestamp protocol, not an fcntl lock). So this release takes the old path
first and the beside-data path second, and releases both. A holder of either path stops the
operation. Every test here uses a timestamp file, so the protocol is the same on every OS.

The rollout switch is storage.ROLLOUT_DUAL_PATHS. Remove it, and these tests, in the release
after next.
"""
import json
import os
from datetime import datetime, timedelta

import pytest

from Lineuparr import storage
from Lineuparr import plugin as plugin_module
from tests.storage_helpers import Log


@pytest.fixture
def lock_paths(monkeypatch, tmp_path):
    """A non-legacy layout with the old and new lock paths under tmp_path."""
    cfg = plugin_module.PluginConfig
    old = str(tmp_path / "old" / "lineuparr_operation.lock")
    plugins = str(tmp_path / "plugins")
    layout = storage.Layout(False, os.path.join(plugins, "_data", "lineuparr"), plugins, "lineuparr", "test")
    new = os.path.join(plugins, "_data", "lineuparr.operation.lock")
    monkeypatch.setattr(cfg, "LEGACY_PATHS", dict(cfg.LEGACY_PATHS, OPERATION_LOCK_FILE=old))
    monkeypatch.setattr(cfg, "STORAGE_LAYOUT", layout)
    monkeypatch.setattr(cfg, "OPERATION_LOCK_FILE", new)
    monkeypatch.setattr(storage._ps, "REFUSE_REAL_DATA", True)
    return old, new


def write_lock(path, minutes_old=0):
    """A lock file in the exact shape the previous release writes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stamp = datetime.now() - timedelta(minutes=minutes_old)
    with open(path, "w") as fh:
        json.dump({"timestamp": stamp.isoformat(), "action": "lineuparr"}, fh)


def acquire():
    return plugin_module.Plugin()._acquire_lock(Log())


def test_the_old_path_comes_first_and_the_new_path_second(lock_paths):
    old, new = lock_paths
    assert storage.operation_lock_files(plugin_module.PluginConfig) == [old, new]


def test_an_old_code_holder_stops_a_new_code_operation(lock_paths):
    old, new = lock_paths
    write_lock(old)  # held by a worker on the previous release

    assert acquire() is False
    assert not os.path.exists(new), "the new path must not be taken when the old one is held"
    assert os.path.exists(old), "a refused operation must leave the old holder's file alone"


def test_a_new_code_holder_stops_the_operation_and_releases_the_old_path_it_took(lock_paths):
    old, new = lock_paths
    write_lock(new)  # held by another worker on this release

    assert acquire() is False
    assert not os.path.exists(old), "the old path taken before the refusal must be released"
    assert os.path.exists(new), "the holder's own file must not be removed"


def test_a_free_lock_is_taken_on_both_paths_and_released_on_both(lock_paths):
    old, new = lock_paths

    assert acquire() is True
    assert os.path.exists(old) and os.path.exists(new)

    plugin_module.Plugin()._release_lock(Log())
    assert not os.path.exists(old) and not os.path.exists(new)


def test_a_stale_old_lock_is_overridden(lock_paths):
    old, new = lock_paths
    write_lock(old, minutes_old=plugin_module.PluginConfig.OPERATION_LOCK_TIMEOUT_MINUTES + 5)

    assert acquire() is True
    assert os.path.exists(new)


def test_with_the_rollout_switch_off_only_the_new_path_is_used(lock_paths, monkeypatch):
    old, new = lock_paths
    monkeypatch.setattr(storage, "ROLLOUT_DUAL_PATHS", False)
    write_lock(old)
    before = open(old).read()

    assert storage.operation_lock_files(plugin_module.PluginConfig) == [new]
    assert acquire() is True, "with the switch off, a holder of the old path does not stop the run"
    assert os.path.exists(new)
    assert open(old).read() == before, "the old holder's file must be left as it was"


def test_legacy_mode_uses_only_the_legacy_path(lock_paths, monkeypatch):
    old, _new = lock_paths
    cfg = plugin_module.PluginConfig
    monkeypatch.setattr(cfg, "STORAGE_LAYOUT", storage.Layout(True, None, None, "lineuparr", "test"))
    monkeypatch.setattr(cfg, "OPERATION_LOCK_FILE", old)

    assert storage.operation_lock_files(cfg) == [old]
    assert acquire() is True
    assert os.path.exists(old)
