"""The whole rendered report page, pinned byte for byte.

The guards in tests/test_report_design.py check the stylesheet's rules and the
sections' structure. Neither notices a change that is merely wrong to look at:
a heading that lost its count, a paragraph that moved, a table column that
vanished from the markup. A diff of a complete rendered page does.

A render change failing this test is the point of it rather than a nuisance.
When the change is wanted, LOOK at the new page first, then run:

    python tests/regenerate_report_fixture.py

The fixture contains invented channel names and a fixed timestamp, so nothing
from the live installation reaches it and regenerating without a code change
produces an identical file.
"""
import os
import sys

import pytest

# The generator sits beside this file and is a script rather than a package
# module, so its directory is not on the path when pytest is run from the
# repository root.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from regenerate_report_fixture import FIXTURE, render  # noqa: E402


def test_the_rendered_page_matches_the_committed_fixture():
    if not os.path.isfile(FIXTURE):
        pytest.fail(
            f"{FIXTURE} is missing. Create it with "
            f"python tests/regenerate_report_fixture.py")
    with open(FIXTURE, encoding="utf-8") as handle:
        expected = handle.read()
    actual = render()
    if actual == expected:
        return
    # A whole page is too long to read in an assertion message, so report the
    # first line that differs and leave the rest to a diff of the file.
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    # strict=False on purpose: a page with a different number of lines is a
    # real case, and it is reported after this loop rather than as a raise.
    for index, (want, got) in enumerate(
            zip(expected_lines, actual_lines, strict=False), start=1):
        if want != got:
            pytest.fail(
                f"the rendered page differs from the fixture at line {index}.\n"
                f"  fixture: {want[:200]}\n"
                f"  now:     {got[:200]}\n"
                f"If the change is wanted, run "
                f"python tests/regenerate_report_fixture.py")
    pytest.fail(
        f"the page has {len(actual_lines)} lines and the fixture has "
        f"{len(expected_lines)}. If the change is wanted, run "
        f"python tests/regenerate_report_fixture.py")


def test_the_fixture_names_nothing_from_the_live_installation():
    """The fixture is readable by anyone, so it must not disclose a channel
    lineup, a provider name or an M3U account label."""
    with open(FIXTURE, encoding="utf-8") as handle:
        text = handle.read()
    for name in ("Example", "EX_Example_lineup.json"):
        assert name in text, "the fixture is not the invented data set"
    assert "0.0.0-fixture" in text, "the fixture carries a real plugin version"


def test_regenerating_twice_produces_the_same_page():
    """The page must not contain the moment it was generated, or the fixture
    would fail on every run."""
    assert render() == render()
