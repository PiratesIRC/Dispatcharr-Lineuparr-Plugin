"""
Mock out Dispatcharr/Django dependencies so fuzzy_matcher tests can run
without a full Dispatcharr installation.
"""
import sys
from unittest.mock import MagicMock

_MOCKED = [
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

for _mod in _MOCKED:
    sys.modules.setdefault(_mod, MagicMock())
