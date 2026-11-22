"""The alias-callsign rescue must not cross networks on a shared callsign.

WEVV is CBS on its main channel and FOX on DT2, so a stream named for the FOX
subchannel carries the same callsign as a CBS channel. The rescue adds any
candidate that carries an alias callsign at score 95; when the channel names a
network, a candidate naming a different one is skipped. A channel that names no
network is not checked, so its behaviour does not change.
"""
import pytest

from Lineuparr.fuzzy_matcher import FuzzyMatcher, network_conflicts, networks_named

CBS_CHANNEL = "CBS - IN Evansville (WEVV)"
CBS_ALIASES = {CBS_CHANNEL: ["WEVV"]}


@pytest.fixture
def matcher():
    return FuzzyMatcher(match_threshold=80)


class TestNetworksNamed:
    @pytest.mark.parametrize("name,expected", [
        ("US FOX 44 (WEVV DT2)", {"FOX"}),
        ("US: CBS 44 (WEVV) EVANSVILLE HD", {"CBS"}),
        ("RK: KGO ABC7 BAY AREA", {"ABC"}),
        ("US: FOX2 DETROIT", {"FOX"}),
        ("US: MY 9 WWOR NEW YORK", {"MY"}),
        ("US: MYTV WWOR", {"MY"}),
        ("US: MYNETWORK TV", {"MY"}),
        ("US: CNBC", set()),
        ("US: ABCDEF", set()),
        ("CITY: MNT WWOR NEW YORK", set()),
        ("", set()),
        (None, set()),
    ])
    def test_labels_are_read_as_networks(self, name, expected):
        assert networks_named(name) == expected


class TestNetworkConflicts:
    def test_another_network_on_the_same_callsign_conflicts(self):
        assert network_conflicts(CBS_CHANNEL, "US FOX 44 (WEVV DT2)") is True

    def test_the_channels_own_network_does_not_conflict(self):
        assert network_conflicts(CBS_CHANNEL, "US: CBS 44 (WEVV) EVANSVILLE HD") is False

    def test_a_stream_naming_no_network_does_not_conflict(self):
        assert network_conflicts(CBS_CHANNEL, "US: WEVV EVANSVILLE HD") is False

    def test_a_channel_naming_no_network_is_never_checked(self):
        assert network_conflicts("Evansville Local (WEVV)", "US FOX 44 (WEVV DT2)") is False

    def test_a_channel_naming_two_networks_is_never_checked(self):
        assert network_conflicts("CBS FOX Evansville (WEVV)", "US FOX 44 (WEVV DT2)") is False


class TestAliasCallsignRescue:
    def test_the_fox_subchannel_is_not_rescued_onto_a_cbs_channel(self, matcher):
        results = matcher.alias_match(CBS_CHANNEL, ["US FOX 44 (WEVV DT2)"], CBS_ALIASES)
        assert results == []

    def test_the_cbs_stream_is_still_rescued(self, matcher):
        results = matcher.alias_match(
            CBS_CHANNEL, ["US FOX 44 (WEVV DT2)", "US: CBS 44 (WEVV) EVANSVILLE HD"], CBS_ALIASES)
        assert [r[0] for r in results] == ["US: CBS 44 (WEVV) EVANSVILLE HD"]
        assert results[0][1] == 95
        assert results[0][2] == "alias-callsign"

    def test_a_channel_naming_no_network_keeps_the_old_rescue(self, matcher):
        results = matcher.alias_match("Evansville Local (WEVV)", ["US FOX 44 (WEVV DT2)"],
                                      {"Evansville Local (WEVV)": ["WEVV"]})
        assert [(r[0], r[2]) for r in results] == [("US FOX 44 (WEVV DT2)", "alias-callsign")]

    def test_a_stream_naming_no_network_is_rescued_on_a_cbs_channel(self, matcher):
        results = matcher.alias_match(CBS_CHANNEL, ["US: WEVV EVANSVILLE HD"], CBS_ALIASES)
        assert [r[0] for r in results] == ["US: WEVV EVANSVILLE HD"]


def test_the_full_pipeline_keeps_the_fox_subchannel_off_the_cbs_channel(matcher):
    results = matcher.match_all_streams(
        CBS_CHANNEL, ["US FOX 44 (WEVV DT2)", "US: CBS 44 (WEVV) EVANSVILLE HD"], CBS_ALIASES)
    assert [r[0] for r in results] == ["US: CBS 44 (WEVV) EVANSVILLE HD"]
