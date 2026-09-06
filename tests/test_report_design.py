"""The report page's design system: the token layer and the section structure.

Adopted from the Dustarr plugin's report. These guards are copied from that
project's own suite because each one was a real defect there before it was
fixed, and nothing about this repository makes it immune to the same four.

The token values are NOT re-derivable here. The category colours and the two
surface colours were validated all pairs for colourblind safety against those
exact surfaces, with a validator that is not in this repository. Changing a
value here throws that away and leaves no way to check the result, which is why
they are pinned rather than merely used.
"""
import re

import pytest

from Lineuparr import reports

# Pinned. See the module docstring for why these are not to be edited without
# re-running a validator this repository does not contain.
PALETTE_LIGHT = {"--never": "#2a78d6", "--watched": "#1baf7a",
                 "--tuned": "#e34948", "--toonew": "#898781",
                 "--track": "#e1e0d9", "--ok": "#0ca30c", "--bad": "#d03b3b"}
PALETTE_DARK = {"--never": "#3987e5", "--watched": "#199e70",
                "--tuned": "#e66767", "--toonew": "#898781",
                "--track": "#2c2c2a", "--ok": "#0ca30c", "--bad": "#d03b3b"}

ROWS = [
    {"Channel": "BBC One HD", "Number": 101, "Score": 100, "Best Match": "UK: BBC ONE HD"},
    {"Channel": "Dave", "Number": 127, "Score": 72, "Best Match": "UK: DAVE"},
    {"Channel": "Really", "Number": 149, "Score": 0, "Best Match": ""},
]
COLUMNS = [("Channel", "Channel"), ("Number", "Number"),
           ("Score", "Score"), ("Best Match", "Best match")]


def build(rows=None, columns=None):
    return reports.build_model(
        "Demo", columns or COLUMNS, ROWS if rows is None else rows,
        account_names=[], settings={}, lineup="UK_Combined_lineup.json",
        version="1.0.0", now=1_700_000_000.0)


# Everything after the `body {` rule is page styling rather than token
# declarations. Colours and spacing there must come from var(), not literals.
#
# COMMENTS ARE STRIPPED FIRST, and that is load bearing rather than tidiness:
# these rules are explained in prose that necessarily quotes the very things
# they forbid. Without the strip every guard below fires on its own
# documentation, and the only way to make them pass is to delete the
# explanation.
def _rule_body(css):
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    return without_comments[without_comments.index("body {"):]


def _light(css):
    return css[:css.index("prefers-color-scheme: dark")]


def _dark(css):
    return css[css.index("prefers-color-scheme: dark"):]


# --------------------------------------------------------------------------- #
# The four token-layer guards
# --------------------------------------------------------------------------- #

def test_no_rule_hardcodes_a_colour_outside_the_token_block():
    """A literal hex in a rule is a colour that light mode and dark mode cannot
    both be right about. That is exactly how the previous stylesheet here ended
    up with four !important overrides in its dark block."""
    stray = sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", _rule_body(reports._CSS))))
    assert not stray, f"use a token instead of these literals: {stray}"


def test_every_spacing_value_comes_from_the_scale():
    """Margins, paddings and gaps pick a step off --s1 to --s5. Sizes, meaning
    widths, heights, font sizes, radii and borders, are not spacing and are
    deliberately out of scope."""
    offenders = []
    for prop, value in re.findall(r"\b(margin|padding|gap)\s*:\s*([^;}]+)",
                                  _rule_body(reports._CSS)):
        for token in value.split():
            if token.endswith("px") and token not in ("0", "0px"):
                offenders.append(f"{prop}: {token}")
    assert not offenders, f"off-scale spacing, use var(--sN): {offenders}"


def test_text_hierarchy_uses_ink_tokens_not_opacity():
    """An opacity value paints a different colour on every surface it lands on,
    so the contrast ratio moves whenever a background changes, and the fade
    applies to everything nested inside. This page has no decorative fill that
    needs one, so the correct number of uses is zero."""
    faded = re.findall(r"([^{}]+)\{[^}]*opacity:", _rule_body(reports._CSS))
    assert [s.strip() for s in faded] == [], faded


def test_the_measured_ink_ramp_is_pinned():
    """Measured against the two validated surfaces: --ink-dim is the weakest at
    5.24:1 on light and 6.89:1 on dark, both clear of the 4.5:1 floor for
    normal text. Changing a value here without re-measuring puts text below it
    with nothing to say so."""
    for var, value in (("--ink", "#16181d"), ("--ink-muted", "#5c616b"),
                       ("--ink-dim", "#656a76")):
        assert f"{var}: {value}" in _light(reports._CSS), f"{var} changed in light"
    for var, value in (("--ink", "#e8eaed"), ("--ink-muted", "#a7adb8"),
                       ("--ink-dim", "#9aa0ab")):
        assert f"{var}: {value}" in _dark(reports._CSS), f"{var} changed in dark"


# --------------------------------------------------------------------------- #
# The rest of the token layer
# --------------------------------------------------------------------------- #

def test_the_light_palette_is_pinned():
    for var, value in PALETTE_LIGHT.items():
        assert f"{var}: {value}" in _light(reports._CSS), f"{var} wrong in light"


def test_every_palette_var_is_redeclared_for_dark():
    for var, value in PALETTE_DARK.items():
        assert f"{var}: {value}" in _dark(reports._CSS), f"{var} wrong in dark"


def test_every_var_referenced_is_defined():
    """A var(--x) with no definition silently resolves to nothing. For a fill
    that means black, which is an invisible mark on the dark surface."""
    referenced = set(re.findall(r"var\((--[a-z0-9-]+)", reports._CSS))
    defined = set(re.findall(r"(--[a-z0-9-]+):", reports._CSS))
    assert referenced <= defined, f"undefined: {referenced - defined}"


def test_light_and_dark_differ_only_in_token_values():
    """Needing !important means the tokens are wrong. The previous stylesheet
    here had four."""
    assert "!important" not in reports._CSS


def test_zebra_striping_is_defined_once_for_both_themes():
    """Dustarr had striping in dark mode only for months, so the two themes
    rendered visibly different tables."""
    body = _rule_body(reports._CSS)
    assert body.count("tr:nth-child(even) td") == 1
    assert "var(--zebra)" in body


def test_the_type_scale_is_sparse():
    """The scale is 15, 17 and 22. A step 7 percent from the body size reads as
    an accident rather than a decision, and this page is sized for reading at
    television distance, so nothing here shrinks."""
    sizes = sorted({int(v) for v in
                    re.findall(r"font-size:\s*(\d+)px", _rule_body(reports._CSS))})
    assert sizes == [15, 17, 22], sizes


def test_the_focus_ring_on_a_section_heading_is_never_removed():
    """That ring is how this page is driven by a television remote control's
    directional pad.

    Comments are stripped first. The rule against removing the outline is
    stated in prose that necessarily quotes the declaration it forbids, so
    without the strip this guard fires on its own documentation and the only
    way to make it pass is to delete the explanation.
    """
    body = _rule_body(reports._CSS)
    assert "outline: none" not in body
    assert "outline:none" not in body


def test_no_svg_carries_a_colour_presentation_attribute():
    """fill="var(--x)" as a presentation attribute has patchy support and fails
    silently to black, which is invisible on the dark surface. Colour is
    applied through a CSS rule keyed on a class."""
    page = reports.render_html(build())
    assert 'fill="var(' not in page
    assert 'stroke="var(' not in page


# --------------------------------------------------------------------------- #
# Section structure
# --------------------------------------------------------------------------- #

def test_sections_are_details_elements_needing_no_javascript():
    """A client that does not implement details renders the content expanded.
    The failure mode is everything visible, never content lost."""
    page = reports.render_html(build())
    assert page.count("<details") == 3
    assert page.count("<summary>") == 3


def test_every_section_starts_collapsed():
    """The page is an index of what the run found, not a wall of tables."""
    page = reports.render_html(build())
    assert "<details open" not in page
    assert "<details>" in page


def test_every_section_carries_a_count_equal_to_its_own_row_count():
    """Same meaning in every section, no exceptions. A reader looking at a
    collapsed page cannot see any distinction that would justify one section
    omitting its number, and a bare section reads as one that forgot."""
    model = build()
    page = reports.render_html(model)
    counts = [int(n) for n in re.findall(r'<span class="count">(\d+)</span>', page)]
    groups = reports.group_entries(model)
    assert counts == [len(g["entries"]) for g in groups]
    assert sum(counts) == len(model["entries"]), "a row was dropped or duplicated"


def test_every_section_says_what_it_holds_and_carries_a_find_hint():
    """A conditional note cannot serve as a section's description: it renders
    only on some boxes, and every section is collapsed, so the summary line is
    all a reader has to decide whether to open it."""
    page = reports.render_html(build())
    bodies = page.split("<details")[1:]
    assert len(bodies) == 3
    for body in bodies:
        assert 'class="sub"' in body, "a section has no description"
        assert "Find in page" in body, "a section has no find-in-page hint"


def test_every_section_heading_carries_a_coloured_dot():
    page = reports.render_html(build())
    dots = re.findall(r'<span class="dot (dot-[a-z]+)"', page)
    assert len(dots) == 3
    assert all(f".{cls} {{" in reports._CSS for cls in set(dots)), dots


def test_the_glyph_is_keyed_on_the_colour_class_not_the_title():
    """A glyph keyed on a title can end up disagreeing with its colour."""
    for dot_class in reports._SECTION_GLYPH:
        assert dot_class.startswith("dot-")


def test_the_glyph_is_hidden_from_assistive_software():
    """It is decoration on top of the coloured dot and the words, never the
    only thing carrying the meaning."""
    page = reports.render_html(build())
    for chunk in page.split('<span class="glyph"')[1:]:
        assert 'aria-hidden="true"' in chunk.split(">")[0]


def test_a_shared_colour_with_no_honest_glyph_gets_none():
    """dot-neutral is used by more than one kind of section, and no single
    glyph fits them all."""
    assert reports._SECTION_GLYPH["dot-neutral"] == ""


# --------------------------------------------------------------------------- #
# Row grouping
# --------------------------------------------------------------------------- #

def test_a_score_column_splits_rows_into_bands():
    groups = reports.group_entries(build())
    assert [g["title"] for g in groups] == [
        "Strong matches", "Worth checking", "Weak or no match"]
    assert [len(g["entries"]) for g in groups] == [1, 1, 1]


def test_the_score_column_is_found_by_its_header_not_its_row_key():
    """The four callers use four different row keys for the same idea: Score,
    Best Score and confidence_score all display as a score."""
    columns = [("Channel", "Channel"), ("confidence_score", "Score")]
    rows = [{"Channel": "A", "confidence_score": 95},
            {"Channel": "B", "confidence_score": 10}]
    groups = reports.group_entries(build(rows=rows, columns=columns))
    assert [len(g["entries"]) for g in groups] == [1, 0, 1]


def test_a_row_with_no_number_in_its_score_cell_counts_as_no_match():
    rows = [{"Channel": "A", "Number": 1, "Score": "", "Best Match": ""},
            {"Channel": "B", "Number": 2, "Score": "n/a", "Best Match": ""}]
    groups = reports.group_entries(build(rows=rows))
    assert [len(g["entries"]) for g in groups] == [0, 0, 2]


def test_a_status_column_groups_by_status_in_first_seen_order():
    columns = [("Channel", "Channel"), ("Status", "Status")]
    rows = [{"Channel": "A", "Status": "created"},
            {"Channel": "B", "Status": "skipped"},
            {"Channel": "C", "Status": "created"}]
    groups = reports.group_entries(build(rows=rows, columns=columns))
    assert [g["title"] for g in groups] == ["created", "skipped"]
    assert [len(g["entries"]) for g in groups] == [2, 1]
    assert all(g["dot"] == "dot-neutral" for g in groups)


def test_neither_column_gives_one_section_holding_every_row():
    columns = [("Channel", "Channel"), ("Number", "Number")]
    rows = [{"Channel": "A", "Number": 1}, {"Channel": "B", "Number": 2}]
    groups = reports.group_entries(build(rows=rows, columns=columns))
    assert len(groups) == 1
    assert len(groups[0]["entries"]) == 2


def test_grouping_never_drops_or_duplicates_a_row():
    for columns, rows in (
        (COLUMNS, ROWS),
        ([("Channel", "Channel"), ("Status", "Status")],
         [{"Channel": "A", "Status": "x"}, {"Channel": "B", "Status": "y"}]),
        ([("Channel", "Channel")], [{"Channel": "A"}, {"Channel": "B"}]),
    ):
        model = build(rows=rows, columns=columns)
        grouped = [tuple(e) for g in reports.group_entries(model) for e in g["entries"]]
        assert sorted(grouped) == sorted(tuple(e) for e in model["entries"])


def test_grouping_a_report_with_no_rows_still_returns_its_sections():
    groups = reports.group_entries(build(rows=[]))
    assert [len(g["entries"]) for g in groups] == [0, 0, 0]


def test_the_score_bands_state_their_own_boundaries():
    """A reader must never have to guess what strong meant."""
    descriptions = " ".join(d for _, _, _, d in reports._SCORE_BANDS)
    assert "90" in descriptions
    assert "60" in descriptions


def test_the_lowest_band_has_a_floor_of_zero_so_every_row_lands_somewhere():
    assert reports._SCORE_BANDS[-1][0] == 0


# --------------------------------------------------------------------------- #
# Rendered copy
# --------------------------------------------------------------------------- #

def _visible_text(page):
    """The page with its stylesheet, its script and its markup removed.

    The style block is excluded deliberately: a CSS custom property is spelled
    with two hyphens, and that is not punctuation a reader ever sees.
    """
    without_style = re.sub(r"<style>.*?</style>", "", page, flags=re.DOTALL)
    without_script = re.sub(r"<script>.*?</script>", "", without_style,
                            flags=re.DOTALL)
    return re.sub(r"<[^>]+>", " ", without_script)


CONTRACTIONS = ("doesn't", "don't", "can't", "won't", "isn't", "aren't",
                "it's", "that's", "you're", "we've", "didn't", "wasn't",
                "hasn't", "haven't", "couldn't", "wouldn't", "shouldn't")


def test_the_rendered_copy_has_no_em_dash_en_dash_or_double_hyphen():
    """A double hyphen reads as an em dash on the page."""
    text = _visible_text(reports.render_html(build()))
    assert "\u2014" not in text, "em dash in rendered copy"
    assert "–" not in text, "en dash in rendered copy"
    assert "--" not in text, "double hyphen in rendered copy"


def test_the_rendered_copy_has_no_contractions():
    text = _visible_text(reports.render_html(build())).lower()
    found = [c for c in CONTRACTIONS if c in text]
    assert not found, found


def test_the_footer_names_the_project_and_its_issue_tracker():
    page = reports.render_html(build())
    assert reports.REPO_URL in page
    assert reports.ISSUES_URL in page


def test_the_footer_credits_newsflasharr_for_emailed_copies():
    """Newsflasharr writes the same credit into the email body. Carrying it in
    the page too means it survives the page being saved or forwarded."""
    page = reports.render_html(build())
    assert reports.NEWSFLASHARR_URL in page
    assert "Newsflasharr" in page


def test_the_credit_is_about_emailed_copies_not_about_this_copy():
    """A report read straight from the report directory was not delivered by
    anything, so the page must not thank a deliverer that did not run."""
    page = reports.render_html(build())
    assert "Emailed copies of this report are delivered courtesy of" in page
