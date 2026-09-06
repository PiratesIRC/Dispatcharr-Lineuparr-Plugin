"""The append-only tally of channels this plugin has created.

WHY A TALLY EXISTS AT ALL. Nothing this plugin writes can be added up afterwards
into a lifetime total. The channels it creates are ordinary Dispatcharr channel
rows, indistinguishable from ones the operator made by hand or that another
plugin created, and the unmatched-channel cleanup deletes some of them again. So
a cumulative number has to be recorded as it happens or it cannot be recovered.

Measured on the live installation 2026-09-05, which is why this starts at zero
rather than being seeded: the configured lineup is AU_Foxtel_lineup.json with
group prefix "AU: ", and no channel group of that shape exists. The 1,477
channels present sit in groups the Stream-Mapparr project's own notes list as its
thirteen configured groups. Counting them would credit this plugin with another
plugin's work, so no seed line was written and none was invented.

WHAT ONE UNIT IS. One channel row created by one run. A run that creates nothing
still records a line, because a run that created nothing is a fact about the run
and dropping it would make the tally silently sparse. A channel deleted later by
the unmatched-channel cleanup does NOT subtract: this is a count of creations
performed, not an inventory of channels that still exist, so the number cannot
go down. That was an explicit operator decision.

PRIVACY. The tally holds integers and one short mode string. No channel name, no
group name, no lineup filename, no URL, no hostname. Only the summed integer
reaches the published badge, and the gist behind that badge is unlisted rather
than private, so treat the number as public.

Ported from the equivalent tally in the sibling IPTV Checker plugin.
"""
import ast
import io
import json
import logging
import os

import pytest

from Lineuparr import plugin as plugin_module
from Lineuparr.plugin import Plugin

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Lineuparr", "plugin.py")


@pytest.fixture
def plugin():
    return Plugin.__new__(Plugin)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "lineuparr_channel_counts.jsonl"
    monkeypatch.setattr(plugin_module.PluginConfig,
                        "CHANNEL_COUNT_LEDGER_FILE", str(path))
    return path


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# What it records
# --------------------------------------------------------------------------- #
def test_a_run_that_created_channels_records_the_count(plugin, ledger):
    assert plugin._record_channels_created(139, "sync_channels") is True
    rows = _lines(ledger)
    assert len(rows) == 1
    assert rows[0]["channels"] == 139
    assert rows[0]["mode"] == "sync_channels"


def test_a_run_that_created_nothing_still_records_a_line(plugin, ledger):
    """Dropping it would make the tally silently sparse."""
    assert plugin._record_channels_created(0, "sync_channels") is True
    assert [r["channels"] for r in _lines(ledger)] == [0]


def test_each_run_appends_rather_than_replacing(plugin, ledger):
    """Several Dispatcharr processes hold this module.

    A read-then-write total loses an increment whenever two of them race. An
    append of one short line does not, which is why this is a JSON-lines tally
    and not a single number in a file.
    """
    for n in (139, 0, 7, 22):
        plugin._record_channels_created(n, "sync_channels")
    assert [r["channels"] for r in _lines(ledger)] == [139, 0, 7, 22]


def test_every_line_carries_a_timestamp(plugin, ledger):
    plugin._record_channels_created(3, "sync_channels")
    assert isinstance(_lines(ledger)[0]["ts"], int)


def test_one_line_holds_exactly_one_json_object(plugin, ledger):
    """The publishing script reads this line by line, so a wrapped line breaks it."""
    plugin._record_channels_created(139, "sync_channels")
    raw = ledger.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert raw.count("\n") == 1


def test_the_tally_records_no_name_of_anything(plugin, ledger):
    """Only integers and a short mode string. The published number is public."""
    plugin._record_channels_created(139, "sync_channels")
    assert set(_lines(ledger)[0]) == {"ts", "channels", "mode"}


# --------------------------------------------------------------------------- #
# What it refuses
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [None, "many", "", object(), 1.5])
def test_a_count_that_is_not_a_whole_number_is_refused(plugin, ledger, bad, caplog):
    """A float would be truncated by int() and under-report silently."""
    with caplog.at_level("WARNING"):
        assert plugin._record_channels_created(bad, "sync_channels") is False
    assert _lines(ledger) == []
    assert caplog.records, f"{bad!r} was refused without saying so"


def test_a_negative_count_is_refused(plugin, ledger, caplog):
    """Nothing can create a negative number of channels, so this is a bug upstream."""
    with caplog.at_level("WARNING"):
        assert plugin._record_channels_created(-4, "sync_channels") is False
    assert _lines(ledger) == []
    assert caplog.records


def test_a_boolean_is_refused_because_bool_is_an_int_in_python(plugin, ledger):
    """True would otherwise be recorded as one channel created."""
    assert plugin._record_channels_created(True, "sync_channels") is False
    assert _lines(ledger) == []


# --------------------------------------------------------------------------- #
# It never turns a run into a failure
# --------------------------------------------------------------------------- #
def test_a_tally_that_cannot_be_written_does_not_raise(plugin, monkeypatch, tmp_path):
    """A tally is worth strictly less than the sync that produced it."""
    monkeypatch.setattr(plugin_module.PluginConfig, "CHANNEL_COUNT_LEDGER_FILE",
                        str(tmp_path / "no-such-directory" / "counts.jsonl"))
    assert plugin._record_channels_created(139, "sync_channels") is False


def test_a_tally_that_cannot_be_written_says_so(plugin, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(plugin_module.PluginConfig, "CHANNEL_COUNT_LEDGER_FILE",
                        str(tmp_path / "no-such-directory" / "counts.jsonl"))
    with caplog.at_level("WARNING"):
        plugin._record_channels_created(139, "sync_channels")
    assert any("tally" in r.getMessage().lower() for r in caplog.records)


def test_it_reports_through_the_logger_it_was_given(plugin, ledger):
    """So the tally line lands beside the rest of the run's messages."""
    seen = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    run_logger = logging.getLogger("test_channel_tally_logger")
    run_logger.handlers = []
    run_logger.addHandler(Grab())
    run_logger.setLevel(logging.WARNING)
    run_logger.propagate = False

    plugin._record_channels_created(-1, "sync_channels", logger=run_logger)
    assert seen, "the refusal was not reported through the given logger"


# --------------------------------------------------------------------------- #
# It is actually wired into the sync, and only on a real run
# --------------------------------------------------------------------------- #
def _sync_channels_function():
    with io.open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_do_sync_channels")


def test_the_channel_sync_records_what_it_created():
    """Read structurally, not by substring.

    A substring search for the call would pass on a call sitting in dead code or
    in a comment. This asserts the call exists inside the function that creates
    channels and is passed the counter that function increments.
    """
    func = _sync_channels_function()
    calls = [n for n in ast.walk(func)
             if isinstance(n, ast.Call)
             and ast.unparse(n.func).endswith("_record_channels_created")]
    assert calls, "_do_sync_channels never records the channels it created"
    args = [ast.unparse(a) for a in calls[0].args]
    assert "created" in args, (
        f"the tally is not passed the created counter; it was passed {args}")


def test_the_tally_is_the_only_new_side_effect_on_the_creation_path():
    """There is exactly one place this plugin creates a channel row.

    If a second appears, the tally silently stops being a total. This fails when
    that happens so the new site gets one too.
    """
    with io.open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    creations = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and ast.unparse(n.func) in ("Channel.objects.get_or_create",
                                             "Channel.objects.create",
                                             "Channel.objects.bulk_create")]
    assert len(creations) == 1, (
        f"{len(creations)} channel-creation sites exist; the tally covers one")


def test_a_dry_run_records_nothing(plugin, ledger, monkeypatch):
    """A preview creates no channel, so it must not add to a total of creations.

    The dry-run branch increments the same `created` counter for what it WOULD
    have made, so recording unconditionally would inflate the badge every time
    somebody pressed Preview.
    """
    func = _sync_channels_function()
    for call in ast.walk(func):
        if (isinstance(call, ast.Call)
                and ast.unparse(call.func).endswith("_record_channels_created")):
            guarded = any(
                isinstance(parent, ast.If)
                and "dry_run" in ast.unparse(parent.test)
                and call in list(ast.walk(parent))
                for parent in ast.walk(func) if isinstance(parent, ast.If))
            assert guarded, (
                "the tally call is not inside a dry-run guard, so a preview "
                "would count channels it never created")
            return
    raise AssertionError("no tally call found in _do_sync_channels")


# --------------------------------------------------------------------------- #
# The file the publishing script has to read
# --------------------------------------------------------------------------- #
def test_the_ledger_path_is_declared_and_is_a_jsonl_file():
    path = plugin_module.PluginConfig.CHANNEL_COUNT_LEDGER_FILE
    assert path.startswith("/data/"), path
    assert path.endswith(".jsonl"), (
        "the publishing script reads this one JSON object per line")


def test_the_ledger_path_does_not_collide_with_another_file_this_plugin_writes():
    cfg = plugin_module.PluginConfig
    others = {cfg.STATE_FILE, cfg.PROGRESS_FILE, cfg.OPERATION_LOCK_FILE}
    assert cfg.CHANNEL_COUNT_LEDGER_FILE not in others
