#!/usr/bin/env python3
"""Static validation of the committed Lineuparr plugin runtime.

Runs in CI (and is safe to run locally) against ONLY the committed files under
Lineuparr/ plus README.md. It does NOT need Django, Dispatcharr, pytest, or the
local test suite (which is intentionally not committed), so it catches a
shipped-broken file before a user installs the plugin from GitHub.

Checks:
  1. Every Lineuparr/*.py byte-compiles.
  2. Every Lineuparr/*_lineup.json: valid JSON, filename shape, non-empty
     categories dict, list-valued categories, no duplicate channel names within
     a category, channels carry name + number.
  3. plugin.json: valid JSON, fields == [] (settings live in Plugin.fields),
     actions present, manifest version matches PLUGIN_VERSION in plugin.py,
     manifest field labels use BMP-only emoji.
  4. No em dashes (U+2014) anywhere in the committed runtime tree or README.

Exit code 0 = all checks passed, 1 = one or more failures (each printed).
"""
from __future__ import annotations

import json
import py_compile
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_DIR = ROOT / "Lineuparr"
EM_DASH = "—"
FILENAME_RE = re.compile(r"^[A-Z]{2,}_.+_lineup\.json$")
PY_VERSION_RE = re.compile(r'PLUGIN_VERSION\s*=\s*"([^"]+)"')

errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def check_python_compiles() -> None:
    for py in sorted(PLUGIN_DIR.glob("*.py")):
        try:
            py_compile.compile(str(py), doraise=True)
        except py_compile.PyCompileError as e:
            err(f"py_compile failed: {py.name}: {e}")


def check_lineups() -> None:
    files = sorted(PLUGIN_DIR.glob("*_lineup.json"))
    if not files:
        err("no *_lineup.json files found")
        return
    for path in files:
        if not FILENAME_RE.match(path.name):
            err(f"{path.name}: filename will not parse as <CC>_<name>_lineup.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            err(f"{path.name}: invalid JSON: {e}")
            continue
        cats = data.get("categories")
        if not isinstance(cats, dict) or not cats:
            err(f"{path.name}: missing or empty 'categories' dict")
            continue
        for cat_name, channels in cats.items():
            if not isinstance(channels, list):
                err(f"{path.name}: category '{cat_name}' must be a list")
                continue
            seen: dict[str, int] = {}
            for i, ch in enumerate(channels):
                if not isinstance(ch, dict):
                    err(f"{path.name}: {cat_name}[{i}] is not an object")
                    continue
                name = ch.get("name")
                if not name:
                    err(f"{path.name}: {cat_name}[{i}] missing 'name'")
                if "number" not in ch:
                    err(f"{path.name}: channel '{name}' in '{cat_name}' missing 'number'")
                seen[name] = seen.get(name, 0) + 1
            dups = [f"{n!r} x{c}" for n, c in seen.items() if c > 1]
            if dups:
                err(f"{path.name}: duplicate names in '{cat_name}': {', '.join(dups)}")


def check_manifest() -> None:
    pj = PLUGIN_DIR / "plugin.json"
    ppy = PLUGIN_DIR / "plugin.py"
    try:
        manifest = json.loads(pj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"plugin.json: invalid JSON: {e}")
        return
    if manifest.get("fields") != []:
        err("plugin.json: 'fields' must be [] (settings live in Plugin.fields)")
    if not manifest.get("actions"):
        err("plugin.json: 'actions' is missing or empty")
    m = PY_VERSION_RE.search(ppy.read_text(encoding="utf-8"))
    if not m:
        err("plugin.py: PLUGIN_VERSION not found")
    elif manifest.get("version") != m.group(1):
        err(f"version drift: plugin.json={manifest.get('version')} plugin.py={m.group(1)}")
    for field in manifest.get("fields", []):
        for key in ("label", "button_label"):
            for chr_ in field.get(key, ""):
                if ord(chr_) > 0xFFFF:
                    err(f"plugin.json field {field.get('id')!r} {key}: non-BMP char U+{ord(chr_):04X}")


def check_no_em_dashes() -> None:
    targets = list(PLUGIN_DIR.glob("*.py")) + list(PLUGIN_DIR.glob("*.json"))
    readme = ROOT / "README.md"
    if readme.exists():
        targets.append(readme)
    for path in targets:
        if EM_DASH in path.read_text(encoding="utf-8"):
            err(f"{path.relative_to(ROOT)}: contains an em dash (U+2014)")


def main() -> int:
    check_python_compiles()
    check_lineups()
    check_manifest()
    check_no_em_dashes()
    if errors:
        print(f"VALIDATION FAILED ({len(errors)} issue(s)):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("Plugin validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
