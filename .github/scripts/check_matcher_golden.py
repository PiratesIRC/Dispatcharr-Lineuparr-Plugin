#!/usr/bin/env python3
"""Golden drift gate for Lineuparr's matcher primitives and exclusion contract.

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
    # bracket group in the MIDDLE of a name (bug-102): every pattern that
    # removes the group also eats the spaces around it, so substituting the
    # match with nothing joins the neighbours
    "Big Ten Network (Southern California) Alternate", "Penthouse (TEN) On Demand",
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
    return _stable_value(val)


def _stable_value(value):
    """Convert matcher output to the same JSON-native shape before comparison."""
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _stable_value(item) for key, item in value.items()}
    return value


def run_corpus(matcher, parse_excluded_aliases):
    out = {
        "process_string": {n: _safe(matcher.process_string_for_matching, n) for n in NAMES},
        "normalize_name": {},
        "calculate_similarity": {f"{a}|{b}": _safe(matcher.calculate_similarity, a, b) for a, b in PAIRS},
        "extract_callsign": {n: _safe(matcher.extract_callsign, n) for n in NAMES},
        "normalize_callsign": {n: _safe(matcher.normalize_callsign, n) for n in NAMES},
        "excluded_aliases": {
            "parse_string": _safe(parse_excluded_aliases, "  US-ReelzChannel  "),
            "parse_list": _safe(
                parse_excluded_aliases,
                ["US-ReelzChannel", "us-reelzchannel", "Reelz Channel"],
            ),
            "parse_invalid_container": _safe(parse_excluded_aliases, {"bad": "value"}),
            "parse_invalid_items": _safe(
                parse_excluded_aliases, ["Reelz Channel", "", None, 42]
            ),
        },
    }
    for label, flags in FLAG_COMBOS:
        out["normalize_name"][label] = {n: _safe(matcher.normalize_name, n, **flags) for n in NAMES}
    reelz_candidates = ["US-ReelzChannel", "US: Other Network HD"]
    reelz_aliases = {
        "REELZ": ["US-ReelzChannel"],
        "Other Channel": ["US-ReelzChannel"],
    }
    quality_aliases = {"Example": ["US: Example 4K"]}
    callsign_aliases = {"My9 New York": ["WWOR"]}
    out["excluded_aliases"]["positive_alias"] = _safe(
        matcher.match_all_streams, "REELZ", reelz_candidates, reelz_aliases
    )
    out["excluded_aliases"]["literal_blocks_positive_alias"] = _safe(
        matcher.match_all_streams, "REELZ", reelz_candidates, reelz_aliases,
        excluded_aliases="US-ReelzChannel",
    )
    out["excluded_aliases"]["normalized_variant_positive"] = _safe(
        matcher.match_all_streams, "REELZ", ["US | Reelz Channel HD"], {}
    )
    out["excluded_aliases"]["unlisted_full_name_survives"] = _safe(
        matcher.match_all_streams, "REELZ", ["US | Reelz Channel HD"], {},
        excluded_aliases=["ReelzChannel"],
    )
    out["excluded_aliases"]["channel_scoped"] = _safe(
        matcher.match_all_streams, "Other Channel", ["US-ReelzChannel"], reelz_aliases
    )
    out["excluded_aliases"]["quality_bypass_positive"] = _safe(
        matcher.match_all_streams, "Example", ["US: Example 4K"], quality_aliases,
        quality_aware=True,
    )
    out["excluded_aliases"]["unlisted_quality_prefix_survives"] = _safe(
        matcher.match_all_streams, "Example", ["US: Example 4K"], quality_aliases,
        quality_aware=True, excluded_aliases=["Example 4K"],
    )
    out["excluded_aliases"]["callsign_rescue_blocked"] = _safe(
        matcher.match_all_streams, "My9 New York", ["US: MY 9 WWOR NEW YORK"],
        callsign_aliases, excluded_aliases=["US: MY 9 WWOR NEW YORK"],
    )
    out["excluded_aliases"]["regex_prefix_is_literal"] = _safe(
        matcher.match_all_streams, "REELZ", ["US-ReelzChannel"], reelz_aliases,
        excluded_aliases=["regex:^US-ReelzChannel$"],
    )
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
    return mod.FuzzyMatcher(), mod.parse_excluded_aliases


def check_exclusion_regressions(matcher, parse_exclusions):
    """Assert survivor identities independently of the recorded golden outputs."""
    cases = [
        ("Game Show Network", ["Game Show Central"],
         ["Game Show Central", "Game Show Network", "Game Show Network HD"], ["Game Show Central"]),
        ("Game Show Network", ["*Game Show Central*"],
         ["US: Game Show Central HD", "GAME  SHOW CENTRAL", "Game Show Network",
          "Game Show Network HD", "GameShowCentral"], ["US: Game Show Central HD", "GAME  SHOW CENTRAL"]),
        ("REELZ", ["US-ReelzChannel"],
         ["US-ReelzChannel", "REELZ", "Reelz Channel", "Reelz HD"], ["US-ReelzChannel"]),
        ("NFL Network", ["NFL Channel"],
         ["NFL Channel", "NFL Network", "US: NFL Channel HD"], ["NFL Channel"]),
        ("Example", ["  uS:   Example  "],
         ["US: Example", "US: Example HD", "Example"], ["US: Example"]),
        ("Example", [r"M\*A\*S\*H"],
         ["M*A*S*H", "MASH", "MxxAxxSxxH"], ["M*A*S*H"]),
        ("Example", [r"US\\M\*A\*S\*H"],
         [r"US\M*A*S*H", r"US\MASH"], [r"US\M*A*S*H"]),
        ("Example", ["US:*Backup"],
         ["US: Example Backup", "US: Example Backup HD", "Example Backup"], ["US: Example Backup"]),
        ("Example", ["Example ? [HD]"],
         ["Example ? [HD]", "Example X H", "Example"], ["Example ? [HD]"]),
        ("Example", ["*", "**", " * * "],
         ["Example", "Example HD"], []),
    ]
    for channel, excluded, candidates, blocked in cases:
        aliases = {channel: candidates}
        before = matcher.match_all_streams(channel, candidates, aliases)
        before_names = {m[0] for m in before}
        assert set(blocked) <= before_names, (channel, "bad test pool")
        assert before_names - set(blocked), (channel, "no positive survivor")
        after = matcher.match_all_streams(channel, candidates, aliases, excluded_aliases=excluded)
        assert after == [m for m in before if m[0] not in blocked], (channel, excluded, after)
        # No leakage into another channel or a later no-exclusions call.
        assert matcher.match_all_streams(channel, candidates, aliases) == before
    assert not parse_exclusions(["*", " * * ", None, ""])
    assert parse_exclusions(r"\*") == [r"\*"]
    # Direct checks cover patterns independently of positive-matcher eligibility.
    # load_matcher does not register the module: helpers remain in method globals.
    helpers = matcher._candidate_is_excluded.__func__.__globals__
    compile_pattern = helpers["_exclusion_parts"]
    match_pattern = helpers["_matches_exclusion"]
    for pattern, name, expected in [
        ("a*a", "a", False), ("a*a", "aa", True),
        ("*ab*bc", "abc", False), ("*ab*bc", "abbc", True),
        ("a**b***c", "abc", True), ("a*b*c", "acb", False),
        ("*Game Show Central*", "game show centralization", True),
        ("Game Show Central", "gameshowcentral", False),
        ("*", "anything", False),
    ]:
        parts = compile_pattern(pattern)
        actual = bool(parts and match_pattern(name, parts))
        assert actual == expected, (pattern, name, actual)
    # Quality bypass and callsign rescue must not revive excluded names.
    for channel, candidates, aliases, kwargs in [
        ("Example", ["US: Example 4K", "Example HD"],
         {"Example": ["US: Example 4K", "Example HD"]}, {"quality_aware": True}),
        ("My9 New York", ["US: MY 9 WWOR NEW YORK", "My9 New York"],
         {"My9 New York": ["WWOR", "My9 New York"]}, {}),
    ]:
        before = matcher.match_all_streams(channel, candidates, aliases, **kwargs)
        assert {m[0] for m in before} == set(candidates)
        after = matcher.match_all_streams(channel, candidates, aliases,
                                          excluded_aliases=[candidates[0]], **kwargs)
        assert {m[0] for m in after} == {candidates[1]}


def main() -> int:
    ap = argparse.ArgumentParser(description="Lineuparr matcher golden drift gate")
    ap.add_argument("--write", action="store_true", help="(re)generate the baseline from current code")
    args = ap.parse_args()

    matcher, parse_exclusions = load_matcher()
    check_exclusion_regressions(matcher, parse_exclusions)
    current = run_corpus(matcher, parse_exclusions)
    if args.write:
        BASELINE.write_text(
            json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
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
        print(f"MATCHER GOLDEN DRIFT ({len(diffs)} matcher output(s) changed):")
        for key, old, new in diffs[:30]:
            print(f"  {key}:  {old!r}  ->  {new!r}")
        print("If intended, re-run with --write and commit the updated baseline.")
        return 1
    print(f"Matcher golden gate passed ({len(base_flat)} matcher outputs match baseline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
