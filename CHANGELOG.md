# Changelog

Newest first. Versions are calver Major.YY.DDDHHMM (UTC).

## 1.26.2901915 (2026-10-17)

### Added

- Four lineups for the Dominican Republic and Peru, taking the built-in count
  from 25 to 29, contributed by taos15 in pull request #29 (issue #28).
  `DO_Altice_lineup.json` carries Altice, 224 channels across 11 categories.
  `DO_Claro_lineup.json` carries Claro, 261 channels across 11 categories.
  `DO_Combined_lineup.json` merges the two into 309 channels across 11
  categories. `PE_Claro_lineup.json` carries Claro Peru, 201 channels across
  12 categories. The `DO` and `PE` alias sections help these lineups match
  local channel names.

### Fixed

- Matching no longer pauses the rest of Dispatcharr while it prepares. Before
  matching, each action prepares every stream or EPG name once, and that step
  never paused for other work: on an installation with about 23,000 EPG names
  it held the Dispatcharr worker for over a second, so other requests and
  streams served by that worker waited. It now pauses every 500 names. Match
  results are unchanged.

### Changed

- The release archive now carries signed build provenance. Check a downloaded
  copy with `gh attestation verify Lineuparr.zip -R
  PiratesIRC/Dispatcharr-Lineuparr-Plugin --signer-workflow
  PiratesIRC/Dispatcharr-Lineuparr-Plugin/.github/workflows/ci.yml`.

### Known

- `DO` and `PE` are not recognized country codes, so stream matching does not
  apply the country filter to these lineups.
- The two Dominican Republic provider lists are dated 2018, the newest public
  lists found.

## 1.26.2881058 (2026-10-15)

### Added

- Three Tanzania lineups, taking the built-in count from 22 to 25, contributed
  by FainiDenis in pull request #36 (issue #35). `TZ_Azam-TV_lineup.json`
  carries Azam TV, 146 channels across 11 categories.
  `TZ_DStv-Premium_lineup.json` carries DStv Premium in Tanzania, 192
  channels across 11 categories. `TZ_Combined_lineup.json` merges the two
  into 310 channels across 13 categories, with each channel the two packages
  share listed once and renumbered by category, because the two providers
  use the same numbers for different channels.

### Known

- `TZ` is not one of the recognized country codes, so stream matching does
  not apply the country filter to the Tanzania lineups.
- In the Azam TV lineup, `Zodiak` and `Zodiak TV` share number 163, and the
  combined lineup carries the same pair at 955 and 956. Matching removes
  "TV" from a name, so one stream can match both.

## 1.26.2880000 (2026-10-15)

### Fixed

- UK Combined lineup: 45 channels are back in their proper category (issue
  #34). Sky Sports Main Event, Mix, News and F1 and TNT Sports 1 to 4 are in
  Sports again (209 to 223), and the same fix returns TRT World to News, Film4
  to Movies, Boomerang, Cartoon Network, Nick Jr. and Nickelodeon to Kids, the
  Sky, U& and factual channels to Entertainment, QVC and four others to
  Shopping, and six Indian channels to International. An earlier clean-up had
  kept the copy of each channel from Sky's "Secondary & Information" list.
  No channel was added or removed, and the other UK lineups were not affected.
  **After updating:** Full Sync creates each moved channel in its new group and
  leaves the old copy, with its streams, in the old group. Delete any of these
  channels that now appear twice; the copy to remove is the one in the old
  group.

## 1.26.2831530 (2026-10-10)

### Added

- Anonymous usage counts. The Channels Created and Active Installs badges now
  count every install that keeps the new "Share anonymous usage counts" setting
  ticked (it is on by default and is the last setting in the Advanced section)
  and runs at least one action or sync. When an action or sync finishes, at most
  once an hour, or ten minutes after the last report when a sync has just
  created channels, the plugin sends its Channels Created total and a random id
  for this plugin on this install to plugin-stats.dpas.workers.dev. No names,
  channels, streams, providers or settings are sent. Unticking the setting stops
  the reports and has this install's figures deleted from the server the next
  time an action or sync finishes. The user guide section "Anonymous usage
  counts" describes exactly what the server stores and for how long.

## 1.26.2821835 (2026-10-09)

### Fixed

- Category Detail set to None now names the single channel group with the
  group prefix alone, as the setting describes, instead of `<prefix>: All`.
  A group created under the old name is renamed in place the next time Full
  Sync or Sync Channels runs, so its channels stay where they are and no
  second group is created. If both names already exist, neither is changed
  and a warning is logged. Contributed by sbcrumb in pull request #32,
  fixing issue #30.
- Automatic channel numbering no longer hands out a number another channel
  already shows. Dispatcharr 0.32.0 keeps a number assigned in its interface
  to an auto-synced channel in a separate override field, and the
  Auto-assign next available, Auto-assign after highest and Use specific
  number modes did not read it. They now count those numbers as taken.

## 1.26.2561550 (2026-09-13)

### Added

- Two contributed lineups, taking the built-in count from 20 to 22.
  `QA_beIN-MENA_lineup.json` carries beIN's Middle East and North Africa
  package, 168 channels across 9 categories, contributed by Adam through the
  Dispatcharr Discord server. `NL_KPN_lineup.json` carries KPN Interactive TV
  in the Netherlands, 290 television and radio channels across 29 categories,
  contributed by ZenimNL in issue #22.

### Fixed

- The country filter now applies to a Qatar lineup. `QA` was missing from the
  recognized country codes, so stream matching skipped the filter entirely for
  the beIN MENA lineup and an Australian, Belgian or French beIN feed could
  attach to one of its channels. Only `QA` was added: the lineup is regional
  and the filter is single country, so recognizing the other Middle East and
  North Africa codes would make it reject streams it legitimately carries.

### Known

- In the beIN MENA lineup, `QBC 4K` at 343 and `QBC HD` at 344 cannot be told
  apart, because matching removes quality tags from a name. Turning on
  Quality Aware Stream Matching separates them.
- In the KPN lineup, four regional radio stations share a name with the
  television channel of the same broadcaster, separated only by an `HD`
  suffix that matching removes: Omroep Brabant, Omroep Zeeland, Omrop
  Fryslan and XITE Klassiek. Four of its categories are empty and will create
  empty channel groups.

## 1.26.2481702 (2026-09-05)

- See the GitHub release for v1.26.2481702.

## 1.26.2421451 (2026-08-30)

- See the GitHub release for v1.26.2421451.

## 1.26.2291211 (2026-08-17)

- See the GitHub release for v1.26.2291211.

## 1.26.2271740 (2026-08-15)

- See the GitHub release for v1.26.2271740.

## 1.26.2241618 (2026-08-12)

- See the GitHub release for v1.26.2241618.

## 1.26.2171315 (2026-08-05)

- See the GitHub release for v1.26.2171315.

## 1.26.2142327 (2026-08-02)

- See the GitHub release for v1.26.2142327.

## 1.26.1931114 (2026-07-12)

- See the GitHub release for v1.26.1931114.

## 1.26.1791747 (2026-06-28)

- See the GitHub release for v1.26.1791747.

## 1.26.1641222 (2026-06-13)

- See the GitHub release for v1.26.1641222.

## 1.26.1582125 (2026-06-07)

- See the GitHub release for v1.26.1582125.

## 1.26.1431300 (2026-05-23)

- See the GitHub release for v1.26.1431300.

## 1.26.1421711 (2026-05-22)

- See the GitHub release for v1.26.1421711.

## 1.26.1370103 (2026-05-16)

- See the GitHub release for v1.26.1370103.

## 1.26.1091027 (2026-04-19)

- See the GitHub release for v1.26.1091027.

## 1.26.1021446 (2026-04-12)

- See the GitHub release for v1.26.1021446.

## 1.26.1001146 (2026-04-10)

- See the GitHub release for v1.26.1001146.

## 1.26.9520 (2026-04-05)

- See the GitHub release for v1.26.9520.
