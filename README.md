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

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Dispatcharr-Lineuparr-Plugin)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Dispatcharr-Lineuparr-Plugin)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Dispatcharr-Lineuparr-Plugin)
![License](https://img.shields.io/github/license/PiratesIRC/Dispatcharr-Lineuparr-Plugin)

## Warning: Backup Your Database
Before installing or using this plugin, it is **highly recommended** that you create a backup of your Dispatcharr database. This plugin creates and modifies channel groups, channels, and stream assignments.

**[Click here for instructions on how to back up your database.](https://dispatcharr.github.io/Dispatcharr-Docs/troubleshooting/?h=backup#how-can-i-make-a-backup-of-the-database)**

## Features

- **Provider Lineup Sync:** Create channel groups and channels that mirror real TV provider packages
- **Fuzzy Stream Matching:** 4-stage matching pipeline (alias, exact, substring, fuzzy token-sort) with length-scaled thresholds and US broadcast-callsign anchoring to minimize false positives
- **Non-Destructive Add Mode:** Optionally append matched streams without deleting existing streams or removing unmatched channels -- safely add a second M3U source
- **Single-Channel Targeting:** Optionally scope stream, EPG, and logo matching to one named lineup channel instead of the whole lineup
- **EPG Assignment:** Fuzzy-match EPG data to channels and assign program guides from any configured EPG source
- **Logo Assignment:** Auto-assign channel logos from EPG icons, Logo Manager, or the [tv-logos](https://github.com/tv-logo/tv-logos) GitHub repository
- **Custom Logo Repository:** Match all existing Dispatcharr channels against an optional public GitHub logo directory without running a lineup sync
- **Quality Ordering:** Automatically sort matched streams by quality (4K > UHD > FHD > HD > SD) using name-based detection or [IPTV Checker](https://github.com/PiratesIRC/Dispatcharr-IPTV-Checker-Plugin) metadata
- **Channel Number Preservation:** Lineup channel numbers are stored and used for tiebreaking during matching
- **East/West/Pacific Filtering:** Regional channel variants are matched to the correct regional streams
- **Country-Aware Matching:** Streams whose name prefix indicates a different country than the active lineup are rejected automatically. Detection covers many real provider formats: parenthesized (`(IN) Bloomberg TV`, `(PLUTO Brazil) MTV`), separator (`UK: Discovery Channel`, `TR: 24 TV`), Unicode box-bar separator (`UK┃Discovery Channel`, `US│ESPN` using `┃` U+2503 or `│` U+2502), bare space (`US beIN SPORTS`), and country glued to a quality tag (`UKHD: Sky Sports`). Matching is **strict**: a lineup keeps only same-country or untagged streams. The sole cross-border exception is specific US Spanish-language networks (Univision, Telemundo, TUDN, etc.) that are genuinely the same feed tagged US or MX. FAST platform tags (Roku/Tubi/Pluto/Xumo/Plex), provider/category prefixes, and decorative Unicode quality badges are stripped for scoring but never treated as a country.
- **Built-in Alias Table:** 200+ channel alias mappings for common IPTV naming variations (CNN US, Fox News Channel, ESPN 2, etc.)
- **Custom Aliases:** User-configurable JSON alias overrides merged on top of built-in aliases
- **Match Sensitivity Modes:** Relaxed, Normal, Strict, and Exact sensitivity presets
- **Rate Limiting:** Configurable throttling between operations (None, Low, Medium, High) to reduce load on Dispatcharr
- **CSV Preview/Export:** Dry-run stream matching with confidence scores exported to CSV for review before committing
- **Channel Profile Support:** Automatically enable synced channels in selected channel profiles
- **Real-Time Progress:** Live ETA and progress, checkable any time via the **Show Status** action -- no need to watch toast notifications or container logs
- **Direct ORM Integration:** Runs inside Dispatcharr with direct database access -- no API credentials needed

## Included Lineups

| File | Provider | Country | Channels |
|------|----------|---------|----------|
| `US_DirecTV-Premier_lineup.json` | DIRECTV Premier | US | ~350 |
| `US_DISH-Top250_lineup.json` | DISH Top 250 | US | ~215 |
| `US_Verizon-FIOS_lineup.json` | Verizon FiOS | US | ~200 |
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

These are community-compiled channel lists based on publicly available provider lineup information. Custom lineup files can be created following the same JSON format and placed in the plugin directory.

## Requirements

### Dispatcharr Setup
- Active Dispatcharr installation (v0.20.0+)
- At least one M3U source configured with streams
- EPG sources configured (optional, for EPG matching)

No API credentials are needed -- the plugin runs inside Dispatcharr with direct database access.

## Installation

1. Log in to Dispatcharr's web UI
2. Navigate to **Plugins**
3. Click **Import Plugin** and upload the `Lineuparr.zip` file
4. Enable the plugin after installation

### Updating the Plugin

1. **Remove Old Plugin**
   - Navigate to **Plugins** in Dispatcharr
   - Click the trash icon next to the old plugin
   - Confirm deletion

2. **Restart Dispatcharr**
   - Log out of Dispatcharr
   - Restart the Docker container:
     ```bash
     docker restart dispatcharr
     ```

3. **Install Updated Plugin**
   - Log back into Dispatcharr
   - Navigate to **Plugins**
   - Click **Import Plugin** and upload the new plugin zip file
   - Enable the plugin after installation

4. **Verify Installation**
   - Check that the plugin appears in the plugin list
   - Reconfigure your settings if needed

## Documentation

| Page | What is in it |
|---|---|
| **[User guide](docs/USER-GUIDE.md)** | Every setting, every action, match sensitivity, country matching, custom aliases, troubleshooting, and how the matching pipeline works. |
| **[Lineup file format](docs/LINEUP-FORMAT.md)** | Writing your own lineup: the JSON shape, the filename rule, per-channel aliases, and marking foreign channels. |

## Usage in one minute

1. Pick a **Lineup File** and an **M3U Source**, then save.
2. Run **Validate Settings** to check the configuration and see the lineup summary.
3. Run **Preview Stream Match**. Nothing is changed; a CSV lands in `/data/exports/` showing what would match and how confidently.
4. If the preview looks right, run **Full Sync**.

Full detail for each step, and for the other eight actions, is in the [user guide](docs/USER-GUIDE.md).

## File Locations

- **CSV Exports:** `/data/exports/lineuparr_*.csv` (persist across container restarts)
- **Plugin Directory:** `/data/plugins/lineuparr/` (inside the Dispatcharr Docker data volume)
- **Logs:** `docker logs dispatcharr | grep "Lineuparr"`

## Contributing

### Reporting Issues
When reporting issues:
1. Include Dispatcharr version information
2. Provide relevant container logs (`docker logs dispatcharr | grep "Lineuparr"`)
3. Run **Preview Stream Match** and attach the CSV export -- this is the most helpful thing you can share. **Make sure no stream URLs are included in the CSV before sharing.**
4. Note your **Match Sensitivity** setting and lineup file used

### Bumping the Plugin Version
Version format: `1.26.{DDD}{HHMM}` (3-digit day-of-year + 4-digit UTC time). Both `Lineuparr/plugin.json` and `PluginConfig.PLUGIN_VERSION` in `Lineuparr/plugin.py` must stay in sync. Use the helper script to update both at once:

```bash
python3 bump_version.py              # auto from current UTC time
python3 bump_version.py 1.26.1031200 # explicit
```

### Submitting Lineup Databases
Community-contributed lineups are welcome. The JSON shape and the filename rule are in the [lineup file format](docs/LINEUP-FORMAT.md); open a pull request with the file, or an issue with the provider name, country, channel list and where the listing came from.

If you would like a provider added but cannot build the file yourself, open a **Lineup Request** issue with the provider name, country, and a link to their channel listing page.

---

## Disclaimer

**Lineuparr provides no television content of any kind.** It supplies no channels, no playlists, no streams, no electronic programme guide data and no provider accounts, and it contains no list of where to obtain any of those. The lineup files it ships are lists of channel names and channel numbers, compiled from publicly available provider channel listings. They contain no stream addresses, no credentials and no provider account details.

The plugin never contacts a media provider. It never opens, fetches, decodes, records, restreams or redistributes any stream. It reads the stream names, EPG entries and channels that Dispatcharr already holds for the sources **you** configured, matches them by name against a lineup you chose, and writes the results back into Dispatcharr. The only network requests it makes on its own are to the [tv-logos](https://github.com/tv-logo/tv-logos) repository on GitHub, when you ask it to assign channel logos.

**You are responsible for what you connect Dispatcharr to.** Whether a particular provider, subscription, playlist or stream is lawful for you to use depends on your agreement with that provider and on the law where you live. Use only sources you are authorised to use. Nothing in this project is intended to enable, encourage or assist access to content you have no right to access.

All product names, channel names, trademarks and registered trademarks mentioned in this project or appearing in its lineup files are the property of their respective owners. This project is an independent, community-built plugin. It is not affiliated with, endorsed by, or sponsored by any television network, broadcaster, streaming service or IPTV provider, and it is not affiliated with the Dispatcharr project beyond being a plugin written for it.

The software is provided as-is, without warranty of any kind, as set out in the licence. This section describes the design of the software and the author's intent. It is not legal advice. If you need to know whether your own use is lawful, ask someone qualified in your jurisdiction.

## License

MIT. See [`LICENSE`](LICENSE).
