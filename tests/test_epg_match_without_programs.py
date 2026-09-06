"""Guard: EPG matching must not give up when no entry has programme data yet.

Dispatcharr downloads programme data only for EPG entries that are already
mapped to a channel. `parse_programs_for_source` says so in its own docstring,
and mapping entries to channels is exactly what this plugin's EPG matching does.
So an EPG source that has never been matched always reports zero entries with
programme data in the next 12 hours.

`_do_apply_epg_match` used to return at that point with a green, successful
looking result, which made every newly added EPG source permanently unmatchable:
no programmes until something is mapped, and nothing gets mapped because there
are no programmes. Measured on a live box on 2026-08-15 with a freshly enabled
Australian source carrying 354 entries and 0 programmes.

The function already builds a second match pass over every entry regardless of
programme data. This test pins that the programme-data check cannot return, so
that second pass stays reachable.
"""
import ast
from pathlib import Path

PLUGIN_PY = Path(__file__).resolve().parent.parent / "Lineuparr" / "plugin.py"
GUARD_TEST = "epg_data_with_programs"


def _epg_match_function():
    tree = ast.parse(PLUGIN_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_do_apply_epg_match":
            return node
    raise AssertionError("_do_apply_epg_match is not defined in plugin.py")


def _emptiness_guards(fn):
    """Every `if not epg_data_with_programs:` statement inside the function."""
    found = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id == GUARD_TEST
        ):
            found.append(node)
    return found


class TestProgrammeDataIsNotRequired:
    def test_the_function_still_checks_for_the_empty_pool(self):
        """The check itself is wanted: it is how the operator learns why."""
        assert _emptiness_guards(_epg_match_function()), (
            "the `if not epg_data_with_programs` check has disappeared, so nothing "
            "tells the operator that matching fell back to the full entry list"
        )

    def test_the_empty_pool_does_not_end_the_run(self):
        for guard in _emptiness_guards(_epg_match_function()):
            returns = [n for n in ast.walk(guard) if isinstance(n, ast.Return)]
            assert not returns, (
                "returning when no EPG entry has programme data makes a newly added "
                "EPG source permanently unmatchable, because Dispatcharr fetches "
                "programmes only for entries already mapped to a channel"
            )

    def test_the_operator_is_warned_rather_than_told_nothing(self):
        """A warning, not an info line: the run is degraded, not normal."""
        for guard in _emptiness_guards(_epg_match_function()):
            calls = [
                n
                for n in ast.walk(guard)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "warning"
            ]
            assert calls, "the fallback path must log at warning level"


class TestBothMatchPassesSurvive:
    def test_the_all_entries_pass_is_still_built(self):
        """The fallback pool is what the degraded run matches against."""
        source = PLUGIN_PY.read_text(encoding="utf-8")
        assert "epg_by_name_all" in source
        assert "unique_epg_names_all" in source
