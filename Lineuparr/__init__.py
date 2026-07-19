from .plugin import Plugin as _BasePlugin


class Plugin(_BasePlugin):
    """Lineuparr plugin with support for aliases embedded in lineup JSON files."""

    def _build_alias_map(self, settings, logger):
        """Merge embedded lineup aliases into the existing alias map.

        Lineup entries may define ``aliases`` as either a string or a list of
        strings. Existing built-in, country, and custom aliases remain intact.
        """
        alias_map = super()._build_alias_map(settings, logger)

        try:
            lineup = self._load_lineup(settings, logger)
        except Exception as exc:
            logger.warning(f"[Lineuparr] Could not load embedded lineup aliases: {exc}")
            return alias_map

        if not isinstance(lineup, dict) or lineup.get("status") == "error":
            return alias_map

        merged = 0
        for channels in lineup.get("categories", {}).values():
            if not isinstance(channels, list):
                continue

            for channel in channels:
                if not isinstance(channel, dict):
                    continue

                name = channel.get("name")
                raw_aliases = channel.get("aliases", [])
                if not name or not raw_aliases:
                    continue

                if isinstance(raw_aliases, str):
                    raw_aliases = [raw_aliases]
                elif not isinstance(raw_aliases, list):
                    logger.warning(
                        f"[Lineuparr] Lineup aliases for '{name}' must be a string or list, "
                        f"got {type(raw_aliases).__name__} - ignored"
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
