"""Execute the report page's sorting script, rather than inspecting its markup.

tests/test_reports.py and tests/test_report_design.py prove the page CONTAINS a
sorting script and the header attributes it needs. They cannot prove the script
works, because pytest has no JavaScript engine, and a guard that only checks a
script tag is present is exactly the kind that passes for months while proving
nothing.

So this runs the shipped script in Node against a minimal document model built
from the real rendered page, and checks the row order that comes out. It is
skipped when Node is absent, so it is a local safety net rather than a gate.
Note that tests/ is not committed in this repository, so nothing here runs in
continuous integration by design.

The harness carries its own control: the second test below feeds it a script
that does nothing and requires it to fail.

Ported from the equivalent test in the sibling Channel-Maparr plugin, and
extended, because a Lineuparr report renders one table per section rather than
one table for the whole page.
"""
import pathlib
import shutil
import subprocess

import pytest

from Lineuparr import reports

HARNESS = pathlib.Path(__file__).resolve().parent / "report_sort_harness.js"

# Scores chosen to put rows in the first and the last score band, so the page
# renders more than one table. Channel numbers chosen so a text sort and a
# number sort disagree: as text the order is 10, 2, 3, and as numbers it is
# 2, 3, 10. Names chosen so a case sensitive sort puts the lowercase entry last.
COLUMNS = [("Channel", "Channel"), ("Number", "Number"), ("Score", "Score")]
ROWS = [
    {"Channel": "Charlie", "Number": 3, "Score": 100},
    {"Channel": "alpha", "Number": 10, "Score": 95},
    {"Channel": "Bravo", "Number": 2, "Score": 92},
    # The weak-match section, listed out of numeric order on purpose.
    {"Channel": "Delta", "Number": 40, "Score": 10},
    {"Channel": "Echo", "Number": 7, "Score": 5},
    {"Channel": "Foxtrot", "Number": 25, "Score": 1},
]


def _node():
    return shutil.which("node")


def _render(tmp_path):
    model = reports.build_model(
        "Sorting check", COLUMNS, ROWS, account_names=[], settings={},
        lineup="US_Test_lineup.json", version="test", now=1_700_000_000.0)
    page = tmp_path / "report.html"
    page.write_text(reports.render_html(model), encoding="utf-8")
    return page


def _run(page, script, tmp_path):
    path = tmp_path / "script.js"
    path.write_text(script, encoding="utf-8")
    return subprocess.run([_node(), str(HARNESS), str(page), str(path)],
                          capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(_node() is None, reason="Node is not installed on this machine")
def test_the_shipped_sorting_script_actually_sorts(tmp_path):
    result = _run(_render(tmp_path), reports._SORT_SCRIPT, tmp_path)
    assert result.returncode == 0, (
        "the sorting script did not behave as required:\n"
        + result.stdout + result.stderr)


@pytest.mark.skipif(_node() is None, reason="Node is not installed on this machine")
def test_the_harness_fails_a_script_that_does_nothing(tmp_path):
    """The control. Without it, a harness that parsed zero rows would report
    success forever and look identical to a real pass."""
    result = _run(_render(tmp_path),
                  "// this script deliberately does nothing\n", tmp_path)
    assert result.returncode != 0, (
        "the harness passed a script that does nothing, so it proves nothing:\n"
        + result.stdout)
