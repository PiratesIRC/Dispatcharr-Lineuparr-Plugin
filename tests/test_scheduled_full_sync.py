"""Automatic Full Sync (GitHub issue #31): an optional django-celery-beat
schedule that reuses the exact manual Full Sync path.

Covers the three constraints the maintainer's issue reply set before
accepting a PR:

  1. The periodic task must be routed to the "dvr" Celery queue, not the
     default "celery" queue - _reconcile_schedule sets queue="dvr" on the
     PeriodicTask row itself.
  2. A scheduled run must take the same operation lock Full Sync's sub-steps
     already use, and skip (not overlap) if it's held -
     run_scheduled_full_sync.
  3. Off by default, and the last scheduled run's outcome (including a
     skip or a failure) must be visible via Validate Settings / Plugin
     Status - _schedule_status_row / _load_schedule_state.

django-celery-beat is not a dependency of this plugin's own test suite (it's
Dispatcharr's), so these tests fake its two models rather than importing
them, the same way tests/test_group_prefix_only.py fakes ChannelGroup.
"""
import json
import logging
import sys
import types
from datetime import datetime, timedelta

import pytest

from Lineuparr.plugin import Plugin, PluginConfig


@pytest.fixture
def plugin():
    return Plugin.__new__(Plugin)


@pytest.fixture
def logger():
    return logging.getLogger("test")


# --- Fakes for django_celery_beat.models -----------------------------------

class _FakeCrontabSchedule:
    _rows = []

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    class objects:
        @staticmethod
        def get_or_create(**kwargs):
            for row in _FakeCrontabSchedule._rows:
                if row.__dict__ == kwargs:
                    return row, False
            row = _FakeCrontabSchedule(**kwargs)
            _FakeCrontabSchedule._rows.append(row)
            return row, True


class _FakePeriodicTask:
    _rows = {}  # name -> _FakePeriodicTask

    def __init__(self, name, **kwargs):
        self.name = name
        for k, v in kwargs.items():
            setattr(self, k, v)

    class objects:
        @staticmethod
        def update_or_create(name, defaults):
            existing = _FakePeriodicTask._rows.get(name)
            created = existing is None
            if existing is None:
                existing = _FakePeriodicTask(name, **defaults)
                _FakePeriodicTask._rows[name] = existing
            else:
                for k, v in defaults.items():
                    setattr(existing, k, v)
            return existing, created

        @staticmethod
        def filter(name=None):
            match = _FakePeriodicTask._rows.get(name)
            return _FakeQuerySet([match] if match else [])


class _FakeQuerySet:
    def __init__(self, items):
        self._items = items

    def delete(self):
        count = len(self._items)
        for item in self._items:
            _FakePeriodicTask._rows.pop(item.name, None)
        return count, {}

    def first(self):
        return self._items[0] if self._items else None


@pytest.fixture(autouse=True)
def _reset_fakes():
    _FakeCrontabSchedule._rows = []
    _FakePeriodicTask._rows = {}
    yield
    _FakeCrontabSchedule._rows = []
    _FakePeriodicTask._rows = {}


@pytest.fixture
def fake_celery_beat(monkeypatch):
    """Install a fake django_celery_beat.models module so
    `from django_celery_beat.models import PeriodicTask, CrontabSchedule`
    resolves inside _reconcile_schedule without the real package installed.

    Also installs a fake celery.schedules.crontab, used only to validate a
    Custom Cron Expression's syntax before writing it to the database. Mimics
    celery's own range checks (minute 0-59, hour 0-23, day_of_month 1-31,
    month_of_year 1-12) closely enough to exercise the validation wiring,
    without depending on the real celery package.
    """
    fake_models = types.ModuleType("django_celery_beat.models")
    fake_models.PeriodicTask = _FakePeriodicTask
    fake_models.CrontabSchedule = _FakeCrontabSchedule
    fake_pkg = types.ModuleType("django_celery_beat")
    fake_pkg.models = fake_models
    monkeypatch.setitem(sys.modules, "django_celery_beat", fake_pkg)
    monkeypatch.setitem(sys.modules, "django_celery_beat.models", fake_models)

    def _fake_crontab_validator(minute, hour, day_of_month, month_of_year, day_of_week):
        def _check(field, low, high):
            if field == "*":
                return
            for part in field.split(","):
                if not part.lstrip("-").isdigit() or not (low <= int(part) <= high):
                    raise ValueError(f"invalid field {field!r} (expected {low}-{high} or *)")
        _check(minute, 0, 59)
        _check(hour, 0, 23)
        _check(day_of_month, 1, 31)
        _check(month_of_year, 1, 12)
        _check(day_of_week, 0, 6)

    fake_schedules = types.ModuleType("celery.schedules")
    fake_schedules.crontab = _fake_crontab_validator
    fake_celery_pkg = types.ModuleType("celery")
    fake_celery_pkg.schedules = fake_schedules
    monkeypatch.setitem(sys.modules, "celery", fake_celery_pkg)
    monkeypatch.setitem(sys.modules, "celery.schedules", fake_schedules)

    return fake_models


class TestReconcileScheduleQueueRouting:
    """Requirement 1: every periodic task this plugin creates must be
    routed to the 'dvr' queue."""

    def test_daily_schedule_is_routed_to_the_dvr_queue(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule(
            {"auto_full_sync": "daily", "auto_full_sync_time": "03:30"}, logger
        )
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.queue == "dvr"
        assert task.task == PluginConfig.SCHEDULED_TASK_CELERY_NAME
        assert task.enabled is True

    def test_weekly_schedule_sets_the_chosen_day(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule(
            {"auto_full_sync": "weekly", "auto_full_sync_time": "04:15", "auto_full_sync_day": "friday"},
            logger,
        )
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.day_of_week == "fri"
        assert task.crontab.hour == "4"
        assert task.crontab.minute == "15"

    def test_settings_snapshot_is_stored_on_the_task(self, plugin, logger, fake_celery_beat):
        settings = {"auto_full_sync": "daily", "auto_full_sync_time": "03:00", "lineup_file": "US_Foo.json"}
        plugin._reconcile_schedule(settings, logger)
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        stored = json.loads(task.kwargs)
        assert stored["settings"]["lineup_file"] == "US_Foo.json"


class TestReconcileScheduleMonthly:
    def test_monthly_sets_the_chosen_day_of_month(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule(
            {"auto_full_sync": "monthly", "auto_full_sync_time": "02:00", "auto_full_sync_day_of_month": 15},
            logger,
        )
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.day_of_month == "15"
        assert task.crontab.day_of_week == "*"
        assert task.crontab.hour == "2"
        assert task.crontab.minute == "0"
        assert task.queue == "dvr"

    def test_monthly_defaults_to_day_1_when_missing(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule({"auto_full_sync": "monthly", "auto_full_sync_time": "02:00"}, logger)
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.day_of_month == "1"

    @pytest.mark.parametrize("bad_day", [0, 32, -1, "not-a-number"])
    def test_invalid_day_of_month_does_not_create_a_schedule(self, plugin, logger, fake_celery_beat, bad_day):
        plugin._reconcile_schedule(
            {"auto_full_sync": "monthly", "auto_full_sync_time": "02:00", "auto_full_sync_day_of_month": bad_day},
            logger,
        )
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows


class TestReconcileScheduleCustom:
    def test_valid_custom_cron_creates_the_schedule_with_exact_fields(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule(
            {"auto_full_sync": "custom", "auto_full_sync_cron": "15 4 1 * 0"},
            logger,
        )
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.minute == "15"
        assert task.crontab.hour == "4"
        assert task.crontab.day_of_month == "1"
        assert task.crontab.month_of_year == "*"
        assert task.crontab.day_of_week == "0"
        assert task.queue == "dvr"

    def test_custom_ignores_sync_time_and_day_settings(self, plugin, logger, fake_celery_beat):
        # Sync Time/Sync Day are for daily/weekly/monthly only - custom must
        # use only the cron expression, even if the other fields are also set
        # (e.g. left over from switching modes in the UI).
        plugin._reconcile_schedule(
            {
                "auto_full_sync": "custom",
                "auto_full_sync_cron": "0 3 * * *",
                "auto_full_sync_time": "23:59",
                "auto_full_sync_day": "sunday",
                "auto_full_sync_day_of_month": 31,
            },
            logger,
        )
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.hour == "3"
        assert task.crontab.minute == "0"

    @pytest.mark.parametrize("bad_cron", [
        "0 3 * *",       # only 4 fields
        "0 3 * * * *",   # 6 fields
        "",               # blank
        "99 3 * * *",    # minute out of range
        "0 25 * * *",    # hour out of range
    ])
    def test_invalid_custom_cron_does_not_create_a_schedule(self, plugin, logger, fake_celery_beat, bad_cron):
        plugin._reconcile_schedule({"auto_full_sync": "custom", "auto_full_sync_cron": bad_cron}, logger)
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows


class TestReconcileScheduleTimeZone:
    """Regression: tested live against a real install with
    DISPATCHARR_TIME_ZONE=America/New_York - a schedule created without an
    explicit timezone fired on UTC instead of the box's local time, silently
    missing the intended run. The Time Zone setting must be explicit and
    passed straight through, never inferred from the server."""

    def test_explicit_timezone_is_passed_to_the_crontab(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule(
            {"auto_full_sync": "daily", "auto_full_sync_time": "03:00", "auto_full_sync_timezone": "America/New_York"},
            logger,
        )
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.timezone == "America/New_York"

    def test_missing_timezone_defaults_to_utc(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"}, logger)
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.timezone == "UTC"

    def test_unrecognized_timezone_does_not_create_a_schedule(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule(
            {"auto_full_sync": "daily", "auto_full_sync_time": "03:00", "auto_full_sync_timezone": "Not/A_Real_Zone"},
            logger,
        )
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows

    def test_blank_timezone_uses_the_detected_default(self, plugin, logger, fake_celery_beat, monkeypatch):
        monkeypatch.setattr(plugin, "_detected_timezone", lambda: "Europe/London")
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"}, logger)
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.timezone == "Europe/London"


class TestDetectedTimezone:
    """_detected_timezone offers Dispatcharr's own configured zone as the
    Time Zone field's default, so a fresh install doesn't have to already
    know its IANA name. Never authoritative once a setting is actually saved."""

    def test_falls_back_to_utc_with_nothing_configured(self, plugin, monkeypatch):
        monkeypatch.delenv("DISPATCHARR_TIME_ZONE", raising=False)
        assert plugin._detected_timezone() == "UTC"

    def test_reads_the_dispatcharr_time_zone_env_var(self, plugin, monkeypatch):
        monkeypatch.setenv("DISPATCHARR_TIME_ZONE", "America/Chicago")
        assert plugin._detected_timezone() == "America/Chicago"

    def test_an_invalid_env_var_value_falls_back_to_utc(self, plugin, monkeypatch):
        monkeypatch.setenv("DISPATCHARR_TIME_ZONE", "Not/A_Real_Zone")
        assert plugin._detected_timezone() == "UTC"

    def test_the_settings_field_defaults_to_the_detected_zone(self, monkeypatch):
        monkeypatch.setenv("DISPATCHARR_TIME_ZONE", "America/Chicago")
        fields = Plugin.__new__(Plugin).fields
        tz_field = next(f for f in fields if f.get("id") == "auto_full_sync_timezone")
        assert tz_field["default"] == "America/Chicago"


class TestReconcileScheduleOffByDefault:
    def test_off_creates_nothing(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule({"auto_full_sync": "off"}, logger)
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows

    def test_missing_setting_defaults_to_off(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule({}, logger)
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows

    def test_turning_off_removes_an_existing_schedule(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"}, logger)
        assert PluginConfig.SCHEDULE_TASK_NAME in _FakePeriodicTask._rows

        plugin._reconcile_schedule({"auto_full_sync": "off"}, logger)
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows

    def test_django_celery_beat_not_installed_does_not_raise(self, plugin, logger):
        # No fake_celery_beat fixture here - the import genuinely fails.
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"}, logger)


class TestReconcileScheduleValidation:
    @pytest.mark.parametrize("bad_time", ["not-a-time", "25:00", "12:60", "12"])
    def test_invalid_time_does_not_create_a_schedule(self, plugin, logger, fake_celery_beat, bad_time):
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": bad_time}, logger)
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows

    def test_empty_time_falls_back_to_the_default(self, plugin, logger, fake_celery_beat):
        # Consistent with how every other setting in this plugin treats "":
        # settings.get(x) or default, not an error.
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": ""}, logger)
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert task.crontab.hour == "3"
        assert task.crontab.minute == "0"

    def test_invalid_day_does_not_create_a_schedule(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule(
            {"auto_full_sync": "weekly", "auto_full_sync_time": "03:00", "auto_full_sync_day": "someday"},
            logger,
        )
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows

    def test_invalid_mode_does_not_create_a_schedule(self, plugin, logger, fake_celery_beat):
        plugin._reconcile_schedule({"auto_full_sync": "hourly"}, logger)
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows


# --- run_scheduled_full_sync: locking + status recording --------------------

class TestRunScheduledFullSyncLocking:
    """Requirement 2: skip rather than overlap when another run is in
    progress. Regression: run_scheduled_full_sync must NOT wrap
    _do_full_sync in its own _acquire_lock/_release_lock - _do_full_sync's
    own sub-steps (stream match, EPG match, logo assignment) already take
    that same lock around their own work, exactly like the manual Full Sync
    button. An outer acquisition here holds the lock across all of them, so
    each one sees it already held (by this same run) and rejects itself -
    measured live: exactly three "Operation locked" warnings from one
    single invocation, and no sync ever completed."""

    def test_skips_and_records_it_when_something_is_already_running(self, plugin, logger, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))
        assert plugin._acquire_lock(logger) is True  # simulate another op holding it
        do_full_sync_called = []
        monkeypatch.setattr(plugin, "_do_full_sync", lambda settings, logger: do_full_sync_called.append(True))

        result = plugin.run_scheduled_full_sync({}, logger)

        assert result == {"status": "skipped"}
        assert not do_full_sync_called  # never even attempted
        state = plugin._load_schedule_state()
        assert state["status"] == "skipped"

    def test_does_not_wrap_do_full_sync_in_its_own_lock(self, plugin, logger, tmp_path, monkeypatch):
        """The regression test: _do_full_sync must be free to take and
        release the lock itself, as many times as its own sub-steps need,
        without finding it already held by this caller."""
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))

        acquired_count = []

        def _fake_do_full_sync(settings, logger):
            # Mimics what _do_full_sync's real sub-steps do: acquire and
            # release the lock themselves, more than once.
            for _ in range(3):
                assert plugin._acquire_lock(logger) is True, "lock was already held - outer caller must not hold it"
                acquired_count.append(True)
                plugin._release_lock(logger)

        monkeypatch.setattr(plugin, "_do_full_sync", _fake_do_full_sync)

        result = plugin.run_scheduled_full_sync({}, logger)

        assert result == {"status": "ok"}
        assert len(acquired_count) == 3
        state = plugin._load_schedule_state()
        assert state["status"] == "ok"

    def test_records_failure_without_ever_holding_a_lock_itself(self, plugin, logger, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))

        def _boom(settings, logger):
            raise RuntimeError("stream matching blew up")
        monkeypatch.setattr(plugin, "_do_full_sync", _boom)

        result = plugin.run_scheduled_full_sync({}, logger)

        assert result["status"] == "error"
        assert "stream matching blew up" in result["error"]
        assert not (tmp_path / "op.lock").exists()  # never created one itself
        state = plugin._load_schedule_state()
        assert state["status"] == "error"


class TestIsOperationLocked:
    def test_false_when_no_lock_file(self, plugin, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        assert plugin._is_operation_locked() is False

    def test_true_when_a_fresh_lock_exists(self, plugin, logger, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        plugin._acquire_lock(logger)
        assert plugin._is_operation_locked() is True

    def test_false_when_the_lock_is_stale(self, plugin, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        old_time = (datetime.now() - timedelta(minutes=20)).isoformat()
        (tmp_path / "op.lock").write_text(json.dumps({"timestamp": old_time, "action": "lineuparr"}))
        assert plugin._is_operation_locked() is False

    def test_does_not_create_or_modify_anything(self, plugin, tmp_path, monkeypatch):
        lock_path = tmp_path / "op.lock"
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(lock_path))
        plugin._is_operation_locked()
        assert not lock_path.exists()  # a peek must never create the file


# --- Status visibility -------------------------------------------------------

class TestScheduleStatusRow:
    """Requirement 3: off by default, and status visible via Validate
    Settings / Plugin Status."""

    def test_off_reports_off(self, plugin):
        row = plugin._schedule_status_row({"auto_full_sync": "off"})
        assert row["Value"] == "Off"
        assert row["Status"] == "OK"

    def test_default_with_no_setting_at_all_is_off(self, plugin):
        row = plugin._schedule_status_row({})
        assert row["Value"] == "Off"

    def test_scheduled_but_never_run_says_so(self, plugin, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "does_not_exist.json"))
        row = plugin._schedule_status_row({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"})
        assert "daily" in row["Value"]
        assert "has not run yet" in row["Status"]

    def test_shows_the_last_recorded_outcome(self, plugin, logger, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))
        plugin._save_schedule_state({"ran_at": "2026-09-29T03:00:00", "status": "ok", "message": ""}, logger)

        row = plugin._schedule_status_row({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"})

        assert "2026-09-29T03:00:00" in row["Status"]
        assert "ok" in row["Status"]

    def test_plugin_status_includes_the_schedule_when_not_off(self, plugin, logger, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "PROGRESS_FILE", str(tmp_path / "progress.json"))
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))
        result = plugin._plugin_status({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"}, logger)
        assert "Automatic Full Sync" in result["message"]

    def test_plugin_status_omits_the_schedule_when_off(self, plugin, logger, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "PROGRESS_FILE", str(tmp_path / "progress.json"))
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))
        result = plugin._plugin_status({"auto_full_sync": "off"}, logger)
        assert "Automatic Full Sync" not in result["message"]


class TestReconcileCalledOnEveryAction:
    """There is no settings-changed hook in Dispatcharr's plugin API, so
    reconciliation has to piggyback on every action dispatch instead."""

    def test_run_calls_reconcile_schedule_before_dispatching(self, monkeypatch):
        plugin = Plugin.__new__(Plugin)
        calls = []
        monkeypatch.setattr(plugin, "_reconcile_schedule", lambda settings, logger: calls.append(settings))
        monkeypatch.setattr(plugin, "_plugin_status", lambda settings, logger: {"status": "ok", "message": "x"})

        plugin.run("plugin_status", {}, {"logger": logging.getLogger("test"), "settings": {"auto_full_sync": "daily"}})

        assert calls == [{"auto_full_sync": "daily"}]

    def test_a_broken_reconcile_does_not_block_the_requested_action(self, monkeypatch):
        plugin = Plugin.__new__(Plugin)

        def _boom(settings, logger):
            raise RuntimeError("schedule DB unreachable")
        monkeypatch.setattr(plugin, "_reconcile_schedule", _boom)
        monkeypatch.setattr(plugin, "_plugin_status", lambda settings, logger: {"status": "ok", "message": "fine"})

        result = plugin.run("plugin_status", {}, {"logger": logging.getLogger("test"), "settings": {}})

        assert result == {"status": "ok", "message": "fine"}
