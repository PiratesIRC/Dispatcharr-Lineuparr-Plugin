"""The Dispatcharr 0.34 settings form built by Plugin.get_fields.

Dispatcharr 0.34 calls get_fields(context) at discovery and draws its result;
0.33 and earlier never call it and draw Plugin.fields. These tests pin the rules
that keep both working:

- the flat form (Plugin.fields) holds only the field types 0.33 knows;
- every flat setting is in the 0.34 form, or is replaced by a new id that
  writes back into it at run time;
- a new field's default comes from the saved text value, and every value
  already saved is an option, because 0.34 rejects a save holding a value that
  is not an option;
- custom_aliases JSON that cannot be shown as table rows keeps its text box,
  and stored rows then never overwrite the text;
- get_fields falls back to the flat form on any exception.
"""
import json

import pytest

import Lineuparr.plugin as plugin_module
from Lineuparr import lineuparr_fields as lf
from Lineuparr.plugin import Plugin

OLD_TYPES = {"string", "number", "boolean", "select", "text", "info"}
NEW_ID_FOR = {"channel_profiles": lf.PROFILE_LIST_ID, "custom_aliases": lf.ALIAS_ROWS_ID,
              "epg_sources": lf.EPG_LIST_ID}
ALL_BUILT = {lf.PROFILE_LIST_ID, lf.ALIAS_ROWS_ID, lf.EPG_LIST_ID}
SOURCES = ("epgshare-AU", "epgshare-UK", "guru-US", "guru-UK", "epg.pw-US", "Solo Source")


def _plugin():
    p = Plugin.__new__(Plugin)
    p._built_form_ids = set()
    return p


def _flat():
    return _plugin().fields


def _form(settings=None, profiles=("Living Room", "Kids"), sources=SOURCES):
    return lf.form_034(_flat(), settings or {}, profiles, plugin_module._clean_json_text,
                       epg_source_names=sources)


def _by_id(fields):
    return {f["id"]: f for f in fields}


def test_flat_form_holds_only_types_033_knows():
    for f in _flat():
        assert f["type"] in OLD_TYPES, f["id"]
        for key in ("section", "collapsed", "columns", "rows", "accept", "max_size"):
            assert key not in f, (f["id"], key)


def test_every_flat_setting_is_in_the_034_form_or_replaced():
    fields, built = _form()
    ids = set(_by_id(fields))
    for f in _flat():
        fid = f["id"]
        if f["type"] == "info":
            continue
        assert fid in ids or NEW_ID_FOR.get(fid) in ids, fid
    assert built == ALL_BUILT


def test_sections_are_markers_and_every_field_after_the_first_names_one():
    fields, _ = _form()
    sections = [f for f in fields if f["type"] == "section"]
    assert [s["id"] for s in sections] == [
        "_sec_sources", "_sec_groups", "_sec_matching", "_sec_notify", "_sec_advanced"]
    assert {s["id"]: s["collapsed"] for s in sections} == {
        "_sec_sources": False, "_sec_groups": False, "_sec_matching": False,
        "_sec_notify": True, "_sec_advanced": True}
    declared = {s["id"] for s in sections}
    for f in fields:
        if f["type"] != "section":
            assert f.get("section") in declared, f["id"]
    by_id = _by_id(fields)
    assert by_id["_sec_sources_about"]["help_text"]
    assert by_id[lf.PROFILE_LIST_ID]["section"] == "_sec_sources"
    assert by_id[lf.EPG_LIST_ID]["section"] == "_sec_sources"
    assert by_id[lf.ALIAS_ROWS_ID]["section"] == "_sec_matching"
    assert by_id["csv_retention_days"]["section"] == "_sec_advanced"


def test_profile_pick_list_default_from_old_text_and_saved_values_are_options():
    fields, _ = _form({"channel_profiles": "living room, Gone Profile",
                       lf.PROFILE_LIST_ID: ["Old Stored"]})
    f = _by_id(fields)[lf.PROFILE_LIST_ID]
    assert f["type"] == "multiselect"
    assert f["default"] == ["Living Room", "Gone Profile"]
    values = {o["value"] for o in f["options"]}
    assert {"Living Room", "Kids", "Gone Profile", "Old Stored"} <= values


def test_profile_name_with_a_comma_is_not_offered():
    fields, _ = _form(profiles=("A, B", "Plain"))
    values = {o["value"] for o in _by_id(fields)[lf.PROFILE_LIST_ID]["options"]}
    assert values == {"Plain"}


def test_profile_none_sentinel_is_an_empty_default():
    fields, _ = _form({"channel_profiles": "_none"})
    assert _by_id(fields)[lf.PROFILE_LIST_ID]["default"] == []


def test_alias_table_default_from_json_with_info_above():
    saved = json.dumps({"CNN": ["CNN US", "CNN HD"], "ESPN": "ESPN East"})
    fields, built = _form({"custom_aliases": saved})
    by_id = _by_id(fields)
    table = by_id[lf.ALIAS_ROWS_ID]
    assert table["type"] == "table" and table["label"] == ""
    assert [c["id"] for c in table["columns"]] == ["channel", "aliases"]
    assert table["default"] == [{"channel": "CNN", "aliases": "CNN US, CNN HD"},
                                {"channel": "ESPN", "aliases": "ESPN East"}]
    assert by_id[lf.ALIAS_ROWS_ID + "_about"]["help_text"]
    assert "custom_aliases" not in by_id
    assert lf.ALIAS_ROWS_ID in built


@pytest.mark.parametrize("saved", [
    "{not json",
    "[\"a list\"]",
    json.dumps({"CNN": {"nested": 1}}),
    json.dumps({"CNN": ["has, comma"]}),
    json.dumps({"CNN": [1, 2]}),
])
def test_alias_json_that_does_not_fit_keeps_the_text_box(saved):
    fields, built = _form({"custom_aliases": saved})
    by_id = _by_id(fields)
    assert lf.ALIAS_ROWS_ID not in by_id
    assert by_id["custom_aliases"]["type"] == "text"
    assert by_id["custom_aliases"]["rows"] == 6
    assert lf.ALIAS_ROWS_ID not in built


def test_write_back_round_trip():
    saved = {"channel_profiles": "Kids", "custom_aliases": json.dumps({"CNN": ["CNN US"]})}
    fields, built = _form(saved)
    by_id = _by_id(fields)
    stored = dict(saved)
    stored[lf.PROFILE_LIST_ID] = by_id[lf.PROFILE_LIST_ID]["default"] + ["Living Room"]
    stored[lf.ALIAS_ROWS_ID] = by_id[lf.ALIAS_ROWS_ID]["default"] + [
        {"channel": "ESPN", "aliases": "ESPN East, ESPN HD"},
        {"channel": "CNN", "aliases": "CNN HD"},
        {"channel": "", "aliases": "orphan"},
    ]
    out = lf.legacy_settings(stored, built)
    assert out["channel_profiles"] == "Kids, Living Room"
    assert json.loads(out["custom_aliases"]) == {
        "CNN": ["CNN US", "CNN HD"], "ESPN": ["ESPN East", "ESPN HD"]}


def test_empty_table_writes_empty_text():
    out = lf.legacy_settings({"custom_aliases": "{\"A\": \"b\"}", lf.ALIAS_ROWS_ID: []},
                             {lf.ALIAS_ROWS_ID})
    assert out["custom_aliases"] == ""


def test_stored_rows_do_not_overwrite_text_when_table_was_not_built():
    settings = {"custom_aliases": "{broken", lf.ALIAS_ROWS_ID: [{"channel": "X", "aliases": "y"}]}
    out = lf.legacy_settings(settings, {lf.PROFILE_LIST_ID})
    assert out["custom_aliases"] == "{broken"


def test_no_write_back_without_get_fields():
    settings = {"channel_profiles": "Kids", lf.PROFILE_LIST_ID: ["Other"]}
    assert lf.legacy_settings(settings, set()) == settings


def test_long_help_is_split_into_titled_info_fields():
    fields, _ = _form()
    by_id = _by_id(fields)
    short = by_id["csv_retention_days"]["help_text"]
    assert short == lf.HELP_PARAGRAPHS["csv_retention_days"][0]
    assert by_id["csv_retention_days_help_1"]["label"] == "Which files"
    assert by_id["csv_retention_days_help_2"]["section"] == "_sec_advanced"


def test_every_new_text_is_one_paragraph_without_em_dashes():
    texts = [m["help_text"] for m in lf.NEW_FIELDS.values()]
    for short, paras in lf.HELP_PARAGRAPHS.values():
        texts.append(short)
        texts.extend(t for _, t in paras)
    for t in texts:
        assert "\n" not in t
        assert chr(0x2014) not in t and chr(0x2013) not in t


def test_get_fields_builds_the_034_form_and_flags_write_back(monkeypatch):
    p = _plugin()
    monkeypatch.setattr(Plugin, "_profile_names", staticmethod(lambda: ["Kids"]))
    monkeypatch.setattr(Plugin, "_epg_source_names", staticmethod(lambda: ["guru-US"]))
    fields = p.get_fields({"settings": {"channel_profiles": "Kids"}})
    assert any(f["type"] == "section" for f in fields)
    assert p._built_form_ids == ALL_BUILT


def test_get_fields_falls_back_to_the_flat_form_on_error(monkeypatch):
    p = _plugin()

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(lf, "form_034", boom)
    fields = p.get_fields({"settings": {}})
    assert [f["id"] for f in fields] == [f["id"] for f in _flat()]
    assert p._built_form_ids == set()


def test_run_writes_back_and_maps_warning_to_partial(monkeypatch):
    p = _plugin()
    p._built_form_ids = {lf.PROFILE_LIST_ID}
    seen = {}

    def fake_validate(settings, logger):
        seen["profiles"] = settings.get("channel_profiles")
        return {"status": "warning", "message": "some failed"}

    monkeypatch.setattr(p, "_validate_settings", fake_validate, raising=False)
    monkeypatch.setattr(p, "_report_usage", lambda *a, **k: None, raising=False)
    result = p.run("validate_settings", {},
                   {"settings": {"channel_profiles": "", lf.PROFILE_LIST_ID: ["Kids", "Den"]}})
    assert seen["profiles"] == "Kids, Den"
    assert result["status"] == "partial"
    assert result["message"] == "some failed"


def test_status_for_dispatcharr_leaves_other_statuses():
    for status in ("ok", "error", "success"):
        r = {"status": status}
        assert lf.status_for_dispatcharr(r) is r


def _epg(settings=None, sources=SOURCES):
    fields, built = _form(settings, sources=sources)
    return _by_id(fields)[lf.EPG_LIST_ID], built


def test_epg_pick_list_keeps_the_saved_order_and_spells_names_as_dispatcharr():
    f, built = _epg({"epg_sources": "GURU-us, epgshare-*, Gone Source"})
    assert f["type"] == "multiselect"
    assert lf.EPG_LIST_ID in built
    # The order is the priority, so the default is never sorted.
    assert f["default"] == ["guru-US", "epgshare-*", "Gone Source"]


def test_epg_every_saved_token_is_an_option():
    f, _ = _epg({"epg_sources": "Gone Source, my-custom-?", lf.EPG_LIST_ID: ["Old*Stored"]})
    values = {o["value"] for o in f["options"]}
    assert {"Gone Source", "my-custom-?", "Old*Stored"} <= values
    assert set(SOURCES) <= values


def test_epg_wildcards_are_derived_only_when_they_match_two_sources():
    f, _ = _epg()
    labels = {o["value"]: o["label"] for o in f["options"]}
    assert labels["epgshare-*"] == "epgshare-* (2 sources)"
    assert labels["guru-*"] == "guru-* (2 sources)"
    assert labels["*-US"] == "*-US (2 sources)"
    assert labels["*-UK"] == "*-UK (2 sources)"
    for lonely in ("epg.*", "Solo *", "* Source", "*-AU"):
        assert lonely not in labels
    # A plain source name is its own label.
    assert labels["guru-US"] == "guru-US"


def test_epg_wildcards_differing_only_in_case_are_offered_once():
    found = lf.derived_wildcards(["guru-US", "pluto-us", "samsung-us"])
    assert found == {"*-US": 3}


def test_epg_name_with_a_comma_is_not_offered_and_makes_no_wildcard():
    f, _ = _epg(sources=("A, B", "A, C", "Plain"))
    values = {o["value"] for o in f["options"]}
    assert values == {"Plain"}


@pytest.mark.parametrize("saved", ["", "*", "all", "_all", "guru-US, _all"])
def test_epg_all_is_an_empty_default(saved):
    f, _ = _epg({"epg_sources": saved})
    assert f["default"] == []


def test_epg_write_back_keeps_the_picked_order():
    out = lf.legacy_settings({"epg_sources": "guru-US", lf.EPG_LIST_ID: ["epgshare-*", "guru-US"]},
                             {lf.EPG_LIST_ID})
    assert out["epg_sources"] == "epgshare-*, guru-US"
    out = lf.legacy_settings({"epg_sources": "guru-US", lf.EPG_LIST_ID: []}, {lf.EPG_LIST_ID})
    assert out["epg_sources"] == ""


def test_epg_stored_list_does_not_overwrite_text_when_not_built():
    settings = {"epg_sources": "guru-US", lf.EPG_LIST_ID: ["epgshare-*"]}
    assert lf.legacy_settings(settings, {lf.PROFILE_LIST_ID})["epg_sources"] == "guru-US"


def test_epg_derived_wildcards_select_what_the_label_says():
    """The count in a wildcard label is what the plugin's own parser selects."""
    import fnmatch
    f, _ = _epg()
    for o in f["options"]:
        if o["label"] != o["value"]:
            n = sum(fnmatch.fnmatch(s.upper(), o["value"].upper()) for s in SOURCES)
            assert o["label"] == f"{o['value']} ({n} sources)"
