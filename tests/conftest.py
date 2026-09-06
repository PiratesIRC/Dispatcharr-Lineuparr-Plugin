"""Pytest configuration: mock Django and Dispatcharr modules so plugin can be imported."""
import sys
from unittest.mock import MagicMock

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
