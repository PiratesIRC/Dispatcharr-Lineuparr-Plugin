#!/usr/bin/env python3
"""Golden drift gate for Lineuparr's pure matcher primitives (Stage 0).

Lineuparr keeps its unit suite OUT of git (tests/ is gitignored) and CI runs only the
static validate_plugin.py, so the matcher golden gate lives here in .github/scripts
instead of a pytest file. It is the Lineuparr equivalent of
Stream/Channel/EPG's tests/test_matcher_golden.py.

It loads Lineuparr/fuzzy_matcher.py directly, runs a shared corpus through the PURE
primitives, and compares against the committed matcher_golden_baseline.json beside this
script. Any unreviewed change to match behavior fails CI. An INTENDED de-drift change is
landed by re-running this with --write (or tools/matcher_parity_check.py --write at the
workspace root) and committing the updated baseline in the same change.

Keep the corpus below in lockstep with tools/matcher_parity_check.py and the per-plugin
tests/test_matcher_golden.py. Needs rapidfuzz installed to match the production path.
See MATCHER-STANDARDIZATION-PLAN.md §7.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # <repo>/.github/scripts -> <repo>
INNER = ROOT / "Lineuparr"
BASELINE = HERE / "matcher_golden_baseline.json"

# --- shared corpus (keep identical to tools/matcher_parity_check.py) ---------
NAMES = [
    "US: USA Network HD", "US| ESPN", "[US] CNN", "UK: BBC One", "UK| ITV 1",
    "Discovery Channel 4K", "HBO HD", "ESPN [FHD]", "Cinemax HD", "TNT UHD RAW",
    "BBC Three", "BBC Four", "Three Angels Broadcasting Network", "ESPN 2", "HBO 2",
    "JusticeCentral.TV", "DangerTV", "NewsNation",
    "HBO East", "HBO West", "HBO (W)", "Fox Sports West", "ESPN Pacific",
    "(PRIME) FOX News", "(D1) CBS",
    "Disney+", "Discovery+", "Paramount+", "Disney Channel", "Discovery Channel",
    "Justice Central", "Justice Central.TV", "Justice Central TV", "True Crime Network",
    "WABC-TV", "KCBS", "KING 5", "WAVE 3", "WOOD TV8", "WHO 13", "KOMO News",
    "\U0001f174\U0001f182\U0001f17f\U0001f175", "┃US┃ ESPN", "★ CNN ★",
    "Россия 1", "France 2", "beИN SPORTS",
    "HLN", "MTV", "getTV", "TUDN", "SEC Network", "NHL Network", "BBC News",
]
PAIRS = [
    ("usanetwork", "usanetwork"), ("espn", "espn2"), ("hbo", "hbo2"),
    ("bbcone", "bbctwo"), ("disney", "disneyplus"), ("foxnews", "foxnews"),
    ("cnn", "cnninternational"), ("discoverychannel", "discovery"), ("e", "ae"),
    ("paramount", "paramountnetwork"), ("nflnetwork", "nhlnetwork"),
    ("justicecentral", "truecrimenetwork"), ("a", "a"), ("", ""),
]
FLAG_COMBOS = [
    ("all_on", dict(ignore_quality=True, ignore_regional=True, ignore_geographic=True, ignore_misc=True)),
    ("regional_off", dict(ignore_quality=True, ignore_regional=False, ignore_geographic=True, ignore_misc=True)),
]


def _safe(fn, *args, **kwargs):
    try:
        val = fn(*args, **kwargs)
    except Exception as exc:
        return f"__ERROR__: {type(exc).__name__}: {exc}"
    if isinstance(val, float):
        return round(val, 9)
    return val


def run_corpus(matcher):
    out = {
        "process_string": {n: _safe(matcher.process_string_for_matching, n) for n in NAMES},
        "normalize_name": {},
        "calculate_similarity": {f"{a}|{b}": _safe(matcher.calculate_similarity, a, b) for a, b in PAIRS},
        "extract_callsign": {n: _safe(matcher.extract_callsign, n) for n in NAMES},
        "normalize_callsign": {n: _safe(matcher.normalize_callsign, n) for n in NAMES},
    }
    for label, flags in FLAG_COMBOS:
        out["normalize_name"][label] = {n: _safe(matcher.normalize_name, n, **flags) for n in NAMES}
    return out


def _flatten(d, prefix=""):
    for k in sorted(d):
        v = d[k]
        if isinstance(v, dict):
            yield from _flatten(v, f"{prefix}{k}.")
        else:
            yield (f"{prefix}{k}", v)


def load_matcher():
    path = INNER / "fuzzy_matcher.py"
    saved_path = list(sys.path)
    saved_aliases = sys.modules.pop("aliases", None)
    sys.path.insert(0, str(INNER))
    try:
        spec = importlib.util.spec_from_file_location("fm_golden_under_test", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("aliases", None)
        if saved_aliases is not None:
            sys.modules["aliases"] = saved_aliases
    return mod.FuzzyMatcher()


def main() -> int:
    ap = argparse.ArgumentParser(description="Lineuparr matcher golden drift gate")
    ap.add_argument("--write", action="store_true", help="(re)generate the baseline from current code")
    args = ap.parse_args()

    current = run_corpus(load_matcher())
    if args.write:
        BASELINE.write_text(
            json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        print(f"wrote {BASELINE}")
        return 0

    if not BASELINE.exists():
        print(f"MATCHER GOLDEN: no baseline at {BASELINE} (run with --write first)")
        return 1
    base_flat = dict(_flatten(json.loads(BASELINE.read_text(encoding="utf-8"))))
    cur_flat = dict(_flatten(current))
    diffs = [(k, base_flat.get(k, "<missing>"), cur_flat.get(k, "<missing>"))
             for k in sorted(set(base_flat) | set(cur_flat))
             if base_flat.get(k, "<missing>") != cur_flat.get(k, "<missing>")]
    if diffs:
        print(f"MATCHER GOLDEN DRIFT ({len(diffs)} primitive output(s) changed):")
        for key, old, new in diffs[:30]:
            print(f"  {key}:  {old!r}  ->  {new!r}")
        print("If intended, re-run with --write and commit the updated baseline.")
        return 1
    print(f"Matcher golden gate passed ({len(base_flat)} primitive outputs match baseline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
