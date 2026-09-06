"""The preamble at the top of every CSV this plugin writes to /data/exports.

Reviewed 2026-09-05. The preamble is what a person actually reads when they open
an export, and it had four problems. None of them was a wrong settings key, which
is the defect the sibling plugin Stream-Mapparr had: every key read here is a
field this plugin really declares, checked by walking the source. What it had
instead:

  IT NEVER SAID WHAT THE FILE WAS. The first line was a version string. Nothing
  told a reader that the lines starting with a hash are a preamble rather than
  data, so a spreadsheet import that is not told to skip comment lines pulls them
  in as rows.

  IT NEVER SAID WHAT THE RUN DID. It described the configuration in nine lines
  and never reported a single count. Someone comparing two exports to work out
  why the results differ could read how the run was set up and not what it
  produced.

  IT PRINTED PYTHON BOOLEANS. "Quality Ordering: True". Dispatcharr also stores
  some booleans as the strings "true" and "false", so the same setting could
  print either way depending on how it was last saved.

  A BARE WORD STOOD IN FOR A NUMBER. "Match Sensitivity: normal" does not say
  what normal is worth, and the four presets map to 70, 80, 90 and 95.

It also omitted the settings that decide whether a run REMOVES anything.
Preserve Existing Streams is the setting that turns Apply Stream Match from an
operation that adds streams into one that replaces a channel's whole list and
then deletes channels left with none, and the preamble never recorded it.

The remaining tests pin plain wording rules: settings read as Yes and No, the
file stays plain ASCII because a spreadsheet may open it under a different
codepage, and every preamble line is commented.
"""
import logging

import pytest

from Lineuparr import plugin as plugin_module
from Lineuparr.plugin import Plugin


def _plugin():
    return Plugin.__new__(Plugin)


def _header(settings=None, **kwargs):
    return _plugin()._generate_csv_header_comments(settings or {}, **kwargs)


def _text(settings=None, **kwargs):
    return "\n".join(_header(settings, **kwargs))


def _line(settings=None, prefix="", **kwargs):
    for line in _header(settings, **kwargs):
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no line starting {prefix!r} in the preamble")


# --------------------------------------------------------------------------- #
# The file says what it is, before it says how it was configured
# --------------------------------------------------------------------------- #
def test_the_preamble_says_what_the_file_is_before_it_lists_settings():
    """Someone opening this in a spreadsheet has no other clue what it is."""
    top = " ".join(_header()[:14]).lower()
    assert "lineuparr" in top
    assert "report" in top or "export" in top


def test_the_preamble_explains_that_the_hash_lines_are_not_data():
    """Without this a spreadsheet import pulls the preamble in as rows."""
    top = " ".join(_header()[:14]).lower()
    assert "#" in " ".join(_header()[:14]) or "hash" in top
    assert any(word in top for word in ("skip", "ignore", "delete them")), (
        "the preamble never tells the reader to skip the comment lines")


def test_the_settings_recorded_are_described_as_the_ones_this_run_used():
    """That is the point of recording them: two exports can be compared."""
    top = " ".join(_header()[:20]).lower()
    assert "this run" in top or "run used" in top


# --------------------------------------------------------------------------- #
# The result the run produced
# --------------------------------------------------------------------------- #
def test_the_preamble_states_what_the_run_did():
    text = _text(summary=[("Channels matched", 12), ("Streams attached", 37)])
    assert "Channels matched: 12" in text
    assert "Streams attached: 37" in text


def test_what_the_run_did_comes_before_the_settings_it_used():
    """Counts are what a reader wants first; configuration is the small print."""
    lines = _header(summary=[("Channels matched", 12)])
    did = next(i for i, line in enumerate(lines) if "Channels matched" in line)
    used = next(i for i, line in enumerate(lines) if line.startswith("# Lineup File:"))
    assert did < used, "the settings are printed before the result"


def test_the_preamble_reports_the_number_of_rows_in_the_table_below_it():
    assert "42" in _line(prefix="# Rows in this table:", row_count=42)


def test_the_action_that_wrote_the_file_is_named():
    assert "Preview Stream Match" in _line(prefix="# Action:",
                                           action_name="Preview Stream Match")


def test_a_run_with_no_counts_to_report_still_produces_a_valid_preamble():
    """Not every export knows a count; the section must not print an empty heading."""
    text = _text()
    assert "What This Run Did" not in text or "Rows in this table" in text


# --------------------------------------------------------------------------- #
# Booleans read as Yes and No
# --------------------------------------------------------------------------- #
def test_settings_read_as_yes_and_no_rather_than_python_booleans():
    text = _text({"prioritize_quality": True, "preserve_existing_streams": False})
    assert "Yes" in _line({"prioritize_quality": True},
                          prefix="# Order Matched Streams by Quality:")
    assert "No" in _line({"preserve_existing_streams": False},
                         prefix="# Preserve Existing Streams:")
    assert "True" not in text and "False" not in text, (
        "raw Python booleans still reach the reader")


@pytest.mark.parametrize("stored,expected", [
    (True, "Yes"), ("true", "Yes"), ("True", "Yes"), ("on", "Yes"), ("1", "Yes"),
    (False, "No"), ("false", "No"), ("", "No"), (None, "No"),
])
def test_a_setting_stored_as_a_string_still_reads_as_yes_or_no(stored, expected):
    """Dispatcharr stores some booleans as the strings true and false."""
    line = _line({"preserve_existing_streams": stored},
                 prefix="# Preserve Existing Streams:")
    assert line.endswith(expected) or f": {expected}" in line, line


def test_the_yes_no_helper_falls_back_to_the_declared_default():
    """An absent key must print the default the form would have applied."""
    assert plugin_module._yes_no(None, True) == "Yes"
    assert plugin_module._yes_no(None, False) == "No"


# --------------------------------------------------------------------------- #
# A bare value is made to say what it means
# --------------------------------------------------------------------------- #
def test_the_match_sensitivity_line_gives_the_number_behind_the_preset():
    """normal is 80 of 100. The word alone says neither the number nor the direction."""
    line = _line({"match_sensitivity": "normal"}, prefix="# Match Sensitivity:")
    assert "80" in line
    assert "100" in line
    assert "strict" in line.lower()


@pytest.mark.parametrize("preset,score", [("relaxed", 70), ("normal", 80),
                                          ("strict", 90), ("exact", 95)])
def test_every_sensitivity_preset_prints_its_own_threshold(preset, score):
    line = _line({"match_sensitivity": preset}, prefix="# Match Sensitivity:")
    assert str(score) in line, line


# --------------------------------------------------------------------------- #
# The settings that decide whether anything is removed
# --------------------------------------------------------------------------- #
def test_the_preamble_records_whether_existing_streams_were_preserved():
    """This is the setting that decides whether a run can delete anything."""
    line = _line({"preserve_existing_streams": False},
                 prefix="# Preserve Existing Streams:")
    assert "replace" in line.lower() or "delete" in line.lower(), (
        "the line records the value without saying what the value causes")


def test_the_preamble_records_quality_aware_matching():
    assert _line(prefix="# Quality-Aware Stream Matching:")


def test_the_preamble_records_the_single_channel_scope():
    line = _line({"single_channel_name": "CNN"}, prefix="# Single Channel Match:")
    assert "CNN" in line


def test_an_unset_single_channel_scope_says_the_whole_lineup_ran():
    line = _line({"single_channel_name": ""}, prefix="# Single Channel Match:")
    assert "lineup" in line.lower() or "all" in line.lower()


# --------------------------------------------------------------------------- #
# Every label matches the one the operator sees in the settings form
# --------------------------------------------------------------------------- #
SETTINGS_LINES = {
    "# Lineup File:": "lineup_file",
    "# Category Detail:": "category_detail",
    "# Channel Group Prefix:": "group_prefix",
    "# Match Sensitivity:": "match_sensitivity",
    "# Order Matched Streams by Quality:": "prioritize_quality",
    "# Quality-Aware Stream Matching:": "quality_aware_stream_matching",
    "# Preserve Existing Streams:": "preserve_existing_streams",
    "# Single Channel Match:": "single_channel_name",
    "# Channel Numbering:": "channel_numbering",
    "# Rate Limiting:": "rate_limiting",
    "# Channel Profile:": "channel_profiles",
}


@pytest.mark.parametrize("prefix,field_id", sorted(SETTINGS_LINES.items()))
def test_each_settings_line_uses_the_label_the_form_shows(prefix, field_id):
    """A reader has to be able to find the setting the line is talking about."""
    label = next(f["label"] for f in _plugin().fields if f.get("id") == field_id)
    label = label.split(" (")[0]
    assert prefix == f"# {label}:", (
        f"the preamble calls {field_id} {prefix!r} while the form calls it {label!r}")
    assert _line(prefix=prefix)


# --------------------------------------------------------------------------- #
# Mechanical rules
# --------------------------------------------------------------------------- #
def test_the_preamble_is_plain_ascii():
    """A CSV may be opened by a spreadsheet under a different codepage."""
    text = _text({"lineup_file": "US_DirecTV-Premier_lineup.json"},
                 summary=[("Channels matched", 1)], row_count=1)
    bad = sorted({c for c in text if ord(c) > 127})
    assert not bad, [hex(ord(c)) for c in bad]


def test_every_preamble_line_is_commented():
    """One uncommented line would be read as data by a spreadsheet import."""
    stray = [line for line in _header() if line and not line.startswith("#")]
    assert stray == []


def test_no_preamble_line_carries_a_newline_of_its_own():
    """The writer joins these with a newline; an embedded one breaks the count."""
    assert [line for line in _header() if "\n" in line] == []


def test_the_preamble_uses_no_em_dash():
    assert chr(0x2014) not in _text()


# --------------------------------------------------------------------------- #
# It reaches the file
# --------------------------------------------------------------------------- #
def test_the_export_writes_the_preamble_above_the_table(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    path = _plugin()._export_csv(
        "lineuparr_match_x.csv", [{"Channel": "Sample"}], ["Channel"],
        logging.getLogger("test_csv_header"),
        {"match_sensitivity": "strict", "preserve_existing_streams": True},
        action_name="Apply Stream Match Only",
        summary=[("Channels matched", 3)])
    assert path
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines()
    assert lines[0].startswith("#")
    assert "# Action: Apply Stream Match Only" in lines
    assert "# Channels matched: 3" in lines
    assert "90" in next(line for line in lines if line.startswith("# Match Sensitivity:"))
    table_start = next(i for i, line in enumerate(lines) if not line.startswith("#"))
    assert lines[table_start] == "Channel"
    assert lines[table_start + 1] == "Sample"


def test_an_export_given_no_settings_still_writes_a_preamble(monkeypatch, tmp_path):
    """Every caller passes settings today, but the parameter is optional."""
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    path = _plugin()._export_csv(
        "lineuparr_match_y.csv", [{"Channel": "S"}], ["Channel"],
        logging.getLogger("test_csv_header"))
    assert path
    assert open(path, encoding="utf-8").read().startswith("#")


# --------------------------------------------------------------------------- #
# The preamble must describe what the RUN did, not what a second copy of the
# defaults says it should have done
# --------------------------------------------------------------------------- #
# Each entry: the settings key, the default the RUNTIME passes to settings.get,
# and the preamble line that reports it. The preamble used to hand-copy each
# default, so a default changed in one place and not the other would make the
# record wrong while every test still passed.
RUNTIME_BOOLEANS = [
    ("preserve_existing_streams", False, "# Preserve Existing Streams:"),
    ("prioritize_quality", True, "# Order Matched Streams by Quality:"),
    ("quality_aware_stream_matching", False, "# Quality-Aware Stream Matching:"),
    ("refresh_epg_after_match", True, "# Refresh EPG After Matching:"),
]


@pytest.mark.parametrize("key,runtime_default,prefix", RUNTIME_BOOLEANS)
@pytest.mark.parametrize("stored", [True, False, None, "absent"])
def test_the_preamble_agrees_with_how_the_run_resolved_the_setting(
        key, runtime_default, prefix, stored):
    """The reachable divergence, measured: a setting stored as None.

    The runtime reads `settings.get(key, default)`, and a default only applies to
    an ABSENT key, so a stored None arrives as None and is falsy: the feature is
    off. The preamble used to pass that None to a renderer that substituted the
    declared default, so a setting stored as None was reported as On while the
    run had it Off. None is reachable here: tests/test_none_settings.py exists
    because a stored None was reported from the interface.

    A stored STRING boolean is deliberately not covered by this test. See
    test_a_string_boolean_is_a_known_divergence_this_test_records below.
    """
    settings = {} if stored == "absent" else {key: stored}
    resolved = settings.get(key, runtime_default)
    expected = "Yes" if resolved else "No"
    line = _line(settings, prefix=prefix)
    assert f": {expected}" in line, f"{key}={stored!r}: run says {expected}, preamble says {line}"


def test_a_string_boolean_is_a_known_divergence_this_test_records():
    """The preamble coerces a string boolean; the run does not. Not fixed here.

    `Lineuparr/plugin.py` reads `settings.get("preserve_existing_streams", False)`
    and uses plain truthiness, so the string "false" is TRUE to the run. The
    preamble renders it as No, because that is what every sibling plugin does and
    what a reader expects. Making the two agree means changing which branch a run
    takes, which is a behaviour change, not a wording one.

    Measured 2026-09-05 on the live installation: all 45 boolean values across
    the ten plugin configs are real Python booleans and none is a string, so this
    divergence is not currently reachable. This test states the choice so that a
    future reader does not take the preamble as proof the run agrees.
    """
    assert plugin_module._yes_no("false") == "No"
    assert bool("false") is True, "if this ever changes, revisit the divergence"


@pytest.mark.parametrize("key,runtime_default,prefix", RUNTIME_BOOLEANS)
def test_the_preamble_uses_the_same_default_the_runtime_uses(key, runtime_default, prefix):
    """The default must come from one place, not be copied into the preamble."""
    line = _line({}, prefix=prefix)
    assert f": {'Yes' if runtime_default else 'No'}" in line, line


# --------------------------------------------------------------------------- #
# The threshold the run actually matched at
# --------------------------------------------------------------------------- #
def test_the_preamble_prints_the_threshold_from_the_legacy_numeric_setting():
    """Measured: _init_fuzzy_matcher falls back to fuzzy_match_threshold when
    match_sensitivity is not one of the four presets, and Dispatcharr never
    prunes a removed setting, so an upgraded installation can still carry one.
    The run then matches at that number while the export named no number at all,
    which is the case where the reader most needs it.
    """
    settings = {"match_sensitivity": "legacy_custom", "fuzzy_match_threshold": 65}
    line = _line(settings, prefix="# Match Sensitivity:")
    assert "65" in line, line
    assert "100" in line


def test_the_legacy_threshold_in_the_preamble_matches_the_matcher():
    """Read the number out of the matcher rather than trusting the map."""
    import logging
    settings = {"match_sensitivity": "legacy_custom", "fuzzy_match_threshold": 65}
    matcher = _plugin()._init_fuzzy_matcher(settings, logging.getLogger("t"))
    assert str(matcher.match_threshold) in _line(settings, prefix="# Match Sensitivity:")


def test_an_out_of_range_legacy_threshold_is_reported_as_the_matcher_clamped_it():
    """The matcher clamps to 0-100; the preamble must report the clamped value."""
    import logging
    settings = {"match_sensitivity": "legacy_custom", "fuzzy_match_threshold": 400}
    matcher = _plugin()._init_fuzzy_matcher(settings, logging.getLogger("t"))
    assert matcher.match_threshold == 100
    assert "100 out of 100" in _line(settings, prefix="# Match Sensitivity:")


# --------------------------------------------------------------------------- #
# The file a spreadsheet has to read
# --------------------------------------------------------------------------- #
def test_the_whole_export_uses_one_line_terminator(monkeypatch, tmp_path):
    """Measured before this test: 35 preamble lines ended LF and 2 table rows
    ended CRLF, in one file the user guide now tells people to import.
    """
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    path = _plugin()._export_csv(
        "lineuparr_terminator.csv", [{"Channel": "CNN"}, {"Channel": "BBC"}], ["Channel"],
        logging.getLogger("test_csv_header"), {}, action_name="Probe")
    raw = open(path, "rb").read()
    crlf = raw.count(b"\r\n")
    bare_lf = raw.count(b"\n") - crlf
    assert bare_lf == 0, f"{bare_lf} line(s) end LF while {crlf} end CRLF"


@pytest.mark.parametrize("key,value", [
    ("group_prefix", "US\nDROPPED"),
    ("single_channel_name", "CNN\r\nDROPPED"),
    ("lineup_file", "aâ€¨b"),
    ("channel_profiles", "Default\rDROPPED"),
])
def test_a_line_break_inside_a_setting_cannot_end_the_comment_block(key, value):
    """Measured: a newline in Channel Group Prefix produced a line with no hash.

    A spreadsheet told to skip comment lines then reads that line as the header
    row and every column is misaligned. U+2028 is included because str.splitlines
    breaks on it even though it is not a newline.
    """
    text = _text({key: value})
    stray = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert stray == [], stray


# --------------------------------------------------------------------------- #
# The preamble must resolve a setting through the same helper the run uses
# --------------------------------------------------------------------------- #
def test_the_numbering_line_honours_the_legacy_override_the_run_honours():
    """Plugin._resolve_numbering_mode forces auto_next when the legacy
    ignore_channel_numbers key is True, and Dispatcharr never prunes a setting
    whose field was removed, so an upgraded installation can still carry it.
    The preamble read channel_numbering raw and so named a mode the run did not
    use, which is the record-versus-run gap the shared threshold resolver was
    added to close.
    """
    settings = {"channel_numbering": "lineup", "ignore_channel_numbers": True}
    assert Plugin._resolve_numbering_mode(settings) == "auto_next"
    line = _line(settings, prefix="# Channel Numbering:")
    assert Plugin.NUMBERING_LABELS["auto_next"] in line, line
    assert Plugin.NUMBERING_LABELS["lineup"] not in line, line


def test_a_failed_source_lookup_is_not_reported_as_an_empty_installation(monkeypatch):
    """`_m3u_account_names` returns None for a failed lookup and [] for an
    installation with no active account. The preamble collapsed both into the
    same sentence, so a database error read as a true statement about the run.
    """
    monkeypatch.setattr(plugin_module.Plugin, "_m3u_account_names",
                        lambda self, logger=None: None)
    text = _plugin()._describe_m3u_sources({})
    assert "could not" in text.lower() or "unavailable" in text.lower(), text


def test_an_installation_with_no_active_source_says_so_differently(monkeypatch):
    """An empty install and a failed lookup must not read the same."""
    monkeypatch.setattr(plugin_module.Plugin, "_m3u_account_names",
                        lambda self, logger=None: [])
    text = _plugin()._describe_m3u_sources({})
    assert "none" in text.lower(), text
    assert "could not" not in text.lower(), text


# --------------------------------------------------------------------------- #
# Every label the preamble prints must be the one the rest of the plugin prints
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", sorted(Plugin.NUMBERING_LABELS))
def test_the_numbering_labels_are_the_ones_the_settings_form_offers(mode):
    """Three copies of these four strings existed: the form options, the map the
    preamble reads, and a dict inside Validate Settings that still carried the
    pre-rename wording, so Validate Settings named a mode differently from the
    form.
    """
    options = {o["value"]: o["label"] for o in
               next(f for f in _plugin().fields if f["id"] == "channel_numbering")["options"]}
    assert options[mode] == Plugin.NUMBERING_LABELS[mode]


def test_validate_settings_names_a_numbering_mode_the_way_the_form_does():
    import ast
    import io
    import os as _os
    source = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                           "Lineuparr", "plugin.py")
    with io.open(source, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    func = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_validate_settings")
    literals = {n.value for n in ast.walk(func)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    stale = [v for v in literals if v in
             ("Use Channel Database Numbers", "Auto-Assign Next Available",
              "Auto-Assign After Highest", "Use Specific Number")]
    assert stale == [], (
        f"Validate Settings carries its own copy of the numbering labels: {stale}")
