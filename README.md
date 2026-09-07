# Dispatcharr Lineuparr Plugin

## Mirror real-world TV provider lineups with automatic stream matching, EPG, and logos

> [!TIP]
> **New to Dispatcharr plugins?** Start with the **[Dispatcharr Plugin Workflow guide](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/)**.
> It explains what each plugin and tool does, where they overlap, and what order to use them in.

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Lineuparr-Plugin)
[![Workflow Guide](https://img.shields.io/badge/%F0%9F%93%96-Workflow_Guide-1F6FEB?style=flat)](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/lineuparr/)
[![Discord](https://img.shields.io/badge/Discord-Discussion-5865F2?logo=discord&logoColor=white)](https://discord.gg/Sp45V5BcxU)
[![Sponsor](https://img.shields.io/badge/Sponsor-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/PiratesIRC)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Dispatcharr-Lineuparr-Plugin?include_prereleases&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Lineuparr-Plugin/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Dispatcharr-Lineuparr-Plugin/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Dispatcharr-Lineuparr-Plugin/releases)
[![Channels Created](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/PiratesIRC/3e4e74f2ef4b1fcadaae7e4aa4dc5f46/raw/lineuparr-channels-created.json)](#what-it-does)

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Dispatcharr-Lineuparr-Plugin)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Dispatcharr-Lineuparr-Plugin)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Dispatcharr-Lineuparr-Plugin)
![License](https://img.shields.io/github/license/PiratesIRC/Dispatcharr-Lineuparr-Plugin)

## Warning: Backup Your Database

Before installing or using this plugin, it is **highly recommended** that you create a backup of your Dispatcharr database. This plugin creates and modifies channel groups, channels, and stream assignments, and unless you turn on **Preserve Existing Streams** it also replaces a channel's whole stream list and deletes channels that end a run with no streams.

**[Click here for instructions on how to back up your database.](https://dispatcharr.github.io/Dispatcharr-Docs/troubleshooting/?h=backup#how-can-i-make-a-backup-of-the-database)**

## What it does

You pick a real provider lineup, such as Sky TV or DIRECTV Premier, and the plugin builds those channel groups and channels in Dispatcharr and attaches your own streams to them by name.

- **Builds the lineup.** Channel groups and channels that mirror the provider's package, keeping the provider's channel numbers.
- **Matches your streams to it.** A four stage pipeline: alias, exact, substring, then fuzzy token sort, with US broadcast callsign anchoring and length scaled thresholds to keep false positives down. Four sensitivity presets from Relaxed to Exact.
- **Keeps reviewed false positives out.** Optional per-channel `excluded_aliases` block a known wrong stream before any positive matching stage without suppressing it from other channels.
- **Rejects streams from the wrong country.** A lineup keeps only same country or untagged streams. Detection covers the tag formats real providers use, including parenthesized, colon separated, box bar separated, bare space, and country glued to a quality tag. When a stream name carries no country at all, the provider group it came from is read instead, which is what catches a provider that labels streams by platform rather than by country.
- **Knows about regional variants**, so an East, West or Pacific channel reaches the matching regional stream.
- **Assigns EPG data and logos.** Programme guides from any configured EPG source, and logos from EPG icons, the Logo Manager, or the [tv-logos](https://github.com/tv-logo/tv-logos) repository.
- **Orders streams by quality**, 4K before UHD before FHD before HD before SD, using the name or [IPTV Checker](https://github.com/PiratesIRC/Dispatcharr-IPTV-Checker-Plugin) metadata.
- **Previews before it commits.** A dry run writes a CSV of what would match and how confidently, so you can read it first.
- **Reports what it did.** A shareable HTML page and CSV per run, optionally emailed through the [Newsflasharr](https://github.com/PiratesIRC/Dispatcharr-Newsflasharr-Plugin) plugin.
- **Replaces by default, appends on request.** A stream match normally replaces each channel's whole stream list and then deletes channels left with no streams. Turn on **Preserve Existing Streams** and it appends instead, skipping duplicates and deleting nothing, so a second M3U source can be layered on safely.
- **Colours its buttons by consequence.** Red can remove a stream or a channel and always asks first, orange writes but removes nothing, blue only reads and reports.
- **Runs inside Dispatcharr** with direct database access, so no API credentials are needed.

The **Channels Created** badge above is this installation's own running total, recorded by the plugin each time Sync Channels Only or Full Sync finishes. It counts creations performed rather than channels that still exist, so a channel later removed by the unmatched-channel cleanup does not subtract from it and the number never falls. It is a floor: it started when the counter shipped, and earlier runs were not recorded because nothing in the database distinguishes a channel this plugin made from one made by hand.

Over 200 built-in channel aliases, plus your own in JSON. Every setting and action is covered in the [user guide](docs/USER-GUIDE.md).

## Included Lineups

| File | Provider | Country | Channels |
|------|----------|---------|----------|
| `US_DirecTV-Premier_lineup.json` | DIRECTV Premier | US | ~350 |
| `US_DISH-Top250_lineup.json` | DISH Top 250 | US | ~215 |
| `US_Verizon-FIOS_lineup.json` | Verizon FiOS | US | ~200 |
| `US_Verizon-FIOS-All-11743_lineup.json` | Verizon FiOS, every channel available at ZIP 11743 | US | ~508 |
| `US_Optimum_lineup.json` | Optimum (Southern Westchester) | US | ~340 |
| `US_Spectrum-Tampa-Bay-All_lineup.json` | Spectrum Tampa Bay, all channels | US | ~410 |
| `US_Spectrum-Tampa-Bay-No-Spanish_lineup.json` | Spectrum Tampa Bay, Spanish channels excluded | US | ~320 |
| `US_Spectrum-Tampa-Bay-Spanish-Only_lineup.json` | Spectrum Tampa Bay, Spanish channels only | US | ~85 |
| `US_Combined_lineup.json` | US Combined (DIRECTV + DISH + Verizon) | US | ~465 |
| `UK_Freeview_lineup.json` | Freeview | UK | ~160 |
| `UK_SkyTV_lineup.json` | Sky TV | UK | ~175 |
| `UK_SkyTV_ENG_full_lineup.json` | Sky TV (Full LineUp) | UK | ~315 |
| `UK_SkyTV_ENG_simple_lineup.json` | Sky TV (Simple LineUp) | UK | ~295 |
| `UK_Combined_lineup.json` | UK Combined (Freeview + Sky TV Full) | UK | ~395 |
| `ES_Movistar_lineup.json` | Movistar+ | ES | ~170 |
| `FR_CanalPlus_lineup.json` | Canal+ | FR | ~275 |
| `FR_CanalPlus_TNT_lineup.json` | Canal+ (with TNT) | FR | ~275 |
| `AU_Foxtel_lineup.json` | Foxtel Platinum Plus | AU | ~140 |
| `CA_Telus-Optik_lineup.json` | Telus Optik | CA | ~130 |
| `NL_ODIDO_lineup.json` | ODIDO | NL | ~155 |

These are community-compiled channel lists based on publicly available provider lineup information. You can write your own: see the [lineup file format](docs/LINEUP-FORMAT.md).

The Verizon FiOS ZIP 11743 lineup is the one to look at if you are writing your own: 434 of its channels carry their own alias lists, which is the per-channel `aliases` array described in that format guide. The three Spectrum Tampa Bay lineups use the same array on every channel.

## Requirements

- Dispatcharr v0.20.0 or newer
- At least one M3U source configured with streams
- EPG sources configured, optional, for EPG matching

No API credentials are needed. The plugin runs inside Dispatcharr with direct database access.

## Install

1. Log in to Dispatcharr's web interface.
2. Go to **Plugins**.
3. Click **Import Plugin** and upload `Lineuparr.zip`.
4. Enable the plugin.

Then pick a lineup file and an M3U source, save, and run **Validate Settings** followed by **Preview Stream Match**. The [user guide](docs/USER-GUIDE.md#the-short-version) walks through it.

### Updating

Remove the old plugin from the **Plugins** page, restart Dispatcharr (`docker restart dispatcharr`), then import the new zip and enable it. Your settings are kept, but check them after upgrading.

## Documentation

| Page | What is in it |
|---|---|
| **[User guide](docs/USER-GUIDE.md)** | Every setting, every action, match sensitivity, country matching, custom aliases, reports and emailing, file locations, troubleshooting, and how the matching pipeline works. |
| **[Lineup file format](docs/LINEUP-FORMAT.md)** | Writing your own lineup: the JSON shape, the filename rule, per-channel aliases, and marking foreign channels. |

## Contributing

### Reporting issues

1. Include your Dispatcharr version.
2. Provide relevant container logs: `docker logs dispatcharr | grep "Lineuparr"`.
3. Run **Preview Stream Match** and attach the CSV export. This is the most useful thing you can share. **Check that no stream URLs are in the CSV before sharing it**, because they can carry your provider credentials.
4. Say which **Match Sensitivity** setting and lineup file you used.

### Submitting lineup databases

Community-contributed lineups are welcome. The JSON shape and the filename rule are in the [lineup file format](docs/LINEUP-FORMAT.md). Open a pull request with the file, or an issue giving the provider name, country, channel list and where the listing came from.

If you would like a provider added but cannot build the file yourself, open a **Lineup Request** issue with the provider name, country, and a link to their channel listing page.

### Bumping the plugin version

Version format is `1.26.{DDD}{HHMM}`, a three digit day of year plus a four digit UTC time. Both `Lineuparr/plugin.json` and `PluginConfig.PLUGIN_VERSION` in `Lineuparr/plugin.py` must stay in step, so use the helper rather than editing them by hand:

```bash
python scripts/bump_version.py              # auto from the current UTC time
python scripts/bump_version.py --set 1.26.1031200 # explicit
```

---

## Disclaimer

**Lineuparr provides no television content of any kind.** It supplies no channels, no playlists, no streams, no electronic programme guide data and no provider accounts, and it contains no list of where to obtain any of those. The lineup files it ships are lists of channel names and channel numbers, compiled from publicly available provider channel listings. They contain no stream addresses, no credentials and no provider account details.

The plugin never contacts a media provider. It never opens, fetches, decodes, records, restreams or redistributes any stream. It reads the stream names, EPG entries and channels that Dispatcharr already holds for the sources **you** configured, matches them by name against a lineup you chose, and writes the results back into Dispatcharr. The only network requests it makes on its own are to the [tv-logos](https://github.com/tv-logo/tv-logos) repository on GitHub, when you ask it to assign channel logos.

**You are responsible for what you connect Dispatcharr to.** Whether a particular provider, subscription, playlist or stream is lawful for you to use depends on your agreement with that provider and on the law where you live. Use only sources you are authorised to use. Nothing in this project is intended to enable, encourage or assist access to content you have no right to access.

All product names, channel names, trademarks and registered trademarks mentioned in this project or appearing in its lineup files are the property of their respective owners. This project is an independent, community-built plugin. It is not affiliated with, endorsed by, or sponsored by any television network, broadcaster, streaming service or IPTV provider, and it is not affiliated with the Dispatcharr project beyond being a plugin written for it.

The software is provided as-is, without warranty of any kind, as set out in the licence. This section describes the design of the software and the author's intent. It is not legal advice. If you need to know whether your own use is lawful, ask someone qualified in your jurisdiction.

## License

MIT. See [`LICENSE`](LICENSE).
