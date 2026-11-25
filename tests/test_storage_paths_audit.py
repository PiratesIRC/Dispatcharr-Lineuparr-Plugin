"""Every /data path literal in the shipped modules is one the storage design accounts for.

plugin.py holds its /data literals only in the PluginConfig class body: the legacy paths that
storage.bind moves, and the user-facing paths that stay (the shared exports folder and the
usage directory's legacy root). The other modules name a path only where the design keeps it:
the vendored usage client and notify client keep their own defaults (the plugin overrides the usage
directory by passing data_dir), the report writer keeps the legacy default that storage.bind
overwrites, and the report counter keeps the fixed root that Newsflasharr reads. A literal
anywhere else is a path the data-dir move does not cover.
"""
import ast
import pathlib

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "Lineuparr"

# storage.py names the legacy paths in its docstrings; plugin_storage.py guards real data.
SKIPPED = {"storage.py", "plugin_storage.py"}

# Every other module, and the exact literals it may hold. Each one is a deliberate default.
ALLOWED = {
    "plugin.py": None,  # checked separately: only inside PluginConfig
    "reports.py": {"/data/lineuparr_reports", "/data/exports"},
    # report_count.py: the counter root is /data because Newsflasharr reads
    # /data/<source>/report_count.json at that fixed path (notifier/newsflasharr/report_count.py,
    # DATA_ROOT and FILENAME). It must not move with the data directory.
    "report_count.py": {"/data"},
    "usage_client.py": {"/data/plugin_stats"},
    "notify_client.py": {"/data/newsflasharr"},
}

CONFIG_LITERALS = {
    "/data/lineuparr_channel_counts.jsonl",
    "/data/lineuparr_state.json",
    "/data/lineuparr_progress.json",
    "/data/lineuparr_operation.lock",
    "/data/plugin_stats",
    "/data/lineuparr_reports",
    "/data/exports",
}


def _data_literals(tree):
    """(value, node) for each string constant starting with /data and holding no whitespace."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("/data") and not any(ch.isspace() for ch in value):
                yield value, node


def _plugin_config_span(tree):
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PluginConfig")
    return cls.lineno, cls.end_lineno


def test_plugin_py_keeps_data_literals_only_in_the_config_class():
    path = PLUGIN_DIR / "plugin.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    first, last = _plugin_config_span(tree)
    found = list(_data_literals(tree))
    outside = sorted({v for v, n in found if not (first <= n.lineno <= last)})
    assert not outside, "a /data literal outside PluginConfig: %s" % outside
    inside = {v for v, n in found if first <= n.lineno <= last}
    assert inside == CONFIG_LITERALS


def test_no_other_module_holds_a_data_literal_outside_the_allowed_set():
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name in SKIPPED or path.name == "plugin.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found = {v for v, _n in _data_literals(tree)}
        allowed = ALLOWED.get(path.name, set())
        strays = found - allowed
        assert not strays, "%s holds unaccounted /data literals: %s" % (path.name, sorted(strays))
