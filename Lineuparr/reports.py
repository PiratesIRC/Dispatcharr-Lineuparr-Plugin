"""Report model and rendering for Lineuparr.

Renders one run's results as a self-contained HTML page with a sortable table,
and as a CSV. This is separate from the plugin's own CSV export in
/data/exports: that file opens with a settings header naming the configured M3U
sources, which on a real installation is the provider's hostname, so it is a
local file and not something to hand around.

Two structural decisions follow from the report being shareable, and both are
pinned by tests:

The model is built by COPYING a named allow-list of columns out of the row dicts
the actions already hold in memory. It is never built by re-reading an export.
Building from an allow-list also means a column added to a CSV writer later
cannot start appearing in a shared report on its own.

The M3U account-name scrub is a PRIMARY redaction input here, not a backstop. A
stream name frequently carries its own account label, so build_model refuses when
the account name list is None, which is how a caller reports that the lookup
failed. An empty list is a different thing and is allowed: an installation can
legitimately have no active M3U accounts.
"""
import base64
import csv
import datetime
import html
import io
import os
import re
import time

# The legacy default. storage.bind resolves the shared Newsflasharr folder (PluginConfig.REPORT_DIR),
# and plugin.py assigns that value to this module's REPORT_DIR at import. Reports are never written under
# /data/logos: Dispatcharr's nginx serves that tree unauthenticated to the entire local network.
REPORT_DIR = "/data/lineuparr_reports"

# Every report file starts with this. The pruner matches on it, so a copied and
# unrenamed prefix would make pruning match nothing and the directory would grow
# forever with no error.
FILENAME_PREFIX = "lineuparr_report_"

# How many of each file type to keep.
KEEP_REPORTS = 8

# A report file younger than this is never pruned, however many newer ones
# exist. A delivery that re-reads the file path on a retry would otherwise find
# it gone.
RETRY_WINDOW_SECONDS = 2400

# The row cap, applied ONCE before rendering. A render, measure, drop rows,
# re-render loop is deliberately not used: a large lineup matched against a large
# M3U produces thousands of rows, and these reports are built inside the uWSGI
# request, where a pure Python loop performs no input or output and so never
# yields. Under gevent that freezes the whole worker, not just the request that
# started it.
#
# Anyone raising this constant must measure in UTF-8 BYTES of the written file,
# not characters: the matcher supports Cyrillic and CJK names, which are two to
# three times longer in bytes than in characters.
MAX_REPORT_ROWS = 2000

# The container path of the full exports. Named in the report so a reader knows
# where the complete file is. It is a locator, not a browsable link.
EXPORTS_LOCATION = "/data/exports"

# The only settings that reach the report. Everything else is dropped, including
# m3u_source, which names the operator's provider account, and custom_aliases,
# which is free text the operator typed.
_SAFE_SETTINGS = (
    ("match_sensitivity", "Match sensitivity"),
    ("category_detail", "Category detail"),
    ("channel_numbering", "Channel numbering"),
    ("preserve_existing_streams", "Preserve existing streams"),
)

# Matches a bare IPv4 address.
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# Matches an IPv6 address, including the compressed forms. Deliberately requires
# either a double colon or at least three colon separated groups, so an ordinary
# clock time such as 20:30 is not mistaken for an address.
_IPV6_RE = re.compile(
    r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b"
    r"|(?:[0-9A-Fa-f]{1,4}:)+:(?:[0-9A-Fa-f]{1,4})?"
    r"|::(?:[0-9A-Fa-f]{1,4}:)*[0-9A-Fa-f]{1,4}"
)

# Collapses the run of spaces left behind when a value is removed from the middle
# of a name.
_MULTISPACE_RE = re.compile(r"\s{2,}")


def sanitise_label(label, account_names):
    """Remove an M3U account name wherever it appears, and nothing else.

    Matching is case insensitive and is not limited to the bracketed form,
    because an account name on a real installation is a literal provider
    hostname, and "ESPN backup provider.tv" leaks exactly as much as
    "ESPN [provider.tv]".

    Account names are matched longest first: "provider.tv" is a prefix of
    "provider.tv-alt1", and matching the shorter one first would leave a "-alt1"
    fragment behind.

    An unknown bracketed value is left alone. Bracketed text in a stream name
    usually carries the region or the quality, and removing every bracketed group
    would collapse distinct streams into one indistinguishable name.
    """
    text = str(label if label is not None else "")
    for account in sorted([a for a in (account_names or []) if a], key=len, reverse=True):
        escaped = re.escape(str(account))
        text = re.sub(r"\s*\[" + escaped + r"\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*\(" + escaped + r"\)", "", text, flags=re.IGNORECASE)
        text = re.sub(escaped, "", text, flags=re.IGNORECASE)
    return _MULTISPACE_RE.sub(" ", text).strip()


def _scrub(value, account_names):
    """Apply every content rule to one free text value."""
    cleaned = sanitise_label(value, account_names)
    cleaned = _IPV4_RE.sub("", cleaned)
    cleaned = _IPV6_RE.sub("", cleaned)
    return _MULTISPACE_RE.sub(" ", cleaned).strip()


def build_model(title, columns, rows, *, account_names, settings, lineup,
                version, now, export_filename=None):
    """Build the report model from in-memory rows.

    `columns` is a list of (row key, display header) pairs and IS the allow list.
    A key absent from it is never copied, whatever the row carries.

    `account_names` of None means the M3U account lookup failed, and this raises
    rather than producing an unscrubbed report. An empty list is allowed.
    """
    if account_names is None:
        raise ValueError(
            "account_names is None, which means the M3U account lookup failed. "
            "The report is not built, because the account names are the primary "
            "redaction input and an empty scrub would ship unredacted names.")

    rows = list(rows or [])
    total_rows = len(rows)
    kept = rows[:MAX_REPORT_ROWS]

    entries = []
    for row in kept:
        entries.append([_scrub(row.get(key), account_names) for key, _ in columns])

    settings = settings if isinstance(settings, dict) else {}
    summary = [("Plugin version", str(version)),
               ("Generated", _fmt_ts(now)),
               ("Lineup", str(lineup or "unknown"))]
    for key, label in _SAFE_SETTINGS:
        if key in settings:
            summary.append((label, str(settings.get(key))))
    summary.append(("Rows", f"{len(entries)} of {total_rows}"))

    return {
        "title": str(title),
        "generated_ts": float(now or 0),
        "summary": summary,
        "headers": [header for _, header in columns],
        "entries": entries,
        "total_rows": total_rows,
        "shown_rows": len(entries),
        "truncated": total_rows > len(entries),
        "export_filename": export_filename,
    }


def truncation_notice(model):
    """The one line stating that rows were dropped, or None when none were.

    It names a count and a filename only. Never the M3U source.
    """
    if not model.get("truncated"):
        return None
    name = model.get("export_filename") or "the export file"
    return (f"Showing the first {model['shown_rows']} of {model['total_rows']} rows. "
            f"The complete file is {name} in {EXPORTS_LOCATION} inside the container.")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

# Styling is inlined because the page is read from a file path or inside an email
# client, where an external stylesheet would not resolve.
#
# THE TOKEN LAYER, adopted from the Dustarr plugin's report. Four rules govern
# it, and each one was a real defect in that plugin before it was fixed:
#
#   Spacing is a scale. Every margin, padding and gap picks --s1 to --s5. The
#   steps are about a third apart, because a linear ramp makes small steps look
#   identical and large ones look arbitrary.
#
#   No opacity for text hierarchy. Use --ink, --ink-muted, --ink-dim. An
#   opacity value paints a different colour on every surface it lands on, so
#   the contrast ratio moves silently whenever a background changes, and the
#   fade applies to everything nested inside. The ramp is 5.24:1 at its weakest
#   against the light surface, over the 4.5:1 floor. Opacity remains fine for a
#   decorative fill that carries no text.
#
#   Light and dark differ only in token values. Needing !important means the
#   tokens are wrong.
#
#   No literal colour inside a rule. Light and dark cannot both be right about
#   one. Literals appear only in the two :root blocks.
#
# The category colours and the two surface colours were validated all pairs for
# colourblind safety against those exact surfaces, with a validator that is not
# in this repository. THE NUMBERS ARE NOT RE-DERIVABLE HERE. Reuse the semantic
# slots for new categories rather than inventing a hex value.
_CSS = """
:root {
  color-scheme: light dark;
  --never: #2a78d6; --watched: #1baf7a; --tuned: #e34948; --toonew: #898781;
  --track: #e1e0d9; --ok: #0ca30c; --bad: #d03b3b;

  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px;

  --bg: #fbfbfd; --raised: #ffffff; --zebra: #f7f8fa; --head: #f2f3f6;
  --ink: #16181d; --ink-muted: #5c616b; --ink-dim: #656a76;
  --line: #e3e5ea; --line-soft: #e6e8ec;
  --warn-bg: #fff4e5; --warn-line: #ffb84d; --warn-ink: #7a4b00;
  --lift: 0 1px 2px rgba(16, 18, 29, .05), 0 4px 12px rgba(16, 18, 29, .04);
}
@media (prefers-color-scheme: dark) {
  :root {
    --never: #3987e5; --watched: #199e70; --tuned: #e66767; --toonew: #898781;
    --track: #2c2c2a; --ok: #0ca30c; --bad: #d03b3b;

    --bg: #14161a; --raised: #1a1d22; --zebra: #191c21; --head: #1e2127;
    --ink: #e8eaed; --ink-muted: #a7adb8; --ink-dim: #9aa0ab;
    --line: #2a2e35; --line-soft: #262a31;
    --warn-bg: #2e2312; --warn-line: #8a6320; --warn-ink: #f2c98a;
    --lift: 0 1px 2px rgba(0, 0, 0, .35), 0 4px 12px rgba(0, 0, 0, .25);
  }
}
body { font: 15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif;
       margin: 0; padding: var(--s5); background: var(--bg); color: var(--ink); }
/* The logo sits beside the title rather than above it, so the masthead costs
   one line of vertical space instead of three. */
.masthead { display: flex; align-items: center; gap: var(--s3);
            margin-bottom: var(--s5); }
.mark { flex: none; width: 48px; height: 48px; display: block; }
/* The type scale is 15 / 17 / 22, hand picked and deliberately sparse. A step
   7 percent from the body size reads as an accident rather than a decision,
   and this page is sized for reading at television distance, so nothing here
   shrinks. Supporting text is separated by colour instead. */
h1 { font-size: 22px; line-height: 1.2; letter-spacing: -.01em;
     margin: 0 0 var(--s1); }
.masthead .sub { margin-bottom: 0; }
.sub { color: var(--ink-muted); font-size: 15px; margin-bottom: var(--s4); }
.card { background: var(--raised); border: 1px solid var(--line);
        border-radius: 10px; box-shadow: var(--lift);
        padding: var(--s3) var(--s4); margin-bottom: var(--s4); }
.notice { background: var(--warn-bg); border: 1px solid var(--warn-line);
          border-radius: 10px; box-shadow: var(--lift);
          padding: var(--s3) var(--s4); margin-bottom: var(--s4);
          color: var(--warn-ink); font-size: 15px; }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
.scroll { overflow-x: auto; }
th, td { text-align: left; padding: var(--s2) var(--s3);
         border-bottom: 1px solid var(--line-soft); vertical-align: top; }
th { background: var(--head); }
/* Zebra striping is defined once, in tokens, so both themes render the same
   table. Dustarr had it in dark mode only for months. */
tr:nth-child(even) td { background: var(--zebra); }
th.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
th.sortable::after { content: " \\2195"; color: var(--ink-dim); font-size: 15px; }
th.sortable[aria-sort="ascending"]::after { content: " \\2191"; color: var(--ink); }
th.sortable[aria-sort="descending"]::after { content: " \\2193"; color: var(--ink); }
.empty { color: var(--ink-muted); font-style: italic; }
dl.meta { margin: 0; display: grid; grid-template-columns: auto 1fr;
          gap: var(--s1) var(--s4); }
dl.meta dt { color: var(--ink-muted); }
dl.meta dd { margin: 0; }
details { border-top: 1px solid var(--track); padding: var(--s1) 0 var(--s2); }
/* Never add outline: none here. The focus ring is how this page is driven by a
   television remote control's directional pad. */
summary { font-size: 17px; font-weight: 600; cursor: pointer;
          padding: var(--s2) var(--s1); list-style: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: '\\25B8'; display: inline-block; width: 1em;
                  color: var(--ink-dim); transition: transform .12s; }
details[open] > summary::before { transform: rotate(90deg); }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
       margin-right: var(--s2); vertical-align: baseline; }
.dot-watched { background: var(--watched); }
.dot-toonew { background: var(--toonew); }
.dot-tuned { background: var(--tuned); }
.dot-never { background: var(--never); }
.dot-neutral { background: var(--track); }
/* The heading is 600 and the count stays at 400, which is what makes the
   number read as data rather than as part of the label. */
.count { font-weight: 400; color: var(--ink-dim);
         font-variant-numeric: tabular-nums; }
/* Emoji ignore colour, so this cannot carry meaning even by accident. It is
   spacing only. */
.glyph { margin-right: var(--s2); }
.hint { font-size: 15px; color: var(--ink-dim); margin: 0 0 var(--s2); }
.colophon { margin-top: var(--s5); padding-top: var(--s4);
            border-top: 1px solid var(--track); color: var(--ink-dim); }
.colophon p { margin: 0 0 var(--s1); }
.colophon a { color: var(--never); }
"""

# The project's own pages, shown in the report footer.
REPO_URL = "https://github.com/PiratesIRC/Dispatcharr-Lineuparr-Plugin"
ISSUES_URL = "https://github.com/PiratesIRC/Dispatcharr-Lineuparr-Plugin/issues"

# The plugin that delivers an emailed copy of this report.
#
# Newsflasharr already writes its own credit line into the EMAIL BODY, in
# email_render.footer_text, reading "Lineuparr report courtesy of
# Newsflasharr". The line below is the same credit carried inside the report
# page, so it survives the page being saved, forwarded or opened from disk
# without the mail around it.
#
# The wording is deliberately about emailed copies rather than about this copy.
# A report read straight from the report directory was never delivered by
# anything, and a page that thanked a deliverer that had not run would be
# saying something untrue in its own footer.
NEWSFLASHARR_URL = "https://github.com/PiratesIRC/Dispatcharr-Newsflasharr-Plugin"

LOGO_FILE = "logo_report.png"

# A logo larger than this is not embedded. The page is mailed as an attachment
# and every byte of the image is carried in every report, so an oversized file
# is a cost paid repeatedly by the person receiving the mail.
#
# logo_report.png is a report sized copy of the plugin icon: 96 by 96 and about
# 23 KB, which encodes to roughly 31 KB of base64 and makes a whole rendered
# report about 41 KB. The plugin icon itself, logo.png, is 440 by 440 and about
# 181 KB, which encoded to roughly 247 KB and was the bulk of every report until
# 2026-08-12. The page displays the image at 48 by 48, so 96 covers a two times
# density display and nothing on the page can use more.
#
# The cap is left well above the file it guards on purpose. It exists to stop a
# replacement image being embedded unnoticed, not to sit one byte above the
# current one, and tests/test_reports.py pins the size of the shipped file
# separately and much more tightly.
MAX_LOGO_BYTES = 262144

# One slot: empty means not yet resolved, one entry means resolved. The failure
# is cached too, so a missing file is not re-read on every render.
_logo_cache = []


def logo_data_uri():
    """The logo as a data URI, or an empty string when it cannot be used.

    Embedded, never linked. This page is opened from a file path and is also
    mailed as an attachment, so a relative path resolves against nothing and a
    remote address is blocked by default in most mail clients. This repository
    was private for much of its life, so a raw link to it would not have
    resolved either.

    Returns an empty string rather than raising on any failure, and the caller
    then renders no image element at all rather than a broken image icon.
    render_html has no safety net of its own: write_report catches the failure
    one level up, so a logo problem must cost the header image and nothing more.
    """
    if not _logo_cache:
        try:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                LOGO_FILE)
            if os.path.getsize(path) > MAX_LOGO_BYTES:
                _logo_cache.append("")
            else:
                with open(path, "rb") as handle:
                    encoded = base64.b64encode(handle.read()).decode("ascii")
                _logo_cache.append(f"data:image/png;base64,{encoded}")
        except Exception:
            _logo_cache.append("")
    return _logo_cache[0]


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _fmt_ts(ts):
    """Render a time in UTC, labelled as such.

    Deliberately not local time: this module has no access to Dispatcharr's
    configured timezone, and a bare unlabelled clock that silently means UTC is
    how a reader gets the day wrong.
    """
    try:
        moment = datetime.datetime.fromtimestamp(float(ts or 0), datetime.timezone.utc)
        return moment.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"


# Click to sort, embedded in the page rather than loaded from anywhere. The page
# is opened from a file path or from a mail attachment, so an external request
# would not resolve and would disclose that the report had been opened.
#
# This is an ADDITION, not a requirement. Every row is present in the markup, so
# a reader whose mail client strips scripts still sees the whole table; they
# simply cannot reorder it.
#
# The comparison reads each cell's data-v attribute, which holds the same value
# the cell displays. Two values that both parse as numbers are compared as
# numbers, because comparing them as text puts 10 before 2, which matters here:
# every report has a channel number column and most have a score column.
#
# EVERY table on the page is wired up, not just the first. The page renders one
# table per section, and an earlier version bound only document.querySelector,
# so a click in any section after the first did nothing at all while the header
# still carried the pointer cursor, the sort arrow and the aria-sort attribute.
# Sorting is per table by design: each section is read on its own, and reordering
# one has no meaning for the others. tests/test_report_sorting.py runs this
# script in Node and fails if a later section stops sorting.
_SORT_SCRIPT = """
(function () {
  var tables = [].slice.call(document.querySelectorAll('table'));
  tables.forEach(function (table) {
  if (!table || !table.tBodies.length) { return; }
  var headers = [].slice.call(table.querySelectorAll('th.sortable'));
  function value(row, index) {
    var cell = row.children[index];
    if (!cell) { return ''; }
    var raw = cell.getAttribute('data-v');
    return raw === null ? cell.textContent : raw;
  }
  function compare(a, b, index) {
    var x = value(a, index), y = value(b, index);
    var nx = Number(x), ny = Number(y);
    if (x !== '' && y !== '' && !isNaN(nx) && !isNaN(ny)) { return nx - ny; }
    return String(x).localeCompare(String(y), undefined,
                                   { numeric: true, sensitivity: 'base' });
  }
  function apply(header, index) {
    var ascending = header.getAttribute('aria-sort') !== 'ascending';
    headers.forEach(function (other) { other.setAttribute('aria-sort', 'none'); });
    header.setAttribute('aria-sort', ascending ? 'ascending' : 'descending');
    var body = table.tBodies[0];
    var rows = [].slice.call(body.rows);
    rows.sort(function (a, b) {
      return ascending ? compare(a, b, index) : compare(b, a, index);
    });
    rows.forEach(function (row) { body.appendChild(row); });
  }
  headers.forEach(function (header, index) {
    header.addEventListener('click', function () { apply(header, index); });
    header.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        apply(header, index);
      }
    });
  });
  });
})();
"""


# The glyph is keyed on the section's COLOUR CLASS rather than on its title, so
# a heading can never end up with a glyph that disagrees with its colour. Each
# one says what the section MEANS rather than decorating it. dot-neutral is
# shared by more than one kind of section and no single honest glyph fits them
# all, so it gets none.
_SECTION_GLYPH = {
    "dot-watched": "\N{GLOWING STAR}",          # keep these, they are right
    "dot-toonew": "\N{HOURGLASS WITH FLOWING SAND}",   # judge these yourself
    "dot-tuned": "\N{WARNING SIGN}",            # these need attention
    "dot-neutral": "",
}

# The score bands. A band is a range of the match score a row carries, and the
# boundaries are stated in the section descriptions so a reader never has to
# guess what "strong" meant. Ordered high to low, and the last entry must have
# a floor of 0 so every row lands somewhere.
_SCORE_BANDS = (
    (90, "Strong matches", "dot-watched",
     "Scored 90 or above. These are the matches least likely to need a second "
     "look, and they are listed so you can confirm rather than search."),
    (60, "Worth checking", "dot-toonew",
     "Scored 60 to 89. Good enough to propose and not good enough to trust "
     "without reading. This is the section to spend your time in."),
    (0, "Weak or no match", "dot-tuned",
     "Scored below 60, or nothing was found at all. Either the lineup names "
     "this channel differently from your provider, or the channel is absent. "
     "An alias is usually the fix."),
)

# Every section is collapsed, so this line appears in each one.
_FIND_HINT = ("<p class=\"hint\">Expand this section before searching it. "
              "Find in page does not reach inside a collapsed section on some "
              "browsers.</p>")


def _section(title, count, body, dot_class, open_by_default=False):
    """One report section as a collapsible details element.

    Every section starts collapsed. The page is an index of what the run found,
    not a wall of tables, and a reader opens the part they care about.

    `count` must equal the number of rows in the table directly beneath it, in
    every section without exception. A reader looking at a collapsed page
    cannot see any distinction that would justify one section omitting it, and
    a section with no number reads as one that forgot.

    A details element needs no JavaScript, and a client that does not implement
    it renders the content expanded. The failure mode is everything visible,
    never content lost.
    """
    open_attr = " open" if open_by_default else ""
    number = "" if count is None else f' <span class="count">{int(count)}</span>'
    # Decoration on top of the coloured dot and the words, never the only thing
    # carrying the meaning, which is why it is hidden from assistive software.
    # A client with no emoji font shows a box or nothing and the heading still
    # reads correctly.
    glyph = _SECTION_GLYPH.get(dot_class, "")
    badge = f'<span class="glyph" aria-hidden="true">{glyph}</span>' if glyph else ""
    return (f'<details{open_attr}><summary>'
            f'<span class="dot {_esc(dot_class)}" aria-hidden="true"></span>'
            f'{badge}{_esc(title)}{number}</summary>{body}</details>')


def _column_index(headers, wanted):
    """The index of the first header whose text contains `wanted`, or None.

    Matched on the DISPLAYED header rather than the row key, because the four
    callers use four different keys for the same idea: Score, Best Score and
    confidence_score all display as a score.
    """
    for index, header in enumerate(headers or []):
        if wanted in str(header).strip().lower():
            return index
    return None


def _as_score(value):
    """A row's score as a float, or None when the cell holds no number.

    Total over its input on purpose. Every cell reaching here has already been
    stringified by build_model, and a provider supplied name can be anything at
    all, so this must not raise on text, on an empty cell or on None.
    """
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def group_entries(model):
    """Split the rendered rows into sections. Returns a list of section dicts.

    -> [{"title", "dot", "description", "entries"}], in display order.

    Three shapes, in order of preference:

    A score column gives score bands, which is the split that matches how the
    report is actually read: the reader wants the doubtful matches, not the
    confident ones. A row whose score cell holds no number is treated as no
    match, which is what an empty score cell means here.

    Failing that, a status column gives one section per distinct status value,
    in first-seen order so the grouping does not reorder the run's own output.

    Failing both, one section holding every row. The page still gains the
    collapse behaviour and the count, and nothing is invented.

    Every row lands in exactly one section, so the counts sum to the number of
    rows rendered. No row is dropped and no row appears twice.
    """
    headers = model.get("headers") or []
    entries = model.get("entries") or []

    score_at = _column_index(headers, "score")
    if score_at is not None:
        buckets = {floor: [] for floor, _, _, _ in _SCORE_BANDS}
        for entry in entries:
            cell = entry[score_at] if score_at < len(entry) else None
            score = _as_score(cell)
            for floor, _, _, _ in _SCORE_BANDS:
                if score is not None and score >= floor:
                    buckets[floor].append(entry)
                    break
            else:
                # No number in the cell, so no match was recorded for this row.
                buckets[_SCORE_BANDS[-1][0]].append(entry)
        return [{"title": title, "dot": dot, "description": description,
                 "entries": buckets[floor]}
                for floor, title, dot, description in _SCORE_BANDS]

    status_at = _column_index(headers, "status")
    if status_at is not None:
        order = []
        buckets = {}
        for entry in entries:
            cell = entry[status_at] if status_at < len(entry) else ""
            key = str(cell).strip() or "No status recorded"
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            buckets[key].append(entry)
        return [{"title": key, "dot": "dot-neutral",
                 "description": f"Every row this run reported as {key.lower()}.",
                 "entries": buckets[key]}
                for key in order]

    return [{"title": "Every row in this run", "dot": "dot-neutral",
             "description": "This run reported no score and no status, so the "
                            "rows are listed exactly as it produced them.",
             "entries": list(entries)}]


def _table(headers, entries):
    """One sortable table, or a line saying the section is empty."""
    if not entries:
        return "<p class=\"empty\">No rows in this section.</p>"
    head = "".join(
        "<th class=\"sortable\" aria-sort=\"none\" tabindex=\"0\" role=\"columnheader\" "
        f"title=\"Sort by {_esc(h)}\">{_esc(h)}</th>"
        for h in headers or [])
    body = "".join(
        "<tr>" + "".join(f"<td data-v=\"{_esc(cell)}\">{_esc(cell)}</td>"
                         for cell in entry) + "</tr>"
        for entry in entries)
    return ("<div class=\"scroll\"><table>"
            "<thead><tr>" + head + "</tr></thead>"
            "<tbody>" + body + "</tbody>"
            "</table></div>")


def render_html(model):
    """Render the model to one self contained HTML page.

    Rows are grouped into collapsible sections, all closed, so the page opens
    as an index of what the run found rather than as one long table. Each
    section states what it holds and what to do about it, because a collapsed
    heading is all a reader has to decide whether to open it.

    Every table is sortable by clicking or keyboard activating a column header.
    See _SORT_SCRIPT for what that does and what it deliberately does not do.
    """
    headers = model.get("headers") or []
    sections = "".join(
        _section(
            group["title"], len(group["entries"]),
            f"<p class=\"sub\">{_esc(group['description'])}</p>"
            + _FIND_HINT
            + "<div class=\"card\">" + _table(headers, group["entries"]) + "</div>",
            group["dot"])
        for group in group_entries(model))

    meta = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>"
                   for k, v in model.get("summary") or [])

    notice = truncation_notice(model)
    notice_html = f"<div class=\"notice\">{_esc(notice)}</div>\n" if notice else ""

    # An empty data URI renders no image element at all, rather than the broken
    # image icon a missing file would otherwise produce.
    logo = logo_data_uri()
    mark = (f'<img class="mark" src="{logo}" alt="" width="48" height="48">'
            if logo else "")

    title = _esc(model.get("title"))
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>Lineuparr: {title}</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f"<header class=\"masthead\">\n{mark}\n<div>\n"
        f"<h1>Lineuparr: {title}</h1>\n"
        f"<div class=\"sub\">{_esc(model.get('shown_rows', 0))} row(s) shown. "
        "Every section below is collapsed. Open the one you want.</div>\n"
        "</div>\n</header>\n"
        + notice_html +
        f"<div class=\"card\"><dl class=\"meta\">{meta}</dl></div>\n"
        f"{sections}\n"
        "<footer class=\"colophon\">\n"
        "<p>Built by Lineuparr, a lineup matcher for Dispatcharr.</p>\n"
        "<p>Click a column heading to sort by it, or focus it and press Enter. "
        "Sorting needs the page open in a browser. A mail client previewing "
        "this file shows every row but cannot reorder them.</p>\n"
        "<p>Stream names here are shown without their M3U source label, and the "
        "plugin settings that name your M3U sources are not included. The "
        f"complete export, which does include them, stays in {EXPORTS_LOCATION} "
        "inside the container and is not emailed.</p>\n"
        "<p>Emailed copies of this report are delivered courtesy of "
        f"<a href=\"{NEWSFLASHARR_URL}\">Newsflasharr</a>.</p>\n"
        f"<p><a href=\"{REPO_URL}\">Source and documentation</a> · "
        f"<a href=\"{ISSUES_URL}\">Report a problem</a></p>\n"
        "</footer>\n"
        f"<script>{_SORT_SCRIPT}</script>\n"
        "</body>\n</html>\n"
    )


# A cell beginning with any of these is evaluated as a formula by Excel,
# LibreOffice and Google Sheets when the file is opened.
_FORMULA_LEADS = ("=", "+", "-", "@")


def _csv_safe(value):
    """Prefix a formula shaped cell with an apostrophe so it stays text."""
    text = str(value if value is not None else "")
    if text[:1] in _FORMULA_LEADS:
        return "'" + text
    return text


def render_csv(model):
    """Render the model to CSV text, with the same content rules as the HTML.

    Deliberately carries no settings header. The plugin's own export in
    /data/exports does carry one, which names the configured M3U sources.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(model.get("headers") or [])
    for entry in model.get("entries") or []:
        writer.writerow([_csv_safe(cell) for cell in entry])
    return buf.getvalue()


def _atomic_write(path, text):
    """Write through a temporary file and rename, so no partial file is ever
    visible at the destination path."""
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune(dirpath, suffix, keep=KEEP_REPORTS, now=None):
    """Keep the newest `keep` report files with this suffix and delete the rest,
    except any file still young enough that a delivery retry could need it.

    Never raises: losing an old report must not fail the run that produced a new
    one.
    """
    try:
        moment = time.time() if now is None else now
        entries = [os.path.join(dirpath, name) for name in os.listdir(dirpath)
                   if name.startswith(FILENAME_PREFIX) and name.endswith(suffix)]
        entries.sort(key=os.path.getmtime, reverse=True)
        for stale in entries[keep:]:
            try:
                if moment - os.path.getmtime(stale) < RETRY_WINDOW_SECONDS:
                    continue  # a queued delivery may still re-read this path
                os.unlink(stale)
            except OSError:
                pass
    except OSError:
        pass


def write_report(model, report_dir, now):
    """Write the HTML and CSV reports and return their paths.

    Returns {"html_path", "csv_path", "error"}. Never raises: reporting is not
    the plugin's real work, and a failure here is reported rather than thrown.

    Both files carry the run's timestamp in their name and are never rewritten,
    because a delivery re-reads the path on every retry attempt.
    """
    result = {"html_path": None, "csv_path": None, "error": None}
    try:
        os.makedirs(report_dir, exist_ok=True)
        stamp = datetime.datetime.fromtimestamp(
            float(now or 0), datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = os.path.join(report_dir, f"{FILENAME_PREFIX}{stamp}")
        html_path, csv_path = base + ".html", base + ".csv"
        _atomic_write(html_path, render_html(model))
        _atomic_write(csv_path, render_csv(model))
        result["html_path"], result["csv_path"] = html_path, csv_path
        _prune(report_dir, ".html")
        _prune(report_dir, ".csv")
    except Exception as error:
        result["error"] = f"could not write the report: {error}"
    return result
