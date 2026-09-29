"""Automatic Full Sync (GitHub issue #31): an optional django-celery-beat
schedule that reuses the exact manual Full Sync path.

Covers the three constraints the maintainer's issue reply set before
accepting a PR, plus three more raised in code review of this PR's first
version:

  1. The periodic task must be routed to the "dvr" Celery queue, not the
     default "celery" queue - _reconcile_schedule sets queue="dvr" on the
     PeriodicTask row itself.
  2. A scheduled run must take the operation lock itself, as the owner for
     the whole run, and skip (not overlap) if a different Plugin instance
     already holds it - run_scheduled_full_sync. Its own groups, channels,
     stream match, EPG match and logo assignment steps reacquire that same
     lock and succeed (reentrant, via an owner token) rather than
     rejecting themselves - _acquire_lock / TestAcquireLockReentrancy.
  3. Off by default, and the last scheduled run's outcome (including a
     skip or a failure) must be visible via Validate Settings / Plugin
     Status - _schedule_status_row / _load_schedule_state.
  4. The default Time Zone must come from Dispatcharr's own
     CoreSettings.get_system_time_zone(), not Django's settings.TIME_ZONE
     (which Dispatcharr pins to UTC regardless of the admin-configured
     zone) - _detected_timezone / TestDetectedTimezone.
  5. _reconcile_schedule, called on every action dispatch, must skip its
     database query when nothing relevant to the schedule changed since
     the last call - TestReconcileSkipsTheQueryWhenUnchanged.

django-celery-beat is not a dependency of this plugin's own test suite (it
is Dispatcharr's), so these tests fake its two models rather than importing
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


@pytest.fixture
def fake_core_settings(monkeypatch):
    """Install a fake core.models.CoreSettings so
    `from core.models import CoreSettings` resolves inside _detected_timezone
    without a real Dispatcharr/Django environment, the same way
    fake_celery_beat fakes django_celery_beat.models.

    Returns the setter used by each test to control what
    get_system_time_zone() reports.
    """
    fake_module = types.ModuleType("core.models")

    class _FakeCoreSettings:
        _zone = None

        @staticmethod
        def get_system_time_zone():
            return _FakeCoreSettings._zone

    fake_module.CoreSettings = _FakeCoreSettings
    monkeypatch.setitem(sys.modules, "core.models", fake_module)

    def _set(zone):
        _FakeCoreSettings._zone = zone

    yield _set
    _FakeCoreSettings._zone = None


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

    def test_prefers_core_settings_system_time_zone_over_the_env_var(self, plugin, monkeypatch, fake_core_settings):
        """Regression from code review: Django's settings.TIME_ZONE is
        pinned to UTC by Dispatcharr regardless of the admin-configured
        zone, so checking it first always returned "UTC" - a valid IANA
        name - without ever reaching CoreSettings or
        DISPATCHARR_TIME_ZONE. Measured live on a container whose system
        time zone was America/Chicago. CoreSettings.get_system_time_zone()
        is Dispatcharr's own setting and must win even when a different
        value is sitting in the env var too."""
        fake_core_settings("America/Chicago")
        monkeypatch.setenv("DISPATCHARR_TIME_ZONE", "Europe/London")
        assert plugin._detected_timezone() == "America/Chicago"

    def test_falls_back_to_the_env_var_when_core_settings_has_no_zone(self, plugin, monkeypatch, fake_core_settings):
        fake_core_settings(None)
        monkeypatch.setenv("DISPATCHARR_TIME_ZONE", "America/Chicago")
        assert plugin._detected_timezone() == "America/Chicago"


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


class TestReconcileSkipsTheQueryWhenUnchanged:
    """Requirement 3 from code review: _reconcile_schedule runs on every
    action dispatch (there is no settings-changed hook), which meant a
    DELETE or an update_or_create on every single button press, including
    read-only ones like Plugin Status. It now compares a hash of what it is
    about to write against a hash of what it wrote last time, stored in
    SCHEDULE_RECONCILE_HASH_FILE, and skips the database call entirely when
    nothing relevant changed.
    """

    @staticmethod
    def _count_calls(monkeypatch, obj, method_name):
        calls = []
        original = getattr(obj, method_name)

        def _wrapped(*args, **kwargs):
            calls.append(True)
            return original(*args, **kwargs)
        monkeypatch.setattr(obj, method_name, _wrapped)
        return calls

    def test_an_unchanged_daily_schedule_only_writes_once(self, plugin, logger, fake_celery_beat, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "SCHEDULE_RECONCILE_HASH_FILE", str(tmp_path / "hash.json"))
        calls = self._count_calls(monkeypatch, _FakePeriodicTask.objects, "update_or_create")
        settings = {"auto_full_sync": "daily", "auto_full_sync_time": "03:00"}

        plugin._reconcile_schedule(settings, logger)
        plugin._reconcile_schedule(dict(settings), logger)
        plugin._reconcile_schedule(dict(settings), logger)

        assert len(calls) == 1

    def test_a_changed_setting_writes_again(self, plugin, logger, fake_celery_beat, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "SCHEDULE_RECONCILE_HASH_FILE", str(tmp_path / "hash.json"))
        calls = self._count_calls(monkeypatch, _FakePeriodicTask.objects, "update_or_create")

        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"}, logger)
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "04:00"}, logger)

        assert len(calls) == 2

    def test_an_unrelated_setting_change_still_refreshes_the_kwargs_snapshot(self, plugin, logger, fake_celery_beat, tmp_path, monkeypatch):
        """The settings snapshot stored in the PeriodicTask row's kwargs is
        what the scheduled task actually runs with, so a change to a
        setting outside the schedule fields (e.g. the selected lineup file)
        must still refresh it, not be skipped as "unchanged"."""
        monkeypatch.setattr(PluginConfig, "SCHEDULE_RECONCILE_HASH_FILE", str(tmp_path / "hash.json"))
        calls = self._count_calls(monkeypatch, _FakePeriodicTask.objects, "update_or_create")

        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "03:00", "lineup_file": "US_Foo.json"}, logger)
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "03:00", "lineup_file": "US_Bar.json"}, logger)

        assert len(calls) == 2
        task = _FakePeriodicTask._rows[PluginConfig.SCHEDULE_TASK_NAME]
        assert json.loads(task.kwargs)["settings"]["lineup_file"] == "US_Bar.json"

    def test_repeatedly_off_only_queries_once(self, plugin, logger, fake_celery_beat, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "SCHEDULE_RECONCILE_HASH_FILE", str(tmp_path / "hash.json"))
        # Establish a schedule first so "off" actually has something to delete.
        plugin._reconcile_schedule({"auto_full_sync": "daily", "auto_full_sync_time": "03:00"}, logger)

        calls = self._count_calls(monkeypatch, _FakePeriodicTask.objects, "filter")
        plugin._reconcile_schedule({"auto_full_sync": "off"}, logger)
        plugin._reconcile_schedule({"auto_full_sync": "off"}, logger)
        plugin._reconcile_schedule({"auto_full_sync": "off"}, logger)

        assert len(calls) == 1
        assert PluginConfig.SCHEDULE_TASK_NAME not in _FakePeriodicTask._rows


# --- run_scheduled_full_sync: locking + status recording --------------------

class TestRunScheduledFullSyncLocking:
    """Requirement 2: skip rather than overlap when another run is in
    progress. Regression from code review on this PR: an earlier version of
    run_scheduled_full_sync only peeked at the lock (_is_operation_locked)
    instead of holding it, so a manual action - running on Dispatcharr's
    own Plugin instance, separate from the one the scheduled task creates -
    could start partway through a scheduled run and touch the same
    groups/channels rows. It now acquires the lock itself as the owner for
    the whole run; _do_full_sync's own groups, channels, stream match, EPG
    match and logo assignment steps each reacquire the same lock on this
    same Plugin instance and succeed (reentrant, via the owner token
    _acquire_lock stores in the lock file) instead of finding it already
    held and rejecting themselves - the self-deadlock measured when an
    even earlier version wrapped _do_full_sync in its own acquire without
    that reentrancy: exactly three "Operation locked" warnings from one
    single invocation, and no sync ever completed.
    """

    def test_skips_and_records_it_when_a_different_instance_holds_the_lock(self, plugin, logger, tmp_path, monkeypatch):
        """A manual action runs on Dispatcharr's own Plugin instance, never
        the one the scheduled task creates - simulate that with a second,
        separate Plugin instance holding the lock."""
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))
        other = Plugin.__new__(Plugin)
        assert other._acquire_lock(logger) is True  # a different Plugin instance holding it

        do_full_sync_called = []
        monkeypatch.setattr(plugin, "_do_full_sync", lambda settings, logger: do_full_sync_called.append(True))

        result = plugin.run_scheduled_full_sync({}, logger)

        assert result == {"status": "skipped"}
        assert not do_full_sync_called  # never even attempted
        state = plugin._load_schedule_state()
        assert state["status"] == "skipped"

    def test_holds_the_lock_across_do_full_syncs_own_reacquires(self, plugin, logger, tmp_path, monkeypatch):
        """The regression test: _do_full_sync's own sub-steps reacquire the
        same lock, on this same Plugin instance, and succeed - rather than
        finding it already held by this same run and rejecting themselves.
        The lock file exists throughout, proving the outer acquire from
        run_scheduled_full_sync is the one actually holding it, and an
        inner release never removes it early."""
        lock_path = tmp_path / "op.lock"
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(lock_path))
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))

        acquired_count = []

        def _fake_do_full_sync(settings, logger):
            # Mimics what _do_full_sync's real sub-steps do: acquire and
            # release the lock themselves, more than once, on this same
            # Plugin instance.
            for _ in range(5):
                assert plugin._acquire_lock(logger) is True, (
                    "a sub-step on the same instance must reuse the outer run's "
                    "lock, not reject itself"
                )
                assert lock_path.exists()
                acquired_count.append(True)
                plugin._release_lock(logger)
                assert lock_path.exists(), "an inner release must not remove the lock while the outer run still holds it"

        monkeypatch.setattr(plugin, "_do_full_sync", _fake_do_full_sync)

        result = plugin.run_scheduled_full_sync({}, logger)

        assert result == {"status": "ok"}
        assert len(acquired_count) == 5
        assert not lock_path.exists()  # released for good once the outer run finished
        state = plugin._load_schedule_state()
        assert state["status"] == "ok"

    def test_records_failure_and_still_releases_the_lock(self, plugin, logger, tmp_path, monkeypatch):
        lock_path = tmp_path / "op.lock"
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(lock_path))
        monkeypatch.setattr(PluginConfig, "SCHEDULE_STATE_FILE", str(tmp_path / "sched.json"))

        def _boom(settings, logger):
            raise RuntimeError("stream matching blew up")
        monkeypatch.setattr(plugin, "_do_full_sync", _boom)

        result = plugin.run_scheduled_full_sync({}, logger)

        assert result["status"] == "error"
        assert "stream matching blew up" in result["error"]
        assert not lock_path.exists()  # released even on failure
        state = plugin._load_schedule_state()
        assert state["status"] == "error"


class TestAcquireLockReentrancy:
    """_acquire_lock's owner-token mechanism, isolated from
    run_scheduled_full_sync: the behavior the three _do_apply_* steps,
    _sync_groups and _do_sync_channels all now depend on."""

    def test_a_second_call_on_the_same_instance_is_reentrant(self, plugin, logger, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        assert plugin._acquire_lock(logger) is True
        assert plugin._acquire_lock(logger) is True  # same instance - extends, does not reject

    def test_a_different_instance_is_rejected_while_held(self, plugin, logger, tmp_path, monkeypatch):
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(tmp_path / "op.lock"))
        assert plugin._acquire_lock(logger) is True
        other = Plugin.__new__(Plugin)
        assert other._acquire_lock(logger) is False

    def test_an_inner_release_does_not_unlock_for_an_outer_holder(self, plugin, logger, tmp_path, monkeypatch):
        lock_path = tmp_path / "op.lock"
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(lock_path))
        assert plugin._acquire_lock(logger) is True  # outer
        assert plugin._acquire_lock(logger) is True  # inner (reentrant)
        plugin._release_lock(logger)  # inner's release
        assert lock_path.exists(), "the outer acquire still holds it"
        plugin._release_lock(logger)  # outer's release
        assert not lock_path.exists()

    def test_reentrant_acquire_refreshes_a_near_stale_timestamp(self, plugin, logger, tmp_path, monkeypatch):
        """The staleness-related case from code review: with the lock held
        only by short-lived sub-steps, one running past
        OPERATION_LOCK_TIMEOUT_MINUTES let a second scheduled run start
        while the first was still going. A reentrant call from the same
        owner now refreshes the timestamp instead of being evaluated
        against it, so a long-running step no longer makes its own run's
        lock look stale to a genuinely different instance."""
        lock_path = tmp_path / "op.lock"
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(lock_path))
        assert plugin._acquire_lock(logger) is True

        old_time = (datetime.now() - timedelta(minutes=15)).isoformat()
        data = json.loads(lock_path.read_text())
        data["timestamp"] = old_time
        lock_path.write_text(json.dumps(data))

        assert plugin._acquire_lock(logger) is True  # same owner - refreshes, ignores staleness

        other = Plugin.__new__(Plugin)
        assert other._acquire_lock(logger) is False  # still genuinely held, from a different instance's view

    def test_a_genuinely_stale_lock_from_a_different_instance_is_overridden(self, plugin, logger, tmp_path, monkeypatch):
        lock_path = tmp_path / "op.lock"
        monkeypatch.setattr(PluginConfig, "OPERATION_LOCK_FILE", str(lock_path))
        old_time = (datetime.now() - timedelta(minutes=20)).isoformat()
        lock_path.write_text(json.dumps({"timestamp": old_time, "action": "lineuparr", "owner": "some-other-owner"}))

        assert plugin._acquire_lock(logger) is True


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
