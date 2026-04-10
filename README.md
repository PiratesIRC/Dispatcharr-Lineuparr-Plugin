# Dispatcharr Lineuparr Plugin

## Mirror real-world TV provider lineups with automatic stream matching, EPG, and logos

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Dispatcharr-Lineuparr-Plugin)

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
- **Fuzzy Stream Matching:** 4-stage matching pipeline (alias, exact, substring, fuzzy token-sort) with length-scaled thresholds to minimize false positives
- **EPG Assignment:** Fuzzy-match EPG data to channels and assign program guides from any configured EPG source
- **Logo Assignment:** Auto-assign channel logos from EPG icons, Logo Manager, or the [tv-logos](https://github.com/tv-logo/tv-logos) GitHub repository
- **Quality Ordering:** Automatically sort matched streams by quality (4K > UHD > FHD > HD > SD) using name-based detection or [IPTV Checker](https://github.com/PiratesIRC/Dispatcharr-IPTV-Checker-Plugin) metadata
- **Channel Number Preservation:** Lineup channel numbers are stored and used for tiebreaking during matching
- **East/West/Pacific Filtering:** Regional channel variants are matched to the correct regional streams
- **Built-in Alias Table:** 200+ channel alias mappings for common IPTV naming variations (CNN US, Fox News Channel, ESPN 2, etc.)
- **Custom Aliases:** User-configurable JSON alias overrides merged on top of built-in aliases
- **Match Sensitivity Modes:** Relaxed, Normal, Strict, and Exact sensitivity presets
- **Rate Limiting:** Configurable throttling between operations (None, Low, Medium, High) to reduce load on Dispatcharr
- **CSV Preview/Export:** Dry-run stream matching with confidence scores exported to CSV for review before committing
- **Channel Profile Support:** Automatically enable synced channels in selected channel profiles
- **Real-Time Progress:** Live ETA calculations with WebSocket progress notifications
- **Direct ORM Integration:** Runs inside Dispatcharr with direct database access -- no API credentials needed

## Included Lineups

| File | Provider | Country | Channels |
|------|----------|---------|----------|
| `US_DirecTV-Premier_lineup.json` | DIRECTV Premier | US | ~300 |
| `US_DISH-Top250_lineup.json` | DISH Top 250 | US | ~210 |
| `US_Verizon-FIOS_lineup.json` | Verizon FiOS | US | ~190 |
| `UK_SkyTV_lineup.json` | Sky TV | UK | ~160 |
| `AU_Foxtel_lineup.json` | Foxtel Platinum Plus | AU | ~140 |
| `CA_Telus-Optik_lineup.json` | Telus Optik | CA | ~140 |
| `NL_ODIDO_lineup.json` | ODIDO | NL | ~140 |

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

## Settings Reference

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| Lineup File | select | `US_DirecTV-Premier_lineup.json` | Provider lineup to use (auto-populated from available JSON files) |
| M3U Source | select | *(empty)* | M3U source to match streams from |
| Channel Profile | select | *(empty)* | Channel profile to enable matched channels in |
| Channel Group Prefix | string | *(empty)* | Prefix added to created channel group names |
| Category Detail | select | `Normal` | Controls how lineup categories are grouped (None, Refined, Simple, Normal) |
| Match Sensitivity | select | `Normal` | Matching strictness: Relaxed, Normal, Strict, or Exact |
| Channel Numbering | select | `Use Channel Database Numbers` | How to assign channel numbers: Database, Auto-Assign Next, Auto-Assign After Highest, or Specific |
| Starting Channel Number | string | *(empty)* | Starting number for "Use Specific Number" mode |
| Order Matched Streams by Quality | boolean | `true` | Sort matched streams by detected quality (4K > HD > SD) |
| Rate Limiting | select | `None` | Throttle between operations: None, Low, Medium, or High delay |
| Custom Channel Aliases (JSON) | string | *(empty)* | JSON object of custom alias overrides (see [Custom Aliases](#custom-aliases)) |
| EPG Sources | select | `All EPG sources` | EPG source(s) to match against |

### Match Sensitivity Levels

| Level | Best For |
|-------|----------|
| Relaxed | Maximum coverage -- cast a wide net, then review the CSV for false positives |
| Normal | General use -- good accuracy with reasonable coverage |
| Strict | High confidence matches only -- fewer results, fewer mistakes |
| Exact | Near-exact matches only -- minimal false positives, may miss valid matches |

## Usage Guide

### Step-by-Step Workflow

1. **Configure Settings**
   - Select your **Lineup File** (e.g., `US_DirecTV-Premier_lineup.json`)
   - Select your **M3U Source**
   - Optionally set a **Channel Group Prefix** and **Channel Profile**
   - Choose your **Match Sensitivity** level
   - Click **Save Settings**

2. **Validate Settings** *(Recommended)*
   - Click **Run** on **Validate Settings**
   - Verifies lineup file, M3U source, and shows lineup summary with channel counts per category

3. **Preview Stream Matches** *(Recommended)*
   - Click **Run** on **Preview Stream Match**
   - Dry-run that shows what streams would be matched to which channels
   - Exports results to CSV with confidence scores for review
   - No changes are made to your channels -- safe to run at any time

4. **Full Sync** *(One-Click Setup)*
   - Click **Run** on **Full Sync**
   - Creates channel groups from lineup categories
   - Creates channels with correct numbering
   - Fuzzy-matches streams to channels
   - Assigns EPG data
   - Assigns logos
   - Enables channels in selected profile
   - **Removes channels that had no streams matched** (see [Unmatched Channel Cleanup](#unmatched-channel-cleanup))

### Individual Actions

For more control, run individual steps instead of Full Sync:

- **Sync Channels Only** -- Create/update groups and channels from lineup (no stream matching)
- **Apply Stream Match Only** -- Attach matched streams to existing channels with quality ordering
- **Apply EPG Match** -- Fuzzy-match EPG data to channels and assign program guides
- **Assign Logos** -- Auto-assign channel logos from EPG icons, Logo Manager, or tv-logos GitHub repo
- **Re-sort Streams by Quality** -- Re-order already-attached streams using latest quality data (see [IPTV Checker Integration](#iptv-checker-integration))
- **Clear CSV Exports** -- Delete all Lineuparr CSV export files

### Unmatched Channel Cleanup

After stream matching, **Full Sync** and **Apply Stream Match Only** will automatically delete any channels in Lineuparr-managed groups that have zero streams attached. This keeps your channel list clean by removing lineup entries that don't exist in your M3U source.

Only channels in groups created by Lineuparr are affected -- your other channels are never touched. If you want to see what would be removed before committing, run **Preview Stream Match** first to review the unmatched channels in the CSV export.

### IPTV Checker Integration

If you use the [IPTV Checker plugin](https://github.com/PiratesIRC/Dispatcharr-IPTV-Checker-Plugin), you can improve stream quality ordering:

1. Run a Full Sync or Apply Stream Match to attach streams to channels
2. Run an IPTV Checker scan on your Lineuparr channel groups
3. Run **Re-sort Streams by Quality** to re-order streams using actual resolution and bitrate data from the scan instead of name-based quality detection

## Custom Lineup Files

Create your own lineup JSON files following this format:

```json
{
  "package": "Provider Name",
  "date": "2026-01-01",
  "description": "Description of the lineup",
  "source": "Where the lineup data came from",
  "categories": {
    "News": [
      { "name": "CNN", "number": 202 },
      { "name": "Fox News", "number": 360 }
    ],
    "Sports": [
      { "name": "ESPN", "number": 206 }
    ]
  }
}
```

**Important:** The filename must follow the format `{CC}_{Provider}_lineup.json` where `{CC}` is a 2-letter ISO country code (e.g., `US`, `UK`, `CA`, `NL`). The country code is used for logo matching against the [tv-logos](https://github.com/tv-logo/tv-logos) repository. For example: `US_MyProvider_lineup.json`, `UK_Freeview_lineup.json`.

Place the file in the plugin directory and it will appear in the **Lineup File** dropdown.

## Custom Aliases

Override or extend the built-in alias table using the **Custom Channel Aliases (JSON)** setting. Paste a JSON object where keys are the **exact lineup channel name** (as it appears in the lineup JSON) and values are arrays of alternate names your IPTV provider uses for that channel:

```json
{
  "FOX News Channel": ["FOX NEWS HD", "FoxNews", "Fox News USA"],
  "HISTORY Channel, The": ["HISTORY", "History Channel HD", "History US"],
  "My Local Station": ["WABC", "WABC-TV", "ABC 7 New York"]
}
```

**How to find the right key:** Open the lineup JSON file and look for the exact `"name"` value of the channel you want to add aliases for. For example, if the lineup has `"name": "HISTORY Channel, The"`, use that exact string as the key.

**How to find what to alias to:** Run **Preview Stream Match** and check the CSV export for unmatched channels. The stream names shown in your M3U source are what you should add as alias values.

Custom aliases are merged on top of the 200+ built-in aliases, so you only need to specify additions or overrides for channels that aren't matching automatically.

## Troubleshooting

### First Step: Restart Container
**For any plugin issues, try refreshing your browser (F5) and then restarting the Dispatcharr container:**
```bash
docker restart dispatcharr
```

### Common Issues

**"Plugin not found" Errors:**
- Refresh browser page (F5)
- Restart Dispatcharr container

**Low Match Rate:**
- Try **Relaxed** sensitivity for initial testing
- Use **Preview Stream Match** to review what's matching and what's not
- Add **Custom Aliases** for channels with unusual IPTV naming
- Check that your M3U source actually contains the channels you expect

**Channels Created But No Streams Attached:**
- Verify your M3U source is selected in settings
- Run **Preview Stream Match** to check for matching issues
- Stream names may differ significantly from lineup names -- add custom aliases

**EPG Not Assigned:**
- Ensure EPG sources are configured in Dispatcharr
- Run **Apply EPG Match** separately to see detailed matching logs
- Check container logs: `docker logs dispatcharr | grep "Lineuparr"`

**Progress Not Updating:**
- Operations run in background and continue even if browser times out
- Check container logs for actual processing status

<details>
<summary><strong>How Matching Works (Technical Details)</strong></summary>

Lineuparr uses a 4-stage matching pipeline for each lineup channel:

1. **Alias Match** -- Checks the built-in alias table and custom aliases for known name variants (e.g., "FOX News Channel" matches "Fox News", "FNC")
2. **Exact Match** -- Normalized name comparison with space/punctuation stripping
3. **Substring Match** -- One name contained within the other with length ratio check
4. **Fuzzy Token-Sort** -- Levenshtein distance on sorted, cleaned tokens

All stages use:
- **Length-scaled thresholds** -- Shorter names require higher similarity to prevent false positives
- **Token overlap guards** -- At least one distinctive token must be shared between names
- **Regional filtering** -- East/West/Pacific variants are matched to correct regional streams
- **Channel number boost** -- 3+ digit channel numbers in stream names provide tiebreaker points (only active in "Use Channel Database Numbers" mode)

</details>

## File Locations

- **CSV Exports:** `/data/exports/lineuparr_*.csv` (persist across container restarts)
- **Plugin Directory:** `/app/plugins/Lineuparr/`
- **Logs:** `docker logs dispatcharr | grep "Lineuparr"`

## Contributing

### Reporting Issues
When reporting issues:
1. Include Dispatcharr version information
2. Provide relevant container logs (`docker logs dispatcharr | grep "Lineuparr"`)
3. Run **Preview Stream Match** and attach the CSV export -- this is the most helpful thing you can share. **Make sure no stream URLs are included in the CSV before sharing.**
4. Note your **Match Sensitivity** setting and lineup file used

### Submitting Lineup Databases
We welcome community-contributed lineup files! If you have a TV provider lineup that isn't included, please submit it as a pull request or open an issue with:
- Provider name and country
- Channel list with channel numbers and categories
- Source where the lineup data was obtained

If you'd like a specific provider added but can't create the file yourself, open a **Lineup Request** issue with the provider name, country, and a link to their channel listing page.

---

*All product names, trademarks, and registered trademarks mentioned in this project are the property of their respective owners. Channel lineup data is community-compiled from publicly available information and is not affiliated with or endorsed by any TV provider.*
