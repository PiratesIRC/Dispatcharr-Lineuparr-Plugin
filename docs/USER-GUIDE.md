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
- [Reports](#reports)
- [Reading a CSV export](#reading-a-csv-export)
- [Tidying up old CSV exports](#tidying-up-old-csv-exports)
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

The settings form is divided into five sections. This table follows the same
order, so a setting is listed where you will find it on screen.

### Lineup and Sources

| Setting | Type | Default | What it does |
|---------|------|---------|--------------|
| Lineup File | select | `DirecTV Premier (US)` | The provider lineup to mirror. The list is built from the `*_lineup.json` files in the plugin directory. The two-letter country code at the front of the filename is also what the country filter compares a stream against. |
| M3U Source | select | `All sources` | Which M3U account's streams to match against. Leave it on All to use every active source. |
| EPG Sources for Matching | string | *(blank)* | Which guide sources to match against. Blank means all of them, in the priority order configured in Dispatcharr. Otherwise give one or more source names separated by commas; `*` and `?` wildcards work, so `UK*` takes every source whose name starts with UK. Earlier names win. |
| Channel Profile | select | `None` | Channel profile that synced channels are enabled in. |

### Channel Groups and Numbering

| Setting | Type | Default | What it does |
|---------|------|---------|--------------|
| Channel Group Prefix | string | *(blank)* | Prefix added to the channel group names the plugin creates. Blank derives one from the lineup name; the word `none` suppresses it entirely. |
| Category Detail | select | `Normal` | How lineup categories become groups: None (one group), Refined (6), Simple (7) or Normal (every category the lineup names). |
| Channel Numbering | select | `Use the lineup file's own numbers` | Where each new channel's number comes from. The default reads the number recorded in the lineup file and sets no number when the file has none. The two auto modes ignore the file and fill free slots, from 1 upwards or above your highest existing channel. |
| Starting Channel Number (Specific mode only) | string | *(blank)* | The first number for the `Use specific number` mode. Every other mode ignores it. |

### Stream and EPG Matching

| Setting | Type | Default | What it does |
|---------|------|---------|--------------|
| Match Sensitivity | select | `Normal (80)` | The score out of 100 a name must reach to count as a match: Relaxed 70, Normal 80, Strict 90, Exact 95. Higher is stricter. Applies to stream and guide matching alike. See [Match sensitivity](#match-sensitivity). |
| Refresh EPG After Matching | boolean | `true` | After guide matching, asks Dispatcharr to refresh the sources that were actually matched, so the newly matched channels pick up programme data. |
| Order Matched Streams by Quality | boolean | `true` | Sorts the streams attached to a channel, 4K before HD before SD. Changes ordering only, never which streams attach. |
| Quality-Aware Stream Matching | boolean | `false` | When the lineup holds both a channel and its upgrade twin, such as TF1 and TF1 UHD, each is restricted to streams of its own quality tier. Channels with no twin are unaffected, and a stream named in a channel's aliases bypasses the restriction. |
| Preserve Existing Streams | boolean | `false` | The setting with the largest consequence here. When off, a stream match replaces each channel's whole stream list and then deletes channels left with no streams. Turn it on to append instead: duplicates are skipped and nothing is deleted. |
| Single Channel Match | string | *(blank)* | Scopes Preview Stream Match, Apply Stream Match, Apply EPG Match and Assign Logos to the one lineup channel with this exact name, case-insensitive. Full Sync ignores it. |
| Custom Channel Aliases (advanced) | text | *(blank)* | Your own alias overrides. See [Custom aliases](#custom-aliases). |

### Report delivery

| Setting | Type | Default | What it does |
|---------|------|---------|--------------|
| Send reports to Newsflasharr | boolean | `false` | Master switch for emailing reports. When off, reports are still written to disk and nothing is sent. See [Reports](#reports). |
| When to send | select | `Never` | Never, or after every run that produces a report. |
| What to attach | select | `Both the HTML page and the CSV` | Which report files are emailed. Both means two emails, because one notification carries one attachment. |

### Advanced

| Setting | Type | Default | What it does |
|---------|------|---------|--------------|
| Rate Limiting | select | `None` | Pauses after each channel a run processes, to leave the database free for the rest of Dispatcharr: None, Low (0.1s), Medium (0.5s) or High (2s). The pause is between database writes, not between requests to your provider, so it does nothing for a slow M3U source. |
| Delete CSV Exports Older Than (Days) | number | `0` | Housekeeping for `/data/exports/`. After each export, this plugin's own exports older than this many days are deleted. `0` keeps every file. See [Tidying up old CSV exports](#tidying-up-old-csv-exports). |

---

## Match sensitivity

The setting is a score out of 100 that a name has to reach before it counts as a
match, so a higher number is stricter.

| Level | Score | Best for |
|-------|-------|----------|
| Relaxed | 70 | Maximum coverage. Cast a wide net, then review the CSV for false positives. |
| Normal | 80 | General use. Good accuracy with reasonable coverage. |
| Strict | 90 | High-confidence matches only. Fewer results, fewer mistakes. |
| Exact | 95 | Near-exact matches only. Minimal false positives, will miss some valid matches. |

The score each run used is recorded in the preamble at the top of its CSV
export, so two exports that disagree can be compared.

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
reports channel counts per category. It also warns when the channel group prefix
or the EPG source filter names a different country from the lineup, and when an
EPG source it would match against is switched off in Dispatcharr. Recommended.

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

The button colour says what the action can do to your data.

| Colour | Meaning |
|---|---|
| Red | Can remove something: a stream, or a whole channel. Always asks first. |
| Orange | Writes data or clears state, but removes no stream and no channel. |
| Cyan | Sends something out of Dispatcharr, to your inbox. |
| Blue | Reads and reports. Changes nothing. |

| Action | Colour | What it does |
|---|---|---|
| **Show Status** | Blue | Live progress of the running operation, or the result of the last one, without opening the container logs. |
| **Validate Settings** | Blue | Checks the lineup file and M3U source and summarizes the lineup. |
| **Preview Stream Match** | Blue | Dry run. Writes a CSV export and an HTML report and changes nothing. |
| **Full Sync** | Red | The whole pipeline in one click. Its stream matching step carries the same removals as Apply Stream Match Only, below. |
| **Sync Channels Only** | Orange | Creates and updates groups and channels from the lineup. No stream matching, and it deletes nothing. |
| **Apply Stream Match Only** | Red | Attaches matched streams to channels that already exist, in quality order. Unless Preserve Existing Streams is on, it replaces each channel's whole stream list rather than adding to it, and then deletes any channel left with no streams. See [Unmatched channel cleanup](#unmatched-channel-cleanup). |
| **Apply EPG Match** | Orange | Matches guide entries to channels and assigns the programme guides. A channel that already has a guide is skipped, so this adds and never replaces. |
| **Assign Logos** | Orange | Assigns channel logos from EPG icons, the Logo Manager, or the tv-logos repository on GitHub. |
| **Re-sort Streams by Quality** | Orange | Re-orders the streams a channel already has, using the newest quality data. No stream is added or removed. See [IPTV Checker integration](#iptv-checker-integration). |
| **Clear CSV Exports** | Orange | Deletes every `lineuparr_*.csv` file in `/data/exports`, however new. It touches no channel, stream or guide, and ignores Delete CSV Exports Older Than. |
| **Email Report Now** | Cyan | Sends the newest report already on disk. It does not run a match. See [Reports](#reports). |

**Single Channel Match** scopes Preview Stream Match, Apply Stream Match Only,
Apply EPG Match and Assign Logos to one channel. Full Sync always runs the whole
lineup regardless of that setting.

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

Some providers do not put a country in the stream name at all. They label by
platform instead, so the same list carries `GO: ESPN`, `RK: VEVO POP` and
`PRIME: SKY NEWS`, and none of those names says where the feed comes from. For
those, the provider group the stream belongs to is read instead, because a group
is normally named for its country, as in `AU| AUSTRALIA VIP` or `US| SPORT`.

Two limits keep that from causing harm. The group is consulted only when the
stream name itself says nothing, so a name that does carry a country is always
judged on the name. And the group is ignored when acting on it would leave a
channel with no candidate streams at all, so a provider whose groups are
mislabelled loses nothing. A stream name appearing under groups of two different
countries is treated as having no country rather than being assigned one.

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

**A brand new EPG source works, and the log will say it is running degraded.**
Dispatcharr downloads programme data only for guide entries that are already
attached to a channel, and attaching them is what Apply EPG Match does, so a
source you have just added always starts with no programme data. Matching runs
against every entry in that case and logs a warning saying so. Once channels are
attached, the refresh that follows fills the programme data in.

**Check whether the source is switched on.** Matching reads guide entries whether
or not their source is enabled, and Dispatcharr never refreshes a disabled
source, so a channel matched to one keeps a guide that quietly stops being
updated. **Validate Settings** lists any source in your filter that is switched
off.

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

## Reports

Every action that produces a CSV also writes a shareable report: one HTML page
and one CSV, both named for the moment they were written, in
`/data/lineuparr_reports` inside the container. The eight newest of each are
kept and older ones are deleted.

### What the page looks like

The page opens as an index rather than as one long table. Rows are grouped into
sections that all start collapsed, so you open the one you care about:

| Section | What it holds |
|---|---|
| **Strong matches** | Scored 90 or above. Least likely to need a second look. |
| **Worth checking** | Scored 60 to 89. Good enough to propose, not good enough to trust without reading. This is where your time goes. |
| **Weak or no match** | Scored below 60, or nothing found at all. An alias is usually the fix. See [Custom aliases](#custom-aliases). |

Each heading carries the number of rows in its own table. A report that has no
score column, such as the channel sync preview, groups by status instead.

Every table sorts by clicking a column heading, or by focusing it and pressing
Enter. Sorting needs a browser: a mail client previewing the file shows every
row but cannot reorder them.

The page is one self-contained file with no external images, fonts or scripts,
so it renders the same opened from disk, forwarded as an attachment, or read on
a television browser with no internet connection. It follows your system's light
or dark setting.

### What is left out, deliberately

Stream names in a report have their M3U source label removed, and the plugin
settings that name your M3U sources are not included. On a real installation
that label is your provider's hostname. The complete export, which does include
it, stays in `/data/exports` inside the container and is never emailed.

Reports are deliberately not written to `/data/logos`. Dispatcharr's web server
publishes that directory to your whole local network with no password.

### Emailing reports

Reports are delivered by the separate **Newsflasharr** plugin, which handles the
mail account. Lineuparr never sends mail itself.

Turn on **Send reports to Newsflasharr** in the settings, choose whether to send
on every run or never, and choose the HTML page, the CSV, or both. Sending both
produces two emails, because one notification carries one attachment.

Set up a routing rule in Newsflasharr matching source `lineuparr` and event
`usage_report`, sending to the mail channel. **Mark the rule exclusive.** Without
that, Newsflasharr adds its default channel as well and the report goes to two
places.

**Email Report Now** sends the newest report already on disk. It does not run a
match, because a match takes minutes and the button asks to send a report rather
than to produce one.

### The report count

The plugin writes the number of reports it has built to
`/data/lineuparr/report_count.json`. Newsflasharr's Show Status action reads it
and prints the count next to this plugin. Nothing else uses it.

It counts reports whose files reached the disk, so a run that failed to write
one does not increase it. It is not a delivery count: a report built while
emailing is switched off still counts. The number is a floor rather than an
exact total, because two reports finishing at the same instant can lose a count
between them.

A plugin appears in that Newsflasharr readout only after it has delivered at
least one report through Newsflasharr, whatever the count file says.

---

## Reading a CSV export

Every run that matches something writes a CSV to `/data/exports/`, named
`lineuparr_<what>_<timestamp>.csv`.

The file opens with a preamble: a block of lines each starting with `#`. Those
lines are explanation, not data. The table starts at the first line without a
`#`, so **tell your spreadsheet to skip comment lines when importing, or delete
them before you open the file.** In LibreOffice Calc and Excel the import dialog
calls this "comment" or "skip lines".

The preamble is in two parts.

**What this run did** comes first: which action wrote the file, how many rows the
table below holds, and the counts that action produced, such as how many channels
were matched and how many streams were attached.

**Settings this run used** comes second, and records the settings as they were at
the moment of the run rather than as they are saved now. That is what makes two
exports comparable: if last week's results differed from today's, these lines say
whether the configuration changed. Match Sensitivity is written with its score,
Preserve Existing Streams is written with what its value caused, and every
on-or-off setting reads as Yes or No.

Nothing in the preamble contains a provider URL or a login.

---

## Tidying up old CSV exports

Every run that exports a CSV writes a new file to `/data/exports/`, and nothing
removes them, so the directory grows for as long as you keep using the plugin.

Set **Delete CSV Exports Older Than (Days)** to a number of days and each export
deletes this plugin's older exports as it finishes. The default is `0`, which
keeps every file, so upgrading changes nothing until you ask for it.

Four things this will not do.

- **It never touches another plugin's files.** `/data/exports/` is shared. On the
  system this was tested against it also held files written by Stream-Mapparr,
  EPG-Janitor, Event-Channel-Managarr, IPTV Checker and Channel-Maparr. Only
  files named `lineuparr_*.csv` are considered.
- **It never deletes the file the run just wrote**, whatever the age says.
- **It always leaves at least one of this plugin's exports in place**, so a small
  number cannot empty the directory.
- **It never turns a successful export into a failure.** If a file cannot be
  deleted the export still reports success and the reason is logged.

Age comes from the file's modification time, not from the timestamp in its name,
and the comparison is strict: with a setting of 7, a file exactly seven days old
is kept.

One thing it does do, which is easy to miss. When an HTML report holds more rows
than it shows, it prints a line telling the reader that the complete file is
`lineuparr_..._.csv` in `/data/exports`. That named file is protected only during
the run that wrote it. A later export can delete it once it is older than the
retention window, and if the report was emailed the reader then has a report
pointing at a file that is gone. The reports directory keeps its own last eight
copies and is not coordinated with this setting. If you email reports and want
the CSV they name to stay available, either leave this setting at `0` or set it
comfortably longer than the age of the oldest report you expect anyone to open.

If you set it and nothing is deleted, check the container log. A value that is
not a whole number of days, such as `7.5`, keeps every file and says so in the
log: `docker logs dispatcharr | grep Lineuparr`.

The **Clear CSV Exports** action is unchanged and ignores this setting. It still
deletes every `lineuparr_*.csv` file, however new.

---

## File locations

| What | Where |
|---|---|
| CSV exports | `/data/exports/lineuparr_*.csv`, kept across container restarts unless you set a retention in days |
| Reports | `/data/lineuparr_reports/lineuparr_report_*.html` and `*.csv`, eight of each kept |
| Report count | `/data/lineuparr/report_count.json`, read by Newsflasharr |
| Channels-created tally | `/data/lineuparr_channel_counts.jsonl`, one line per finished sync, summed by the Channels Created badge on the README |
| Plugin directory | `/data/plugins/lineuparr/` inside the Dispatcharr data volume |
| Lineup files | the same plugin directory, named `{CC}_{Provider}_lineup.json` |
| Logs | `docker logs dispatcharr \| grep "Lineuparr"` |
