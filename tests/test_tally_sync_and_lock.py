"""The channel tally is read and written consistently with the old-code worker's tail.

Two fixes, one file. The Channels Created total (USAGE.total_fn) syncs the tally tail from the
old ledger before it sums, so lines an old-code worker appended count even when this worker
has not written since. The tally append takes the same tally lock that sync_tally_tail takes, so
an append never interleaves with a tail sync on another worker. Every path is under tmp_path.
"""
import contextlib
import os

import pytest
from Lineuparr.plugin import Plugin, PluginConfig

from Lineuparr import plugin as plugin_module
from Lineuparr import storage
from tests.storage_helpers import Log, make_cfg, make_layout, tmp_legacy


def _tally_layout(monkeypatch, tmp_path):
    """A non-legacy layout bound onto PluginConfig, with one old tally line carried over."""
    legacy = tmp_legacy(tmp_path)
    cfg = make_cfg(dict(legacy))
    cfg.LEGACY_PATHS = dict(legacy)
    layout = make_layout(storage, str(tmp_path / "root"))
    monkeypatch.setattr(storage._ps, "resolve", lambda *a, **k: layout)
    monkeypatch.setattr(storage._ps, "REFUSE_REAL_DATA", True)
    storage._ps._state(storage.SPEC).migrated_dirs.clear()

    os.makedirs(os.path.dirname(legacy["CHANNEL_COUNT_LEDGER_FILE"]), exist_ok=True)
    with open(legacy["CHANNEL_COUNT_LEDGER_FILE"], "wb") as fh:
        fh.write(b'{"ts": 1, "channels": 3, "mode": "sync"}\n')
    storage.migrate(layout, storage.items_for(cfg, layout), Log(), version="t")

    new_tally = os.path.join(layout.data_dir, "lineuparr_channel_counts.jsonl")
    monkeypatch.setattr(PluginConfig, "STORAGE_LAYOUT", layout)
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", new_tally)
    return layout, legacy, new_tally


# --------------------------------------------------------------------------- #
# L1: the total includes old-worker lines
# --------------------------------------------------------------------------- #
def test_the_total_counts_old_worker_lines_without_a_write_by_this_worker(monkeypatch, tmp_path):
    """Old code appends after the copy and this worker writes nothing: the total must still see it."""
    _, legacy, new_tally = _tally_layout(monkeypatch, tmp_path)
    with open(legacy["CHANNEL_COUNT_LEDGER_FILE"], "ab") as fh:
        fh.write(b'{"ts": 2, "channels": 5, "mode": "sync"}\n')

    assert plugin_module.USAGE.total_fn() == 8, "the old-worker line was not counted"
    assert b'"channels": 5' in open(new_tally, "rb").read(), "the tail was not brought in"


def test_the_total_does_not_double_count_on_a_second_read(monkeypatch, tmp_path):
    _, legacy, _ = _tally_layout(monkeypatch, tmp_path)
    with open(legacy["CHANNEL_COUNT_LEDGER_FILE"], "ab") as fh:
        fh.write(b'{"ts": 2, "channels": 5, "mode": "sync"}\n')

    assert plugin_module.USAGE.total_fn() == 8
    assert plugin_module.USAGE.total_fn() == 8


def test_the_total_still_sums_when_the_tail_sync_fails(monkeypatch, tmp_path):
    """The total is a usage figure: a failing sync must not stop it reporting the ledger it has."""
    _tally_layout(monkeypatch, tmp_path)

    def broken(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(storage, "sync_tally_tail", broken)
    assert plugin_module.USAGE.total_fn() == 3


# --------------------------------------------------------------------------- #
# L2: the append holds the tally lock
# --------------------------------------------------------------------------- #
class _RecordingLock:
    """Stands in for storage.tally_lock: records that it was entered and what the file held
    the moment it was released, so the test sees the append happen inside the lock."""

    def __init__(self, path):
        self.path = path
        self.entered = 0
        self.lines_at_release = None

    @contextlib.contextmanager
    def __call__(self, layout):
        self.entered += 1
        yield
        with open(self.path, encoding="utf-8") as fh:
            self.lines_at_release = fh.read().count("\n")


def test_the_tally_append_runs_inside_the_tally_lock(monkeypatch, tmp_path):
    ledger = tmp_path / "counts.jsonl"
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", str(ledger))
    lock = _RecordingLock(str(ledger))
    monkeypatch.setattr(storage, "tally_lock", lock)

    assert Plugin.__new__(Plugin)._record_channels_created(4, "sync_channels") is True

    assert lock.entered == 1, "the tally append did not take the tally lock"
    assert lock.lines_at_release == 1, "the line was not written before the lock was released"


def test_a_tally_lock_that_fails_records_nothing_and_does_not_raise(monkeypatch, tmp_path):
    ledger = tmp_path / "counts.jsonl"
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", str(ledger))

    @contextlib.contextmanager
    def failing(layout):
        raise OSError("lock file unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(storage, "tally_lock", failing)
    assert Plugin.__new__(Plugin)._record_channels_created(4, "sync_channels") is False
    assert not ledger.exists() or ledger.read_text(encoding="utf-8") == ""


def test_the_tally_lock_is_a_no_op_in_legacy_mode(tmp_path):
    """Legacy mode has no data directory to hold a lock in, so the lock is skipped there."""
    layout = storage.Layout(True, None, None, "lineuparr", "test")
    with storage.tally_lock(layout):
        pass


@pytest.mark.skipif(os.name == "nt", reason="fcntl locks run on POSIX only")
def test_the_tally_lock_file_lives_beside_the_tally_in_the_data_dir(tmp_path):
    layout = storage.Layout(False, str(tmp_path), str(tmp_path), "lineuparr", "test")
    with storage.tally_lock(layout):
        assert os.path.exists(os.path.join(str(tmp_path), storage.SPEC.tally_lock))
