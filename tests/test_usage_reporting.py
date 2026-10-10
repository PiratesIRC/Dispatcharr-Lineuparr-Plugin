"""Lineuparr reports its Channels Created total through the vendored usage client.

Two call sites: the end of run() for actions that finish in the request (with the
live settings dict), and the end of _record_channels_created after a successful
ledger write (on the background sync thread, so consent comes from settings_fn).
"""
import json

import pytest

from Lineuparr import plugin as plugin_module
from Lineuparr.plugin import Plugin, PluginConfig


class FakeUsage:
    def __init__(self):
        self.calls = []

    def report(self, settings=None, logger=None):
        self.calls.append(settings)


@pytest.fixture
def usage(monkeypatch):
    fake = FakeUsage()
    monkeypatch.setattr(plugin_module, "USAGE", fake)
    return fake


def test_the_reporter_is_configured_for_lineuparr():
    u = plugin_module.USAGE
    assert (u.plugin, u.counter, u.label) == ("lineuparr", "channels_created", "Channels Created")
    assert u.settings_fn is not None


def test_the_total_sums_the_channel_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "counts.jsonl"
    ledger.write_text(json.dumps({"channels": 5}) + "\n" + json.dumps({"channels": 7}) + "\n", encoding="utf-8")
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", str(ledger))
    assert plugin_module.USAGE.total_fn() == 12


def test_the_checkbox_is_the_last_field_of_the_form():
    fields = Plugin().fields
    assert fields[-1]["id"] == "share_usage_counts"
    assert fields[-1]["type"] == "boolean" and fields[-1]["default"] is True
    assert sum(1 for f in fields if f.get("id") == "share_usage_counts") == 1


def test_a_request_action_reports_with_the_live_settings(usage, monkeypatch):
    p = Plugin()
    monkeypatch.setattr(p, "_plugin_status", lambda settings, logger: {"status": "ok", "message": "fine"})
    settings = {"share_usage_counts": True, "x": 1}
    p.run("plugin_status", {}, {"settings": settings, "logger": plugin_module.LOGGER})
    assert usage.calls == [settings]


def test_a_background_action_does_not_report_from_run(usage, monkeypatch):
    p = Plugin()
    monkeypatch.setattr(p, "_sync_channels", lambda settings, logger: {"status": "ok", "message": "started", "background": True})
    p.run("sync_channels", {}, {"settings": {}, "logger": plugin_module.LOGGER})
    assert usage.calls == []


def test_a_failing_action_does_not_report(usage, monkeypatch):
    p = Plugin()

    def boom(settings, logger):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(p, "_plugin_status", boom)
    result = p.run("plugin_status", {}, {"settings": {}, "logger": plugin_module.LOGGER})
    assert usage.calls == []
    assert isinstance(result, dict) and result.get("status") == "error"


def test_a_recorded_sync_reports_after_the_ledger_write(usage, tmp_path, monkeypatch):
    ledger = tmp_path / "counts.jsonl"
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", str(ledger))
    assert Plugin()._record_channels_created(3, "sync_channels", plugin_module.LOGGER) is True
    assert ledger.read_text(encoding="utf-8").count("\n") == 1
    assert usage.calls == [None]


def test_a_rejected_count_does_not_report(usage, tmp_path, monkeypatch):
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", str(tmp_path / "c.jsonl"))
    assert Plugin()._record_channels_created(-1, "sync_channels", plugin_module.LOGGER) is False
    assert usage.calls == []


def test_a_reporter_that_raises_never_breaks_the_plugin(monkeypatch, tmp_path):
    class Exploding:
        def report(self, settings=None, logger=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(plugin_module, "USAGE", Exploding())
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", str(tmp_path / "c.jsonl"))
    assert Plugin()._record_channels_created(2, "sync_channels", plugin_module.LOGGER) is True


def test_a_failed_ledger_write_does_not_report(usage, tmp_path, monkeypatch):
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", str(tmp_path / "no-dir" / "c.jsonl"))
    assert Plugin()._record_channels_created(2, "sync_channels", plugin_module.LOGGER) is False
    assert usage.calls == []


def test_a_throttled_report_after_a_sync_is_skipped_quietly(tmp_path, monkeypatch):
    from Lineuparr import usage_client as uc
    sent = []
    real = uc.UsageReporter(
        plugin="lineuparr", counter="channels_created", label="Channels Created",
        total_fn=lambda: 1, settings_fn=lambda: {}, data_dir=str(tmp_path / "stats"),
        http=lambda method, url, body: sent.append(body) or 202,
        start=lambda fn: fn(), lock=lambda fd: True,
    )
    monkeypatch.setattr(plugin_module, "USAGE", real)
    monkeypatch.setattr(PluginConfig, "CHANNEL_COUNT_LEDGER_FILE", str(tmp_path / "c.jsonl"))
    p = Plugin()
    assert p._record_channels_created(2, "sync_channels", plugin_module.LOGGER) is True
    assert p._record_channels_created(3, "sync_channels", plugin_module.LOGGER) is True
    assert len(sent) == 1


def test_the_checkbox_sits_in_the_advanced_section():
    from tests.test_settings_sections import _fields_under
    assert "share_usage_counts" in _fields_under("_sec_advanced")


def test_the_advanced_heading_no_longer_says_both():
    heading = next(f for f in Plugin().fields if f.get("id") == "_sec_advanced")
    assert "leave both alone" not in heading["help_text"]
    assert "usage counts" in heading["help_text"]


def test_a_background_action_reports_when_its_thread_finishes(usage):
    p = Plugin()
    ran = []
    assert p._try_start_thread(lambda a, b: ran.append((a, b)), ("s", "l")) is True
    p._thread.join(5)
    assert ran == [("s", "l")]
    assert usage.calls == [None]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_background_action_that_raises_still_reports(usage):
    p = Plugin()

    def boom(a, b):
        raise RuntimeError("background failed")

    assert p._try_start_thread(boom, ("s", "l")) is True
    p._thread.join(5)
    assert usage.calls == [None]
