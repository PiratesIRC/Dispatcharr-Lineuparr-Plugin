"""Dispatcharr 0.34 task banner: long actions run inline inside a Celery task.

Outside a task (0.33, or a test with no Celery task) the daemon thread path is
unchanged. Inside a task the action runs on the task's own thread and returns its
real result, so the banner turns green or red when it finishes, and the
operation guard still refuses a second start.
"""
import json

import pytest

from Lineuparr import plugin as plugin_module
from Lineuparr.plugin import Plugin, PluginConfig, ProgressTracker, _BUSY, _OP_STATE, result_text

ASYNC_ACTIONS = {
    "preview_stream_match", "full_sync", "sync_channels",
    "apply_stream_match", "apply_epg_match", "apply_logo_match",
}


class FakeUsage:
    def __init__(self):
        self.calls = []

    def report(self, settings=None, logger=None, force=False):
        self.calls.append((settings, force))


@pytest.fixture
def usage(monkeypatch):
    fake = FakeUsage()
    monkeypatch.setattr(plugin_module, "USAGE", fake)
    return fake


@pytest.fixture
def in_task(monkeypatch):
    monkeypatch.setattr(plugin_module, "_in_celery_task", lambda: True)


@pytest.fixture
def not_in_task(monkeypatch):
    monkeypatch.setattr(plugin_module, "_in_celery_task", lambda: False)


def _manifest():
    path = plugin_module.__file__.replace("plugin.py", "plugin.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_the_six_long_actions_are_async_and_nothing_else_is():
    actions = _manifest()["actions"]
    marked = {a["id"] for a in actions if a.get("async") is True}
    assert marked == ASYNC_ACTIONS
    assert all("async" not in a or a["async"] is True for a in actions)


def test_background_tasks_is_declared_beside_the_existing_capabilities():
    caps = _manifest()["capabilities"]
    assert set(caps) == {"outbound_network", "filesystem_write", "background_tasks"}


def test_outside_a_task_the_thread_path_is_used(not_in_task):
    p = Plugin()
    seen = []
    started = p._start_operation(lambda a, b: seen.append((a, b)), ("s", "l"), lambda a, b: "inline")
    assert started is None
    p._thread.join(5)
    assert seen == [("s", "l")]


def test_inside_a_task_the_action_runs_inline_and_returns_its_result(in_task):
    p = Plugin()
    threads_before = plugin_module.threading.active_count()
    result = p._start_operation(lambda a, b: {"status": "ok", "message": "bg"},
                                ("s", "l"),
                                lambda a, b: {"status": "ok", "message": f"inline {a} {b}"})
    assert result == {"status": "ok", "message": "inline s l"}
    assert p._thread is None
    assert plugin_module.threading.active_count() == threads_before


def test_inline_run_sets_the_inline_flag_only_for_its_own_thread(in_task):
    p = Plugin()
    flags = []
    p._start_operation(None, (), lambda: flags.append(plugin_module._in_inline_operation()) or {"status": "ok"})
    assert flags == [True]
    assert plugin_module._in_inline_operation() is False


def test_a_second_start_while_inline_is_refused(in_task):
    p = Plugin()
    p._inline_running = True
    assert p._start_operation(None, (), lambda: {"status": "ok"}) is _BUSY
    p._inline_running = False


def test_a_live_thread_refuses_an_inline_start(in_task):
    import threading
    p = Plugin()
    release = threading.Event()
    p._thread = threading.Thread(target=release.wait, daemon=True)
    p._thread.start()
    try:
        assert p._start_operation(None, (), lambda: {"status": "ok"}) is _BUSY
    finally:
        release.set()
        p._thread.join(5)


def test_an_inline_exception_becomes_an_error_result_and_frees_the_guard(in_task):
    p = Plugin()

    def boom():
        raise RuntimeError("step failed")

    result = p._start_operation(None, (), boom)
    assert result["status"] == "error"
    assert "step failed" in result["error"]
    assert p._inline_running is False
    assert plugin_module._in_inline_operation() is False


def test_the_inline_guard_is_released_after_a_normal_run(in_task):
    p = Plugin()
    p._start_operation(None, (), lambda: {"status": "ok", "message": "done"})
    assert p._inline_running is False
    assert p._start_operation(None, (), lambda: {"status": "ok", "message": "again"})["message"] == "again"


def test_sub_steps_run_directly_when_inside_an_inline_operation(in_task):
    p = Plugin()
    seen = []

    def sub_step():
        seen.append(p._runs_nested())
        return {"status": "ok"}

    p._start_operation(None, (), sub_step)
    assert seen == [True]


def test_a_daemon_thread_counts_as_nested_outside_a_task(not_in_task):
    import threading
    p = Plugin()
    seen = []
    worker = threading.Thread(target=lambda: seen.append(p._runs_nested()))
    worker.start()
    worker.join(5)
    assert seen == [True]
    assert p._runs_nested() is False


def test_progress_is_forwarded_only_for_the_top_level_action(monkeypatch, tmp_path):
    monkeypatch.setattr(PluginConfig, "PROGRESS_FILE", str(tmp_path / "progress.json"))
    calls = []
    _OP_STATE.action = "sync_channels"
    _OP_STATE.reporter = lambda pct, msg: calls.append((pct, msg))
    try:
        tracker = ProgressTracker(4, "sync_channels", plugin_module.LOGGER)
        tracker.update(2)
        tracker.finish()
        sub = ProgressTracker(4, "apply_stream_match", plugin_module.LOGGER)
        sub.update(4)
    finally:
        _OP_STATE.action = None
        _OP_STATE.reporter = None
    assert calls[0] == (0, "sync_channels: 0/4")
    assert (100, "sync_channels: 2/4") in calls
    assert all(msg.startswith("sync_channels") for _, msg in calls)


def test_no_progress_is_forwarded_without_a_reporter(monkeypatch, tmp_path):
    monkeypatch.setattr(PluginConfig, "PROGRESS_FILE", str(tmp_path / "progress.json"))
    _OP_STATE.action = "sync_channels"
    _OP_STATE.reporter = None
    try:
        ProgressTracker(2, "sync_channels", plugin_module.LOGGER).finish()
    finally:
        _OP_STATE.action = None


def test_run_hands_the_task_reporter_to_the_action_and_clears_it(usage):
    p = Plugin()
    captured = {}

    def handler(settings, logger):
        captured["action"] = _OP_STATE.action
        captured["reporter"] = _OP_STATE.reporter
        return {"status": "ok", "message": "validated"}

    p._validate_settings = handler
    reporter = lambda pct, msg: None
    p.run("validate_settings", {}, {"settings": {}, "report_progress": reporter})
    assert captured == {"action": "validate_settings", "reporter": reporter}
    assert _OP_STATE.action is None
    assert _OP_STATE.reporter is None


def test_full_sync_cancelled_before_step_one_returns_a_warning(usage):
    p = Plugin()
    p._stop_event.set()
    result = p._do_full_sync({"single_channel_name": ""}, plugin_module.LOGGER)
    assert result["status"] == "warning"
    assert "cancelled" in result_text(result)
    assert p._suppress_refresh is False


def test_the_entry_points_still_return_the_started_banner_outside_a_task(monkeypatch, usage):
    p = Plugin()
    monkeypatch.setattr(p, "_load_lineup", lambda s, l: {"categories": {}})
    monkeypatch.setattr(p, "_do_sync_channels_bg", lambda s, l: None)
    result = p._sync_channels({}, plugin_module.LOGGER)
    p._thread.join(5)
    assert result["background"] is True
    assert result["status"] == "ok"
