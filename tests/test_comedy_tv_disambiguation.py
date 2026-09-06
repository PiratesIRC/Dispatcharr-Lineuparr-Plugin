"""
Regression: when multiple EPG candidates tie at the top score (because
normalize_name collapses brand-name timezones like "Comedy Central" and
"Comedy TV" both to "comedy"), the matcher must prefer the candidate whose
ORIGINAL name shares the most tokens with the lineup's original name.

Without this tie-breaker, lineup "Comedy TV" silently matches the EPG for
Comedy Central - a completely different channel.
"""
from Lineuparr.fuzzy_matcher import FuzzyMatcher


def _build_matcher(candidates):
    m = FuzzyMatcher()
    m.precompute_normalizations(candidates)
    return m


def test_comedy_tv_picks_comedy_tv_over_comedy_central():
    candidates = [
        "(US) Comedy Central (S)",
        "USA: Comedy TV",
        "US: Comedy tv",
        "US: COMEDY CENTRAL",
        "US: Comedy Central",
    ]
    m = _build_matcher(candidates)
    results = m.match_all_streams("Comedy TV", candidates, alias_map={})

    assert results, "expected at least one match"
    best_name, best_score, _ = results[0]
    # Best match must contain the "TV" token, not "Central"
    assert "TV" in best_name or "tv" in best_name, (
        f"Comedy TV should not be hijacked by Comedy Central; got {best_name!r}"
    )
    assert "Central" not in best_name and "CENTRAL" not in best_name, (
        f"Comedy TV must NOT match a Comedy Central EPG; got {best_name!r}"
    )


def test_comedy_central_still_picks_comedy_central():
    """The tie-breaker must not regress the other direction."""
    candidates = [
        "USA: Comedy TV",
        "US: Comedy tv",
        "(US) Comedy Central (S)",
        "US: COMEDY CENTRAL",
        "US: Comedy Central",
    ]
    m = _build_matcher(candidates)
    results = m.match_all_streams("Comedy Central", candidates, alias_map={})

    assert results
    best_name, _, _ = results[0]
    assert "Central" in best_name or "CENTRAL" in best_name, (
        f"Comedy Central should still match its own EPG; got {best_name!r}"
    )


def test_justice_central_picks_justice_central():
    """Same class of brand-name-collapse bug for Justice Central."""
    candidates = [
        "US: Justice Central TV",
        "US: Justice Network",
        "US: Justice Channel",
    ]
    m = _build_matcher(candidates)
    results = m.match_all_streams("Justice Central", candidates, alias_map={})

    assert results
    best_name, _, _ = results[0]
    # Either Justice Central TV or Justice Channel could tie; prefer Central
    assert "Central" in best_name, f"got {best_name!r}"
