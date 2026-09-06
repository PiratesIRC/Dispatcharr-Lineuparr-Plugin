"""Deleting this plugin's own old CSV exports by age.

/data/exports is SHARED with at least six other plugins. Measured on the live
system it held stream_mapparr_, epg_janitor_, event_channel_managarr_,
lineuparr_, iptv_checker_results_ and channel_mapparr_ files, and a seven day
rule would have matched over a hundred files belonging to other projects. So the
selection is scoped to this plugin's own filename prefix AND the .csv suffix,
and a mutation widening either is caught here, because the alternative is
deleting another project's data.

Four further rules exist because this deletes files on other people's
installations:

  - it is off unless a positive number of days is set, so nobody loses files
    merely by upgrading;
  - the file just written is never deleted, whatever the arithmetic says;
  - at least one of this plugin's files always survives, so a small number
    cannot empty the directory;
  - it never raises, because it runs immediately after a successful export and
    a failure to tidy up must not turn that export into a reported error.

Ported from the equivalent guard in the sibling IPTV Checker plugin.

A WARNING ABOUT WRITING MORE TESTS HERE: any test of the age rule or of the
off-by-default rule needs SEVERAL old files. With a single old file the
"one always survives" rule keeps it regardless, so the test passes even when the
guard it names has been deleted. That happened in IPTV Checker and only mutation
testing found it.
"""

import logging
import os

import pytest

from Lineuparr import plugin as plugin_module
from Lineuparr.plugin import Plugin


DAY = 86400.0
NOW = 1_800_000_000.0
MINE = "lineuparr_"


@pytest.fixture
def plugin():
    return Plugin()


@pytest.fixture
def pmod():
    return plugin_module


def _entry(name, days_old):
    return (name, NOW - days_old * DAY)


def _plan(plugin, entries, days=5, now=NOW, protect=None):
    return plugin._csv_exports_to_delete(entries, days, now, protect)


# --- off by default ---------------------------------------------------------

@pytest.mark.parametrize("days", [0, None, "", -1, "abc"])
def test_no_retention_configured_deletes_nothing(plugin, days):
    # Several old files, not one. With a single file the survivor rule keeps it
    # anyway, so the test would pass even with the off-by-default guard removed.
    entries = [_entry(MINE + "match_applied_a.csv", 400),
               _entry(MINE + "match_applied_b.csv", 300),
               _entry(MINE + "match_applied_c.csv", 500)]
    assert _plan(plugin, entries, days=days) == []


# --- the age rule -----------------------------------------------------------

def test_a_file_older_than_the_limit_is_deleted(plugin):
    entries = [_entry(MINE + "old.csv", 9), _entry(MINE + "new.csv", 1)]
    assert _plan(plugin, entries, days=5) == [MINE + "old.csv"]


def test_several_old_files_are_deleted_at_once(plugin):
    """Several old files, so the survivor rule cannot mask the age rule."""
    entries = [_entry(MINE + "a.csv", 40), _entry(MINE + "b.csv", 30),
               _entry(MINE + "c.csv", 20), _entry(MINE + "fresh.csv", 0)]
    assert _plan(plugin, entries, days=5) == [
        MINE + "a.csv", MINE + "b.csv", MINE + "c.csv"]


def test_a_file_younger_than_the_limit_is_kept(plugin):
    entries = [_entry(MINE + "a.csv", 1), _entry(MINE + "b.csv", 4)]
    assert _plan(plugin, entries, days=5) == []


def test_the_boundary_is_not_deleted(plugin):
    """Exactly five days old is not OLDER than five days."""
    entries = [_entry(MINE + "edge_a.csv", 5), _entry(MINE + "edge_b.csv", 5),
               _entry(MINE + "keep.csv", 0)]
    assert _plan(plugin, entries, days=5) == []


def test_just_past_the_boundary_is_deleted(plugin):
    entries = [_entry(MINE + "edge.csv", 5.001), _entry(MINE + "keep.csv", 0)]
    assert _plan(plugin, entries, days=5) == [MINE + "edge.csv"]


# --- never another plugin's files -------------------------------------------

def test_another_plugins_csv_is_never_deleted(plugin):
    """The export directory is shared. This is the most important test here.

    The foreign names are the real ones measured in /data/exports, and there are
    several old ones so that removing the prefix check cannot be hidden by the
    survivor rule.
    """
    entries = [
        _entry("stream_mapparr_sorted_20260101_000000.csv", 400),
        _entry("epg_janitor_20260101_000000.csv", 400),
        _entry("event_channel_managarr_20260101.csv", 400),
        _entry("iptv_checker_results_20260101.csv", 400),
        _entry("channel_mapparr_20260101.csv", 400),
        _entry(MINE + "mine.csv", 400),
        _entry(MINE + "recent.csv", 0),
    ]
    assert _plan(plugin, entries, days=5) == [MINE + "mine.csv"]


def test_a_name_merely_containing_the_prefix_is_not_ours(plugin):
    """The check is startswith, not "contains"."""
    entries = [_entry("backup_of_" + MINE + "old.csv", 400),
               _entry("copy_of_" + MINE + "older.csv", 500),
               _entry(MINE + "mine.csv", 400),
               _entry(MINE + "recent.csv", 0)]
    assert _plan(plugin, entries, days=5) == [MINE + "mine.csv"]


def test_a_file_that_is_not_a_csv_is_never_deleted(plugin):
    entries = [
        _entry(MINE + "notes.txt", 400),
        _entry(MINE + "report.html", 500),
        _entry(MINE + "archive.zip", 600),
        _entry(MINE + "real.csv", 400),
        _entry(MINE + "recent.csv", 0),
    ]
    assert _plan(plugin, entries, days=5) == [MINE + "real.csv"]


# --- protections ------------------------------------------------------------

def test_the_file_just_written_is_never_deleted(plugin):
    entries = [_entry(MINE + "just_written.csv", 400),
               _entry(MINE + "other_a.csv", 400),
               _entry(MINE + "other_b.csv", 300)]
    plan = _plan(plugin, entries, days=5, protect=MINE + "just_written.csv")
    assert MINE + "just_written.csv" not in plan
    assert plan == [MINE + "other_a.csv", MINE + "other_b.csv"]


def test_the_protected_file_survives_even_though_it_is_the_oldest(plugin):
    """The protection is not the same rule as "keep the newest"."""
    entries = [_entry(MINE + "just_written.csv", 900),
               _entry(MINE + "a.csv", 400),
               _entry(MINE + "b.csv", 300)]
    plan = _plan(plugin, entries, days=5, protect=MINE + "just_written.csv")
    assert plan == [MINE + "a.csv", MINE + "b.csv"]


def test_at_least_one_file_always_survives(plugin):
    """Every file is old. Keeping the newest stops a small number emptying it."""
    entries = [_entry(MINE + "a.csv", 400), _entry(MINE + "b.csv", 300),
               _entry(MINE + "c.csv", 500)]
    plan = _plan(plugin, entries, days=5)
    assert MINE + "b.csv" not in plan, "the newest of this plugin's files must survive"
    assert plan == [MINE + "a.csv", MINE + "c.csv"]


def test_a_single_old_file_is_kept(plugin):
    assert _plan(plugin, [_entry(MINE + "only.csv", 400)], days=5) == []


def test_the_survivor_is_not_counted_from_another_plugins_files(plugin):
    """A newer foreign file must not license deleting all of ours."""
    entries = [_entry("stream_mapparr_sorted_x.csv", 0),
               _entry(MINE + "mine.csv", 400)]
    assert _plan(plugin, entries, days=5) == []


# --- total over its input ---------------------------------------------------

def test_an_empty_directory_is_fine(plugin):
    assert _plan(plugin, [], days=5) == []


@pytest.mark.parametrize("mtime", [None, "not a number"])
def test_an_unreadable_timestamp_is_left_alone(plugin, mtime):
    entries = [(MINE + "odd.csv", mtime), _entry(MINE + "recent.csv", 0)]
    assert _plan(plugin, entries, days=5) == []


def test_a_timestamp_that_is_not_a_number_cannot_become_the_survivor(plugin):
    """Keeping it would let it stand in as the file that survives.

    Comparisons against a not-a-number value are all false, so it would win the
    "newest" test and every real file would be deleted instead of one surviving.
    """
    entries = [(MINE + "odd.csv", float("nan")),
               _entry(MINE + "old_a.csv", 400),
               _entry(MINE + "old_b.csv", 300)]

    plan = _plan(plugin, entries, days=5)

    assert plan == [MINE + "old_a.csv"]
    assert MINE + "old_b.csv" not in plan, "the newest real file must survive"
    assert MINE + "odd.csv" not in plan


# --- the part that touches the filesystem -----------------------------------

def _seed(directory, name, days_old, now):
    import os
    path = directory / name
    path.write_text("x", encoding="utf-8")
    stamp = now - days_old * DAY
    os.utime(path, (stamp, stamp))
    return path


def test_pruning_removes_only_the_old_files_of_this_plugin(
        plugin, pmod, monkeypatch, tmp_path):
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    old_a = _seed(tmp_path, MINE + "old_a.csv", 30, now)
    old_b = _seed(tmp_path, MINE + "old_b.csv", 40, now)
    recent = _seed(tmp_path, MINE + "recent.csv", 1, now)
    foreign = _seed(tmp_path, "stream_mapparr_sorted_x.csv", 30, now)

    removed = plugin._prune_csv_exports(5)

    assert removed == 2
    assert not old_a.exists()
    assert not old_b.exists()
    assert recent.exists()
    assert foreign.exists(), "another plugin's file was deleted"


def test_pruning_protects_the_file_just_written(
        plugin, pmod, monkeypatch, tmp_path):
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    just_written = _seed(tmp_path, MINE + "just.csv", 30, now)
    other_a = _seed(tmp_path, MINE + "other_a.csv", 30, now)
    other_b = _seed(tmp_path, MINE + "other_b.csv", 40, now)

    plugin._prune_csv_exports(5, protect=MINE + "just.csv")

    assert just_written.exists()
    assert not other_a.exists()
    assert not other_b.exists()


def test_pruning_a_missing_directory_does_not_raise(plugin, pmod, monkeypatch, tmp_path):
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path / "gone"))
    assert plugin._prune_csv_exports(5) == 0


def test_pruning_survives_a_file_it_cannot_delete(
        plugin, pmod, monkeypatch, tmp_path):
    """It runs after a successful export and must never turn one into a failure."""
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    _seed(tmp_path, MINE + "a.csv", 30, now)
    _seed(tmp_path, MINE + "b.csv", 40, now)
    _seed(tmp_path, MINE + "keep.csv", 0, now)

    def boom(path):
        raise OSError("permission denied")

    monkeypatch.setattr(pmod.os, "remove", boom)

    assert plugin._prune_csv_exports(5) == 0


# --- wired into the export --------------------------------------------------

def _export(plugin, pmod, monkeypatch, tmp_path, settings, name="lineuparr_match_x.csv"):
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    return plugin._export_csv(name, [{"Channel": "Sample"}], ["Channel"],
                              logging.getLogger("test_csv_retention"), settings)


def test_exporting_prunes_old_files(plugin, pmod, monkeypatch, tmp_path):
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    old_a = _seed(tmp_path, MINE + "old_a.csv", 30, now)
    old_b = _seed(tmp_path, MINE + "old_b.csv", 40, now)
    recent = _seed(tmp_path, MINE + "recent.csv", 1, now)

    written = _export(plugin, pmod, monkeypatch, tmp_path, {"csv_retention_days": 5})

    assert written, "the export wrote nothing, so this test proves nothing"
    assert not old_a.exists()
    assert not old_b.exists()
    assert recent.exists()


def test_exporting_without_the_setting_prunes_nothing(plugin, pmod, monkeypatch, tmp_path):
    """Control: nobody loses files just by upgrading."""
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    old_a = _seed(tmp_path, MINE + "old_a.csv", 900, now)
    old_b = _seed(tmp_path, MINE + "old_b.csv", 800, now)
    old_c = _seed(tmp_path, MINE + "old_c.csv", 700, now)

    written = _export(plugin, pmod, monkeypatch, tmp_path, {})

    assert written, "the export wrote nothing, so this test proves nothing"
    assert old_a.exists()
    assert old_b.exists()
    assert old_c.exists()


def test_exporting_never_deletes_the_file_it_just_wrote(
        plugin, pmod, monkeypatch, tmp_path):
    """A one day rule, and nothing else recent, so only the protection saves it."""
    import os
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    _seed(tmp_path, MINE + "old_a.csv", 900, now)
    _seed(tmp_path, MINE + "old_b.csv", 800, now)

    written = _export(plugin, pmod, monkeypatch, tmp_path,
                      {"csv_retention_days": 1}, name=MINE + "match_new.csv")

    assert written, "the export wrote nothing, so this test proves nothing"
    # Backdate nothing: the file just written is young anyway, so make the
    # protection the only thing that can save it.
    assert os.path.exists(written)


def test_exporting_with_a_backdated_new_file_still_keeps_it(
        plugin, pmod, monkeypatch, tmp_path):
    """The protection is by NAME, not by age.

    _export_csv passes the name it just wrote, so even a clock that makes the
    new file look ancient cannot delete it.
    """
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    _seed(tmp_path, MINE + "old_a.csv", 900, now)
    _seed(tmp_path, MINE + "old_b.csv", 800, now)

    real_prune = Plugin._prune_csv_exports
    seen = {}

    def record(self, retention_days, protect=None, logger=None):
        seen["protect"] = protect
        return real_prune(self, retention_days, protect, logger)

    monkeypatch.setattr(pmod.Plugin, "_prune_csv_exports", record)

    _export(plugin, pmod, monkeypatch, tmp_path,
            {"csv_retention_days": 1}, name=MINE + "match_new.csv")

    assert seen.get("protect") == MINE + "match_new.csv", (
        "the export must name the file it just wrote as protected")


def test_a_failed_export_prunes_nothing(plugin, pmod, monkeypatch, tmp_path):
    """Pruning happens only after a file is successfully written."""
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    old_a = _seed(tmp_path, MINE + "old_a.csv", 900, now)
    old_b = _seed(tmp_path, MINE + "old_b.csv", 800, now)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pmod, "open", boom, raising=False)

    written = plugin._export_csv(MINE + "match_new.csv", [{"Channel": "S"}], ["Channel"],
                                 logging.getLogger("test_csv_retention"),
                                 {"csv_retention_days": 1})

    assert written is None
    assert old_a.exists()
    assert old_b.exists()


def test_clearing_all_exports_ignores_the_age_setting(plugin, pmod, monkeypatch, tmp_path):
    """Someone pressing Clear CSV Exports expects everything of ours cleared."""
    import time

    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    recent = _seed(tmp_path, MINE + "recent.csv", 0, now)
    old = _seed(tmp_path, MINE + "old.csv", 900, now)
    foreign = _seed(tmp_path, "stream_mapparr_sorted_x.csv", 900, now)

    result = plugin._clear_csv_exports({"csv_retention_days": 0},
                                       logging.getLogger("test_csv_retention"))

    assert result["status"] == "ok"
    assert not recent.exists()
    assert not old.exists()
    assert foreign.exists(), "another plugin's file was deleted"


# --- the setting is declared ------------------------------------------------

def test_the_setting_is_offered_in_the_settings_form(plugin):
    """This plugin builds its settings form in fields(), not in plugin.json."""
    fields = {f["id"]: f for f in plugin.fields}
    assert "csv_retention_days" in fields, (
        "the retention setting must appear in Plugin.fields(), because that is "
        "what Dispatcharr renders as the settings form for this plugin")
    field = fields["csv_retention_days"]
    assert field["type"] == "number"
    assert field["default"] == 0, "off unless configured"


# --------------------------------------------------------------------------- #
# A value that cannot be read must say so
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["7.5", "seven", "1,5"])
def test_an_unreadable_retention_value_is_reported_rather_than_ignored(
        plugin, pmod, monkeypatch, tmp_path, caplog, value):
    """Measured: "7.5" deleted nothing and logged nothing.

    A user who types 7.5 into the number field gets silence and a directory that
    grows forever. Treating it as "keep everything" is the right SAFE choice; not
    saying so is not.
    """
    import time
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    now = time.time()
    _seed(tmp_path, MINE + "a.csv", 400, now)
    _seed(tmp_path, MINE + "b.csv", 300, now)

    with caplog.at_level("WARNING"):
        removed = plugin._prune_csv_exports(value)

    assert removed == 0, "an unreadable value must still delete nothing"
    assert any(value in rec.getMessage() for rec in caplog.records), (
        f"nothing was logged about the unreadable retention value {value!r}")


@pytest.mark.parametrize("value", [0, "0", None, "", -1])
def test_a_value_meaning_keep_everything_is_not_logged_as_a_problem(
        plugin, pmod, monkeypatch, tmp_path, caplog, value):
    """Off is the default and is not a mistake, so it must not warn every export."""
    import time
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    _seed(tmp_path, MINE + "a.csv", 400, time.time())
    with caplog.at_level("WARNING"):
        plugin._prune_csv_exports(value)
    assert [r.getMessage() for r in caplog.records] == []


def test_the_retention_field_refuses_a_negative_number_in_the_interface():
    """A negative value silently means keep everything, so do not offer one."""
    field = next(f for f in Plugin.__new__(Plugin).fields
                 if f["id"] == "csv_retention_days")
    assert field.get("min") == 0, "the number field declares no minimum"


# --------------------------------------------------------------------------- #
# The pruning has to report into the same place as the export around it
# --------------------------------------------------------------------------- #
def test_pruning_reports_through_the_logger_the_export_was_given(
        plugin, pmod, monkeypatch, tmp_path):
    """It logged to the module logger while _export_csv logged to the run's.

    /data/exports is shared and can hold root-owned files, so os.remove from a
    dispatch worker can raise PermissionError. That warning landing in a
    different stream from the rest of the export's messages is how a retention
    that never works stays invisible.
    """
    import logging
    import time

    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    now = time.time()
    _seed(tmp_path, MINE + "a.csv", 400, now)
    _seed(tmp_path, MINE + "b.csv", 300, now)
    _seed(tmp_path, MINE + "keep.csv", 0, now)

    seen = []

    class Recorder(logging.Logger):
        pass

    recorder = logging.getLogger("test_prune_logger_capture")
    recorder.handlers = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    recorder.addHandler(Grab())
    recorder.setLevel(logging.INFO)
    recorder.propagate = False

    plugin._prune_csv_exports(5, logger=recorder)

    assert any("Deleted CSV export" in m for m in seen), (
        "the prune reported nothing through the logger it was given")


def test_a_failed_delete_is_reported_through_the_given_logger(
        plugin, pmod, monkeypatch, tmp_path):
    import logging
    import time

    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    now = time.time()
    _seed(tmp_path, MINE + "a.csv", 400, now)
    _seed(tmp_path, MINE + "b.csv", 300, now)
    _seed(tmp_path, MINE + "keep.csv", 0, now)
    monkeypatch.setattr(pmod.os, "remove",
                        lambda p: (_ for _ in ()).throw(OSError("permission denied")))

    seen = []
    recorder = logging.getLogger("test_prune_failure_capture")
    recorder.handlers = []

    class Grab(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    recorder.addHandler(Grab())
    recorder.setLevel(logging.WARNING)
    recorder.propagate = False

    assert plugin._prune_csv_exports(5, logger=recorder) == 0
    assert any("permission denied" in m for m in seen), seen


def test_the_export_passes_its_own_logger_to_the_pruning(
        plugin, pmod, monkeypatch, tmp_path):
    """So that everything one export says lands in one place."""
    import logging
    seen = {}
    real = Plugin._prune_csv_exports

    def record(self, retention_days, protect=None, logger=None):
        seen["logger"] = logger
        return real(self, retention_days, protect, logger)

    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(pmod.Plugin, "_prune_csv_exports", record)
    run_logger = logging.getLogger("test_export_logger_identity")
    plugin._export_csv("lineuparr_x.csv", [{"Channel": "S"}], ["Channel"],
                       run_logger, {"csv_retention_days": 5})
    assert seen.get("logger") is run_logger


# --------------------------------------------------------------------------- #
# The scan must not happen at all when there is nothing to delete
# --------------------------------------------------------------------------- #
# Measured 2026-09-05 against the real shape of /data/exports on the live
# system: 124 files across six plugins, of which 10 are this plugin's. With the
# shipped default of 0 the prune still listed the directory and read the
# modification time of all 124, for a guaranteed empty result, on every export.
SHARED_DIRECTORY = (["stream_mapparr_%d.csv" % i for i in range(65)]
                    + ["epg_janitor_%d.csv" % i for i in range(26)]
                    + ["event_channel_managarr_%d.csv" % i for i in range(14)]
                    + [MINE + "%d.csv" % i for i in range(10)]
                    + ["iptv_checker_results_%d.csv" % i for i in range(5)]
                    + ["channel_mapparr_%d.csv" % i for i in range(4)])


def _fill(tmp_path):
    for name in SHARED_DIRECTORY:
        (tmp_path / name).write_text("x", encoding="utf-8")


@pytest.mark.parametrize("off", [0, "0", None, "", -1, "7.5", "seven"])
def test_no_directory_is_read_when_nothing_can_be_deleted(
        plugin, pmod, monkeypatch, tmp_path, off):
    """Off is the default, so this runs on every export of every installation."""
    _fill(tmp_path)
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    touched = []
    monkeypatch.setattr(pmod.os, "listdir",
                        lambda p: touched.append(p) or [])
    assert plugin._prune_csv_exports(off) == 0
    assert touched == [], (
        "the export directory was listed even though nothing could be deleted")


def test_the_modification_time_is_read_only_for_this_plugins_files(
        plugin, pmod, monkeypatch, tmp_path):
    """114 of the 124 files in the shared directory belong to other plugins."""
    _fill(tmp_path)
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    seen = []
    real = pmod.os.path.getmtime
    monkeypatch.setattr(pmod.os.path, "getmtime",
                        lambda p: (seen.append(os.path.basename(p)), real(p))[1])

    plugin._prune_csv_exports(5)

    foreign = [n for n in seen if not n.startswith(MINE)]
    assert foreign == [], f"{len(foreign)} file(s) belonging to other plugins were stat'd"
    assert len(seen) == 10, seen


def test_pruning_still_works_after_the_early_exit_was_added(
        plugin, pmod, monkeypatch, tmp_path):
    """Control: the early exit must not disable the feature it guards."""
    import time
    now = time.time()
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    old_a = _seed(tmp_path, MINE + "old_a.csv", 30, now)
    old_b = _seed(tmp_path, MINE + "old_b.csv", 40, now)
    keep = _seed(tmp_path, MINE + "keep.csv", 0, now)
    foreign = _seed(tmp_path, "stream_mapparr_x.csv", 40, now)

    assert plugin._prune_csv_exports(5) == 2
    assert not old_a.exists() and not old_b.exists()
    assert keep.exists() and foreign.exists()


# --------------------------------------------------------------------------- #
# A fractional value must not silently become a shorter window
# --------------------------------------------------------------------------- #
# Measured 2026-09-05: int(3.7) is 3, so a retention of 3.7 deleted files on a
# three day window while the setting help text and the user guide both promised
# that anything other than a whole number keeps every file and says so in the
# log. The field declares type "number", so a float is exactly what the form
# yields. The string "3.7" was rejected correctly; the float was not, so the
# earlier test using only strings did not cover the reachable case.
@pytest.mark.parametrize("value", [3.7, 7.5, "3.7", "7.5", 0.5])
def test_a_fractional_number_of_days_keeps_every_file_and_says_so(
        plugin, pmod, monkeypatch, tmp_path, caplog, value):
    import time
    monkeypatch.setattr(pmod.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    now = time.time()
    kept = [_seed(tmp_path, MINE + "old_%d.csv" % i, 400 - i * 10, now) for i in range(5)]

    with caplog.at_level("WARNING"):
        removed = plugin._prune_csv_exports(value)

    assert removed == 0, f"{value!r} deleted {removed} file(s)"
    assert all(p.exists() for p in kept), "files were deleted on a truncated window"
    assert any(str(value) in rec.getMessage() for rec in caplog.records), (
        f"nothing was logged about the fractional retention value {value!r}")


@pytest.mark.parametrize("value,expected_days", [
    (7, 7), ("7", 7), (7.0, 7), ("7.0", 7), (" 7 ", 7),
])
def test_a_whole_number_of_days_is_accepted_however_it_was_typed(value, expected_days):
    """A number field can hand back 7, 7.0 or "7" for the same keystroke."""
    days, problem = Plugin._resolve_retention_days(value)
    assert (days, problem) == (expected_days, None)


@pytest.mark.parametrize("value", [0, "0", None, "", -1, -7.0])
def test_a_value_meaning_keep_everything_resolves_to_zero_without_complaint(value):
    days, problem = Plugin._resolve_retention_days(value)
    assert days == 0
    assert problem is None, f"{value!r} means keep everything and is not a mistake"


@pytest.mark.parametrize("value", [3.7, "3.7", "seven", "1,5", object()])
def test_a_value_that_is_not_a_whole_number_of_days_reports_a_problem(value):
    days, problem = Plugin._resolve_retention_days(value)
    assert days == 0
    assert problem, f"{value!r} was accepted silently"


def test_the_pure_planner_and_the_wrapper_agree_about_what_a_value_means(plugin):
    """One definition of a readable number of days, not two that can drift.

    The planner used to run its own int() and would delete on a three day window
    for 3.7 while the wrapper decided the same value was fine.
    """
    entries = [(MINE + "%d.csv" % i, 1e9 - i * 86400.0 * 2) for i in range(6)]
    for value in (3.7, "3.7", 7.5, "seven", -1, 0, None, ""):
        days, _problem = Plugin._resolve_retention_days(value)
        plan = plugin._csv_exports_to_delete(entries, value, 1e9)
        if days <= 0:
            assert plan == [], f"{value!r} resolves to {days} days but deleted {len(plan)}"
