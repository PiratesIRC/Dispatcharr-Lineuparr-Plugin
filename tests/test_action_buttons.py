"""Action button colour, and the single place the buttons are declared.

WHICH LIST DISPATCHARR SERVES, measured 2026-09-05 by reading
`apps/plugins/loader.py` in the running container. At line 368 it takes
`getattr(instance, "actions", [])`, and at line 229 it falls back to the manifest
only when that attribute is empty. This plugin's `Plugin` class defines NO
`actions` attribute, so the manifest `Lineuparr/plugin.json` is what Dispatcharr
renders. That is the opposite arrangement from the settings form, which lives in
`Plugin.fields` while the manifest declares an empty fields array, so it is easy
to check the wrong file for either one.

The sibling plugin Stream-Mapparr declares actions in BOTH places and they had
drifted apart across 22 values. There is nothing to drift here while only one
declaration exists, and the last test in this file is what keeps that true: if a
`Plugin.actions` attribute is ever added it must agree with the manifest,
otherwise Dispatcharr silently starts serving the new one.

COLOUR DID NOT TRACK CONSEQUENCE, measured against the code:

  Apply Stream Match Only was green. With Preserve Existing Streams off it
  deletes every stream row on a channel and recreates the list
  (Lineuparr/plugin.py, ChannelStream.objects.filter(channel_id=...).delete()),
  and afterwards deletes every channel in this plugin's groups that ended the
  run with no streams. Full Sync was violet, a colour outside any scheme here,
  and reaches both of those through its stream matching step.

  Clear CSV Exports was the only red button on the page. It deletes files in
  /data/exports and no channel data at all.

  Preview Stream Match was cyan. Cyan means the action sends something outward.
  It writes a local CSV and an HTML report and sends nothing.

The rule adopted here, and what each colour means:

  red     can REMOVE something the user cares about: a stream or a channel
  orange  writes data or clears state, but removes no stream and no channel
  green   runs a normal operation that writes no user data
  cyan    sends something outward, to an inbox
  blue    reads and reports, changing nothing

Re-sort Streams by Quality is deliberately orange rather than red. Measured: it
only calls `.update(order=idx)` on existing rows, so the SET of streams on a
channel is unchanged and it cannot empty one. Apply Stream Match can.
"""
import io
import json
import os

import pytest

from Lineuparr.plugin import Plugin

MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Lineuparr", "plugin.json")

# Each action is classified by what it CAN DO, and the colour and the variant
# are derived from that. A hand-written table of colours would be a change
# detector: rekey a colour, watch the test fail, edit the table to match. That
# is how the state this replaced came about, with Apply Stream Match Only green
# while it deletes stream rows and channels, and Clear CSV Exports the only red
# button while it deletes no channel data at all. Adding an action now means
# classifying it, and the colour follows.
REMOVES_CHANNEL_DATA = {
    # Measured in Lineuparr/plugin.py: with Preserve Existing Streams off,
    # applying a stream match deletes every stream row on a channel and
    # recreates the list, then deletes channels left with no streams. Full Sync
    # reaches both through its stream matching step.
    "full_sync",
    "apply_stream_match",
}
SENDS_OUTWARD = {
    "email_report",
}
READ_ONLY = {
    "validate_settings",
    "plugin_status",
    # A dry run. It writes a CSV export and an HTML report and changes no
    # channel, stream or guide.
    "preview_stream_match",
}
# Everything else writes data or clears state and removes neither a stream nor
# a channel. Re-sorting only calls .update(order=...) on rows that already
# exist, so the SET of streams on a channel cannot change. Clearing the CSV
# exports deletes files in /data/exports and no channel data.
WRITES_BUT_REMOVES_NOTHING = {
    "sync_channels",
    "apply_epg_match",
    "apply_logo_match",
    "resort_streams",
    "clear_csv_exports",
}


def _consequence_colour(action_id):
    if action_id in REMOVES_CHANNEL_DATA:
        return "red"
    if action_id in SENDS_OUTWARD:
        return "cyan"
    if action_id in READ_ONLY:
        return "blue"
    if action_id in WRITES_BUT_REMOVES_NOTHING:
        return "orange"
    raise AssertionError(
        f"{action_id} has not been classified by what it can do, so no colour "
        f"can be derived for it")


EXPECTED_COLOURS = {a: _consequence_colour(a) for a in
                    (REMOVES_CHANNEL_DATA | SENDS_OUTWARD | READ_ONLY
                     | WRITES_BUT_REMOVES_NOTHING)}

# Dispatcharr's Mantine buttons accept these. An unrecognised value makes the
# serializer drop the whole action, so the button vanishes with only a log line.
ALLOWED_COLOURS = {"red", "orange", "green", "cyan", "blue"}
ALLOWED_VARIANTS = {"outline", "filled"}

# An action that only reads is drawn as an outline; one that acts is filled.


def _manifest():
    with io.open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


def _actions():
    """The list Dispatcharr serves for this plugin: the manifest."""
    return _manifest()["actions"]


def _action(action_id):
    return next((a for a in _actions() if a["id"] == action_id), None)


# --------------------------------------------------------------------------- #
# Every button is labelled and coloured
# --------------------------------------------------------------------------- #
def test_every_action_has_a_button_label():
    missing = [a["id"] for a in _actions() if not a.get("button_label")]
    assert missing == []


def test_every_action_has_a_button_colour():
    missing = [a["id"] for a in _actions() if not a.get("button_color")]
    assert missing == []


def test_no_action_is_invoked_by_an_event():
    """An event handler is never pressed, so it must carry no button metadata.

    This plugin has none. The test states the rule so that adding one without
    stripping its button metadata fails here rather than putting a button on the
    page that nobody can usefully press.
    """
    for a in _actions():
        if a.get("events"):
            assert "button_color" not in a, a["id"]
            assert "button_label" not in a, a["id"]


@pytest.mark.parametrize("key,allowed", [("button_color", ALLOWED_COLOURS),
                                         ("button_variant", ALLOWED_VARIANTS)])
def test_every_button_value_is_one_dispatcharr_recognises(key, allowed):
    """An unrecognised value drops the entire action from the served list."""
    bad = {a["id"]: a.get(key) for a in _actions()
           if a.get(key) is not None and a.get(key) not in allowed}
    assert bad == {}


# --------------------------------------------------------------------------- #
# Colour means one thing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action_id,colour", sorted(EXPECTED_COLOURS.items()))
def test_the_action_carries_the_colour_its_consequence_calls_for(action_id, colour):
    action = _action(action_id)
    assert action is not None, f"{action_id} is not declared"
    assert action.get("button_color") == colour


def test_every_declared_action_has_an_expected_colour():
    """A new action must be given a place in the scheme, not left uncoloured."""
    declared = {a["id"] for a in _actions()}
    assert declared == set(EXPECTED_COLOURS)


def test_red_is_reserved_for_the_actions_classified_as_removing_something():
    """If red spreads to merely noisy actions it stops carrying any warning."""
    red = {a["id"] for a in _actions() if a.get("button_color") == "red"}
    assert red == REMOVES_CHANNEL_DATA, red


def test_every_action_is_classified_exactly_once():
    """Two classifications would make the derived colour depend on check order."""
    groups = [REMOVES_CHANNEL_DATA, SENDS_OUTWARD, READ_ONLY,
              WRITES_BUT_REMOVES_NOTHING]
    seen = [a for group in groups for a in group]
    assert len(seen) == len(set(seen)), "an action appears in two groups"
    assert set(seen) == {a["id"] for a in _actions()}


def test_clearing_exports_is_not_red_because_it_removes_no_channel_data():
    """It was the only red button while deleting nothing but its own files."""
    assert _action("clear_csv_exports")["button_color"] == "orange"


def test_the_dry_run_preview_reads_and_reports_rather_than_sending():
    """Cyan means the action sends something outward. The preview writes files."""
    assert _action("preview_stream_match")["button_color"] == "blue"


def test_every_action_that_can_remove_something_also_asks_for_confirmation():
    """Colour is the glance; the dialog is the guard. A red button needs both."""
    for a in _actions():
        if a.get("button_color") == "red":
            assert a.get("confirm"), f"{a['id']} is red but has no confirm dialog"


@pytest.mark.parametrize("action_id", sorted(EXPECTED_COLOURS))
def test_an_action_that_only_reads_is_an_outline_and_one_that_acts_is_filled(action_id):
    expected = "outline" if action_id in READ_ONLY else "filled"
    assert _action(action_id).get("button_variant") == expected


# --------------------------------------------------------------------------- #
# The manifest is the only declaration, and every action can actually run
# --------------------------------------------------------------------------- #
def test_the_plugin_class_declares_no_action_list_of_its_own():
    """Dispatcharr prefers a `Plugin.actions` attribute over the manifest.

    While none exists the manifest is unambiguously what is served. If one is
    added later it has to agree with the manifest, and this test is the reminder
    to write that comparison rather than leaving two lists to drift.
    """
    assert not hasattr(Plugin, "actions"), (
        "Plugin now declares its own action list, which Dispatcharr will serve "
        "INSTEAD of plugin.json; add a test comparing the two")


def test_every_declared_action_is_routed_to_a_handler():
    """A button whose id is not in the routing map returns Unknown action."""
    import ast
    source = os.path.join(os.path.dirname(MANIFEST), "plugin.py")
    with io.open(source, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    routed = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", None) == "action_map"
                and isinstance(node.value, ast.Dict)):
            routed = {k.value for k in node.value.keys
                      if isinstance(k, ast.Constant)}
    assert routed, "action_map was not found in plugin.py"
    unrouted = sorted({a["id"] for a in _actions()} - routed)
    assert unrouted == [], f"declared actions with no handler: {unrouted}"


# --------------------------------------------------------------------------- #
# Wording of what the button says it will do
# --------------------------------------------------------------------------- #
def test_no_action_text_uses_an_em_dash():
    em_dash = chr(0x2014)
    offenders = [a["id"] for a in _actions()
                 if em_dash in json.dumps(a, ensure_ascii=False)]
    assert offenders == []


@pytest.mark.parametrize("action_id", ["full_sync", "apply_stream_match"])
def test_a_red_actions_description_says_what_it_can_remove(action_id):
    """The colour warns; the description has to say what the warning is about."""
    text = (_action(action_id).get("description") or "").lower()
    assert "delete" in text or "remove" in text or "replace" in text, text
