"""
Regression: lineup channel names with dotted suffixes or CamelCase concatenation
(e.g. "JusticeCentral.TV", "DangerTV") failed to normalize the same as their
spaced equivalents ("Justice Central TV", "Danger TV"), producing NO MATCH
even when a matching EPG exists.

The normalizer now:
  1. Replaces dots between word chars with spaces (JusticeCentral.TV → JusticeCentral TV)
  2. Splits lower→Upper CamelCase boundaries (JusticeCentral → Justice Central)

All-caps acronyms (ESPN, HBO, MSNBC) must remain intact since they have no
lower→Upper transitions.
"""
from Lineuparr.fuzzy_matcher import FuzzyMatcher


def test_dotted_tv_suffix_normalizes_to_spaced():
    m = FuzzyMatcher()
    assert m.normalize_name("JusticeCentral.TV") == m.normalize_name("Justice Central TV")


def test_camelcase_splits():
    m = FuzzyMatcher()
    assert m.normalize_name("DangerTV") == m.normalize_name("Danger TV")
    assert m.normalize_name("BeautyIQ") == m.normalize_name("Beauty IQ")


def test_all_caps_acronyms_preserved():
    m = FuzzyMatcher()
    # No lower→Upper boundary in these - must not be split
    assert m.normalize_name("ESPN") == "ESPN" or m.normalize_name("ESPN").lower() == "espn"
    assert m.normalize_name("HBO").upper() == "HBO"
    assert m.normalize_name("MSNBC").upper() == "MSNBC"


def test_known_callsigns_not_broken():
    """The CamelCase split must not corrupt existing channel names that
    legitimately use a lowercase-Uppercase boundary as part of the brand."""
    m = FuzzyMatcher()
    # MeTV → "Me TV" - acceptable and desirable
    assert "TV" in m.normalize_name("MeTV") or m.normalize_name("MeTV") == "Me"
    # truTV is all-lowercase plus uppercase tail - TV suffix stripped anyway
    n = m.normalize_name("truTV")
    assert "tru" in n.lower()


def test_normalization_equivalence_for_match():
    """End-to-end: JusticeCentral.TV should match against 'Justice Central TV'."""
    m = FuzzyMatcher()
    candidates = ["US: Justice Central TV", "(US) Justice Channel"]
    m.precompute_normalizations(candidates)
    results = m.match_all_streams("JusticeCentral.TV", candidates, alias_map={})
    assert results, "JusticeCentral.TV should match Justice Central TV"
    best, score, _ = results[0]
    assert "Central" in best, f"got {best!r}"
