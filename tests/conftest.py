"""Pytest configuration: mock Django and Dispatcharr modules so plugin can be imported."""
import contextlib
import os
import sys
from unittest.mock import MagicMock

import pytest

# Mock Django and Dispatcharr dependencies before any test imports
_django_mods = [
    "django",
    "django.db",
    "django.db.transaction",
    "apps",
    "apps.channels",
    "apps.channels.models",
    "apps.m3u",
    "apps.m3u.models",
    "apps.epg",
    "apps.epg.models",
    "core",
    "core.utils",
]

for mod in _django_mods:
    sys.modules.setdefault(mod, MagicMock())

# Make transaction.atomic usable as a context manager/decorator
import django.db
django.db.transaction = MagicMock()
django.db.transaction.atomic = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))

# ---- gevent hub-hold guard (same as Sentinelarr's and Stream-Mapparr's) ------
# uWSGI runs this plugin under `gevent = 400` with early monkey-patching, so a
# thread here is a greenlet and code that runs without yielding holds the whole
# worker. hub_hold(max_ms) fails a test when the body runs longer than max_ms
# between two switches into the hub, measured by gevent.util.assert_switches
# (gevent pinned to the container's version, 26.8.0). Without gevent the guard
# skips locally but FAILS under CI (GitHub Actions sets CI=true), so a CI run
# cannot pass it vacuously.

def _import_gevent_util():
    try:
        import gevent.util
        return gevent.util
    except ImportError:
        if os.environ.get("CI"):
            pytest.fail("gevent is not installed; CI pins gevent==26.8.0 for this guard")
        pytest.skip("gevent is not installed (pip install gevent==26.8.0)")


@pytest.fixture
def hub_hold():
    util = _import_gevent_util()
    import gevent

    @contextlib.contextmanager
    def guard(max_ms):
        with util.assert_switches(max_blocking_time=max_ms / 1000.0, hub_only=True):
            # The tracer measures from the greenlet's LAST switch, not from
            # entering the block, so test setup that ran without yielding is
            # charged to the body (measured 2026-10-17 in Sentinelarr: a 5 ms
            # body failed a 50 ms limit). One switch to the hub starts it here.
            gevent.sleep(0)
            yield

    return guard
