"""A stream's provider group tells you its country when its name does not.

Found on a live box on 2026-08-15. An Australian lineup was matched against a
provider whose streams carry platform prefixes rather than country prefixes:
`GO:`, `RK:`, `PRIME:`, `NOW:`, `TUBI:`. `detect_stream_country` returns None
for every one of those, so the country filter treated them as untagged and let
them into the Australian lineup. 33 of the 49 streams attached were not
Australian feeds. The clearest wrong result: lineup channel `ESPN` took
`GO: ESPN`, the United States feed, while `AU: ESPN 1 HD` sat unused, because an
untagged exact name outscores an own-country fuzzy one and the existing
country-preference sort never sees a tie.

The signal that settles it is the provider group the stream came from, which is
named `US| ENTERTAINMENT`, `AU| AUSTRALIA VIP` and so on. The matcher never
received it, because only the stream name was passed in.

Two rules this pins:
  - the group is consulted ONLY when the name itself carries no country marker,
    so nothing that already worked changes its answer;
  - the group-derived filter is skipped when it would remove every remaining
    candidate, the same escape the region filter already uses, so a provider
    whose groups are mislabelled loses no matches.
"""
import pytest

from Lineuparr.fuzzy_matcher import FuzzyMatcher, detect_category_country, detect_stream_country


class TestProviderGroupNamesResolveToCountries:
    """The real group names from the live provider, not invented ones."""

    @pytest.mark.parametrize("group,expected", [
        ("AU| AUSTRALIA VIP", "AU"),
        ("AU| PRIME ᴿᴬᵂ ⁶⁰ᶠᵖˢ", "AU"),
        ("AU| 9NOW ᴿᴬᵂ", "AU"),
        ("US| ENTERTAINMENT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ", "US"),
        ("US| NEWS ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ", "US"),
        ("UK| ENTERTAINMENT", "UK"),
    ])
    def test_a_country_prefixed_group_resolves(self, group, expected):
        assert detect_category_country(group) == expected

    @pytest.mark.parametrize("group", ["24/7 Streams", "", None])
    def test_a_group_with_no_country_resolves_to_nothing(self, group):
        assert detect_category_country(group) is None


class TestTheStreamNameStillWinsWhenItHasOne:
    """The group is a fallback, never an override."""

    def test_a_country_prefixed_stream_name_is_still_read_from_the_name(self):
        assert detect_stream_country("AU: FOX CRICKET HD") == "AU"

    def test_a_platform_prefixed_stream_name_yields_nothing_on_its_own(self):
        """This is the gap the group is there to close."""
        for name in ["GO: ESPN", "RK: VEVO POP", "PRIME: CNN INTERNATIONAL", "NOW: COMEDY CENTRAL"]:
            assert detect_stream_country(name) is None


class TestGroupCountryFiltersCandidates:
    def _matcher(self):
        return FuzzyMatcher(match_threshold=70)

    def test_a_wrong_country_group_is_dropped_when_an_own_country_stream_survives(self):
        """A real case from the live box: ESPN2 attached both feeds.

        Both names normalize to `ESPN 2` and both scored 100, so the channel
        ended up carrying the Australian feed and the United States one. The
        Australian stream is judged by its name and kept; the other has no
        country in its name and its group places it in the United States.
        """
        m = self._matcher()
        candidates = ["GO: ESPN2", "AU: ESPN 2 HD"]
        m.precompute_normalizations(candidates)
        results = m.match_all_streams(
            "ESPN2", candidates, {}, lineup_country="AU",
            candidate_countries={"GO: ESPN2": "US", "AU: ESPN 2 HD": "AU"},
        )
        names = [r[0] for r in results]
        assert "AU: ESPN 2 HD" in names
        assert "GO: ESPN2" not in names, (
            "a stream from a United States provider group must not be offered to "
            "an Australian lineup when an Australian stream is available"
        )

    def test_the_espn_case_is_a_name_shape_gap_and_this_change_does_not_touch_it(self):
        """Recorded so nobody re-reads the live symptom as a country problem.

        The lineup channel is `ESPN`. The Australian stream is `AU: ESPN 1 HD`,
        which normalizes to `ESPN 1` and never scores against `ESPN` at all, so
        the matcher never chose between the two feeds. Only the United States
        stream was ever a candidate, and the escape below therefore keeps it.
        Fixing this needs a name change, not a country rule.
        """
        m = self._matcher()
        candidates = ["GO: ESPN", "AU: ESPN 1 HD"]
        m.precompute_normalizations(candidates)
        results = m.match_all_streams(
            "ESPN", candidates, {}, lineup_country="AU",
            candidate_countries={"GO: ESPN": "US", "AU: ESPN 1 HD": "AU"},
        )
        assert [r[0] for r in results] == ["GO: ESPN"]

    def test_a_wrong_country_group_is_kept_when_it_is_the_only_candidate(self):
        """No total loss: a mislabelled provider must not empty the result."""
        m = self._matcher()
        candidates = ["RK: VEVO POP"]
        m.precompute_normalizations(candidates)
        results = m.match_all_streams(
            "Vevo Pop", candidates, {}, lineup_country="AU",
            candidate_countries={"RK: VEVO POP": "US"},
        )
        assert [r[0] for r in results] == ["RK: VEVO POP"]

    def test_an_own_country_group_passes(self):
        m = self._matcher()
        candidates = ["PRIME: BBC FIRST"]
        m.precompute_normalizations(candidates)
        results = m.match_all_streams(
            "BBC First", candidates, {}, lineup_country="AU",
            candidate_countries={"PRIME: BBC FIRST": "AU"},
        )
        assert [r[0] for r in results] == ["PRIME: BBC FIRST"]

    def test_omitting_the_map_leaves_behaviour_exactly_as_before(self):
        """Every existing caller passes nothing, and must be unaffected."""
        m = self._matcher()
        candidates = ["GO: ESPN", "AU: ESPN 1 HD"]
        m.precompute_normalizations(candidates)
        results = m.match_all_streams("ESPN", candidates, {}, lineup_country="AU")
        assert "GO: ESPN" in [r[0] for r in results]

    def test_a_stream_named_for_the_wrong_country_is_still_dropped_by_its_name(self):
        """The name-based filter keeps working when a group map is supplied."""
        m = self._matcher()
        candidates = ["US: ESPN", "AU: ESPN 1 HD"]
        m.precompute_normalizations(candidates)
        results = m.match_all_streams(
            "ESPN", candidates, {}, lineup_country="AU",
            candidate_countries={"US: ESPN": "AU"},  # group disagrees with the name
        )
        assert "US: ESPN" not in [r[0] for r in results], (
            "the stream name carries an explicit country and must outrank its group"
        )


class TestBuildingTheMapFromStreamRows:
    """Plugin._stream_countries_by_name turns database rows into the map."""

    def _fn(self):
        import Lineuparr.plugin as plugin_module
        return plugin_module.Plugin._stream_countries_by_name

    def test_a_group_prefix_becomes_the_streams_country(self):
        rows = [{"name": "GO: ESPN", "_group_name": "US| SPORT"}]
        assert self._fn()(rows) == {"GO: ESPN": "US"}

    def test_a_group_with_no_country_contributes_nothing(self):
        rows = [{"name": "Some Channel", "_group_name": "24/7 Streams"}]
        assert self._fn()(rows) == {}

    def test_a_missing_group_contributes_nothing(self):
        rows = [{"name": "Some Channel", "_group_name": None}]
        assert self._fn()(rows) == {}

    def test_one_name_in_two_countries_is_treated_as_unknown(self):
        """Ambiguity must not be resolved by whichever row came first."""
        rows = [
            {"name": "ESPN", "_group_name": "US| SPORT"},
            {"name": "ESPN", "_group_name": "AU| AUSTRALIA VIP"},
        ]
        assert self._fn()(rows) == {}

    def test_one_name_in_two_groups_of_the_same_country_is_kept(self):
        rows = [
            {"name": "ESPN", "_group_name": "US| SPORT"},
            {"name": "ESPN", "_group_name": "US| ENTERTAINMENT"},
        ]
        assert self._fn()(rows) == {"ESPN": "US"}
