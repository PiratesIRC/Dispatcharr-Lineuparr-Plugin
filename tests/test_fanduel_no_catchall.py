"""
Regression: regional FanDuel Sports channels (Cincinnati, Detroit, Florida,
North, Ohio, Oklahoma, SoCal, South, Southeast, Sun, Wisconsin) had alias
entries forcing them to match the generic "FanDuel TV Extra" EPG when no
regional EPG was available. That is worse than NO MATCH because it gives
every regional sports channel the same wrong EPG schedule.

The aliases were removed. Regions with a real regional EPG still match via
direct name comparison; regions without one correctly return NO MATCH.
"""
from Lineuparr.aliases import CHANNEL_ALIASES


# Regional names that previously aliased to "FanDuel TV Extra".
_REMOVED_REGIONAL_ALIASES = [
    "FanDuel Sports Cincinnati",
    "FanDuel Sports Detroit",
    "FanDuel Sports Florida",
    "FanDuel Sports Midwest",
    "FanDuel Sports North",
    "FanDuel Sports Ohio",
    "FanDuel Sports Oklahoma",
    "FanDuel Sports SoCal",
    "FanDuel Sports South",
    "FanDuel Sports Southeast",
    "FanDuel Sports Southwest",
    "FanDuel Sports Sun",
    "FanDuel Sports West",
    "FanDuel Sports Wisconsin",
]


def test_no_fanduel_regional_aliases_to_tv_extra():
    for name in _REMOVED_REGIONAL_ALIASES:
        aliases = CHANNEL_ALIASES.get(name, [])
        assert "FanDuel TV Extra" not in aliases, (
            f"{name!r} still has a catch-all alias to 'FanDuel TV Extra' - "
            "this gives every regional FanDuel channel the same wrong EPG."
        )


def test_fanduel_tv_main_alias_preserved():
    """The legitimate FanDuel TV alias must remain."""
    assert "FanDuel TV" in CHANNEL_ALIASES
    aliases = CHANNEL_ALIASES["FanDuel TV"]
    assert "TVG" in aliases  # TVG was the legacy name for FanDuel TV
