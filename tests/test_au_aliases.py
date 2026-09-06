"""Australian alias bridges, written from names measured on a live install.

Seven channels in AU_Foxtel_lineup.json could not reach a stream or a guide
entry that demonstrably exists, because the provider and the guide source spell
the channel differently: they append the word National, or a city, or a feed
number. Measured on 2026-08-15.

These are one to one bridges, which is the alias table's intended use, and they
live in COUNTRY_ALIASES["AU"] so they are merged only for Australian lineups.
They cannot be global: `CNN International` mapping to `CNN` is right in
Australia, where the carried feed IS CNN International, and wrong in the United
States, where CNN is a separate domestic channel.

The alternative considered and rejected was changing name normalization to strip
a trailing "National". Measured against the 4228 channels in the shipped lineup
files, that would damage `YES National` and `Sportsnet New York National` in the
United States lineups, which are genuinely different channels from `YES` and
`Sportsnet New York`.
"""
import json
from pathlib import Path

import pytest

from Lineuparr.aliases import COUNTRY_ALIASES
from Lineuparr.fuzzy_matcher import FuzzyMatcher

LINEUP = Path(__file__).resolve().parent.parent / "Lineuparr" / "AU_Foxtel_lineup.json"

# channel in the lineup -> the real stream or guide name it must reach.
# Every right-hand value was read from the live install, not invented.
MEASURED_TARGETS = {
    "ABC News": "AU: ABC NEWS AUSTRALIA HD",
    "Racing.com": "AU: RACING.COM NATIONAL",
    "SBS Food": "AU: SBS FOOD NATIONAL",
    "SBS World Movies": "AU: SBS WORLD MOVIES NATIONAL",
    "ESPN": "AU: ESPN 1 HD",
    "NITV": "NITV Sydney",
}

# Tried, measured, and removed on the operator's decision on 2026-08-15.
# Bridging "CNN International" to the guide entry "CNN" worked, and it also
# reached the United States stream "GO: CNN", which attached as a backup feed
# carrying different programming. The alias table applies to streams and guide
# entries alike, with no way to scope an entry to one of them.
REJECTED = {"CNN International"}


@pytest.fixture(scope="module")
def au_table():
    assert "AU" in COUNTRY_ALIASES, "COUNTRY_ALIASES has no AU entry"
    return COUNTRY_ALIASES["AU"]


@pytest.fixture(scope="module")
def lineup_channel_names():
    data = json.loads(LINEUP.read_text(encoding="utf-8"))
    return {c["name"] for chans in data["categories"].values() for c in chans}


class TestEveryKeyIsARealChannel:
    """A misspelled key does nothing at all and nothing reports it."""

    def test_each_alias_key_exists_in_the_australian_lineup(self, au_table, lineup_channel_names):
        missing = sorted(k for k in au_table if k not in lineup_channel_names)
        assert not missing, (
            f"these alias keys match no channel in AU_Foxtel_lineup.json and are "
            f"therefore dead: {missing}"
        )

    def test_every_channel_measured_as_broken_is_covered(self, au_table):
        missing = sorted(k for k in MEASURED_TARGETS if k not in au_table)
        assert not missing, f"no alias written for: {missing}"


class TestEachAliasReachesItsMeasuredTarget:
    """The point of the alias is that the matcher scores it as an exact hit."""

    @pytest.mark.parametrize("channel,target", sorted(MEASURED_TARGETS.items()))
    def test_the_alias_matches_the_real_name(self, au_table, channel, target):
        matcher = FuzzyMatcher(match_threshold=95)
        matcher.precompute_normalizations([target])
        results = matcher.match_all_streams(
            channel, [target], {channel: au_table[channel]}, lineup_country="AU"
        )
        assert results, f"{channel!r} still cannot reach {target!r}"
        assert results[0][0] == target


class TestTheseStayInsideAustralia:
    def test_no_australian_alias_key_leaks_into_another_country_table(self, au_table):
        for cc, table in COUNTRY_ALIASES.items():
            if cc == "AU":
                continue
            overlap = sorted(set(au_table) & set(table))
            assert not overlap, f"AU and {cc} both claim: {overlap}"

    def test_espn_is_not_bridged_globally(self):
        """ESPN 1 is the Australian feed name and nobody else's."""
        from Lineuparr.aliases import CHANNEL_ALIASES
        assert "ESPN 1" not in CHANNEL_ALIASES.get("ESPN", []), (
            "ESPN must not reach ESPN 1 outside Australia, where the feed is not "
            "numbered"
        )


class TestTheRejectedBridgeStaysOut:
    """A bridge that was tried, measured and removed must not quietly return."""

    def test_no_alias_exists_for_a_rejected_channel(self, au_table):
        back = sorted(REJECTED & set(au_table))
        assert not back, (
            f"{back} was removed on 2026-08-15 because the alias reached a United "
            f"States stream as well as the intended guide entry. Read the note in "
            f"Lineuparr/aliases.py before adding it again."
        )


class TestShape:
    def test_every_value_is_a_list_of_strings(self, au_table):
        for key, val in au_table.items():
            assert isinstance(val, list), f"{key} does not hold a list"
            assert val, f"{key} holds an empty list, which does nothing"
            for item in val:
                assert isinstance(item, str) and item.strip(), f"{key} holds {item!r}"

    def test_no_alias_repeats_its_own_channel_name(self, au_table):
        for key, val in au_table.items():
            assert key not in val, f"{key} lists itself, which adds nothing"
