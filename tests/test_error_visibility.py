"""Guard: a failure result must set the key Dispatcharr renders in red.

Dispatcharr's plugin card renders exactly three keys from an action result:
`message` (a transient green toast, auto-closing after about four seconds),
`error` (persistent, red) and `file`. `status` renders NOWHERE. So a return that
sets `"status": "error"` and puts the reason in `message` looks EXACTLY like a
success to the operator, and it disappears before it can be read carefully.

The convention this pins: `status` says whether the action ran, `error` says the
operator must see it, and exactly one of `message` or `error` is set.

Ported from the equivalent guard in the sibling Channel-Maparr plugin, which
found the same class of defect there.
"""
import ast
from pathlib import Path

PLUGIN_PY = Path(__file__).resolve().parent.parent / "Lineuparr" / "plugin.py"


class TestResultText:
    """`result_text` reads whichever of the two rendered keys a result carries.

    Once a failure result carries its reason under `error` rather than `message`,
    anything that reads a sub-action's result has to look in both places. Full
    Sync indexes `result['message']` directly on its abort path, so reading the
    old key alone would raise a KeyError at the exact moment a step failed.
    """

    def _fn(self):
        import Lineuparr.plugin as plugin_module
        return plugin_module.result_text

    def test_reads_the_error_key_when_the_result_failed(self):
        assert self._fn()({"status": "error", "error": "boom"}) == "boom"

    def test_reads_the_message_key_when_the_result_succeeded(self):
        assert self._fn()({"status": "ok", "message": "done"}) == "done"

    def test_prefers_the_error_key_when_a_result_carries_both(self):
        assert self._fn()({"error": "boom", "message": "done"}) == "boom"

    def test_returns_the_default_for_a_result_carrying_neither(self):
        assert self._fn()({"status": "ok"}, "fallback") == "fallback"

    def test_returns_the_default_for_a_non_dict(self):
        assert self._fn()(None, "fallback") == "fallback"

    def test_an_empty_error_string_falls_through_to_the_message(self):
        assert self._fn()({"error": "", "message": "done"}) == "done"


def _error_returns_without_error_key(source):
    """Line numbers of `return {... "status": "error" ...}` carrying no "error".

    Blind spots, by design rather than oversight. This walks only `ast.Return`
    nodes whose value is a literal `ast.Dict`, so it cannot see:

    (a) a computed status, as in `return {"status": status, "message": msg}`.
        Lineuparr/plugin.py has three of those. They are listed in
        COMPUTED_STATUS_RETURNS below and were read by hand.
    (b) a result dict that is ASSIGNED rather than returned, which is how the
        background threads publish their outcome.

    A clean run of this guard is therefore not proof that every failure path in
    the file is visible to the operator.
    """
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        pairs = {}
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant):
                pairs[key.value] = value
        status = pairs.get("status")
        if (isinstance(status, ast.Constant) and status.value == "error"
                and "error" not in pairs):
            offenders.append(node.lineno)
    return offenders


def test_every_literal_error_return_sets_the_error_key():
    offenders = _error_returns_without_error_key(
        PLUGIN_PY.read_text(encoding="utf-8"))
    assert not offenders, (
        f"status='error' returns with no `error` key, which the plugin card "
        f"renders as a green success toast, at lines: {offenders}"
    )


def test_no_return_sets_both_message_and_error():
    """Exactly one of the two rendered keys, so the card shows one thing."""
    both = []
    for node in ast.walk(ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict):
            continue
        keys = {key.value for key in node.value.keys
                if isinstance(key, ast.Constant)}
        if {"message", "error"} <= keys:
            both.append(node.lineno)
    assert not both, f"returns setting both `message` and `error` at lines: {both}"


# The detector has to BITE. An AST guard with no positive fixture passes for as
# long as it is silently broken, and a passing guard and a dead guard look the
# same from the outside.

BAD = '''
def f():
    return {"status": "error", "message": "boom"}
'''

GOOD_ERROR_KEY = '''
def f():
    return {"status": "error", "error": "boom"}
'''

GOOD_NOT_AN_ERROR = '''
def f():
    return {"status": "success", "message": "fine"}
'''


def test_detector_flags_an_error_return_without_the_key():
    assert _error_returns_without_error_key(BAD) == [3]


def test_detector_accepts_a_visible_error():
    assert _error_returns_without_error_key(GOOD_ERROR_KEY) == []


def test_detector_ignores_success_returns():
    assert _error_returns_without_error_key(GOOD_NOT_AN_ERROR) == []


class TestActionResult:
    """`action_result` puts the text under the key that matches the status.

    Two actions compute their status from how much of the work succeeded:
    Validate Settings reports an error when any check failed, and Sync Channels
    reports one when every channel failed. Both used to put the failure detail
    under `message`, so a validation listing real errors rendered as a green
    toast. The AST guard above cannot see either, because the status is a
    variable rather than a literal, so they go through this helper instead.
    """

    def _fn(self):
        import Lineuparr.plugin as plugin_module
        return plugin_module.action_result

    def test_an_error_status_puts_the_text_under_the_error_key(self):
        assert self._fn()("error", "3 errors found") == {
            "status": "error", "error": "3 errors found"}

    def test_an_ok_status_puts_the_text_under_the_message_key(self):
        assert self._fn()("ok", "all valid") == {
            "status": "ok", "message": "all valid"}

    def test_a_warning_status_puts_the_text_under_the_message_key(self):
        """A warning is a run that happened, so it is not red and not persistent."""
        assert self._fn()("warning", "2 of 40 failed") == {
            "status": "warning", "message": "2 of 40 failed"}

    def test_extra_keys_are_carried_through(self):
        assert self._fn()("ok", "done", data=[1]) == {
            "status": "ok", "message": "done", "data": [1]}

    def test_never_sets_both_rendered_keys(self):
        for status in ("ok", "warning", "error"):
            keys = set(self._fn()(status, "text"))
            assert not {"message", "error"} <= keys


def test_no_computed_status_return_hand_builds_the_result_dict():
    """A computed status must route through `action_result`, not a literal dict.

    This is the form the AST guard above is blind to, so without this check a
    new `return {"status": status, "message": msg}` would reintroduce exactly
    the defect this file exists to prevent, and every test here would pass.
    """
    tree = ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))
    # The helper itself is the one place allowed to build such a dict, since
    # building it correctly is the whole of what it does.
    helper = [node for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and node.name == "action_result"]
    assert len(helper) == 1, "action_result is missing or defined more than once"
    exempt = {node for node in ast.walk(helper[0])}

    hand_built = []
    for node in ast.walk(tree):
        if (not isinstance(node, ast.Return) or not isinstance(node.value, ast.Dict)
                or node in exempt):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if (isinstance(key, ast.Constant) and key.value == "status"
                    and not isinstance(value, ast.Constant)):
                hand_built.append(node.lineno)
    assert not hand_built, (
        f"returns with a computed status built as a literal dict, which cannot "
        f"choose the right rendered key, at lines: {hand_built}"
    )
