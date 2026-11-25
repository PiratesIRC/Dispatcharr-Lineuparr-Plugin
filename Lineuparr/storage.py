"""Lineuparr's storage: where its private state lives and what moves over from /data.

The logic (resolving the data directory, carrying the old /data state over, the tally tail and
binding the state constants) is in plugin_storage.py, a vendored copy of _shared/plugin_storage.py.
This module holds only what belongs to this plugin: the Spec, the table of state paths, the lock
kept beside the data directories and the rollout switch. Each public function passes SPEC to the
vendored module, so callers see the same names they always did.
"""
from __future__ import annotations

try:
    from . import plugin_storage as _ps
except ImportError:  # loaded as a bare module, as plugin.py allows
    import plugin_storage as _ps

# Rollout switch for this release only: remove in the release after next, with the dual-path
# branch in operation_lock_files and tests/test_storage_rollout.py. While it is on, a worker on
# this code also honours the operation lock that workers on the previous release keep at the
# old /data path, and takes that old lock first.
ROLLOUT_DUAL_PATHS = True

# (constant name, kind, rel under the data dir). rel is None for the rows that live beside the
# data dirs or in the shared reports dir; those are named in _BESIDE.
_BIND_TABLE = (
    ("STATE_FILE", "file", "lineuparr_state.json"),
    ("PROGRESS_FILE", "file", "lineuparr_progress.json"),
    ("CHANNEL_COUNT_LEDGER_FILE", "tally", "lineuparr_channel_counts.jsonl"),
    ("OPERATION_LOCK_FILE", "nocopy", None),
    ("USAGE_DATA_DIR", "usage_dir", "usage"),
    ("REPORT_DIR", "dir", None),
)

# The operation lock is coordination state, so it sits beside the data directories (every worker
# computes the same path whatever key it resolved) and is never copied.
_BESIDE = {
    "OPERATION_LOCK_FILE": ".operation.lock",
}

# The tally file names follow the file on disk; do not change them.
SPEC = _ps.Spec(
    label="Lineuparr",
    state_name="_lineuparr_storage_state",
    table=_BIND_TABLE,
    beside=_BESIDE,
    reports_subdir="lineuparr",
    usage_subdir="lineuparr",
    tally_offsets="lineuparr_channel_counts.offsets.json",
    tally_lock="lineuparr_channel_counts.lock",
    reports_constant="REPORT_DIR",
)

Layout = _ps.Layout
Item = _ps.Item
MigrateResult = _ps.MigrateResult
atomic_write_bytes = _ps.atomic_write_bytes
atomic_write_json = _ps.atomic_write_json


def resolve(plugin_dir, key, logger):
    """Resolve the data directory for the plugin folder plugin_dir. See plugin_storage.resolve."""
    return _ps.resolve(SPEC, plugin_dir, key, logger)


def migrate(layout, items, logger, version=None):
    """Copy the old state into the data dir once. See plugin_storage.migrate."""
    return _ps.migrate(SPEC, layout, items, logger, version=version)


def sync_tally_tail(layout, tally_path, logger):
    """Append the tally's new old-source bytes to it. See plugin_storage.sync_tally_tail."""
    return _ps.sync_tally_tail(SPEC, layout, tally_path, logger)


def items_for(cfg, layout):
    """The moves bind hands to migrate. See plugin_storage.items_for."""
    return _ps.items_for(SPEC, cfg, layout)


def bind(cfg, plugin_file, logger):
    """Resolve, rebind the state constants and carry the old state over. See plugin_storage.bind."""
    return _ps.bind(SPEC, cfg, plugin_file, logger)


def check_context(layout, context, logger):
    """Warn once when Dispatcharr's data_dir differs from the resolved one. See plugin_storage."""
    return _ps.check_context(SPEC, layout, context, logger)


def operation_lock_files(cfg):
    """The operation lock paths in the order they are taken: the old /data path first, then the new.

    Old code (the previous release) takes only the old path, so a new worker that took the new path
    alone could run beside an old one. Taking the old path first closes that gap, and with the
    rollout switch off, or in legacy mode, the list is just the current path.
    """
    current = cfg.OPERATION_LOCK_FILE
    layout = getattr(cfg, "STORAGE_LAYOUT", None)
    if not ROLLOUT_DUAL_PATHS or layout is None or layout.legacy:
        return [current]
    old = (getattr(cfg, "LEGACY_PATHS", None) or {}).get("OPERATION_LOCK_FILE")
    if not old or old == current:
        return [current]
    return [old, current]
