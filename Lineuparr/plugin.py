"""Dispatcharr loader entrypoint for Lineuparr.

The upstream implementation lives in plugin_base.py. This wrapper keeps
Dispatcharr's direct plugin.py loading path while adding aliases embedded in
lineup JSON channel entries.
"""

import re

from . import plugin_base as _plugin_base
from .plugin_base import *
from .plugin_base import Plugin as _BasePlugin
from .fuzzy_matcher import FuzzyMatcher as _BaseFuzzyMatcher


class _EmbeddedAliasFuzzyMatcher(_BaseFuzzyMatcher):
    """Fuzzy matcher with safe whole-token matching for US callsign aliases."""

    @staticmethod
    def _callsign_alias_base(alias):
        """Return a base US broadcast callsign or None.

        Accepts forms such as WWOR, WWOR-TV and WWORDT, but deliberately does
        not treat generic short aliases such as HBO, CNN or MAX as callsigns.
        """
        compact = re.sub(r"[^A-Za-z0-9]", "", alias or "").upper()
        compact = re.sub(r"(?:DT|TV)$", "", compact)
        if re.fullmatch(r"[WK][A-Z]{2,5}", compact):
            return compact.lower()
        return None

    def alias_match(self, lineup_name, candidate_names, alias_map, user_ignored_tags=None):
        matches = super().alias_match(
            lineup_name,
            candidate_names,
            alias_map,
            user_ignored_tags,
        )

        aliases = alias_map.get(lineup_name, []) if alias_map else []
        if isinstance(aliases, str):
            aliases = [aliases]

        callsigns = {
            base
            for alias in aliases
            for base in [self._callsign_alias_base(alias)]
            if base
        }
        if not callsigns:
            return matches

        existing = {name for name, _, _ in matches}
        for candidate in candidate_names:
            if candidate in existing or self._is_group_header(candidate):
                continue

            normalized = self.normalize_name(candidate, user_ignored_tags or [])
            if not normalized:
                continue

            # Hyphens, parentheses and provider separators normalize to token
            # boundaries, so this matches WWOR in MNT-WWOR and (WWOR), while
            # refusing partial words such as WWORLD.
            tokens = set(re.findall(r"[a-z0-9]+", normalized.lower()))
            if callsigns & tokens:
                matches.append((candidate, 100, "alias-callsign-token"))

        matches.sort(key=lambda item: item[1], reverse=True)
        return matches


# plugin_base creates the matcher through its module-global FuzzyMatcher name.
# Replace that reference before the Plugin class is instantiated.
_plugin_base.FuzzyMatcher = _EmbeddedAliasFuzzyMatcher


class Plugin(_BasePlugin):
    """Lineuparr with aliases embedded in lineup JSON entries."""

    def _build_alias_map(self, settings, logger):
        alias_map = super()._build_alias_map(settings, logger)

        try:
            lineup = self._load_lineup(settings, logger)
        except Exception as exc:
            logger.warning(f"[Lineuparr] Could not load embedded lineup aliases: {exc}")
            return alias_map

        if not isinstance(lineup, dict) or lineup.get("status") == "error":
            return alias_map

        merged = 0
        categories = lineup.get("categories", {})
        if not isinstance(categories, dict):
            return alias_map

        for channels in categories.values():
            if not isinstance(channels, list):
                continue

            for channel in channels:
                if not isinstance(channel, dict):
                    continue

                name = str(channel.get("name") or "").strip()
                raw_aliases = channel.get("aliases")
                if not name or not raw_aliases:
                    continue

                if isinstance(raw_aliases, str):
                    raw_aliases = [raw_aliases]
                elif not isinstance(raw_aliases, list):
                    logger.warning(
                        f"[Lineuparr] Embedded aliases for '{name}' must be a "
                        f"string or list, got {type(raw_aliases).__name__} - ignored"
                    )
                    continue

                clean = [
                    alias.strip()
                    for alias in raw_aliases
                    if isinstance(alias, str) and alias.strip()
                ]
                if not clean:
                    continue

                alias_map[name] = list(dict.fromkeys(alias_map.get(name, []) + clean))
                merged += 1

        if merged:
            logger.info(
                f"[Lineuparr] Merged embedded aliases from {merged} lineup "
                f"{'channel' if merged == 1 else 'channels'}"
            )

        return alias_map


__all__ = ["Plugin"]
