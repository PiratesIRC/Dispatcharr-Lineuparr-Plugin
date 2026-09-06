"""The emit layer that hands reports to Newsflasharr for delivery.

This module owns the guard boundary. The vendored client's notify() never raises,
but the code around it can, and a bug here must never break the matching run that
produced the report. Every public function is written so a failure is reported
rather than thrown, and that is pinned below.

Settings are read from the dict passed in on every call and never cached on an
instance. A value primed on one entry path and read back with getattr on another
fails silently, with no crash and no log line.
"""
import hashlib
import json
import os

import pytest
from Lineuparr import notify_bridge


class Recorder:
    """Stands in for the vendored client's notify()."""

    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


ON = {"notify_enabled": True, "notify_report_on": "every_run"}


def written(tmp_path, html=True, csv=True, error=None):
    out = {"html_path": None, "csv_path": None, "error": error}
    if html:
        p = tmp_path / "lineuparr_report_20260802_120000.html"
        p.write_text("<html></html>", encoding="utf-8")
        out["html_path"] = str(p)
    if csv:
        p = tmp_path / "lineuparr_report_20260802_120000.csv"
        p.write_text("Channel\n", encoding="utf-8")
        out["csv_path"] = str(p)
    return out


class TestResolvers:
    @pytest.mark.parametrize("value,expected", [
        ("never", "never"), ("every_run", "every_run"),
        ("EVERY_RUN", "every_run"), ("  never  ", "never"),
        ("nonsense", "never"), (None, "never"), (5, "never"), ("", "never"),
    ])
    def test_trigger_resolves_to_a_known_value_or_the_default(self, value, expected):
        assert notify_bridge.resolve_report_trigger({"notify_report_on": value}) == expected

    def test_absent_trigger_defaults_to_never(self):
        """Off by default. A plugin that starts emailing on upgrade because a
        setting was absent is worse than one that has to be switched on."""
        assert notify_bridge.resolve_report_trigger({}) == "never"

    @pytest.mark.parametrize("value,expected", [
        ("html", "html"), ("csv", "csv"), ("both", "both"),
        ("BOTH", "both"), ("nonsense", "both"), (None, "both"), ({}, "both"),
    ])
    def test_format_resolves_to_a_known_value_or_the_default(self, value, expected):
        assert notify_bridge.resolve_report_format({"notify_report_format": value}) == expected

    @pytest.mark.parametrize("value,expected", [
        (True, True), ("true", True), ("YES", True), ("1", True),
        (False, False), ("false", False), ("", False), (None, False), (0, False),
    ])
    def test_enabled_accepts_the_forms_a_stored_setting_can_take(self, value, expected):
        assert notify_bridge.is_enabled({"notify_enabled": value}) is expected


class TestShouldEmit:
    def test_off_when_the_master_toggle_is_off(self):
        ok, reason = notify_bridge.should_emit({"notify_report_on": "every_run"})
        assert ok is False
        assert "switched off" in reason

    def test_off_when_the_trigger_says_never(self):
        ok, reason = notify_bridge.should_emit({"notify_enabled": True,
                                                "notify_report_on": "never"})
        assert ok is False
        assert "never" in reason

    def test_on_when_both_agree(self):
        ok, reason = notify_bridge.should_emit(ON)
        assert ok is True
        assert reason is None


class TestEmit:
    def test_both_formats_send_two_notifications(self, tmp_path):
        """A notification carries one attachment, so two files means two emails."""
        rec = Recorder()
        result = notify_bridge.emit_reports(rec, dict(ON, notify_report_format="both"),
                                            written(tmp_path))
        assert result["sent"] == 2
        assert {os.path.basename(c["attachment"]) for c in rec.calls} == {
            "lineuparr_report_20260802_120000.html",
            "lineuparr_report_20260802_120000.csv"}

    @pytest.mark.parametrize("fmt,suffix", [("html", ".html"), ("csv", ".csv")])
    def test_a_single_format_sends_only_that_file(self, tmp_path, fmt, suffix):
        rec = Recorder()
        result = notify_bridge.emit_reports(rec, dict(ON, notify_report_format=fmt),
                                            written(tmp_path))
        assert result["sent"] == 1
        assert rec.calls[0]["attachment"].endswith(suffix)

    def test_the_source_and_event_are_the_stable_routing_keys(self, tmp_path):
        rec = Recorder()
        notify_bridge.emit_reports(rec, ON, written(tmp_path, csv=False))
        assert rec.calls[0]["source"] == "lineuparr"
        assert rec.calls[0]["event"] == notify_bridge.EVENT

    def test_nothing_is_sent_when_disabled(self, tmp_path):
        rec = Recorder()
        result = notify_bridge.emit_reports(rec, {"notify_enabled": False}, written(tmp_path))
        assert result["sent"] == 0
        assert rec.calls == []
        assert result["skipped_reason"]

    def test_a_missing_file_is_skipped_rather_than_sent(self, tmp_path):
        """A green write result does not prove the artifact exists. Sending a
        path that is gone produces mail with no attachment."""
        rec = Recorder()
        payload = written(tmp_path)
        os.remove(payload["html_path"])
        result = notify_bridge.emit_reports(rec, ON, payload)
        assert result["sent"] == 1
        assert rec.calls[0]["attachment"].endswith(".csv")

    def test_a_failed_report_write_is_reported_not_sent(self, tmp_path):
        rec = Recorder()
        result = notify_bridge.emit_reports(rec, ON, written(tmp_path, error="disk full"))
        assert result["sent"] == 0
        assert result["skipped_reason"] == "disk full"

    def test_a_refused_send_is_counted_as_not_sent(self, tmp_path):
        rec = Recorder(result=False)
        result = notify_bridge.emit_reports(rec, ON, written(tmp_path))
        assert result["sent"] == 0

    def test_an_exception_in_the_emit_path_is_contained(self, tmp_path):
        def explode(**kwargs):
            raise RuntimeError("spool on fire")

        result = notify_bridge.emit_reports(explode, ON, written(tmp_path))
        assert result["sent"] == 0
        assert "contained" in result["skipped_reason"]

    def test_none_written_payload_does_not_raise(self):
        assert notify_bridge.emit_reports(Recorder(), ON, None)["sent"] == 0


class TestRoutesToSmtp:
    """Newsflasharr sends an unmatched event to its default channels, so a
    missing routing rule is invisible from this side: the spool write succeeds
    and the mail goes somewhere other than the inbox."""

    def test_a_matching_rule_routing_to_smtp_is_found(self):
        rules = json.dumps([{"match": {"source": "lineuparr", "event": notify_bridge.EVENT},
                             "channels": ["smtp"]}])
        assert notify_bridge.routes_to_smtp({"routing_rules": rules}) is True

    def test_routing_rules_are_accepted_as_a_json_string(self):
        """Newsflasharr stores this setting as a JSON string, not a list.
        Treating it as a list iterates its characters."""
        rules = json.dumps([{"match": {}, "channels": ["smtp"]}])
        assert isinstance(rules, str)
        assert notify_bridge.routes_to_smtp({"routing_rules": rules}) is True

    def test_a_rule_for_another_source_does_not_count(self):
        rules = json.dumps([{"match": {"source": "sentinelarr"}, "channels": ["smtp"]}])
        assert notify_bridge.routes_to_smtp({"routing_rules": rules}) is False

    def test_the_default_channel_counts_when_no_rule_matches(self):
        assert notify_bridge.routes_to_smtp({"routing_rules": "[]",
                                             "default_channels": "smtp,discord"}) is True

    def test_no_rule_and_no_smtp_default_is_false(self):
        assert notify_bridge.routes_to_smtp({"routing_rules": "[]",
                                             "default_channels": "discord"}) is False

    @pytest.mark.parametrize("bad", ["not json", None, 5, {"a": 1}])
    def test_malformed_routing_rules_never_raise(self, bad):
        assert notify_bridge.routes_to_smtp({"routing_rules": bad}) in (True, False)


class TestVendoredClient:
    def test_the_vendored_client_matches_its_pinned_hash(self):
        """The same gate CI runs. A hand-edit to the vendored copy, or a CRLF
        checkout, changes the hash without changing any code."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest = json.load(open(os.path.join(root, ".github", "scripts",
                                               "client_manifest.json"), encoding="utf-8"))
        with open(os.path.join(root, "Lineuparr", "notify_client.py"), "rb") as fh:
            actual = hashlib.sha256(fh.read()).hexdigest()
        assert actual == manifest["notify_client.py"]

    def test_the_action_and_its_button_are_declared_in_the_manifest(self):
        """An action missing from plugin.json has no button, and a button with an
        unrecognised variant or colour makes Dispatcharr's serializer drop the
        whole action silently."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest = json.load(open(os.path.join(root, "Lineuparr", "plugin.json"),
                                  encoding="utf-8"))
        action = next((a for a in manifest["actions"] if a["id"] == "email_report"), None)
        assert action, "email_report is not declared in plugin.json"
        assert action["button_variant"] in ("outline", "filled")
        assert action["button_color"] in (
            "blue", "cyan", "green", "orange", "red", "violet", "gray", "grape", "teal")

    def test_every_declared_action_has_a_handler(self):
        import inspect

        import Lineuparr.plugin as plugin_module
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        manifest = json.load(open(os.path.join(root, "Lineuparr", "plugin.json"),
                                  encoding="utf-8"))
        source = inspect.getsource(plugin_module.Plugin.run)
        for action in manifest["actions"]:
            assert f'"{action["id"]}"' in source, f"no handler wired for {action['id']}"

    def test_the_notification_settings_are_declared(self):
        import Lineuparr.plugin as plugin_module
        fields = plugin_module.Plugin.fields.fget(plugin_module.Plugin.__new__(plugin_module.Plugin))
        ids = {f["id"] for f in fields}
        assert {"notify_enabled", "notify_report_on", "notify_report_format"} <= ids

    def test_sending_is_off_by_default_in_the_settings(self):
        """An upgrade must not start emailing on its own."""
        import Lineuparr.plugin as plugin_module
        fields = plugin_module.Plugin.fields.fget(plugin_module.Plugin.__new__(plugin_module.Plugin))
        by_id = {f["id"]: f for f in fields}
        assert by_id["notify_enabled"]["default"] is False
        assert by_id["notify_report_on"]["default"] == "never"

    def test_the_client_is_imported_lazily_so_a_missing_file_cannot_break_import(self):
        """The plugin must load even if the vendored client is absent, because a
        notification path is never worth failing the plugin over."""
        import inspect

        import Lineuparr.plugin as plugin_module
        source = inspect.getsource(plugin_module)
        assert "import notify_client" not in source.split("def ")[0], (
            "the client must not be imported at module scope"
        )
