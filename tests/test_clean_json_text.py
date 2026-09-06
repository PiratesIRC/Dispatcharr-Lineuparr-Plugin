"""Regression: invisible paste artifacts (BOM, zero-width spaces) must not
break custom_aliases JSON parsing.

Bug: user pasted JSON from a rich-text source that injected a leading zero-width
space (U+200B). Python's str.strip() doesn't strip it, so the field looked
non-empty; then json.loads failed with "Expecting value: line 1 column 1
(char 0)" even though the visible text was valid JSON.
"""
import json
import pytest

from Lineuparr.plugin import _clean_json_text


VALID_JSON = '{"FOX News Channel": ["FOX NEWS HD", "FoxNews"]}'


@pytest.mark.parametrize("prefix", [
    "\ufeff",          # UTF-8 BOM
    "\u200b",          # zero-width space
    "\u200c",          # zero-width non-joiner
    "\u200d",          # zero-width joiner
    "\u2060",          # word joiner
    "\ufeff\u200b",    # combined
    "  \ufeff  ",      # mixed whitespace + BOM
])
def test_invisible_prefix_is_stripped(prefix):
    cleaned = _clean_json_text(prefix + VALID_JSON)
    assert json.loads(cleaned) == {"FOX News Channel": ["FOX NEWS HD", "FoxNews"]}


def test_plain_json_unchanged():
    assert _clean_json_text(VALID_JSON) == VALID_JSON


def test_empty_string():
    assert _clean_json_text("") == ""


def test_none_like():
    assert _clean_json_text(None or "") == ""


def test_only_invisibles_returns_empty():
    assert _clean_json_text("\ufeff\u200b\u2060") == ""


def test_trailing_invisibles():
    cleaned = _clean_json_text(VALID_JSON + "\ufeff\u200b")
    assert json.loads(cleaned)
