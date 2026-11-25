"""The carry-over copies old state into the data directory once and never destroys anything.

Proven here: a new file wins over an old one, an old file is never deleted, renamed or edited,
the channel tally and the usage install id arrive byte-identical, a failed install id copy blocks
the usage reports of that process, the operation lock is never copied, the old reports reach the
shared folder, and the tally tail appends each old-code line exactly once. Every path is under
tmp_path.
"""
import json
import os

import pytest

from Lineuparr import storage
from tests.storage_helpers import Log, make_cfg, make_layout, tmp_legacy


def build(monkeypatch, tmp_path):
    """Bind a fake non-legacy layout with real old files under tmp_path; return (cfg, layout, legacy)."""
    legacy = tmp_legacy(tmp_path)
    cfg = make_cfg(dict(legacy))
    cfg.LEGACY_PATHS = dict(legacy)
    layout = make_layout(storage, str(tmp_path / "root"))
    monkeypatch.setattr(storage._ps, "resolve", lambda *a, **k: layout)
    monkeypatch.setattr(storage._ps, "REFUSE_REAL_DATA", True)
    # migrate skips a data dir it already settled in this process; clear that per test.
    storage._ps._state(storage.SPEC).migrated_dirs.clear()
    return cfg, layout, legacy


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def items(cfg, layout):
    return storage.items_for(cfg, layout)


def test_a_new_file_wins_and_the_old_file_is_untouched(monkeypatch, tmp_path):
    cfg, layout, legacy = build(monkeypatch, tmp_path)
    write(legacy["STATE_FILE"], b'{"old": true}\n')
    new_state = os.path.join(layout.data_dir, "lineuparr_state.json")
    write(new_state, b'{"new": true}\n')
    old_stat = os.stat(legacy["STATE_FILE"])

    storage.migrate(layout, items(cfg, layout), Log(), version="t")

    assert open(new_state, "rb").read() == b'{"new": true}\n'
    assert open(legacy["STATE_FILE"], "rb").read() == b'{"old": true}\n'
    now = os.stat(legacy["STATE_FILE"])
    assert (now.st_size, now.st_mtime_ns) == (old_stat.st_size, old_stat.st_mtime_ns)


def test_an_old_file_is_copied_and_never_deleted_or_edited(monkeypatch, tmp_path):
    cfg, layout, legacy = build(monkeypatch, tmp_path)
    write(legacy["PROGRESS_FILE"], b'{"progress": 1}\n')

    result = storage.migrate(layout, items(cfg, layout), Log(), version="t")

    new_progress = os.path.join(layout.data_dir, "lineuparr_progress.json")
    assert open(new_progress, "rb").read() == b'{"progress": 1}\n'
    assert open(legacy["PROGRESS_FILE"], "rb").read() == b'{"progress": 1}\n'
    assert new_progress in result.copied
    assert result.failed == []


def test_the_channel_tally_and_install_id_arrive_byte_identical(monkeypatch, tmp_path):
    cfg, layout, legacy = build(monkeypatch, tmp_path)
    tally = b'{"ts": 1, "channels": 3, "mode": "sync"}\n'
    write(legacy["CHANNEL_COUNT_LEDGER_FILE"], tally)
    install = b"0123456789abcdef0123456789abcdef"
    write(os.path.join(legacy["USAGE_DATA_DIR"], "lineuparr", "install_id"), install)

    result = storage.migrate(layout, items(cfg, layout), Log(), version="t")

    new_tally = os.path.join(layout.data_dir, "lineuparr_channel_counts.jsonl")
    assert open(new_tally, "rb").read() == tally
    new_install = os.path.join(layout.data_dir, "usage", "lineuparr", "install_id")
    assert open(new_install, "rb").read() == install
    assert open(os.path.join(legacy["USAGE_DATA_DIR"], "lineuparr", "install_id"), "rb").read() == install
    assert not result.install_id_blocked
    # The offset is recorded, so the tail sync starts after the copied bytes.
    offsets = storage._ps._load_offsets(
        os.path.join(layout.data_dir, "lineuparr_channel_counts.offsets.json"))
    assert offsets == {legacy["CHANNEL_COUNT_LEDGER_FILE"]: len(tally)}


def test_a_failed_install_id_copy_blocks_the_usage_reports(monkeypatch, tmp_path):
    cfg, layout, legacy = build(monkeypatch, tmp_path)
    install = b"0123456789abcdef0123456789abcdef"
    old_install = os.path.join(legacy["USAGE_DATA_DIR"], "lineuparr", "install_id")
    write(old_install, install)
    real = storage._ps._read_bytes

    def flaky(path):
        if path == old_install:
            raise OSError("unreadable")
        return real(path)

    monkeypatch.setattr(storage._ps, "_read_bytes", flaky)
    result = storage.migrate(layout, items(cfg, layout), Log(), version="t")

    assert result.install_id_blocked is True
    assert "install_id" in result.failed
    assert not os.path.exists(os.path.join(layout.data_dir, "usage", "lineuparr", "install_id"))


def test_the_operation_lock_is_never_copied(monkeypatch, tmp_path):
    """The lock is coordination state kept beside the data directories, so no old lock is carried."""
    cfg, layout, legacy = build(monkeypatch, tmp_path)
    write(legacy["OPERATION_LOCK_FILE"], b'{"timestamp": "2026-01-01T00:00:00", "action": "lineuparr"}')

    storage.migrate(layout, items(cfg, layout), Log(), version="t")

    beside_lock = layout.beside(".operation.lock")
    assert not os.path.exists(beside_lock), "the lock must not be copied beside the data dir either"
    assert not os.path.exists(os.path.join(layout.data_dir, "lineuparr_operation.lock"))
    assert os.path.exists(legacy["OPERATION_LOCK_FILE"]), "the old lock is left for old-code workers"


def test_old_reports_reach_the_shared_folder(monkeypatch, tmp_path):
    cfg, layout, legacy = build(monkeypatch, tmp_path)
    report = b"<html>report</html>"
    write(os.path.join(legacy["REPORT_DIR"], "lineuparr_report_20260101.html"), report)

    storage.migrate(layout, items(cfg, layout), Log(), version="t")

    shared = os.path.join(layout.plugins_dir, "_shared", "newsflasharr", "lineuparr",
                          "lineuparr_report_20260101.html")
    assert open(shared, "rb").read() == report


def test_the_tally_tail_appends_each_old_line_exactly_once(monkeypatch, tmp_path):
    """An old-code worker appends to the old tally after the copy. The tail sync brings those
    bytes into the new tally once, and a second sync with nothing new appends nothing."""
    cfg, layout, legacy = build(monkeypatch, tmp_path)
    first = b'{"ts": 1, "channels": 3, "mode": "sync"}\n'
    write(legacy["CHANNEL_COUNT_LEDGER_FILE"], first)
    storage.migrate(layout, items(cfg, layout), Log(), version="t")
    new_tally = os.path.join(layout.data_dir, "lineuparr_channel_counts.jsonl")

    # Old code keeps appending to the old file.
    late = b'{"ts": 2, "channels": 5, "mode": "sync"}\n'
    with open(legacy["CHANNEL_COUNT_LEDGER_FILE"], "ab") as fh:
        fh.write(late)

    appended = storage.sync_tally_tail(layout, new_tally, Log())
    again = storage.sync_tally_tail(layout, new_tally, Log())

    assert appended == len(late)
    assert again == 0
    assert open(new_tally, "rb").read() == first + late
    total = sum(json.loads(line)["channels"] for line in open(new_tally, encoding="utf-8"))
    assert total == 8, "each line must count once: 3 from the copy plus 5 from the tail"


def test_the_tally_tail_is_a_no_op_in_legacy_mode(tmp_path):
    layout = storage.Layout(True, None, None, "lineuparr", "test")
    assert storage.sync_tally_tail(layout, str(tmp_path / "x.jsonl"), Log()) == 0
