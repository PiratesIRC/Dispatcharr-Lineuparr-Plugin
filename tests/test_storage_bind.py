"""storage.bind rebinds the plugin's state constants to the data directory, once per import.

These tests drive storage.bind with a fake layout (storage._ps.resolve is replaced) and with the
legacy literals read from the source, so they never touch a real /data path.
"""
import ast
import os

import pytest

from Lineuparr import report_count, storage
from Lineuparr import plugin as plugin_module
from tests.storage_helpers import (PLUGIN_SOURCE, STATE_NAMES, Log, make_cfg, make_layout,
                                   source_literals, tmp_legacy)


def test_bind_rebinds_every_state_row_and_records_the_legacy_paths(monkeypatch, tmp_path):
    legacy = tmp_legacy(tmp_path)
    cfg = make_cfg(dict(legacy, EXPORT_DIR="/data/exports"))
    layout = make_layout(storage, str(tmp_path))
    monkeypatch.setattr(storage._ps, "resolve", lambda *a, **k: layout)

    got = storage.bind(cfg, str(tmp_path / "plugins" / "lineuparr" / "plugin.py"), Log())

    assert got is layout
    assert cfg.LEGACY_PATHS == legacy
    assert cfg.STATE_FILE == os.path.join(layout.data_dir, "lineuparr_state.json")
    assert cfg.PROGRESS_FILE == os.path.join(layout.data_dir, "lineuparr_progress.json")
    assert cfg.CHANNEL_COUNT_LEDGER_FILE == os.path.join(layout.data_dir, "lineuparr_channel_counts.jsonl")
    assert cfg.USAGE_DATA_DIR == os.path.join(layout.data_dir, "usage")
    assert cfg.REPORT_DIR == layout.shared_reports_dir("lineuparr")
    # The lock sits beside the data directories, so every worker finds the same file.
    assert cfg.OPERATION_LOCK_FILE == os.path.join(layout.plugins_dir, "_data", "lineuparr.operation.lock")
    # User-facing paths are not table rows, so bind leaves them alone.
    assert cfg.EXPORT_DIR == "/data/exports"
    assert cfg.STORAGE_MIGRATION is not None


def test_bind_names_the_legacy_paths_the_way_the_source_does():
    """The table row names must be PluginConfig attributes the source defines as /data literals."""
    literals = source_literals()
    for name in STATE_NAMES:
        assert name in literals, f"{name} is not a PluginConfig literal"
        assert literals[name].startswith("/data/")


def test_legacy_mode_keeps_the_old_paths_exactly(monkeypatch, tmp_path):
    literals = source_literals()
    cfg = make_cfg(dict(literals))
    monkeypatch.setattr(
        storage._ps, "resolve",
        lambda spec, plugin_dir, key, logger: storage.Layout(True, None, None, key, "test"))

    layout = storage.bind(cfg, str(tmp_path / "plugin.py"), Log())

    assert layout.legacy is True
    for name in STATE_NAMES:
        assert getattr(cfg, name) == literals[name], name
    assert cfg.STORAGE_MIGRATION is None


def test_a_failed_carry_over_falls_back_to_the_legacy_paths(monkeypatch, tmp_path):
    literals = source_literals()
    cfg = make_cfg(dict(literals))
    layout = make_layout(storage, str(tmp_path))
    monkeypatch.setattr(storage._ps, "resolve", lambda *a, **k: layout)

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(storage._ps, "migrate", boom)
    log = Log()
    result = storage.bind(cfg, str(tmp_path / "plugin.py"), log)

    assert result.legacy is True
    for name in STATE_NAMES:
        assert getattr(cfg, name) == literals[name], name
    assert log.errors, "a failed carry-over must be logged"


def test_the_session_plugin_runs_in_legacy_mode():
    """No Dispatcharr sandbox in the test process, so the real module never resolves a directory."""
    assert plugin_module.PluginConfig.STORAGE_LAYOUT.legacy is True


def test_the_usage_reporter_is_built_after_bind_on_the_rebound_directory():
    """The usage reporter is built at import, so it needs the rebound directory and must come
    after storage.bind in the source (a path captured before bind would stay legacy)."""
    tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"))
    bind_line = usage_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "bind" \
                and getattr(node.func.value, "id", "") == "storage":
            bind_line = node.lineno
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "USAGE" for t in node.targets):
            usage_line = node.lineno
            keywords = {k.arg: k.value for k in node.value.keywords}
            assert ast.unparse(keywords["data_dir"]) == "PluginConfig.USAGE_DATA_DIR"
    assert bind_line is not None and usage_line is not None
    assert bind_line < usage_line


def test_the_import_hands_the_bound_reports_folder_to_the_report_writer():
    """The report writer reads REPORT_DIR at call time, so the import must assign the bound value
    to its module. Checked in the source: legacy mode makes the two values equal anyway."""
    tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"))
    assigned = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and ast.unparse(node.targets[0]) == "reports.REPORT_DIR"
        and ast.unparse(node.value) == "PluginConfig.REPORT_DIR"
    ]
    assert assigned, "plugin.py must set reports.REPORT_DIR from PluginConfig.REPORT_DIR after bind"


def test_a_blocked_install_id_turns_the_usage_report_off():
    """install_id_blocked: the reporter is silenced, so this process sends nothing."""
    from types import SimpleNamespace as NS
    calls = []
    usage = NS(report=lambda *a, **k: calls.append(a))
    plugin_module._block_usage_if_install_id_blocked(usage, NS(install_id_blocked=True))
    usage.report("settings", Log())
    assert calls == []


def test_an_unblocked_install_id_leaves_the_usage_report_on():
    from types import SimpleNamespace as NS
    calls = []
    usage = NS(report=lambda *a, **k: calls.append(a))
    log = Log()
    plugin_module._block_usage_if_install_id_blocked(usage, NS(install_id_blocked=False))
    plugin_module._block_usage_if_install_id_blocked(usage, None)
    usage.report("settings", log)
    assert calls == [("settings", log)]


def test_the_report_counter_path_does_not_move_with_the_data_directory(monkeypatch, tmp_path):
    """Newsflasharr reads /data/<source>/report_count.json at this fixed path, so bind must not move it."""
    layout = make_layout(storage, str(tmp_path))
    monkeypatch.setattr(storage._ps, "resolve", lambda *a, **k: layout)
    storage.bind(make_cfg(dict(tmp_legacy(tmp_path))), str(tmp_path / "plugin.py"), Log())
    assert report_count.DATA_ROOT == "/data"
    assert report_count.counter_path() == "/data/lineuparr/report_count.json"
    assert report_count.counter_path("/data") == "/data/lineuparr/report_count.json"
