"""The settings form is divided into sections, and every setting sits under the right one.

Measured 2026-09-05 against the served field list: this plugin builds its form in
`Plugin.fields`, a property, and `Lineuparr/plugin.json` declares an EMPTY fields
array, so the Python property is the only place the form exists. Dispatcharr
confirms this at `apps/plugins/loader.py`, which reads the instance attribute
first and falls back to the manifest only when the attribute is empty.

The form already carried five section headings across twenty settings with none
left dangling, which is the defect the sibling plugin Stream-Mapparr had. What it
did NOT have was a body on each heading saying anything a reader could not get
from the field labels underneath it, and one body had gone out of date: the
"Advanced" heading described itself as performance tuning after a file
housekeeping setting was added under it.

These tests lock the section boundaries so a setting added later cannot silently
land under the wrong heading, and they pin the two rules that govern this text:
an information panel body must be one flowing paragraph, because Dispatcharr
renders it as a single Mantine Text element and line breaks are not safe there
(measured by reading the compiled PluginCard component in the running container),
and no copy this plugin shows the operator may contain an em dash.
"""
import pytest

from Lineuparr.plugin import Plugin


# Each section heading and the setting that must come directly after it. Locking
# the BOUNDARY rather than the full membership means adding a setting inside a
# section does not need a test change, while moving a boundary does.
SECTION_BOUNDARIES = [
    ("_sec_sources", "lineup_file"),
    ("_sec_groups", "group_prefix"),
    ("_sec_matching", "match_sensitivity"),
    ("_sec_notify", "notify_enabled"),
    ("_sec_advanced", "rate_limiting"),
]

SECTION_PREFIX = "_sec_"


def _fields():
    return Plugin.__new__(Plugin).fields


def _ids():
    return [f.get("id") for f in _fields()]


def _section_ids():
    return [f.get("id") for f in _fields()
            if str(f.get("id", "")).startswith(SECTION_PREFIX)]


def _fields_under(section_id):
    """The field ids between `section_id` and the next section heading."""
    ids = _ids()
    start = ids.index(section_id) + 1
    out = []
    for fid in ids[start:]:
        if str(fid).startswith(SECTION_PREFIX):
            break
        out.append(fid)
    return out


def _body(section_id):
    field = next(f for f in _fields() if f.get("id") == section_id)
    return field.get("help_text") or ""


# --------------------------------------------------------------------------- #
# The sections exist, are in order, and nothing escapes them
# --------------------------------------------------------------------------- #
def test_every_expected_section_heading_is_served():
    served = _section_ids()
    missing = [s for s, _first in SECTION_BOUNDARIES if s not in served]
    assert not missing, f"missing section heading(s): {missing}"


def test_the_sections_appear_in_the_expected_order():
    assert _section_ids() == [name for name, _first in SECTION_BOUNDARIES]


@pytest.mark.parametrize("section_id,first_field", SECTION_BOUNDARIES)
def test_each_section_is_followed_by_the_setting_that_opens_it(section_id, first_field):
    """Locks where one section ends and the next begins."""
    ids = _ids()
    assert section_id in ids, f"{section_id} is not served at all"
    assert ids[ids.index(section_id) + 1] == first_field


def test_no_setting_sits_above_the_first_section_heading():
    ids = _ids()
    first = next(i for i, f in enumerate(ids) if str(f).startswith(SECTION_PREFIX))
    assert first == 0, f"{ids[:first]} appear before any heading"


def test_every_setting_sits_under_some_heading():
    """A heading that is never closed makes every later setting read as its own."""
    covered = set()
    for section_id, _first in SECTION_BOUNDARIES:
        covered.update(_fields_under(section_id))
    settings = [f.get("id") for f in _fields()
                if not str(f.get("id", "")).startswith(SECTION_PREFIX)]
    assert set(settings) == covered


def test_no_section_holds_more_than_eight_settings():
    """Past about eight, a heading stops helping anyone find anything."""
    oversized = {s: len(_fields_under(s)) for s, _f in SECTION_BOUNDARIES
                 if len(_fields_under(s)) > 8}
    assert oversized == {}


# --------------------------------------------------------------------------- #
# The specific memberships that were wrong or are easy to get wrong
# --------------------------------------------------------------------------- #
def test_the_export_housekeeping_setting_sits_under_the_advanced_heading():
    """It was added after the heading body was written, which made that body wrong."""
    assert "csv_retention_days" in _fields_under("_sec_advanced")


def test_the_advanced_heading_does_not_call_itself_performance_tuning_only():
    """It also governs deleting old CSV exports, which is not performance tuning."""
    body = _body("_sec_advanced").lower()
    assert "export" in body, (
        "the Advanced heading never mentions the export housekeeping setting "
        "underneath it, so its body describes only half of what it governs")


def test_the_matching_heading_warns_that_a_run_can_remove_streams_and_channels():
    """Measured in Lineuparr/plugin.py: with Preserve Existing Streams off,
    Apply Stream Match deletes every stream row on a channel and recreates them,
    then deletes channels in this plugin's groups that end with no streams.
    Nothing in the field labels of that section says so."""
    body = _body("_sec_matching").lower()
    assert "preserve existing streams" in body
    assert "delete" in body or "remove" in body


def test_the_notification_settings_sit_together():
    under = _fields_under("_sec_notify")
    assert under == ["notify_enabled", "notify_report_on", "notify_report_format"]


# --------------------------------------------------------------------------- #
# Every heading actually says something
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("section_id", [s for s, _f in SECTION_BOUNDARIES])
def test_every_section_heading_has_a_body_that_says_more_than_its_label(section_id):
    """A heading with no body, or one that restates the label, guides nobody."""
    body = _body(section_id)
    assert body.strip(), f"{section_id} has no body"
    assert len(body.split()) >= 25, (
        f"{section_id} body is {len(body.split())} words; it should say what the "
        f"section governs plus something the field labels do not already say")


# --------------------------------------------------------------------------- #
# Rules for text this plugin shows the operator
# --------------------------------------------------------------------------- #
def test_no_section_body_contains_a_line_break():
    """An info panel body is one flowing paragraph; line breaks are not safe there.

    Measured in the running container: PluginCard renders an info field as a
    label element plus one Mantine Text element holding
    `field.help_text ?? field.description ?? field.value`, so a newline collapses.
    """
    offenders = [f.get("id") for f in _fields()
                 if str(f.get("id", "")).startswith(SECTION_PREFIX)
                 and "\n" in (f.get("help_text") or "")]
    assert not offenders, offenders


def test_no_setting_copy_uses_an_em_dash():
    """Standing instruction for this repository: no em dashes in committed text."""
    em_dash = chr(0x2014)
    offenders = [f.get("id") for f in _fields()
                 if em_dash in (f.get("label") or "")
                 or em_dash in (f.get("help_text") or "")]
    assert not offenders, offenders


def test_section_headings_are_information_panels_that_store_nothing():
    """A heading must never become a stored setting: Dispatcharr never prunes one."""
    for f in _fields():
        if str(f.get("id", "")).startswith(SECTION_PREFIX):
            assert f.get("type") == "info", f["id"]
            assert "default" not in f, f["id"]


def test_the_information_panels_carry_their_text_under_help_text():
    """PluginCard reads help_text first and description only as a fallback.

    Measured by reading the compiled component in the running container:
    `const i = e.help_text ?? e.description ?? e.value`. A body written only
    under `description` still renders, but mixing the two across one form makes
    the next edit a guess, so this pins the key actually used here.
    """
    for f in _fields():
        if str(f.get("id", "")).startswith(SECTION_PREFIX):
            assert f.get("help_text"), f["id"]


# --------------------------------------------------------------------------- #
# A section body must not contradict the field help directly beneath it
# --------------------------------------------------------------------------- #
def test_the_groups_section_counts_the_auto_modes_the_way_the_field_does():
    """The section body said "the three auto-assign modes"; there are two.

    Measured against Plugin.NUMBERING_LABELS: the four modes are lineup,
    auto_next, auto_highest and specific. Only auto_next and auto_highest fill
    the first free slot. The specific mode counts up from Starting Channel
    Number, so a reader who believed the section header would pick it expecting
    first-free-slot numbering and get numbering from their chosen value instead.
    The channel_numbering field help immediately below already says "two".
    """
    body = _body("_sec_groups")
    assert "three auto-assign modes" not in body, (
        "the section body still claims three auto-assign modes")
    auto_modes = [k for k in Plugin.NUMBERING_LABELS if k.startswith("auto_")]
    assert len(auto_modes) == 2, auto_modes
    assert "two auto" in body.lower(), (
        "the section body should say how many auto modes there are, as the "
        "field help beneath it does")


def test_the_groups_section_names_the_specific_mode_as_the_form_spells_it():
    """The body referred to 'Use Specific Number'; the option reads differently."""
    body = _body("_sec_groups")
    label = Plugin.NUMBERING_LABELS["specific"]
    assert label in body, (
        f"the section body should name the option exactly as the form does: {label!r}")
