#!/usr/bin/env python3
"""Regenerate the committed sample report that tests/test_report_fixture.py pins.

    python tests/regenerate_report_fixture.py

The fixture is a WHOLE RENDERED PAGE. A render change failing its test is the
point of it rather than a nuisance: the four token guards in
tests/test_report_design.py check the stylesheet's rules, and the structural
tests check counts and section shape, but neither notices a change that is
merely wrong to look at. A diff of the rendered page does.

Run this only after LOOKING at the new page and deciding the change is wanted.

The data below is fixed and fake. Nothing from the live installation reaches
it, so the fixture can be read by anyone without disclosing a channel lineup, a
provider name or an M3U account label. The timestamp is a constant for the same
reason a random value would be wrong here: the fixture has to be reproducible,
so the page must not contain the moment it was generated.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "sample_report.html")

# A fixed instant, so regenerating without a code change produces an identical
# file. 2026-06-06 12:00:00 UTC.
FIXED_NOW = 1780488000.0

COLUMNS = [("Channel", "Channel"), ("Number", "Number"),
           ("Score", "Score"), ("Best Match", "Best match"),
           ("Match Type", "Match type")]

# Invented names covering all three score bands, an empty match, and text that
# exercises the HTML escaping.
ROWS = [
    {"Channel": "Example News HD", "Number": 101, "Score": 100,
     "Best Match": "EX: EXAMPLE NEWS HD", "Match Type": "exact"},
    {"Channel": "Example Sport 1", "Number": 102, "Score": 96,
     "Best Match": "EX: EXAMPLE SPORT ONE", "Match Type": "alias"},
    {"Channel": "Example Movies", "Number": 103, "Score": 91,
     "Best Match": "EX: EXAMPLE MOVIES HD", "Match Type": "fuzzy"},
    {"Channel": "Example Two & Three", "Number": 201, "Score": 78,
     "Best Match": "EX: EXAMPLE 2 AND 3", "Match Type": "fuzzy"},
    {"Channel": "Example Kids <Regional>", "Number": 202, "Score": 64,
     "Best Match": "EX: EXAMPLE KIDS", "Match Type": "fuzzy"},
    {"Channel": "Example Documentary", "Number": 301, "Score": 41,
     "Best Match": "EX: EXAMPLE DOCS", "Match Type": "weak"},
    {"Channel": "Example Local", "Number": 302, "Score": 0,
     "Best Match": "", "Match Type": ""},
]

SETTINGS = {"match_sensitivity": 70, "category_detail": "detailed",
            "channel_numbering": "lineup", "preserve_existing_streams": True}


def load_reports():
    """Import the renderer without importing the package.

    Lineuparr/__init__.py imports plugin.py, which imports Django and the
    Dispatcharr models, neither of which exists outside the container.
    """
    path = os.path.join(os.path.dirname(HERE), "Lineuparr", "reports.py")
    spec = importlib.util.spec_from_file_location("lineuparr_reports", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render():
    reports = load_reports()
    model = reports.build_model(
        "Sample report", COLUMNS, ROWS,
        account_names=[], settings=SETTINGS,
        lineup="EX_Example_lineup.json", version="0.0.0-fixture",
        now=FIXED_NOW, export_filename="sample_export.csv")
    return reports.render_html(model)


def main():
    page = render()
    os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
    previous = None
    if os.path.isfile(FIXTURE):
        with open(FIXTURE, encoding="utf-8") as handle:
            previous = handle.read()
    if previous == page:
        print("fixture unchanged")
        return 0
    with open(FIXTURE, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(page)
    was = "created" if previous is None else "updated"
    print(f"{was} {FIXTURE} ({len(page)} characters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
