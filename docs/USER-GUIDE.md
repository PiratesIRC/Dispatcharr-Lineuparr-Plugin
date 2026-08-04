# Lineuparr user guide

Everything needed to configure and run Lineuparr, in the order you will need it.
Newer to the plugin? Read [the project front page](../README.md) first for what
it does and how to install it.

| Also available | |
|---|---|
| [Project front page](../README.md) | what the plugin is, the lineups it ships, and installation |
| [Lineup file format](LINEUP-FORMAT.md) | writing or contributing your own lineup |

---

## Contents

- [The short version](#the-short-version)
- [Settings reference](#settings-reference)
- [Match sensitivity](#match-sensitivity)
- [Step by step](#step-by-step)
- [The actions, one by one](#the-actions-one-by-one)
- [Unmatched channel cleanup](#unmatched-channel-cleanup)
- [Custom aliases](#custom-aliases)
- [Country matching](#country-matching)
- [IPTV Checker integration](#iptv-checker-integration)
- [Troubleshooting](#troubleshooting)
- [How matching works](#how-matching-works)
- [File locations](#file-locations)

---

## The short version

1. Pick a **Lineup File** and an **M3U Source**, then save.
2. Run **Validate Settings**. It reports the lineup summary and catches a bad
   configuration before anything is created.
3. Run **Preview Stream Match**. Nothing is changed; a CSV lands in
   `/data/exports/` showing what would match, with a confidence score per channel.
4. If the preview looks right, run **Full Sync**.

Steps 2 and 3 are optional and worth doing anyway. A preview costs nothing and
tells you whether your match sensitivity is set sensibly for your source.

---

## Settings reference

| Setting | Type | Default | What it does |
|---------|------|---------|--------------|
| Lineup File | select | `US_DirecTV-Premier_lineup.json` | The provider lineup to mirror. The list is built from the `*_lineup.json` files in the plugin directory. |
| M3U Source | select | *(empty)* | Which M3U account's streams to match against. Leave unset to use every active source. |
| Channel Profile | select | *(empty)* | Channel profile that synced channels are enabled in. |
| Channel Group Prefix | string | *(empty)* | Prefix added to the channel group names the plugin creates. |
| Category Detail | select | `Normal` | How lineup categories are grouped: None, Refined, Simple or Normal. |
| Match Sensitivity | select | `Normal` | Matching strictness. See [Match sensitivity](#match-sensitivity). |
| Channel Numbering | select | `Use Channel Database Numbers` | Database numbers, auto-assign next, auto-assign after highest, or start from a specific number. |
| Starting Channel Number | string | *(empty)* | Only used by the "specific number" mode. |
| Order Matched Streams by Quality | boolean | `true` | Sorts the streams attached to a channel, 4K before HD before SD. Changes ordering only, never which streams attach. |
| Preserve Existing Streams | boolean | `false` | Appends newly matched streams instead of replacing them, skips duplicates, and keeps channels that matched nothing. Use this when a second source already populates the same channels. |
| Single Channel Match | string | *(empty)* | Scopes Preview Stream Match, Apply Stream Match, Apply EPG Match and Assign Logos to the one lineup channel with this exact name, case-insensitive. Full Sync ignores it. |
| Rate Limiting | select | `None` | Throttles between operations: None, Low, Medium or High. Use it if a large sync makes Dispatcharr sluggish. |
| Custom Channel Aliases (JSON) | string | *(empty)* | Your own alias overrides. See [Custom aliases](#custom-aliases). |
| EPG Sources | select | `All EPG sources` | Which EPG source or sources to match against. "All" uses every source in the priority order configured in Dispatcharr. |
| GitHub Logo Source | string | *(empty)* | Optional public logo directory in `owner/repository@branch:path/to/logos` format. Used only by Assign Custom Logos. |
| Replace Existing Logos When Matched | boolean | `false` | Allows a successful custom repository match to replace a channel's current logo. Channels with no match keep their current logo. |

---

## Match sensitivity

| Level | Best for |
|-------|----------|
| Relaxed | Maximum coverage. Cast a wide net, then review the CSV for false positives. |
| Normal | General use. Good accuracy with reasonable coverage. |
| Strict | High-confidence matches only. Fewer results, fewer mistakes. |
| Exact | Near-exact matches only. Minimal false positives, will miss some valid matches. |

### Noisy or multi-country sources

Lineuparr attaches every stream at or above the threshold to a channel, so the
channel has failover options. A large multi-country playlist can therefore
attach a sibling-but-different feed that shares a common word: a US "Fox Sports
1" picking up "TNT Sports 1", "Sky Sports F1" or "AFN Sports", or "Sports Mix"
picking up "Sky Sports Mix". Those land in the 81 to 89 percent range, so
**Strict** removes them while keeping the genuine matches.

If Strict still lets a few through, there are two more levers. Matching only
ever reads the stream **name**, never its channel group, so:

- **Limit the M3U Source.** The cleanest fix. If the foreign feeds come from a
  different M3U account than the channels you want, do not select that source
  for the run. Those streams never enter the candidate pool at any sensitivity.
  Sorting streams into country-named *groups* does not help, because the matcher
  does not read group names.
- **Prefix the stream name with a country code.** A stream whose name carries a
  recognized country marker different from the lineup's country is dropped:
  `UK: Sky Sports F1`, `UK| Sky Sports`, `UK Sky Sports` or `(UK) Sky Sports`.
  Bulk-renaming the offending streams makes them drop out of a US lineup
  automatically. One exception: a bare `IN ` prefix is not read as India,
  because it collides with the English word "in" (the real channel "In Country
  Television"), so use `(IN)` or `IN:` for Indian feeds.

---

## Step by step

**1. Configure.** Select the Lineup File and M3U Source, optionally set a
Channel Group Prefix and Channel Profile, choose a Match Sensitivity, and save.

**2. Validate Settings.** Verifies the lineup file and the M3U source, and
reports channel counts per category. Recommended.

**3. Preview Stream Match.** A dry run. Shows what would match, with a
confidence score, and writes a CSV to `/data/exports/`. Nothing is changed, so
this is safe at any time. Recommended.

**4. Full Sync.** Creates channel groups from the lineup categories, creates the
channels with the right numbers, matches streams, assigns EPG data, assigns
logos, enables the channels in the selected profile, and removes channels that
matched no streams. See [Unmatched channel cleanup](#unmatched-channel-cleanup).

---

## The actions, one by one

Run these individually when you want more control than Full Sync gives. They all
live on the Actions tab of the plugin panel:

![The top of the Lineuparr Actions tab, showing Validate Settings, Show Status, Preview Stream Match, Full Sync and Sync Channels Only](screenshots/actions-panel-top.jpg)

![The rest of the Actions tab, showing Apply Stream Match Only, Apply EPG Match, Assign Logos, Re-sort Streams by Quality, Clear CSV Exports and Email Report Now](screenshots/actions-panel-bottom.jpg)

| Action | What it does |
|---|---|
| **Show Status** | Live progress of the running operation, or the result of the last one, without opening the container logs. |
| **Validate Settings** | Checks the lineup file and M3U source and summarizes the lineup. |
| **Preview Stream Match** | Dry run with a CSV export. Changes nothing. |
| **Full Sync** | The whole pipeline in one click. |
| **Sync Channels Only** | Creates and updates groups and channels from the lineup. No stream matching. |
| **Apply Stream Match Only** | Attaches matched streams to channels that already exist, in quality order. |
| **Apply EPG Match** | Matches EPG entries to channels and assigns the programme guides. |
| **Assign Logos** | Assigns channel logos from EPG icons, the Logo Manager, or the tv-logos repository on GitHub. |
| **Assign Custom Logos** | Matches the configured public GitHub logo directory against every existing Dispatcharr channel. It does not run a lineup sync or alter streams or EPG assignments. |
| **Re-sort Streams by Quality** | Re-orders already-attached streams using the newest quality data. See [IPTV Checker integration](#iptv-checker-integration). |
| **Clear CSV Exports** | Deletes the plugin's CSV exports. |

**Single Channel Match** scopes Preview Stream Match, Apply Stream Match Only,
Apply EPG Match and Assign Logos to one channel. Full Sync always runs the whole
lineup regardless of that setting.

---

## Custom logo repository

Set **GitHub Logo Source** to a public directory using this format:

```text
owner/repository@branch:path/to/logos
```

For example, `example/media@main:logos/channels` reads image files from the
`logos/channels` directory on the `main` branch. The field is empty by default,
and the plugin does not include or prefer any particular repository.

Logo filenames should describe the channel with words separated by hyphens or
underscores. An optional two-letter country suffix limits a file to that
country, such as `example-news-us.png` or `example-sports-gb.png`. Supported
extensions are PNG, SVG, JPG, JPEG, GIF and WebP. East is treated as the
standard feed, while Pacific remains a distinct filename term.

Run **Assign Custom Logos** independently from the other actions. It examines
all existing Dispatcharr channels and changes only their logo reference. With
**Replace Existing Logos When Matched** off, channels that already have a logo
are skipped. With it on, an existing logo is replaced only when the repository
contains a match. A channel with no match is never cleared.

---

## Unmatched channel cleanup

After stream matching, **Full Sync** and **Apply Stream Match Only** delete any
channel in a Lineuparr-managed group that ended up with zero streams. This keeps
the channel list free of lineup entries your source does not carry.

Two things bound it:

- Only channels in groups Lineuparr created are affected. Your other channels
  are never touched.
- With **Preserve Existing Streams** enabled the cleanup is skipped entirely, so
  a non-destructive add cannot remove channels another source populated.

To see what would go before committing to it, run **Preview Stream Match** and
read the unmatched rows in the CSV.

---

## Custom aliases

An alias is another name your provider uses for a channel. The plugin ships more
than 200 built-in aliases; the **Custom Channel Aliases (JSON)** setting adds
your own on top. Keys are the **exact lineup channel name**, values are the
provider's names for it. A single alias may be a plain string instead of a
one-item list.

```json
{
  "FOX News Channel": ["FOX NEWS HD", "FoxNews", "Fox News USA"],
  "HISTORY Channel, The": ["HISTORY", "History Channel HD", "History US"],
  "My Local Station": ["WABC", "WABC-TV", "ABC 7 New York"]
}
```

**Finding the key.** Open the lineup JSON and copy the `"name"` value exactly. If
the lineup says `"name": "HISTORY Channel, The"`, that whole string is the key.

**Finding the values.** Run **Preview Stream Match** and look at the unmatched
rows in the CSV. The stream names in your own M3U are what to add.

A lineup file can also carry aliases per channel, which is the better home for
variants specific to one provider. See the
[lineup file format](LINEUP-FORMAT.md).

---

## Country matching

A stream whose name carries a country marker that differs from the lineup's own
country is dropped. That is what stops a Canadian feed attaching to a US channel.
Streams with no marker at all are kept, because they cannot be proven wrong and
dropping them would break sources that never tag country.

The lineup's country normally comes from its filename, `US_DirecTV-Premier_lineup.json`
being US. Individual channels can override it, which is how you keep a block of
foreign channels inside an otherwise single-country lineup. Both forms are in the
[lineup file format](LINEUP-FORMAT.md).

---

## IPTV Checker integration

If you also run the
[IPTV Checker plugin](https://github.com/PiratesIRC/Dispatcharr-IPTV-Checker-Plugin),
you can order streams by measured quality rather than by what their names claim:

1. Run **Full Sync** or **Apply Stream Match Only** to attach streams.
2. Run an IPTV Checker scan over the Lineuparr channel groups.
3. Run **Re-sort Streams by Quality**. It uses the resolution and bitrate the
   scan measured instead of the quality words in the stream name.

---

## Troubleshooting

### Start here

Refresh the browser with F5, then restart the container:

```bash
docker restart dispatcharr
```

A surprising share of plugin problems are a stale browser page or a plugin
module still resident in memory from before an update.

### Plugin not found

Refresh the page, then restart the container. Dispatcharr discovers plugins at
worker start and when the Plugins page is opened.

### Low match rate

- Try **Relaxed** while you are finding your feet, then tighten.
- Run **Preview Stream Match** and read the CSV. It names every channel that
  found nothing.
- Add **Custom Aliases** for channels whose provider names differ.
- Confirm the M3U source actually carries the channels you expect.

### Channels created but no streams attached

- Check that the M3U Source setting points at the right account.
- Run **Preview Stream Match** to see the scores.
- If the stream names differ a lot from the lineup names, aliases are the fix.

### EPG not assigned

- Confirm EPG sources are configured in Dispatcharr.
- Run **Apply EPG Match** on its own to get the detailed log.
- Read the logs: `docker logs dispatcharr | grep "Lineuparr"`.

### Progress not updating

Operations run in the background and keep going even if the browser gives up.
Click **Show Status** for live progress and an estimated finish time, or the last
run's summary. The container logs carry the same detail.

---

## How matching works

Each lineup channel goes through four stages, in order, and stops at the first
that produces a confident answer:

1. **Alias match.** The built-in table, the lineup's own per-channel aliases, and
   your custom aliases.
2. **Exact match.** Normalized name comparison with spacing and punctuation
   stripped.
3. **Substring match.** One name contained in the other, with a length-ratio
   check so a short name cannot swallow a long one.
4. **Fuzzy token sort.** Edit distance over sorted, cleaned tokens.

Five guards apply across all four:

- **Length-scaled thresholds.** Shorter names must be more similar to pass, since
  a one-character difference matters more in a five-character name.
- **Token overlap.** A distinctive token has to be shared, which is what stops
  "ABC News" matching "BBC News".
- **Regional filtering.** East, West and Pacific variants only match streams of
  the same region.
- **Callsign anchoring.** A shared high-confidence US broadcast callsign such as
  "WABC" rescues a correct match, and a disagreeing one rejects a false match.
- **Channel number boost.** A three-or-more-digit channel number appearing in the
  stream name breaks ties. Only active in "Use Channel Database Numbers" mode.

---

## File locations

| What | Where |
|---|---|
| CSV exports | `/data/exports/lineuparr_*.csv`, kept across container restarts |
| Plugin directory | `/data/plugins/lineuparr/` inside the Dispatcharr data volume |
| Lineup files | the same plugin directory, named `{CC}_{Provider}_lineup.json` |
| Logs | `docker logs dispatcharr \| grep "Lineuparr"` |
