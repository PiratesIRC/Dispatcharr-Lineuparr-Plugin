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
NEW_ID_FOR = {"channel_profiles": lf.PROFILE_LIST_ID, "custom_aliases": lf.ALIAS_ROWS_ID}


def _plugin():
    p = Plugin.__new__(Plugin)
    p._built_form_ids = set()
    return p


def _flat():
    return _plugin().fields


def _form(settings=None, profiles=("Living Room", "Kids")):
    return lf.form_034(_flat(), settings or {}, profiles, plugin_module._clean_json_text)


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
    assert built == {lf.PROFILE_LIST_ID, lf.ALIAS_ROWS_ID}


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
    fields = p.get_fields({"settings": {"channel_profiles": "Kids"}})
    assert any(f["type"] == "section" for f in fields)
    assert p._built_form_ids == {lf.PROFILE_LIST_ID, lf.ALIAS_ROWS_ID}


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
