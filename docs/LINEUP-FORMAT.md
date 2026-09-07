# Lineup file format

A lineup file is a list of the channels a TV provider carries, with their channel
numbers, grouped into categories. It holds names and numbers only: no stream
addresses, no credentials, no provider account details.

Lineuparr matches the names in this file against the stream names Dispatcharr
already holds for your own sources.

| Also available | |
|---|---|
| [Project front page](../README.md) | what the plugin is and how to install it |
| [User guide](USER-GUIDE.md) | settings, actions, troubleshooting |

---

## The minimum

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

`categories` is the only required key. `package`, `date`, `description` and
`source` are shown in the interface and in the lineup dropdown.

## The filename decides the country

The file must be named `{CC}_{Provider}_lineup.json`, where `{CC}` is a
two-letter ISO country code: `US_MyProvider_lineup.json`,
`UK_Freeview_lineup.json`.

The country code does two jobs. It selects the country directory when logos are
matched against the [tv-logos](https://github.com/tv-logo/tv-logos) repository,
and it is the default country for every channel in the file, which is what stops
a foreign stream attaching to one of these channels.

Hyphens in the provider part become spaces in the dropdown label, so
`US_Verizon-FIOS_lineup.json` reads as "Verizon FIOS (US)".

Place the file in the plugin directory, `/data/plugins/lineuparr/`, and it
appears in the **Lineup File** dropdown.

---

## Per-channel aliases

A channel entry may carry its own `aliases` array. These are merged with the
built-in alias table for this lineup only, which makes it the right home for
stream-name variants specific to one provider, rather than adding them globally.

```json
{ "name": "My9 New York", "number": 509, "aliases": ["WWOR", "WWOR-TV", "MY9"] }
```

A single alias may be given as a plain string instead of a one-item list.

**Callsign aliases reach into longer names.** When an alias is a US broadcast
callsign, a stream carrying that callsign anywhere in its name matches, so
`WWOR` reaches `US: MY 9 WWOR NEW YORK` and `CITY: MNT WWOR NEW YORK` even
though the channel is called "My9 New York". Ordinary English words that happen
to have callsign shape (KIDS, WORLD, WOMEN, WEST, KISS) are excluded, so they
never pull in unrelated streams.

Only an alias that is *entirely* a callsign counts. `WWOR` and `WWOR-TV` do;
`WWOR New York` does not, because a display name is not a claim about which
streams belong to the channel.

## Per-channel excluded aliases

A channel entry may also carry `excluded_aliases`. Use it for a stream name that
looks like a good match but is known not to belong to that channel.

```json
{
  "name": "Game Show Network",
  "number": 128,
  "aliases": ["GSN"],
  "excluded_aliases": ["*Game Show Central*"]
}
```

A single exclusion may be a plain string instead of a one-item list. Exclusions
compare original full stream names case-insensitively, trimming outer whitespace
and collapsing repeated whitespace to one space. They do not use the lossy
positive-alias normalizer: prefixes, punctuation, network words, quality tags,
and word spacing remain significant. They are channel-scoped: the excluded stream cannot
match this channel through an alias, callsign, exact, substring, fuzzy, quality
bypass or channel-number boost, but it can still match another channel.

Without an unescaped `*`, the whole name must match. An unescaped `*` matches
zero or more characters: `*Game Show Central*` rejects `US: Game Show Central HD`
but preserves `Game Show Network` and does not match `GameShowCentral`.
Patterns have no implicit word boundaries: that pattern also matches
`Game Show Centralization`. Empty and wildcard-only patterns are invalid.
Use Preview Stream Match to check broad exclusions before applying changes.

Only `*` is a wildcard. Question marks, brackets, `!`, and regex syntax have
no special meaning. Escape an asterisk with a backslash for a literal star;
escape a backslash with another backslash. JSON requires those backslashes
to be doubled. Other backslashes are literal. Matching uses ordered literal
searches, not executable regular expressions.

Exclusions affect stream matching only; they do not affect EPG/guide matching.
The same stream name in multiple M3U accounts is excluded from this channel
in every account. Preserve Existing Streams can retain previously attached
excluded streams: an exclusion prevents new matching, not a guaranteed purge.
Older plugin versions do not support this wildcard/escape contract.

For example, this JSON excludes the literal title with asterisks, not
arbitrary text between its letters:

```json
{"excluded_aliases": ["M\\*A\\*S\\*H"]}
```

---

## Foreign channels inside a single-country lineup

Matching is restricted to the lineup's own country. To carry a block of foreign
channels anyway, mark them. There are two ways, and you can use both in one file.

### A country prefix on the channel name

Name the channel `{CC}_{Name}`. It is then matched against that country instead
of the lineup's, wherever it sits:

```json
"International": [
  { "name": "UK_CNN", "number": 501 },
  { "name": "FR_TF1", "number": 502 }
]
```

The prefix is stripped before anything else sees it, so Dispatcharr creates the
channel as `CNN`, not `UK_CNN`, and the matcher looks for "CNN". Each marked
channel also gets that country's aliases.

Only recognized country codes count, so an ordinary name that happens to contain
an underscore, such as `MTV_Live`, is left exactly as written. A name with
nothing after the prefix is also left alone.

### A country prefix on the category name

A whole category can be marked instead: `UK| International`, `UK: International`
or `UK International`. Use this when the foreign channels are already grouped
together.

### Which one wins

A channel's own prefix beats its category's, which beats the lineup filename.
When a channel's prefix overrides the others, the plugin logs it, so a channel
that unexpectedly matches nothing can be traced.

### What this cannot do

The country filter drops a stream whose name carries a *different* country
marker, and keeps a stream with no marker at all. So a `UK_` channel is still
eligible for an untagged stream. Marking a channel decides which country is
filtered for; it cannot make the filter more certain than the stream names allow.

---

## Contributing a lineup

Community-contributed lineups are welcome. Open a pull request with the file, or
open an issue including the provider name, the country, the channel list with
numbers and categories, and where the listing came from.

If you would like a provider added but cannot build the file yourself, open a
**Lineup Request** issue with the provider name, the country, and a link to their
public channel listing page.
