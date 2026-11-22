"""User-facing file names and the CSV "# Generated:" line use Dispatcharr's timezone.

The worker that runs a Celery task keeps UTC, so datetime.now() named files in
UTC. format_local_timestamp reads the display timezone instead. Machine stamps
(the lock file, last_sync) keep their own clock and are not covered here.
"""
import datetime as dt
import re
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from Lineuparr import plugin as plugin_module
from Lineuparr import progress_status
from Lineuparr.plugin import Plugin

# 2026-11-22 18:00 UTC is 12:00 in Chicago (CST, UTC-6).
UTC_EVENING = dt.datetime(2026, 11, 22, 18, 0, 0, tzinfo=dt.timezone.utc).timestamp()


@pytest.fixture
def chicago(monkeypatch):
    monkeypatch.setattr(progress_status, "_display_tz", lambda: ZoneInfo("America/Chicago"))


def test_the_file_stamp_is_in_the_display_timezone(chicago):
    assert progress_status.format_local_timestamp(UTC_EVENING, "%Y%m%d_%H%M%S") == "20261122_120000"


def test_the_generated_line_is_in_the_display_timezone(chicago, monkeypatch):
    monkeypatch.setattr(plugin_module, "time", SimpleNamespace(time=lambda: UTC_EVENING))
    header = Plugin.__new__(Plugin)._generate_csv_header_comments({})
    assert "# Generated: 2026-11-22 12:00:00" in header


def test_no_user_facing_name_is_built_from_datetime_now():
    source = (Path(plugin_module.__file__)).read_text(encoding="utf-8")
    assert not re.search(r"datetime\.now\(\)\.strftime", source)
