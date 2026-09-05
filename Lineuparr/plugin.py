"""
Dispatcharr Lineuparr Plugin
Mirror real-world provider channel lineups by creating groups, channels,
and fuzzy-matching IPTV streams to them.
"""

import copy
import logging
import json
import csv
import os
import re
import time
import fnmatch
import threading
from datetime import datetime, timedelta
from glob import glob

from django.db import transaction

from .fuzzy_matcher import (FuzzyMatcher, has_upgrade_quality, detect_category_country,
                            detect_name_country_prefix, country_codes_in_text)
from .aliases import CHANNEL_ALIASES, COUNTRY_ALIASES
from .progress_status import save_progress_atomic, load_progress, build_status_message
from . import notify_bridge, report_count, reports

from apps.channels.models import Channel, ChannelGroup, ChannelProfile, ChannelProfileMembership, ChannelStream, Stream
from apps.m3u.models import M3UAccount
from core.utils import send_websocket_update

# EPG models (optional - EPG matching disabled if not available)
try:
    from apps.epg.models import EPGData, EPGSource, ProgramData
    _EPG_AVAILABLE = True
except ImportError:
    _EPG_AVAILABLE = False

# Logo model (optional - logo assignment disabled if not available)
try:
    from apps.channels.models import Logo
    _LOGO_AVAILABLE = True
except ImportError:
    _LOGO_AVAILABLE = False

LOGGER = logging.getLogger("plugins.lineuparr")
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    LOGGER.addHandler(_handler)
LOGGER.setLevel(logging.DEBUG)
LOG_PREFIX = "[Lineuparr]"


def result_text(result, default=""):
    """The human-readable text of an action result, whichever key carries it.

    Dispatcharr renders `error` in red and persistently, and `message` as a
    green toast that closes itself, so a failure puts its reason under `error`
    and a success puts its summary under `message`. Nothing sets both. Anything
    that consumes another action's result therefore has to look in both places,
    and reading only `message` would raise a KeyError at the exact moment a
    step failed, which is the moment the text is most needed.

    `error` wins when both are somehow present, because a failure the operator
    must see outranks a summary. An empty string counts as absent.
    """
    if not isinstance(result, dict):
        return default
    return result.get("error") or result.get("message") or default


def action_result(status, text, **extra):
    """Build an action result whose text lands under the key that gets rendered.

    An action whose status is decided at run time, from how much of the work
    succeeded, cannot know at the point of writing which key to use. Building
    the dict by hand always picked `message`, so a failure was shown as a green
    toast that closed itself after about four seconds.

    A warning is deliberately not red: the run happened and produced a partial
    result, so it reads as a summary rather than as a refusal.
    """
    key = "error" if status == "error" else "message"
    return {"status": status, key: text, **extra}


def _yes_no(value, default=False):
    """Render a setting as Yes or No for a person, not as a Python boolean.

    Dispatcharr stores some booleans as the strings "true" and "false", so the
    same setting can arrive as either type. Both have to read the same way in a
    report, and neither should reach the reader as True or False.
    """
    if value is None:
        value = default
    if isinstance(value, str):
        value = value.strip().lower() in ("true", "yes", "1", "on")
    return "Yes" if value else "No"


def _setting_yes_no(settings, key, default):
    """Render a setting the way the RUN resolved it, not the way it was declared.

    The runtime reads `settings.get(key, default)`, and a dict default applies
    only to an ABSENT key. A setting stored as None therefore reaches the run as
    None and is falsy, so the feature is off. Passing that None to `_yes_no`
    substituted the declared default instead and reported the feature as on, so
    the record disagreed with the run in exactly the case a reader is trying to
    explain. Resolving here with the same call the runtime makes, against the
    same default constant, removes both that gap and the chance of the two
    copies of a default drifting apart.
    """
    return _yes_no((settings or {}).get(key, default), default=False)


# Characters that end a line for something reading this file. str.splitlines
# also breaks on U+2028 and U+2029, so a value carrying one would escape the
# comment block even though it is not a newline. They are built from codepoints
# rather than typed, because a literal one is invisible in review.
_LINE_BREAKS = ("\r\n", "\n", "\r", chr(0x2028), chr(0x2029), chr(0x0085))


def _one_line(text):
    """Flatten a value so it cannot end a commented preamble line early.

    A stored setting is free text. A line break inside one produced a line with
    no leading hash, which a spreadsheet told to skip comment lines reads as the
    header row, misaligning every column of the import.
    """
    text = str(text)
    for ch in _LINE_BREAKS:
        text = text.replace(ch, " ")
    return text


# BOM + zero-width characters that sneak in from rich-text paste (editors,
# Slack/Discord, web docs). Python's str.strip() treats none of these as
# whitespace, so they survive `.strip()` and trip json.loads with
# "Expecting value: line 1 column 1 (char 0)".
_INVISIBLE_CHARS = "\ufeff\u200b\u200c\u200d\u2060"


def _clean_json_text(s):
    """Strip whitespace and invisible paste artifacts from a JSON blob."""
    if not s:
        return ""
    return s.strip().strip(_INVISIBLE_CHARS).strip()


class PluginConfig:
    PLUGIN_VERSION = "1.26.2481702"

    DEFAULT_FUZZY_MATCH_THRESHOLD = 80
    DEFAULT_PRIORITIZE_QUALITY = True
    DEFAULT_QUALITY_AWARE_STREAM_MATCHING = False
    DEFAULT_RATE_LIMITING = "none"
    DEFAULT_CHANNEL_NUMBERING = "lineup"

    RATE_LIMIT_NONE = 0.0
    RATE_LIMIT_LOW = 0.1
    RATE_LIMIT_MEDIUM = 0.5
    RATE_LIMIT_HIGH = 2.0

    # ETA estimation constants (seconds per item)
    ESTIMATED_SECONDS_PER_CHANNEL_SYNC = 0.05
    ESTIMATED_SECONDS_PER_STREAM_MATCH = 0.5
    ESTIMATED_SECONDS_PER_EPG_MATCH = 0.1

    ESTIMATED_SECONDS_PER_LOGO_MATCH = 0.05

    # Map ISO country codes to tv-logo/tv-logos directory names
    COUNTRY_DIR_MAP = {
        "US": "united-states",
        "CA": "canada",
        "GB": "united-kingdom",
        "AU": "australia",
        "DE": "germany",
        "FR": "france",
        "IT": "italy",
        "ES": "spain",
        "MX": "mexico",
        "BR": "brazil",
        "IN": "india",
        "IE": "ireland",
    }

    TV_LOGOS_REPO = "tv-logo/tv-logos"
    TV_LOGOS_BRANCH = "main"

    DATA_DIR = "/data"
    EXPORTS_DIR = "/data/exports"
    # The exports directory is SHARED with at least six other plugins, so
    # anything that deletes from it selects on this plugin's own filename
    # prefix AND the .csv suffix. Every CSV this plugin writes is named
    # lineuparr_<what>_<timestamp>.csv.
    CSV_EXPORT_PREFIX = "lineuparr_"
    CSV_EXPORT_SUFFIX = ".csv"
    SECONDS_PER_DAY = 86400.0
    # Append-only tally of channels this plugin has created, one JSON object per
    # run. Nothing else can be added up into a lifetime total: the channels it
    # creates are ordinary Dispatcharr rows, indistinguishable afterwards from
    # ones made by hand or by another plugin, and the unmatched-channel cleanup
    # deletes some of them again. A cumulative number has to be recorded as it
    # happens or it cannot be recovered.
    CHANNEL_COUNT_LEDGER_FILE = "/data/lineuparr_channel_counts.jsonl"
    STATE_FILE = "/data/lineuparr_state.json"
    PROGRESS_FILE = "/data/lineuparr_progress.json"
    OPERATION_LOCK_FILE = "/data/lineuparr_operation.lock"
    OPERATION_LOCK_TIMEOUT_MINUTES = 10

    # Simplified category mapping: simplified name -> list of original categories to merge
    SIMPLIFIED_CATEGORIES = {
        "News & Info": ["News", "Discovery", "Crime"],
        "Sports & Outdoors": ["Sports", "Regional Sports", "Outdoors"],
        "Entertainment & Lifestyle": ["Entertainment", "Comedy", "Reality & Lifestyle", "Food & Travel"],
        "Movies": ["Movies"],
        "Kids & Family": ["Kids", "Faith"],
        "Foreign Language": ["Spanish", "French"],
        "Music & Shopping": ["Music", "Shopping"],
    }

    REFINED_CATEGORIES = {
        "News": ["News"],
        "Sports": ["Sports", "Regional Sports"],
        "Movies": ["Movies"],
        "Family": ["Kids", "Faith"],
        "Foreign": ["Spanish", "French"],
        "Entertainment": ["Entertainment", "Reality & Lifestyle", "Comedy", "Discovery", "Crime", "Music", "Shopping", "Outdoors", "Food & Travel"],
    }

    CHANNEL_QUALITY_TAG_ORDER = ["[4K]", "[UHD]", "[FHD]", "[HD]", "[SD]", "[Unknown]", "[Slow]", ""]
    STREAM_QUALITY_ORDER = [
        "[4K]", "(4K)", "4K",
        "[UHD]", "(UHD)", "UHD",
        "[FHD]", "(FHD)", "FHD",
        "[HD]", "(HD)", "HD", "(H)",
        "[SD]", "(SD)", "SD",
        "(F)", "(D)",
        "Slow", "[Slow]", "(Slow)"
    ]


class ProgressTracker:
    """Tracks operation progress with periodic logging."""

    def __init__(self, total_items, action_id, logger):
        self.total_items = max(total_items, 1)
        self.action_id = action_id
        self.logger = logger
        self.start_time = time.time()
        self.last_update_time = self.start_time
        # Adaptive interval: shorter for smaller jobs so they still show progress
        self.update_interval = 3 if total_items <= 50 else 5 if total_items <= 200 else 10
        self.processed_items = 0
        logger.info(f"{LOG_PREFIX} [{action_id}] Starting: {total_items} items to process")
        send_websocket_update('updates', 'update', {
            "type": "plugin", "plugin": "Lineuparr",
            "message": f"🔄 {action_id}: Starting ({total_items} items)"
        })
        self._publish("running")

    def update(self, items_processed=1):
        self.processed_items += items_processed
        now = time.time()
        if now - self.last_update_time >= self.update_interval:
            self.last_update_time = now
            elapsed = now - self.start_time
            pct = (self.processed_items / self.total_items) * 100
            remaining = (elapsed / self.processed_items) * (self.total_items - self.processed_items) if self.processed_items > 0 else 0
            eta_str = self._format_eta(remaining)
            self.logger.info(f"{LOG_PREFIX} [{self.action_id}] {pct:.0f}% ({self.processed_items}/{self.total_items}) - ETA: {eta_str}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": f"🔄 {self.action_id}: {pct:.0f}% ({self.processed_items}/{self.total_items}) - ⏱️ ETA: {eta_str}"
            })
            self._publish("running")

    def finish(self, summary=None):
        elapsed = time.time() - self.start_time
        eta_str = self._format_eta(elapsed)
        self.logger.info(f"{LOG_PREFIX} [{self.action_id}] Complete: {self.processed_items}/{self.total_items} in {eta_str}")
        send_websocket_update('updates', 'update', {
            "type": "plugin", "plugin": "Lineuparr",
            "message": f"✅ {self.action_id}: Complete ({self.processed_items}/{self.total_items}) in {eta_str}"
        })
        self._publish("done", summary=summary)

    def _publish(self, status, summary=None):
        """Write the progress state file (best-effort - a write failure is
        logged but never interrupts the operation)."""
        record = {
            "status": status,
            "action": self.action_id,
            "current": self.processed_items,
            "total": self.total_items,
            "start_time": self.start_time,
            "updated_at": time.time(),
        }
        if status == "done":
            record["finished_at"] = record["updated_at"]
            if summary:
                record["summary"] = summary
        try:
            save_progress_atomic(PluginConfig.PROGRESS_FILE, record)
        except Exception as e:
            self.logger.warning(f"{LOG_PREFIX} Could not write progress file: {e}")

    def _format_eta(self, seconds):
        return ProgressTracker._format_eta_static(seconds)

    @staticmethod
    def _format_eta_static(seconds):
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}h {m}m"


class SmartRateLimiter:
    """Rate limiting for DB operations."""

    DELAYS = {
        "none": PluginConfig.RATE_LIMIT_NONE,
        "low": PluginConfig.RATE_LIMIT_LOW,
        "medium": PluginConfig.RATE_LIMIT_MEDIUM,
        "high": PluginConfig.RATE_LIMIT_HIGH,
    }

    def __init__(self, setting_value="none"):
        self.delay = self.DELAYS.get(setting_value, 0.0)

    def wait(self):
        if self.delay > 0:
            time.sleep(self.delay)


class Plugin:
    name = "Lineuparr"
    version = PluginConfig.PLUGIN_VERSION

    def __init__(self):
        self._thread = None
        self._thread_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._lineup_cache = None
        self._lineup_cache_file = None
        # Set True while a multi-step operation (Full Sync) runs so its
        # sub-steps don't each fire a channel-list refresh; the parent
        # operation fires exactly one refresh when it finishes.
        self._suppress_refresh = False

    def _try_start_thread(self, target, args):
        """Atomically check if a thread is running and start a new one.
        Returns True if started, False if another operation is running."""
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(target=target, args=args, daemon=True)
            self._thread.start()
            return True

    @property
    def fields(self):
        """Dynamically generate field definitions with current options."""
        # Discover lineup files
        plugin_dir = os.path.dirname(__file__)
        lineup_options = []
        lineup_metadata = {}
        for f in sorted(glob(os.path.join(plugin_dir, "*_lineup.json"))):
            fname = os.path.basename(f)
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data.get("categories"), dict):
                    chans = sum(len(v) for v in data["categories"].values())
                    date_str = data.get("date", "")
                    cc, provider = self._parse_lineup_filename(fname)
                    if cc and provider:
                        base = f"{provider.replace('-', ' ')} ({cc}) - {chans} channels"
                    else:
                        base = f"{data.get('package', fname)} ({chans} channels)"
                    label = f"{base} - {date_str}" if date_str else base
                    lineup_options.append({"value": fname, "label": label})
                    lineup_metadata[fname] = {
                        "description": data.get("description", ""),
                    }
            except Exception:
                pass
        # Show the dropdown alphabetically by its visible label
        lineup_options.sort(key=lambda o: o["label"].lower())
        if not lineup_options:
            lineup_options = [{"value": "_none", "label": "No lineup files found"}]

        # Discover M3U sources
        m3u_options = [{"value": "_all", "label": "All sources"}]
        try:
            for acc in M3UAccount.objects.filter(is_active=True).values('id', 'name'):
                m3u_options.append({"value": acc['name'], "label": acc['name']})
        except Exception:
            pass
        # Alphabetize discovered sources, keeping "All sources" pinned first
        m3u_options[1:] = sorted(m3u_options[1:], key=lambda o: o["label"].lower())

        # Discover EPG sources
        epg_source_options = [{"value": "_all", "label": "All EPG sources"}]
        if _EPG_AVAILABLE:
            try:
                for src in EPGSource.objects.all().values('id', 'name').order_by('name'):
                    epg_source_options.append({"value": src['name'], "label": src['name']})
            except Exception:
                pass
        # Alphabetize (case-insensitive), keeping "All EPG sources" pinned first
        epg_source_options[1:] = sorted(epg_source_options[1:], key=lambda o: o["label"].lower())
        # Readable list of discovered EPG source names for the free-text field's
        # help (the field is no longer a single-select, so the dropdown options
        # are surfaced here instead).
        epg_source_names = [o["label"] for o in epg_source_options[1:]]
        epg_sources_hint = ", ".join(epg_source_names) if epg_source_names else "(none discovered)"

        # Discover channel profiles
        profile_options = [{"value": "_none", "label": "None (don't enable in profiles)"}]
        try:
            for p in ChannelProfile.objects.all().values('id', 'name'):
                profile_options.append({"value": p['name'], "label": p['name']})
        except Exception:
            pass
        # Alphabetize discovered profiles, keeping "None" pinned first
        profile_options[1:] = sorted(profile_options[1:], key=lambda o: o["label"].lower())

        return [
            # --- Section: Lineup & Sources ---
            {
                "id": "_sec_sources",
                "type": "info",
                "label": "Lineup & Sources",
                "help_text": "The provider lineup this plugin mirrors, and where the streams and guide data come from. The lineup file decides more than which channels get created: the two-letter country code at the start of its filename is what the country filter compares a stream against, so picking a lineup from another country also changes which of your streams are eligible to match at all.",
            },
            {
                "id": "lineup_file",
                "label": "Lineup File",
                "type": "select",
                "default": "US_DirecTV-Premier_lineup.json",
                "options": lineup_options,
                "help_text": "Select a provider channel lineup to mirror. Channels, groups, and numbering are based on this file.",
            },
            {
                "id": "m3u_sources",
                "label": "M3U Source",
                "type": "select",
                "default": "_all",
                "options": m3u_options,
                "help_text": "Which M3U source to match streams from. 'All' uses every available source.",
            },
            {
                "id": "epg_sources",
                "label": "EPG Sources for Matching",
                "type": "string",
                "default": "",
                "placeholder": "blank = All  |  UK*, EPG Share  |  Jessman",
                "help_text": (
                    "Which EPG source(s) to match against. Leave blank for All (every source, "
                    "ordered by the priority configured in Dispatcharr). Otherwise list one or "
                    "more source names separated by commas; shell wildcards * and ? are allowed "
                    "(e.g. 'UK*' matches every source whose name starts with UK). Earlier names "
                    "win on priority. Available sources: " + epg_sources_hint
                ),
            },
            {
                "id": "channel_profiles",
                "label": "Channel Profile",
                "type": "select",
                "default": "_none",
                "options": profile_options,
                "help_text": "Automatically enable matched channels in this profile after sync.",
            },
            # --- Section: Channel Groups & Numbering ---
            {
                "id": "_sec_groups",
                "type": "info",
                "label": "Channel Groups & Numbering",
                "help_text": "The names of the channel groups this plugin creates, and the number each new channel gets. The default numbering takes each number from the lineup file itself, which is why two lineups can disagree about what channel 206 is; the two auto modes ignore the file's numbers and fill the first free slots instead. Starting Channel Number is read only by the Use specific number mode and is ignored by every other one, including the two auto modes.",
            },
            {
                "id": "group_prefix",
                "label": "Channel Group Prefix",
                "type": "string",
                "default": "",
                "placeholder": "blank = auto  |  none = no prefix  |  e.g. US ",
                "help_text": "Blank = auto from lineup name. 'none' = no prefix. Add trailing separator to control format (e.g. 'US ' or 'DTV-').",
            },
            {
                "id": "category_detail",
                "label": "Category Detail",
                "type": "select",
                "default": "normal",
                "options": [
                    {"value": "none", "label": "None - single group (prefix only, no categories)"},
                    {"value": "refined", "label": "Refined - 6 broad categories (News, Sports, Movies, Family, Foreign, Entertainment)"},
                    {"value": "simple", "label": "Simple - 7 merged categories (News & Info, Sports & Outdoors, etc.)"},
                    {"value": "normal", "label": "Normal - all individual categories"},
                ],
                "help_text": "Controls how lineup categories are grouped. Refined merges into 6, Simple into 7, Normal keeps all original categories.",
            },
            {
                "id": "channel_numbering",
                "label": "Channel Numbering",
                "type": "select",
                "default": "lineup",
                # Built from NUMBERING_LABELS so the form, the CSV preamble
                # and Validate Settings cannot name the same mode differently.
                "options": [{"value": value, "label": label}
                            for value, label in self.NUMBERING_LABELS.items()],
                "help_text": "How each new channel gets its number. The default takes the number recorded in the lineup file, which is the provider's own number, and sets no number at all when the file has none for that channel. The two auto modes ignore the file and fill free slots, either from 1 upwards or starting above your highest existing channel. Use specific number counts up from Starting Channel Number below.",
            },
            {
                "id": "starting_channel_number",
                "label": "Starting Channel Number (Specific mode only)",
                "type": "string",
                "default": "",
                "placeholder": "e.g. 1000",
                "help_text": "The first number to use when Channel Numbering is set to 'Use specific number'. Channels are numbered up from this value, skipping any number already taken by an existing channel. Every other numbering mode ignores this setting.",
            },
            # --- Section: Stream & EPG Matching ---
            {
                "id": "_sec_matching",
                "type": "info",
                "label": "Stream & EPG Matching",
                "help_text": "How closely a stream or guide name has to resemble a lineup channel before it counts as a match, and what happens to the streams a channel already has. Preserve Existing Streams is the setting with the largest consequence here: with it off, Apply Stream Match and Full Sync replace each channel's whole stream list rather than adding to it, and then delete any channel in this plugin's groups that finished the run with no streams. Turn it on to add a second source without losing anything.",
            },
            {
                "id": "match_sensitivity",
                "label": "Match Sensitivity",
                "type": "select",
                "default": "normal",
                "options": [
                    {"value": "relaxed", "label": "Relaxed (70) - more matches, more false positives"},
                    {"value": "normal", "label": "Normal (80) - balanced"},
                    {"value": "strict", "label": "Strict (90) - fewer matches, high confidence"},
                    {"value": "exact", "label": "Exact (95) - near-exact matches only"},
                ],
                "help_text": "How closely a stream or guide name must resemble a lineup channel name before it counts as a match. The number in each option is the score out of 100 a name has to reach, so a higher setting is stricter: fewer channels get a match, and fewer get the wrong one. This applies to stream matching and EPG matching alike.",
            },
            {
                "id": "refresh_epg_after_match",
                "label": "Refresh EPG After Matching",
                "type": "boolean",
                "default": True,
                "help_text": "After EPG matching, trigger a Dispatcharr EPG refresh so newly matched channels get program info. Only the sources listed in 'EPG Sources for Matching' that were actually matched to lineup channels are refreshed.",
            },
            {
                "id": "prioritize_quality",
                "label": "Order Matched Streams by Quality",
                "type": "boolean",
                "default": True,
                "help_text": "Sort attached streams by quality (4K > UHD > FHD > HD > SD). Uses probed resolution if available.",
            },
            {
                "id": "quality_aware_stream_matching",
                "label": "Quality-Aware Stream Matching",
                "type": "boolean",
                "default": False,
                "help_text": (
                    "When on, if the lineup contains both TF1 and TF1 UHD, each is restricted "
                    "to streams of its own quality tier: TF1 only matches standard streams, "
                    "TF1 UHD only matches upgrade streams (4K/8K/UHD/HDR). "
                    "Channels with no twin in the lineup are unaffected. "
                    "Streams listed in a channel's aliases bypass this filter."
                ),
            },
            {
                "id": "preserve_existing_streams",
                "label": "Preserve Existing Streams",
                "type": "boolean",
                "default": False,
                "help_text": "When on, newly matched streams are appended to channels without deleting existing streams, duplicates are skipped, and unmatched channels are not deleted. Use this to add a second M3U source non-destructively.",
            },
            {
                "id": "single_channel_name",
                "label": "Single Channel Match",
                "type": "string",
                "default": "",
                "placeholder": "e.g. CNN  (blank = whole lineup)",
                "help_text": "When set, Preview Stream Match, Apply Stream Match, Apply EPG Match, and Assign Logos operate ONLY on the lineup channel(s) whose name equals this value (case-insensitive). Leave blank to process the whole lineup. Full Sync ignores this setting.",
            },
            {
                "id": "custom_aliases",
                "label": "Custom Channel Aliases (advanced)",
                "type": "text",
                "default": "",
                "placeholder": "{\"Channel Name\": [\"alias 1\", \"alias 2\"]}",
                "help_text": "JSON object mapping a lineup channel name to extra alias names (a bare string is accepted as a single alias). Leave blank to use built-in aliases only.",
            },
            # --- Section: Notifications ---
            {
                "id": "_sec_notify",
                "type": "info",
                "label": "Report delivery",
                "help_text": "Every run that exports a CSV also writes a shareable HTML report with a sortable table. Newsflasharr can email it. This is off by default and needs the Newsflasharr plugin installed, enabled, and routing this plugin's usage_report event to your email channel.",
            },
            {
                "id": "notify_enabled",
                "label": "Send reports to Newsflasharr",
                "type": "boolean",
                "default": False,
                "help_text": "Master switch. When off, reports are still written to disk and nothing is sent.",
            },
            {
                "id": "notify_report_on",
                "label": "When to send",
                "type": "select",
                "default": "never",
                "options": [
                    {"value": "never", "label": "Never - write the report, send nothing"},
                    {"value": "every_run", "label": "Every run that produces a report"},
                ],
                "help_text": "Applies to the channel sync preview, preview stream match, apply stream match, and EPG match.",
            },
            {
                "id": "notify_report_format",
                "label": "What to attach",
                "type": "select",
                "default": "both",
                "options": [
                    {"value": "both", "label": "Both the HTML page and the CSV"},
                    {"value": "html", "label": "HTML page only"},
                    {"value": "csv", "label": "CSV only"},
                ],
                "help_text": "A notification carries one attachment, so choosing both sends two emails per run.",
            },
            # --- Section: Advanced ---
            {
                "id": "_sec_advanced",
                "type": "info",
                "label": "Advanced",
                "help_text": "How fast a run is allowed to write to the database, and how long its CSV exports are kept. Most setups can leave both alone. Rate Limiting pauses between database writes rather than between network calls, so it helps when a large sync makes the rest of Dispatcharr sluggish and does nothing for a slow provider. The export cleanup only ever deletes this plugin's own lineuparr_*.csv files, because /data/exports is shared with several other plugins.",
            },
            {
                "id": "rate_limiting",
                "label": "Rate Limiting",
                "type": "select",
                "default": "none",
                "options": [
                    {"value": "none", "label": "None - no pause, fastest"},
                    {"value": "low", "label": "Low - pause 0.1s per channel"},
                    {"value": "medium", "label": "Medium - pause 0.5s per channel"},
                    {"value": "high", "label": "High - pause 2s per channel, slowest"},
                ],
                "help_text": "Pauses for this long after each channel a run processes, to leave the database free for the rest of Dispatcharr. Use it only if a large sync makes the interface unresponsive. The pause is between database writes, not between requests to your provider, so it does nothing for a slow M3U source. At High, a 500 channel lineup takes about 17 minutes longer.",
            },
            {
                "id": "csv_retention_days",
                "label": "Delete CSV Exports Older Than (Days)",
                "type": "number",
                "default": 0,
                # A negative value silently means the same as 0, so do not offer
                # one. A fractional value cannot be read as days at all and is
                # reported in the log rather than passing unnoticed.
                "min": 0,
                "step": 1,
                "help_text": "Housekeeping for the CSV files this plugin writes to /data/exports/. After each export, its own exports older than this many days are deleted. 0 keeps every file, which is the default, so nothing is removed unless you ask for it. The file just written is never deleted, and at least one file always survives. Only files named lineuparr_*.csv are touched, because that directory is shared with other plugins. A whole number of days is required; anything else keeps every file and says so in the container log. Note that an HTML report which had to drop rows names its full CSV by filename, so setting this shorter than the age of the reports people still open leaves those reports pointing at a deleted file. Clear CSV Exports still clears everything and ignores this setting.",
            },
        ]

    def run(self, action, params, context):
        logger = context.get("logger", LOGGER)
        settings = context.get("settings", {})

        try:
            action_map = {
                "validate_settings": self._validate_settings,
                "plugin_status": self._plugin_status,
                "scan_lineups": self._scan_lineups,
                "preview_stream_match": self._preview_stream_match,
                "full_sync": self._full_sync,
                "sync_channels": self._sync_channels,
                "apply_stream_match": self._apply_stream_match,
                "apply_epg_match": self._apply_epg_match,
                "apply_logo_match": self._apply_logo_match,
                "resort_streams": self._resort_streams,
                "clear_csv_exports": self._clear_csv_exports,
                "email_report": self._email_report,
                # Legacy actions (no UI buttons, kept for API compatibility)
                "preview_groups": self._preview_groups,
                "sync_groups": self._sync_groups,
                "preview_channels": self._preview_channels,
            }

            handler = action_map.get(action)
            if not handler:
                logger.warning(f"{LOG_PREFIX} Unknown action: {action}")
                return {"status": "error", "error": f"Unknown action: {action}"}

            logger.info(f"{LOG_PREFIX} ▶ Action triggered: {action}")
            result = handler(settings, logger)
            status = result.get("status", "?") if isinstance(result, dict) else "ok"
            msg = result_text(result)[:200]
            is_bg = result.get("background", False) if isinstance(result, dict) else False
            logger.info(f"{LOG_PREFIX} ◀ Action complete: {action} → {status} | {msg}")

            # Send GUI notification for non-background actions
            # (background actions send their own notifications on completion)
            if not is_bg:
                emoji = "✅" if status == "ok" else "❌"
                # Build detailed notification
                notify_msg = msg.split("\n")[0] if msg else action
                # Include data summary if available
                data = result.get("data", []) if isinstance(result, dict) else []
                if data and isinstance(data, list):
                    notify_msg += f" ({len(data)} items)"
                send_websocket_update('updates', 'update', {
                    "type": "plugin", "plugin": "Lineuparr",
                    "message": f"{emoji} {notify_msg}"
                })

            return result

        except Exception as e:
            logger.exception(f"{LOG_PREFIX} Error in action '{action}': {e}")
            return {"status": "error", "error": f"Internal error: {str(e)}"}

    def stop(self, context):
        logger = context.get("logger", LOGGER)
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info(f"{LOG_PREFIX} Plugin stopped.")

    def _plugin_status(self, settings, logger):
        """Report current/last operation status from the progress file, so
        the user can check progress on demand without reading the logs."""
        progress = load_progress(PluginConfig.PROGRESS_FILE)
        message = build_status_message(progress)
        logger.info(f"{LOG_PREFIX} Status requested: {message.splitlines()[0]}")
        return {"status": "ok", "message": message}

    # ========================================================================
    # HELPERS
    # ========================================================================

    def _load_lineup(self, settings, logger):
        """Load and parse the selected lineup JSON file."""
        lineup_file = settings.get("lineup_file", "")
        plugin_dir = os.path.realpath(os.path.dirname(__file__))
        filepath = os.path.realpath(os.path.join(plugin_dir, lineup_file))

        # Prevent path traversal
        if not filepath.startswith(plugin_dir + os.sep):
            raise ValueError(f"Lineup file path escapes plugin directory: {lineup_file}")

        detail = settings.get("category_detail", "normal")
        cache_key = f"{filepath}:{detail}"
        if self._lineup_cache and self._lineup_cache_file == cache_key:
            return self._lineup_cache

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Lineup file not found: {lineup_file}")

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if "categories" not in data:
            raise ValueError(f"Invalid lineup file: missing 'categories' key")

        self._rewrite_country_prefixes(data, logger)

        # Apply category detail level transformation
        data = self._apply_category_detail(data, detail)

        self._lineup_cache = data
        self._lineup_cache_file = cache_key
        logger.info(f"{LOG_PREFIX} Loaded lineup: {data.get('package', 'Unknown')} ({sum(len(v) for v in data['categories'].values())} channels, {len(data['categories'])} categories, detail={detail})")
        return data

    @staticmethod
    def _rewrite_country_prefixes(data, logger):
        """Turn a "CC_" channel-name prefix into the channel's own country.

        "UK_CNN" inside a US lineup becomes name "CNN" with country UK. Done here,
        once, before anything else sees the lineup: the name is used for channel
        creation, for matching, for reports and for EPG matching, so a prefix left
        in place would have to be stripped correctly in several unrelated places.

        Mutates data in place. Runs before _apply_category_detail so every detail
        level sees the rewritten entries.
        """
        rewritten = 0
        for channels in data.get("categories", {}).values():
            if not isinstance(channels, list):
                continue
            for entry in channels:
                if not isinstance(entry, dict):
                    continue
                country, stripped = detect_name_country_prefix(entry.get("name"))
                if not country:
                    continue
                entry["name"] = stripped
                entry["_country"] = country
                rewritten += 1

        if rewritten:
            logger.info(
                f"{LOG_PREFIX} {rewritten} lineup "
                f"{'channel carries' if rewritten == 1 else 'channels carry'} a "
                f"country prefix in the name"
            )

    @staticmethod
    def _resolve_entry_country(entry, category_cc, logger=None):
        """Country code to enforce for ONE channel.

        Most specific first: the channel's own country (from its name prefix),
        then the category's, then the lineup filename's, which category_cc already
        carries. Mirrors _resolve_category_country one level down.

        The override is logged for the same reason the category-level one is: a
        channel that does not match is otherwise guesswork.
        """
        entry_cc = entry.get("_country") if isinstance(entry, dict) else None
        if not entry_cc:
            return category_cc
        if logger is not None and entry_cc != category_cc:
            logger.info(
                f"{LOG_PREFIX} Channel '{entry.get('name')}' country: {entry_cc} "
                f"(from name prefix, overriding {category_cc or 'lineup default'})"
            )
        return entry_cc

    def _apply_category_detail(self, data, detail):
        """Transform lineup categories based on the detail level setting.
        Always returns a deep copy so callers cannot mutate the cache."""
        result = copy.deepcopy(data)

        if detail == "normal":
            return result

        original_cats = result["categories"]

        if detail == "none":
            # Merge all channels into a single flat list (no category breakdown)
            all_channels = []
            for channels in original_cats.values():
                all_channels.extend(channels)
            result["categories"] = {"All": all_channels}
            return result

        if detail in ("simple", "refined"):
            # Merge into simplified/refined categories
            cat_map = PluginConfig.REFINED_CATEGORIES if detail == "refined" else PluginConfig.SIMPLIFIED_CATEGORIES
            merged_cats = {}
            mapped_originals = set()
            for group_name, originals in cat_map.items():
                merged = []
                for orig in originals:
                    if orig in original_cats:
                        merged.extend(original_cats[orig])
                        mapped_originals.add(orig)
                if merged:
                    merged_cats[group_name] = merged
            # Fold any category not covered by the map into the broad
            # Entertainment catch-all, so Refined/Simple yield only their
            # documented fixed set of groups. A lineup with non-standard
            # categories (e.g. UK lineups carrying Adult / Radio /
            # International / HD) must not leak those through as their own
            # groups -- that defeats the whole point of Refined/Simple.
            catchall = "Entertainment" if detail == "refined" else "Entertainment & Lifestyle"
            for orig_name, channels in original_cats.items():
                if orig_name not in mapped_originals and channels:
                    merged_cats.setdefault(catchall, []).extend(channels)
            result["categories"] = merged_cats
            return result

        # Unknown detail value - treat as normal
        return result

    def _parse_lineup_filename(self, filename):
        """Extract country code and provider from lineup filename.
        Format: {CC}_{Provider}_lineup.json
        Returns (country_code, provider) or (None, None) if format doesn't match."""
        match = re.match(r'^([A-Z]{2})_(.+)_lineup\.json$', filename)
        if match:
            return match.group(1), match.group(2)
        return None, None

    @staticmethod
    def _resolve_category_country(category_name, lineup_cc, logger=None):
        """Country code to enforce for a category's channels.

        Mixed-country lineups (filename like "AU-NZ-UK_Test_Mixed_lineup.json")
        do not match the single {CC}_ filename pattern, so lineup_cc is None and
        the country filter would otherwise be disabled, letting foreign streams
        (e.g. a Canadian beIN feed) attach to an Australian channel as backups.
        For those lineups the country is encoded in the category name
        ("AU| AUSTRALIA VIP"). Prefer the per-category code when present; fall
        back to the lineup-level code for ordinary single-country lineups whose
        categories are plain themes ("News", "Sports").

        When a logger is supplied and the category overrides the lineup code,
        the override is logged (so the three matching loops stay consistent).
        """
        cat_cc = detect_category_country(category_name) or lineup_cc
        if logger is not None and cat_cc and cat_cc != lineup_cc:
            logger.info(f"{LOG_PREFIX} Category '{category_name}' country: {cat_cc} (from category name)")
        return cat_cc

    @staticmethod
    def _parse_source_filter(raw):
        """Tokenize a comma-separated source filter into (tokens, is_all).

        Tokens are comma-split and individually stripped (each may carry shell
        wildcards). is_all is True when the filter means "every source": a blank
        value, or any token equal to "_all", "all", or "*". Single source of
        truth for the EPG-source "all" test used across filtering, validation,
        and the CSV header.
        """
        tokens = [t.strip() for t in (raw or "").split(",") if t.strip()]
        is_all = (not tokens) or any(t.lower() in ("_all", "all", "*") for t in tokens)
        return tokens, is_all

    @staticmethod
    def _extract_epg_country(tvg_id):
        """Extract 2-letter country code from a tvg_id like 'CNN.us' or 'CNN.US_source1'.
        Returns lowercase country code or None."""
        if not tvg_id:
            return None
        m = re.match(r'^.+\.([a-zA-Z]{2})(?:_.*)?$', tvg_id)
        return m.group(1).lower() if m else None

    @staticmethod
    def _pick_epg_by_country(entries, country_code):
        """From a list of EPG entries for the same name, prefer the one matching country_code.
        Falls back to first entry if no country match found."""
        if not entries:
            return None
        if not country_code:
            return entries[0]
        cc = country_code.lower()
        for e in entries:
            epg_cc = Plugin._extract_epg_country(e.get('tvg_id', ''))
            if epg_cc == cc:
                return e
        return entries[0]

    def _get_group_prefix(self, settings, lineup_data):
        """Get the group prefix (user override or auto from lineup package name).
        'none' = no prefix (category names only). Blank = auto from package.
        Trailing separators preserved so 'US ' gives 'US News', not 'US: News'."""
        prefix = (settings.get("group_prefix") or "").lstrip()
        if prefix.strip().lower() == "none":
            return ""
        if prefix.strip():
            return prefix
        package = lineup_data.get("package", "")
        if package:
            # "DIRECTV Premier" -> "DIRECTV"
            return package.split()[0] if package else ""
        return ""

    @staticmethod
    def _parse_channel_number(raw):
        """Parse a channel number, handling ranges like '923-946' by taking the first number."""
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        # Handle ranges like "923-946" -> 923
        if '-' in s:
            first = s.split('-', 1)[0].strip()
            if first.isdigit():
                return int(first)
        # Handle plain integers
        if s.isdigit():
            return int(s)
        # Return as-is for other formats (let Django handle/reject it)
        return raw

    @staticmethod
    def _resolve_numbering_mode(settings):
        """Resolve the effective numbering mode, with backwards compatibility."""
        mode = settings.get("channel_numbering", PluginConfig.DEFAULT_CHANNEL_NUMBERING)
        if settings.get("ignore_channel_numbers") is True:
            mode = "auto_next"
        return mode

    def _get_channel_number(self, settings, entry, assigner_state):
        """Get the channel number for a lineup entry based on the channel_numbering setting.

        assigner_state is a dict with 'next' key tracking the next number to assign.
        It must be initialized before the first call using _init_assigner_state().
        """
        mode = self._resolve_numbering_mode(settings)

        if mode == "lineup":
            return self._parse_channel_number(entry.get("number"))

        # For auto/specific modes, assign sequentially and skip used numbers
        used = assigner_state['used']
        n = assigner_state['next']
        while n in used:
            n += 1
        used.add(n)
        assigner_state['next'] = n + 1
        return n

    @classmethod
    def _init_assigner_state(cls, settings):
        """Initialize the channel number assigner state based on settings."""
        mode = cls._resolve_numbering_mode(settings)

        used = set()
        if mode != "lineup":
            for n in Channel.objects.values_list("channel_number", flat=True):
                if n is not None:
                    try:
                        used.add(int(n))
                    except (ValueError, TypeError):
                        pass

        if mode == "auto_next":
            start = 1
        elif mode == "auto_highest":
            start = (max(used) + 1) if used else 1
        elif mode == "specific":
            raw = (settings.get("starting_channel_number") or "").strip()
            start = int(raw) if raw.isdigit() and int(raw) > 0 else 1
        else:
            start = 1

        return {'next': start, 'used': used}

    def _make_group_name(self, prefix, category):
        """Build full group name from prefix and category.
        If prefix ends with a separator character, append category directly.
        Otherwise add ': ' between prefix and category.
        Examples: 'DTV-' + 'News' = 'DTV-News', 'DIRECTV' + 'News' = 'DIRECTV: News'"""
        if prefix:
            if prefix[-1] in ":_-/ ":
                return f"{prefix}{category}"
            return f"{prefix}: {category}"
        return category

    def _resolve_m3u_sources(self, settings, logger):
        """Resolve M3U source names to account IDs. Returns (valid_ids, m3u_priority_map, warnings)."""
        m3u_str = (settings.get("m3u_sources") or "").strip()
        active_ids = list(
            M3UAccount.objects.filter(is_active=True).values_list('id', flat=True)
        )
        if not m3u_str or m3u_str == "_all":
            return active_ids, {}, []

        all_accounts = {
            a['name']: a['id']
            for a in M3UAccount.objects.filter(is_active=True).values('id', 'name')
        }
        source_names = [s.strip() for s in m3u_str.split(",") if s.strip()]

        valid_ids = []
        priority_map = {}
        warnings = []

        for idx, name in enumerate(source_names):
            if name in all_accounts:
                aid = all_accounts[name]
                valid_ids.append(aid)
                priority_map[aid] = idx
            else:
                warnings.append(f"M3U source not found: '{name}'")
                logger.warning(f"{LOG_PREFIX} M3U source not found: '{name}'")

        return valid_ids, priority_map, warnings

    def _get_all_streams(self, settings, logger):
        """Get all streams, optionally filtered by M3U sources."""
        valid_ids, priority_map, _ = self._resolve_m3u_sources(settings, logger)

        qs = Stream.objects.all().values(
            'id', 'name', 'm3u_account', 'stream_stats', 'channel_group__name'
        )
        if valid_ids is not None:
            qs = qs.filter(m3u_account__in=valid_ids)

        streams = list(qs)
        for s in streams:
            s['_m3u_priority'] = priority_map.get(s.get('m3u_account'), 999)
            s['_stream_stats'] = s.pop('stream_stats', None) or {}
            # The provider group this stream came from. Its name usually carries
            # a country ("AU| AUSTRALIA VIP"), which is the only country signal
            # available for a provider that prefixes stream names by platform
            # ("GO:", "RK:") rather than by country.
            s['_group_name'] = s.pop('channel_group__name', None)

        logger.info(f"{LOG_PREFIX} Loaded {len(streams)} streams" + (f" from {len(valid_ids)} M3U sources" if valid_ids else ""))
        return streams

    @staticmethod
    def _disabled_selected_sources(sources, tokens, select_all):
        """Names of EPG sources this run will match against that are switched off.

        EPG matching reads every EPGData row without checking whether its source
        is enabled, so a disabled source still supplies candidates. Dispatcharr
        will never refresh those rows, so a channel matched to one gets a guide
        that quietly stops being true, and nothing else about the plugin looks
        wrong when it happens.

        Args:
            sources: rows of {"name", "is_active", "entry_count"}
            tokens: the source-filter tokens, which may carry shell wildcards
            select_all: True when the filter selects every source

        Returns the names in alphabetical order, so the reported list is stable.
        """
        hits = []
        for src in sources or []:
            if src.get("is_active"):
                continue
            name = src.get("name") or ""
            if select_all:
                # Every source is in the candidate pool, so only a source that
                # actually holds entries can mislead anyone.
                if src.get("entry_count"):
                    hits.append(name)
                continue
            for token in tokens or []:
                if fnmatch.fnmatch(name.upper(), str(token).upper()):
                    hits.append(name)
                    break
        return sorted(hits)

    @staticmethod
    def _stream_countries_by_name(streams):
        """Map each stream NAME to the country of the provider group it came from.

        Only the stream name reaches the matcher, and a provider that prefixes
        names by platform ("GO: ESPN", "RK: VEVO POP") leaves the matcher with no
        country to work from, so a United States feed passes the country filter
        into an Australian lineup. The provider group name carries the country
        that the stream name does not.

        A name that appears under groups of two different countries is left OUT
        of the map rather than resolved by whichever row was read first: an
        ambiguous answer here silently drops the wrong stream, and absence just
        falls back to the previous behaviour.
        """
        seen = {}
        for s in streams:
            name = s.get('name')
            if not name:
                continue
            cc = detect_category_country(s.get('_group_name'))
            if cc is None:
                continue
            if name in seen and seen[name] != cc:
                seen[name] = None  # ambiguous, and stays that way
            elif name not in seen:
                seen[name] = cc
        return {k: v for k, v in seen.items() if v is not None}

    def _alias_map_provider(self, settings, logger):
        """Return a callable mapping a country code to that country's alias map.

        A lineup can carry channels from several countries (a category named
        "UK| Sports" inside a mixed lineup), and each needs the alias table for
        ITS country, not for the lineup filename's country.

        Memoized for the life of ONE run and no longer. The map depends on the
        custom_aliases setting and on the selected lineup's embedded aliases,
        both of which change between runs while the plugin object survives in a
        long-lived worker process. Instance-level caching would serve a stale
        map after a settings edit, and would carry one lineup's embedded aliases
        into another lineup of the same country.
        """
        cache = {}

        def alias_map_for(country):
            key = (country or "").upper()
            if key not in cache:
                cache[key] = self._build_alias_map(settings, logger, country=key or None)
            return cache[key]

        return alias_map_for

    def _build_alias_map(self, settings, logger, country=None):
        """Merge built-in aliases with user custom aliases.

        Built-in aliases are global (keyed by channel name). Country-scoped
        overrides from COUNTRY_ALIASES are merged on top for ONE country only, so
        a name that exists in multiple markets (e.g. "TLC", "MTV") does not leak
        one country's stream-name variants into another (bug-063). Custom user
        aliases are merged last and win.

        The country defaults to the one in the lineup filename. Callers that know
        a channel's own country (from its category name or its name prefix) pass
        it explicitly, so a UK channel inside a US lineup gets UK aliases.
        """
        alias_map = dict(CHANNEL_ALIASES)

        if country:
            lineup_cc = country
        else:
            try:
                lineup_cc, _ = self._parse_lineup_filename(settings.get("lineup_file", ""))
            except Exception:
                lineup_cc = None
        if lineup_cc:
            for k, v in COUNTRY_ALIASES.get(lineup_cc.upper(), {}).items():
                alias_map[k] = list(dict.fromkeys(alias_map.get(k, []) + list(v)))

        self._merge_embedded_aliases(alias_map, settings, logger)

        custom_str = _clean_json_text(settings.get("custom_aliases") or "")
        if custom_str:
            try:
                custom = json.loads(custom_str)
            except json.JSONDecodeError as e:
                logger.warning(f"{LOG_PREFIX} Failed to parse custom_aliases JSON: {e}")
                custom = None

            if isinstance(custom, dict):
                merged = 0
                for k, v in custom.items():
                    # Accept a list of aliases, or a bare string as a single
                    # alias. Anything else is a mistake - warn instead of
                    # silently dropping it (silent drops were a known
                    # source of "my aliases don't work" complaints).
                    if isinstance(v, str):
                        aliases = [v]
                    elif isinstance(v, list):
                        aliases = v
                    else:
                        logger.warning(
                            f"{LOG_PREFIX} custom_aliases: ignoring '{k}' - value "
                            f"must be a string or list, got {type(v).__name__}"
                        )
                        continue
                    # Keep only non-empty string aliases.
                    clean = [a.strip() for a in aliases
                             if isinstance(a, str) and a.strip()]
                    if not clean:
                        logger.warning(
                            f"{LOG_PREFIX} custom_aliases: ignoring '{k}' - no "
                            f"usable (non-empty string) aliases"
                        )
                        continue
                    if k in alias_map:
                        alias_map[k] = list(dict.fromkeys(alias_map[k] + clean))
                    else:
                        alias_map[k] = clean
                    merged += 1
                logger.info(
                    f"{LOG_PREFIX} Merged {merged} custom alias "
                    f"{'entry' if merged == 1 else 'entries'}"
                )
            elif custom is not None:
                logger.warning(
                    f"{LOG_PREFIX} custom_aliases must be a JSON object mapping "
                    f"channel names to aliases, got {type(custom).__name__} - ignored"
                )

        return alias_map

    def _merge_embedded_aliases(self, alias_map, settings, logger):
        """Merge per-channel "aliases" arrays carried inside the lineup JSON.

        A lineup channel entry may declare its own aliases:

            {"name": "My9 New York", "number": 509,
             "aliases": ["WWOR", "WWOR-TV", "MY9"]}

        This lets a lineup ship the stream-name variants that are specific to it
        (provider callsigns, HDHomeRun and XMLTV display names) without adding
        them to the global built-in table, where a name that exists in several
        markets would leak across countries.

        Merged after the built-in and country-scoped tables and before the user's
        custom aliases, so the user setting still has the last word. A lineup
        that cannot be read leaves the alias map untouched: aliases are an
        enhancement, and failing the whole matching run over them would trade a
        missing alias for no matches at all.
        """
        try:
            lineup = self._load_lineup(settings, logger)
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} Could not read embedded lineup aliases: {e}")
            return

        categories = lineup.get("categories") if isinstance(lineup, dict) else None
        if not isinstance(categories, dict):
            return

        merged = 0
        for channels in categories.values():
            if not isinstance(channels, list):
                continue
            for channel in channels:
                if not isinstance(channel, dict):
                    continue

                name = str(channel.get("name") or "").strip()
                raw = channel.get("aliases")
                if not name or not raw:
                    continue

                # Same shapes the custom_aliases setting accepts: a list, or a
                # bare string for a single alias. Anything else is a mistake in
                # the lineup file and is reported rather than dropped silently.
                if isinstance(raw, str):
                    candidates = [raw]
                elif isinstance(raw, list):
                    candidates = raw
                else:
                    logger.warning(
                        f"{LOG_PREFIX} Lineup aliases for '{name}' must be a string "
                        f"or list, got {type(raw).__name__} - ignored"
                    )
                    continue

                clean = [a.strip() for a in candidates
                         if isinstance(a, str) and a.strip()]
                if not clean:
                    continue

                alias_map[name] = list(dict.fromkeys(alias_map.get(name, []) + clean))
                merged += 1

        if merged:
            logger.info(
                f"{LOG_PREFIX} Merged embedded aliases from {merged} lineup "
                f"{'channel' if merged == 1 else 'channels'}"
            )

    def _build_upgrade_twin_set(self, lineup, matcher):
        """Return lineup channel names (both tiers) that have a twin in the other tier.

        Example: if the lineup contains both "France 2" and "France 2 UHD", both
        are returned. The matcher then restricts each to streams of its own quality
        tier (standard streams for "France 2", upgrade streams for "France 2 UHD").
        """
        all_names = [ch["name"] for cat in lineup["categories"].values() for ch in cat]
        upgrade_bases = set()
        standard_bases = set()
        for name in all_names:
            base = matcher.normalize_name(name, ignore_quality=True)
            if base:
                if has_upgrade_quality(name):
                    upgrade_bases.add(base.lower())
                else:
                    standard_bases.add(base.lower())
        twin_set = set()
        for name in all_names:
            base = matcher.normalize_name(name, ignore_quality=True)
            if base:
                if has_upgrade_quality(name) and base.lower() in standard_bases:
                    twin_set.add(name)
                elif not has_upgrade_quality(name) and base.lower() in upgrade_bases:
                    twin_set.add(name)
        return twin_set

    def _get_upgrade_twin_set(self, settings, lineup, matcher, logger=None):
        """Return the upgrade twin set if quality_aware_stream_matching is enabled, else empty set."""
        if not settings.get("quality_aware_stream_matching", PluginConfig.DEFAULT_QUALITY_AWARE_STREAM_MATCHING):
            return set()
        twin_set = self._build_upgrade_twin_set(lineup, matcher)
        if logger and twin_set:
            logger.info(f"{LOG_PREFIX} Quality-aware matching active for {len(twin_set)} channel(s)")
            logger.debug(f"{LOG_PREFIX} Quality-aware twins: {sorted(twin_set)}")
        return twin_set

    def _get_filtered_epg_data(self, settings, logger):
        """Fetch EPG data, optionally filtered and prioritized by selected sources."""
        if not _EPG_AVAILABLE:
            logger.warning(f"{LOG_PREFIX} EPG models not available. Skipping EPG matching.")
            return []

        all_epg = list(EPGData.objects.all().values('id', 'name', 'tvg_id', 'epg_source'))
        logger.info(f"{LOG_PREFIX} Fetched {len(all_epg)} EPG data entries")

        # Comma-separated source names, each of which may carry shell wildcards
        # (* and ?). Blank / "_all" / "all" / "*" mean "every source".
        tokens, select_all = self._parse_source_filter(settings.get("epg_sources"))

        if select_all:
            # "All": order EPG entries by Dispatcharr's per-source priority
            # (EPGSource.priority - higher number = higher priority) so
            # downstream consumers that take the first match honor the priority
            # the user configured in Dispatcharr.
            priority_by_id = {
                src['id']: (src['priority'] or 0)
                for src in EPGSource.objects.all().values('id', 'priority')
            }
            all_epg.sort(
                key=lambda e: priority_by_id.get(e.get('epg_source'), 0),
                reverse=True,
            )
            logger.info(
                f"{LOG_PREFIX} EPG 'All': {len(all_epg)} entries ordered by "
                f"source priority ({len(priority_by_id)} sources)"
            )
            return all_epg

        # Match each token against available source names (case-insensitive,
        # wildcards via fnmatch). A plain name with no wildcard is an exact
        # match. Sources keep the order their first matching token appeared in,
        # so earlier tokens win on priority.
        available = [
            (src['name'], src['id'])
            for src in EPGSource.objects.all().values('id', 'name')
        ]
        # src_id -> priority index (insertion order = first matching token wins).
        priority_map = {}
        for token in tokens:
            tk = token.upper()
            matched_any = False
            for name, src_id in available:
                if fnmatch.fnmatch((name or "").upper(), tk):
                    matched_any = True
                    priority_map.setdefault(src_id, len(priority_map))
            if not matched_any:
                logger.warning(f"{LOG_PREFIX} EPG source pattern matched nothing: '{token}'")

        if not priority_map:
            logger.warning(f"{LOG_PREFIX} No valid EPG sources matched. Using all EPG data.")
            return all_epg

        filtered = [e for e in all_epg if e.get('epg_source') in priority_map]
        filtered.sort(key=lambda e: priority_map.get(e.get('epg_source'), 999))
        logger.info(f"{LOG_PREFIX} Filtered to {len(filtered)} EPG entries from {len(priority_map)} source(s)")
        return filtered

    def _get_epg_ids_with_programs(self, epg_ids, logger):
        """Pre-fetch which EPG IDs have program data in the next 12 hours."""
        if not _EPG_AVAILABLE or not epg_ids:
            return set()

        from django.utils import timezone
        now = timezone.now()
        end_time = now + timedelta(hours=12)

        ids_with_data = set(
            ProgramData.objects.filter(
                epg_id__in=epg_ids,
                end_time__gte=now,
                start_time__lt=end_time
            ).values_list('epg_id', flat=True).distinct()
        )
        logger.info(f"{LOG_PREFIX} {len(ids_with_data)}/{len(epg_ids)} EPG entries have program data in next 12h")
        return ids_with_data

    SENSITIVITY_MAP = {
        "relaxed": 70,
        "normal": 80,
        "strict": 90,
        "exact": 95,
    }

    @classmethod
    def _resolve_match_threshold(cls, settings):
        """The score a name must reach, resolved exactly as a run resolves it.

        Shared with the CSV preamble so the export cannot report a different
        number from the one the matcher used. The legacy numeric field is still
        read because Dispatcharr never prunes a setting whose field was removed,
        so an installation upgraded from before the four presets can still carry
        one, and a run then matches at that number.
        """
        settings = settings or {}
        threshold = cls.SENSITIVITY_MAP.get(settings.get("match_sensitivity", "normal"))
        if threshold is None:
            try:
                threshold = int(settings.get("fuzzy_match_threshold",
                                             PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD))
            except (TypeError, ValueError):
                threshold = PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD
        return max(0, min(100, threshold))

    def _init_fuzzy_matcher(self, settings, logger):
        """Create a configured FuzzyMatcher instance."""
        return FuzzyMatcher(match_threshold=self._resolve_match_threshold(settings),
                            logger=logger)

    def _resolve_channel_profiles(self, settings, logger):
        """Resolve comma-separated profile names to profile objects.
        Returns list of {'id': int, 'name': str} dicts, or empty list if not configured."""
        profiles_str = (settings.get("channel_profiles") or "").strip()
        if not profiles_str or profiles_str == "_none":
            return []

        all_profiles = list(ChannelProfile.objects.all().values('id', 'name'))
        profile_names = [p.strip() for p in profiles_str.split(",") if p.strip()]
        resolved = []

        for name in profile_names:
            found = None
            for p in all_profiles:
                if p['name'].lower() == name.lower():
                    found = p
                    break
            if found:
                resolved.append(found)
            else:
                logger.warning(f"{LOG_PREFIX} Channel profile '{name}' not found")

        return resolved

    def _enable_channels_in_profiles(self, channel_ids, settings, logger):
        """Enable channels in the configured channel profiles."""
        profiles = self._resolve_channel_profiles(settings, logger)
        if not profiles or not channel_ids:
            return

        for profile in profiles:
            pid = profile['id']
            pname = profile['name']

            # Ensure memberships exist, then enable
            existing = set(ChannelProfileMembership.objects.filter(
                channel_profile_id=pid,
                channel_id__in=channel_ids
            ).values_list('channel_id', flat=True))

            # Create missing memberships
            new_memberships = [
                ChannelProfileMembership(
                    channel_profile_id=pid,
                    channel_id=cid,
                    enabled=True
                )
                for cid in channel_ids if cid not in existing
            ]
            if new_memberships:
                ChannelProfileMembership.objects.bulk_create(new_memberships, ignore_conflicts=True)

            # Enable all
            updated = ChannelProfileMembership.objects.filter(
                channel_profile_id=pid,
                channel_id__in=channel_ids,
                enabled=False
            ).update(enabled=True)

            total_new = len(new_memberships)
            logger.info(f"{LOG_PREFIX} Profile '{pname}': {total_new} added, {updated} enabled ({len(channel_ids)} total channels)")

    @staticmethod
    def _get_quality_tier(stream):
        """Determine quality tier from stream_stats (probed metadata) or name tags.
        Returns 0 (best/4K) to 5 (unknown). Uses actual resolution when available."""
        # First try probed resolution from stream_stats (set by iptv_checker)
        stats = stream.get('_stream_stats') or {}
        height = stats.get('height', 0) if isinstance(stats, dict) else 0
        if height >= 2160:
            return 0  # 4K/UHD
        elif height >= 1080:
            return 1  # FHD
        elif height >= 720:
            return 2  # HD
        elif height >= 480:
            return 3  # SD
        elif height > 0:
            return 4  # Low quality

        # Fallback: detect quality from stream name
        name_upper = stream.get('name', '').upper()
        if any(tag in name_upper for tag in ['4K', 'UHD']):
            return 0
        elif any(tag in name_upper for tag in ['FHD', '1080']):
            return 1
        elif re.search(r'\bHD\b', name_upper):
            return 2
        elif re.search(r'\bSD\b', name_upper):
            return 3
        return 5  # Unknown

    def _sort_streams_by_quality(self, streams, prioritize_quality=True):
        """Sort matched stream dicts by quality tier and M3U priority.
        Uses probed resolution from stream_stats when available, falls back to name tags."""
        def get_quality_score(stream):
            m3u_priority = stream.get('_m3u_priority', 999)
            tier = self._get_quality_tier(stream)
            if prioritize_quality:
                return (tier, m3u_priority)
            else:
                return (m3u_priority, tier)

        return sorted(streams, key=get_quality_score)

    def _acquire_lock(self, logger):
        """Acquire operation lock. Returns True if acquired."""
        lock_file = PluginConfig.OPERATION_LOCK_FILE
        try:
            if os.path.exists(lock_file):
                with open(lock_file, 'r') as f:
                    lock_data = json.load(f)
                lock_time = datetime.fromisoformat(lock_data.get('timestamp', ''))
                elapsed = (datetime.now() - lock_time).total_seconds() / 60
                if elapsed < PluginConfig.OPERATION_LOCK_TIMEOUT_MINUTES:
                    logger.warning(f"{LOG_PREFIX} Operation locked (started {elapsed:.0f}m ago)")
                    return False
                logger.warning(f"{LOG_PREFIX} Stale lock detected ({elapsed:.0f}m old), overriding")

            os.makedirs(os.path.dirname(lock_file), exist_ok=True)
            tmp = lock_file + ".tmp"
            with open(tmp, 'w') as f:
                json.dump({"timestamp": datetime.now().isoformat(), "action": "lineuparr"}, f)
            os.replace(tmp, lock_file)
            return True
        except Exception as e:
            logger.error(f"{LOG_PREFIX} Failed to acquire lock: {e}")
            return False

    def _release_lock(self, logger):
        """Release operation lock."""
        try:
            if os.path.exists(PluginConfig.OPERATION_LOCK_FILE):
                os.remove(PluginConfig.OPERATION_LOCK_FILE)
        except Exception as e:
            logger.error(f"{LOG_PREFIX} Failed to release lock: {e}")

    def _m3u_account_names(self, logger):
        """Active M3U account names, or None when the lookup failed.

        None and an empty list mean different things to the report builder: an
        installation may legitimately have no active accounts, but a failed
        lookup must not be mistaken for one, because these names are the report's
        primary redaction input.
        """
        try:
            return [a['name'] for a in M3UAccount.objects.filter(is_active=True).values('name')
                    if a.get('name')]
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} Could not read M3U account names for the report: {e}")
            return None

    def _write_html_report(self, title, columns, rows, settings, logger, export_filename=None):
        """Write the shareable HTML and CSV report for a run. Returns the HTML
        path, or None.

        Never raises and never fails the run that produced the rows: a report is
        not the plugin's real work. A failure is logged and the action carries on.

        `columns` is the allow-list of (row key, header) pairs. A key absent from
        it never reaches the report, so a column added to a CSV writer later
        cannot start appearing in a shared file on its own.
        """
        try:
            accounts = self._m3u_account_names(logger)
            if accounts is None:
                logger.warning(f"{LOG_PREFIX} HTML report skipped: M3U account names unavailable, "
                               f"so stream names could not be scrubbed")
                return None
            model = reports.build_model(
                title, columns, rows,
                account_names=accounts,
                settings=settings or {},
                lineup=(settings or {}).get("lineup_file", ""),
                version=PluginConfig.PLUGIN_VERSION,
                now=time.time(),
                export_filename=export_filename,
            )
            result = reports.write_report(model, reports.REPORT_DIR, time.time())
            if result.get("error"):
                logger.warning(f"{LOG_PREFIX} HTML report not written: {result['error']}")
                return None
            logger.info(f"{LOG_PREFIX} Report written: {result['html_path']}")
            self._emit_reports(result, settings, logger)
            # Last, deliberately. This is the cosmetic step and delivery is the
            # one the operator asked for, so nothing here can delay or precede
            # it. Were this ever to raise, the enclosing except would swallow
            # the return value and the caller would read a written report as a
            # failed one.
            self._publish_report_count(result, logger)
            return result["html_path"]
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} HTML report failed: {e}")
            return None

    @staticmethod
    def _publish_report_count(written, logger):
        """Add one to the count Newsflasharr shows for this plugin.

        Called from the one place a report build is known to have succeeded.
        write_report degrades instead of raising, so a failed publish otherwise
        looks exactly like a good one, and a counter that incremented anyway
        would turn that failure into apparent success.

        Two independent conditions are required, because they can disagree. The
        result dict must carry no error, which is how write_report reports a
        failure, and the named file must exist. write_report can return a path
        AND an error together: the paths are assigned before the old-report
        pruning step, so a failure inside pruning leaves both keys populated.
        Checking only the path would count that as a clean build. Checking only
        the error would count a build whose file was deleted underneath it.

        The reverse case is a deliberate under-count. If the HTML file is
        written and the CSV write then fails, write_report reports the whole
        build as failed and this does not increment, even though a complete
        HTML report is sitting on disk. That is the safe direction: the
        alternative counts half-written builds as successes.

        Counts builds, not deliveries: a report built while notifications are
        off still counts, because it was still published to disk. The on-demand
        Email Report Now action deliberately does not call this, since it sends
        a report that already exists rather than building one.

        Never raises and never fails the run. A cosmetic number is not worth a
        report for.
        """
        try:
            written = written or {}
            path = written.get("html_path")
            if written.get("error") or not path or not os.path.isfile(path):
                return None
            value = report_count.increment(report_count.counter_path())
            if value is None:
                # Logged at warning, not debug. The usual cause is the counter
                # directory having been created root-owned by something run as
                # root inside the container, which the plugin can then never
                # rewrite. Nothing else about the plugin looks unhealthy when
                # that happens, so a debug line would hide it permanently.
                logger.warning(f"{LOG_PREFIX} Report counter not updated. Check that "
                               f"{report_count.counter_dir()} is owned by dispatch.")
            return value
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} Report counter not updated: {e}")
            return None

    @staticmethod
    def _notify_client():
        """Import the vendored client lazily.

        Deliberately not a module-scope import: a notification path is never worth
        failing the plugin's load over, and the fallback form is what lets the
        module be imported by a test harness that has no package parent.
        """
        try:
            from . import notify_client
        except ImportError:
            import notify_client
        return notify_client

    def _emit_reports(self, written, settings, logger):
        """Hand the written report files to Newsflasharr. Returns its result dict.

        Never raises and never fails the run that produced the report.
        """
        try:
            result = notify_bridge.emit_reports(
                self._notify_client().notify, settings or {}, written)
            if result.get("sent"):
                logger.info(f"{LOG_PREFIX} Report queued for delivery: "
                            f"{result['sent']} notification(s)")
            elif result.get("skipped_reason"):
                logger.debug(f"{LOG_PREFIX} Report not sent: {result['skipped_reason']}")
            return result
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} Report delivery failed: {e}")
            return {"sent": 0, "skipped_reason": str(e)}

    def _email_report(self, settings, logger):
        """Send the newest report on disk, on demand.

        Sends what is already there rather than re-running a match: a match is
        minutes of work and the operator asked to send a report, not to produce
        one.
        """
        if not notify_bridge.is_enabled(settings):
            return {"status": "error",
                    "error": "Sending reports to Newsflasharr is switched off. Turn on "
                             "'Send reports to Newsflasharr' in the settings above, save, "
                             "and try again."}
        try:
            entries = []
            for suffix in (".html", ".csv"):
                found = sorted(
                    (os.path.join(reports.REPORT_DIR, n)
                     for n in os.listdir(reports.REPORT_DIR)
                     if n.startswith(reports.FILENAME_PREFIX) and n.endswith(suffix)),
                    key=os.path.getmtime, reverse=True)
                entries.append(found[0] if found else None)
        except OSError:
            entries = [None, None]

        if not any(entries):
            return {"status": "error",
                    "error": "No report to send yet. Run Preview Stream Match, or any "
                             "action that exports a CSV, and a report is written "
                             f"to {reports.REPORT_DIR}."}

        written = {"html_path": entries[0], "csv_path": entries[1], "error": None}
        # The on-demand button always sends, whatever the schedule setting says:
        # the operator pressing it IS the trigger.
        result = self._emit_reports(written, dict(settings or {}, notify_report_on="every_run"),
                                    logger)
        if not result.get("sent"):
            reason = result.get("skipped_reason") or "no report file could be read"
            return {"status": "error", "error": f"Nothing was sent: {reason}"}
        names = ", ".join(os.path.basename(p) for p in entries if p)
        return {"status": "ok",
                "message": f"Queued {result['sent']} notification(s) with Newsflasharr: {names}",
                "file": entries[0] or entries[1]}

    @staticmethod
    def _csv_exports_to_delete(entries, retention_days, now, protect=None):
        """Which of this plugin's CSV exports are old enough to remove.

        entries is a sequence of (filename, modification time) pairs, normally
        the whole export directory. Returns the names to delete, sorted. This is
        pure: it touches no filesystem, which is where its tests live.

        Nothing is deleted unless a positive number of days is configured, so an
        installation that never asked for this keeps every file. The file just
        written is never deleted. At least one of this plugin's files always
        survives, so a small number cannot empty the directory.
        """
        # Resolved through the same helper the wrapper uses, so the two cannot
        # disagree about what a value means. This guard stays even though the
        # wrapper already returns early: this function deletes nothing itself,
        # but it names the files something else will delete.
        days, _problem = Plugin._resolve_retention_days(retention_days)
        if days <= 0:
            return []

        mine = []
        for name, mtime in entries:
            if not (name.startswith(PluginConfig.CSV_EXPORT_PREFIX)
                    and name.endswith(PluginConfig.CSV_EXPORT_SUFFIX)):
                continue
            try:
                stamp = float(mtime)
            except (TypeError, ValueError):
                continue
            if stamp != stamp:  # not a number, so its age is unknown
                # Keeping it would be worse than skipping it: comparisons
                # against it are all false, so it would win the "which is
                # newest" test, become the survivor, and every real file would
                # be deleted instead of one being kept.
                continue
            mine.append((name, stamp))
        if not mine:
            return []

        # One file is guaranteed to survive. The file just written is the
        # natural choice when it is here, otherwise the most recent one.
        survivor = protect if any(name == protect for name, _ in mine) else None
        if survivor is None:
            survivor = max(mine, key=lambda pair: (pair[1], pair[0]))[0]

        cutoff = float(now) - days * PluginConfig.SECONDS_PER_DAY
        return sorted(name for name, stamp in mine
                      if stamp < cutoff and name != survivor)

    @staticmethod
    def _resolve_retention_days(value):
        """Read the retention setting once, for everything that needs it.

        Returns (days, problem). days is 0 whenever nothing should be deleted,
        and problem is a sentence to log, or None when there is nothing to say.

        Blank, absent and any number at or below zero all mean "keep every
        file". That is the default and is not a mistake, so they report no
        problem. Anything that is not a whole number of days is a mistake: it
        must not quietly become a shorter window. A plain int() would turn 3.7
        into a three day window, and the field is declared as a number, so a
        float is exactly what the form hands back.

        Both the pure planner and the wrapper resolve through this, so they
        cannot disagree about what a value means.
        """
        if value is None:
            return 0, None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return 0, None
        elif isinstance(value, bool):
            return (1, None) if value else (0, None)
        else:
            text = value
        try:
            number = float(text)
        except (TypeError, ValueError):
            return 0, ("is not a number of days, so every export was kept. "
                       "Set a whole number, or 0 to keep every file.")
        if number != int(number):
            return 0, ("is not a WHOLE number of days, so every export was "
                       "kept rather than rounded to a shorter window. Set a "
                       "whole number, or 0 to keep every file.")
        days = int(number)
        return (days, None) if days > 0 else (0, None)

    def _prune_csv_exports(self, retention_days, protect=None, logger=None):
        """Delete this plugin's CSV exports older than retention_days.

        Returns how many were removed. NEVER raises: this runs immediately after
        a successful export, and a failure to tidy up must not turn a successful
        export into a reported error.

        `logger` is the run's own logger, so everything one export has to say
        lands in one place. Without it the warning from a delete that failed
        went to the module logger while the export's other messages went to the
        run's, which is how a retention setting that never works stays invisible.
        """
        log = logger or LOGGER
        days, problem = self._resolve_retention_days(retention_days)
        if problem:
            log.warning(f"{LOG_PREFIX} 'Delete CSV Exports Older Than (Days)' is "
                        f"set to {retention_days!r}, which {problem}")
        if days <= 0:
            # Nothing can be deleted, so do not touch the filesystem at all.
            # This is the shipped default, and /data/exports is shared: measured
            # at 124 files, of which 10 were this plugin's, so the old code cost
            # a listing plus 124 modification-time reads on every export for a
            # result that was always empty.
            return 0

        directory = PluginConfig.EXPORTS_DIR
        entries = []
        try:
            names = os.listdir(directory)
        except OSError:
            return 0
        for name in names:
            # Filter by name BEFORE asking the filesystem anything. Only about
            # one file in twelve here belongs to this plugin.
            if not (name.startswith(PluginConfig.CSV_EXPORT_PREFIX)
                    and name.endswith(PluginConfig.CSV_EXPORT_SUFFIX)):
                continue
            try:
                entries.append((name, os.path.getmtime(os.path.join(directory, name))))
            except OSError:
                # It vanished between listing and asking, so there is nothing
                # left to delete.
                continue

        removed = 0
        for name in self._csv_exports_to_delete(entries, retention_days,
                                                time.time(), protect):
            try:
                os.remove(os.path.join(directory, name))
                removed += 1
                log.info(f"{LOG_PREFIX} Deleted CSV export older than "
                         f"{retention_days} days: {name}")
            except OSError as exc:
                log.warning(f"{LOG_PREFIX} Could not delete old CSV export "
                            f"{name}: {exc}")
        return removed

    # The label each preset carries in the settings form, and what it is worth.
    # A preamble that prints the bare word "normal" tells the reader neither the
    # number nor which direction is stricter.
    SENSITIVITY_LABELS = {
        "relaxed": "Relaxed",
        "normal": "Normal",
        "strict": "Strict",
        "exact": "Exact",
    }

    CATEGORY_DETAIL_LABELS = {
        "none": "None, one group for the whole lineup",
        "refined": "Refined, 6 broad categories",
        "simple": "Simple, 7 merged categories",
        "normal": "Normal, every category the lineup names",
    }

    NUMBERING_LABELS = {
        "lineup": "Use the lineup file's own numbers",
        "auto_next": "Auto-assign next available",
        "auto_highest": "Auto-assign after highest",
        "specific": "Use specific number",
    }

    def _describe_m3u_sources(self, settings, logger=None):
        """The M3U source names this run used, never a URL.

        Reads through `_m3u_account_names`, which returns None for a lookup that
        FAILED and an empty list for an installation that genuinely has no
        active account. Collapsing those two into one sentence would let a
        database error read as a true statement about the run.
        """
        value = settings.get('m3u_sources') or '_all'
        if value != '_all':
            return str(value)
        names = self._m3u_account_names(logger or LOGGER)
        if names is None:
            return "(all active sources; the list could not be read)"
        if not names:
            return "(all active sources, of which there are none)"
        return f"{', '.join(names)} (all active sources)"

    def _describe_epg_sources(self, settings):
        """The EPG source names this run matched against."""
        value = settings.get('epg_sources') or ''
        if not _EPG_AVAILABLE:
            return "(EPG models unavailable in this Dispatcharr build)"
        try:
            _, matches_all = self._parse_source_filter(value)
            if not matches_all:
                return str(value)
            names = [src['name'] for src in EPGSource.objects.all().values('name')]
            if not names:
                return "(all sources, of which there are none)"
            return f"{', '.join(names)} (all sources)"
        except Exception:
            return str(value) or "(all sources)"

    def _generate_csv_header_comments(self, settings, action_name="Unknown",
                                      summary=None, row_count=0, logger=None):
        """The commented preamble written above the table in every CSV export.

        Returns a list of lines, each already starting with a hash and carrying
        no newline of its own, so the writer controls the line ending.

        The order is deliberate. A reader opening this in a spreadsheet is told
        first what the file is and that the hash lines are not data, then what
        the run produced, and only then how it was configured. Counts come
        before configuration because they are what somebody comparing two
        exports looks for first.

        `summary` is a sequence of (label, value) pairs supplied by whichever
        action wrote the file, because only that action knows what it counted.
        """
        settings = settings or {}
        sensitivity = str(settings.get('match_sensitivity') or 'normal')
        # The same number the matcher was built with, including the legacy
        # numeric field, so the export cannot name a threshold the run did not use.
        threshold = self._resolve_match_threshold(settings)
        known_preset = sensitivity in self.SENSITIVITY_MAP
        sensitivity_label = self.SENSITIVITY_LABELS.get(sensitivity, sensitivity)
        preserve_on = settings.get('preserve_existing_streams', False)
        single = (settings.get('single_channel_name') or '').strip()
        # Through the shared resolver, so the legacy ignore_channel_numbers
        # override the run honours is honoured here too.
        numbering = str(self._resolve_numbering_mode(settings))
        detail = str(settings.get('category_detail') or 'normal')

        lines = [
            f"# Lineuparr Export v{PluginConfig.PLUGIN_VERSION}",
            f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "#",
            "# WHAT THIS FILE IS",
            "# A report written by the Lineuparr plugin for Dispatcharr. Every line",
            "# that starts with a hash is explanation rather than data. The table",
            "# begins at the first line without one, so tell your spreadsheet to skip",
            "# comment lines when importing, or delete them first.",
            "#",
            "# The settings listed further down are the ones THIS run used, not the",
            "# ones saved now. That is the point of recording them: two exports that",
            "# disagree can be compared by reading these lines.",
            "#",
            "# === What This Run Did ===",
            f"# Action: {action_name}",
            f"# Rows in this table: {row_count}",
        ]
        for label, value in (summary or []):
            lines.append(f"# {label}: {value}")
        lines += [
            "#",
            "# === Settings This Run Used ===",
            f"# Lineup File: {settings.get('lineup_file') or '(none selected)'}",
            f"# M3U Source: {self._describe_m3u_sources(settings, logger)}",
            f"# EPG Sources for Matching: {self._describe_epg_sources(settings)}",
            f"# Channel Profile: {settings.get('channel_profiles') or '_none'}",
            f"# Channel Group Prefix: {settings.get('group_prefix') or '(auto, from the lineup name)'}",
            f"# Category Detail: {self.CATEGORY_DETAIL_LABELS.get(detail, detail)}",
            f"# Channel Numbering: {self.NUMBERING_LABELS.get(numbering, numbering)}",
        ]
        if known_preset:
            sensitivity_note = ("higher is stricter; a name must score at least "
                                "this to count as a match")
        else:
            sensitivity_note = ("not one of the four presets, so the score came "
                                "from the older numeric setting this installation "
                                "still carries; higher is stricter")
        lines.append(f"# Match Sensitivity: {sensitivity_label}, {threshold} "
                     f"out of 100 ({sensitivity_note})")
        if preserve_on:
            preserve_note = ("matched streams were added to each channel and "
                             "nothing was deleted")
        else:
            preserve_note = ("a stream match replaced each channel's whole "
                             "stream list, and channels left with no streams "
                             "were deleted")
        lines += [
            f"# Preserve Existing Streams: "
            f"{_setting_yes_no(settings, 'preserve_existing_streams', False)} "
            f"({preserve_note})",
            f"# Order Matched Streams by Quality: "
            f"{_setting_yes_no(settings, 'prioritize_quality', PluginConfig.DEFAULT_PRIORITIZE_QUALITY)}",
            f"# Quality-Aware Stream Matching: "
            f"{_setting_yes_no(settings, 'quality_aware_stream_matching', PluginConfig.DEFAULT_QUALITY_AWARE_STREAM_MATCHING)}",
            f"# Refresh EPG After Matching: "
            f"{_setting_yes_no(settings, 'refresh_epg_after_match', True)}",
            f"# Single Channel Match: {single or '(blank, so the whole lineup ran)'}",
            f"# Custom Channel Aliases: "
            f"{'set' if (settings.get('custom_aliases') or '').strip() else '(none)'}",
            f"# Rate Limiting: {settings.get('rate_limiting') or 'none'}",
            "#",
        ]
        # Flatten in ONE place rather than per line. Every value above is free
        # text a user or a provider chose, and a line break inside any of them
        # would end the comment block early, so a spreadsheet told to skip
        # comment lines would read the remainder as the header row.
        return [_one_line(line) for line in lines]

    def _record_channels_created(self, count, mode, logger=None):
        """Append one line recording how many channels a finished run created.

        APPEND, NOT READ-MODIFY-WRITE. Several Dispatcharr processes hold this
        module, and a read-then-write total would lose an increment whenever two
        of them raced. An append of one short line does not.

        IT NEVER RAISES. A tally is worth strictly less than the sync that
        produced it, so a tally that cannot be written is logged and the run
        carries on reporting whatever it was already reporting.

        A count of zero IS written: a run that created nothing is a fact about
        the run, and dropping it would make the tally silently sparse.

        The count is creations performed, not channels that still exist. The
        unmatched-channel cleanup deleting one later does not subtract, so the
        published total cannot go down. That was an explicit operator decision.

        Only integers and a short mode string are recorded. No channel name, no
        group name, no lineup filename. The summed total is published publicly.
        """
        log = logger or LOGGER
        # bool before int: bool subclasses int in Python, so True would
        # otherwise be recorded as one channel created.
        if isinstance(count, bool) or not isinstance(count, int):
            log.warning(f"{LOG_PREFIX} Channel tally skipped: the count was "
                        f"{count!r}, which is not a whole number")
            return False
        if count < 0:
            log.warning(f"{LOG_PREFIX} Channel tally skipped: the count was "
                        f"negative ({count})")
            return False
        line = json.dumps({"ts": int(time.time()), "channels": count, "mode": mode})
        try:
            with open(PluginConfig.CHANNEL_COUNT_LEDGER_FILE, "a",
                      encoding="utf-8") as handle:
                handle.write(line + "\n")
            return True
        except Exception as exc:
            log.warning(f"{LOG_PREFIX} Could not record the channel tally: {exc}")
            return False

    def _export_csv(self, filename, rows, fieldnames, logger, settings=None,
                    action_name="Unknown", summary=None):
        """Export data to CSV in the exports directory, under a commented preamble.

        `action_name` and `summary` describe what the run did, and are written
        above the settings. Only the calling action knows those counts, so it
        supplies them as (label, value) pairs.
        """
        try:
            os.makedirs(PluginConfig.EXPORTS_DIR, exist_ok=True)
            filepath = os.path.join(PluginConfig.EXPORTS_DIR, filename)
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                # CRLF, matching csv.DictWriter's own default line terminator.
                # The file was previously written with LF comment lines above
                # CRLF data rows, and the user guide now tells people to import
                # it, so one terminator throughout is the safer shape.
                for line in self._generate_csv_header_comments(
                        settings, action_name=action_name, summary=summary,
                        row_count=len(rows), logger=logger):
                    f.write(line + "\r\n")
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"{LOG_PREFIX} Exported CSV: {filepath} ({len(rows)} rows)")
            # Tidy up here rather than on a schedule: files only accumulate when
            # one is written, so pruning at that moment keeps the directory
            # bounded at all times, and every export path, manual or scheduled,
            # goes through this function. The file just written is protected by
            # name, whatever the age arithmetic says.
            self._prune_csv_exports((settings or {}).get('csv_retention_days'),
                                    protect=filename, logger=logger)
            return filepath
        except Exception as e:
            logger.error(f"{LOG_PREFIX} CSV export failed: {e}")
            return None

    def _save_state(self, state_data, logger):
        """Save plugin state atomically."""
        try:
            os.makedirs(os.path.dirname(PluginConfig.STATE_FILE), exist_ok=True)
            tmp = PluginConfig.STATE_FILE + ".tmp"
            with open(tmp, 'w') as f:
                json.dump(state_data, f, indent=2)
            os.replace(tmp, PluginConfig.STATE_FILE)
        except Exception as e:
            logger.error(f"{LOG_PREFIX} Failed to save state: {e}")

    def _trigger_frontend_refresh(self, logger):
        """Tell the Dispatcharr UI to refetch the channel list.

        The frontend re-queries channels and streams when it receives a
        'channels_created' websocket event -- verified against Dispatcharr's
        own handler, which calls requeryChannels()/requeryStreams() for that
        type. A generic plugin toast does NOT refresh the list, so this emits
        the same event shape Dispatcharr itself sends after creating channels
        (see apps/channels/api_views.py). count is omitted, so the frontend
        shows "...created multiple channel(s)"; Lineuparr's own completion
        notification carries the exact numbers."""
        if self._suppress_refresh:
            return  # a multi-step op (Full Sync) fires one refresh at the end
        try:
            send_websocket_update('updates', 'update', {
                "type": "channels_created",
            })
        except Exception as e:
            logger.error(f"{LOG_PREFIX} WebSocket refresh failed: {e}")

    # ========================================================================
    # NON-DESTRUCTIVE ACTIONS
    # ========================================================================

    def _validate_settings(self, settings, logger):
        """Check configuration validity."""
        results = []
        errors = 0

        # Check lineup file
        lineup_file = settings.get("lineup_file", "")
        if not lineup_file:
            results.append({"Setting": "Lineup File", "Value": "(none)", "Status": "ERROR: No lineup file selected"})
            errors += 1
        elif os.path.exists(os.path.join(os.path.dirname(__file__), lineup_file)):
            filepath = os.path.join(os.path.dirname(__file__), lineup_file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cats = len(data.get("categories", {}))
                chans = sum(len(v) for v in data.get("categories", {}).values())
                results.append({"Setting": "Lineup File", "Value": lineup_file, "Status": f"OK ({cats} categories, {chans} channels)"})
            except Exception as e:
                results.append({"Setting": "Lineup File", "Value": lineup_file, "Status": f"ERROR: {e}"})
                errors += 1
        else:
            results.append({"Setting": "Lineup File", "Value": lineup_file, "Status": "ERROR: File not found"})
            errors += 1

        # Check match sensitivity
        sensitivity = settings.get("match_sensitivity", "normal")
        threshold = self.SENSITIVITY_MAP.get(sensitivity)
        if threshold:
            results.append({"Setting": "Match Sensitivity", "Value": f"{sensitivity} ({threshold})", "Status": "OK"})
        else:
            # Fallback: legacy numeric threshold
            try:
                t = int(settings.get("fuzzy_match_threshold", PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD))
                if 0 <= t <= 100:
                    results.append({"Setting": "Match Sensitivity", "Value": f"custom ({t})", "Status": "OK"})
                else:
                    results.append({"Setting": "Match Sensitivity", "Value": str(t), "Status": "ERROR: Must be 0-100"})
                    errors += 1
            except (ValueError, TypeError):
                results.append({"Setting": "Match Sensitivity", "Value": str(sensitivity), "Status": "ERROR: Invalid"})
                errors += 1

        # Check channel numbering
        numbering = self._resolve_numbering_mode(settings)
        labels = self.NUMBERING_LABELS
        label = labels.get(numbering, numbering)
        if numbering == "specific":
            raw = (settings.get("starting_channel_number") or "").strip()
            if raw and raw.isdigit() and int(raw) > 0:
                results.append({"Setting": "Channel Numbering", "Value": f"{label} (start: {raw})", "Status": "OK"})
            else:
                results.append({"Setting": "Channel Numbering", "Value": label, "Status": "ERROR: Starting channel number must be a positive integer (1 or higher)"})
                errors += 1
        elif numbering in labels:
            results.append({"Setting": "Channel Numbering", "Value": label, "Status": "OK"})
        else:
            results.append({"Setting": "Channel Numbering", "Value": str(numbering), "Status": "ERROR: Invalid option"})
            errors += 1

        # Check M3U sources
        m3u_str = (settings.get("m3u_sources") or "").strip()
        if m3u_str and m3u_str != "_all":
            valid_ids, _, warnings = self._resolve_m3u_sources(settings, logger)
            if valid_ids:
                results.append({"Setting": "M3U Sources", "Value": m3u_str, "Status": f"OK ({len(valid_ids)} sources resolved)"})
            else:
                results.append({"Setting": "M3U Sources", "Value": m3u_str, "Status": "ERROR: No valid sources found"})
                errors += 1
            for w in warnings:
                results.append({"Setting": "M3U Warning", "Value": "", "Status": w})
        else:
            stream_count = Stream.objects.count()
            results.append({"Setting": "M3U Sources", "Value": "(all)", "Status": f"OK (using all - {stream_count} streams)"})

        # Check custom aliases
        custom_str = _clean_json_text(settings.get("custom_aliases") or "")
        if custom_str:
            try:
                custom = json.loads(custom_str)
                if isinstance(custom, dict):
                    results.append({"Setting": "Custom Aliases", "Value": f"{len(custom)} entries", "Status": "OK"})
                else:
                    results.append({"Setting": "Custom Aliases", "Value": "", "Status": "ERROR: Must be a JSON object"})
                    errors += 1
            except json.JSONDecodeError as e:
                results.append({"Setting": "Custom Aliases", "Value": "", "Status": f"ERROR: Invalid JSON - {e}"})
                errors += 1
        else:
            results.append({"Setting": "Custom Aliases", "Value": "(none)", "Status": f"OK (using {len(CHANNEL_ALIASES)} built-in aliases)"})

        # Check channel profiles
        profiles_str = (settings.get("channel_profiles") or "").strip()
        if profiles_str:
            resolved = self._resolve_channel_profiles(settings, logger)
            profile_names = [p.strip() for p in profiles_str.split(",") if p.strip()]
            resolved_names = [p['name'] for p in resolved]
            missing = [n for n in profile_names if n.lower() not in [r.lower() for r in resolved_names]]
            if missing:
                results.append({"Setting": "Channel Profiles", "Value": profiles_str, "Status": f"ERROR: Not found: {', '.join(missing)}"})
                errors += 1
            else:
                results.append({"Setting": "Channel Profiles", "Value": profiles_str, "Status": f"OK ({len(resolved)} profiles)"})
        else:
            results.append({"Setting": "Channel Profiles", "Value": "(none)", "Status": "OK (channels won't be enabled in any profile)"})

        # Check EPG availability
        if _EPG_AVAILABLE:
            try:
                epg_count = EPGData.objects.count()
                source_count = EPGSource.objects.count()
                results.append({"Setting": "EPG Data", "Value": f"{epg_count} entries from {source_count} sources", "Status": "OK"})

                epg_sources_str = (settings.get("epg_sources") or "").strip()
                _, epg_all = self._parse_source_filter(epg_sources_str)
                if not epg_all:
                    filtered = self._get_filtered_epg_data(settings, logger)
                    results.append({"Setting": "EPG Sources Filter", "Value": epg_sources_str, "Status": f"OK ({len(filtered)} entries after filtering)"})
                else:
                    results.append({"Setting": "EPG Sources Filter", "Value": "All sources", "Status": "OK"})

                # A disabled EPG source is still matched against, because EPG
                # matching does not filter on EPGSource.is_active. Dispatcharr
                # never refreshes a disabled source, so any channel matched to
                # one gets a guide that stops being true and nothing looks wrong.
                epg_tokens, epg_select_all = self._parse_source_filter(epg_sources_str)
                source_rows = []
                for src in EPGSource.objects.all().values('id', 'name', 'is_active'):
                    source_rows.append({
                        "name": src['name'],
                        "is_active": src['is_active'],
                        "entry_count": EPGData.objects.filter(epg_source_id=src['id']).count(),
                    })
                disabled = self._disabled_selected_sources(source_rows, epg_tokens, epg_select_all)
                if disabled:
                    results.append({
                        "Setting": "EPG Sources: disabled",
                        "Value": ", ".join(disabled),
                        "Status": ("WARNING: these EPG sources are switched off in Dispatcharr but "
                                   "are still matched against. Dispatcharr does not refresh a "
                                   "disabled source, so channels matched to one keep a guide that "
                                   "stops being updated. Enable them, or exclude them from the "
                                   "EPG Sources for Matching filter."),
                    })
            except Exception as e:
                results.append({"Setting": "EPG Data", "Value": str(e), "Status": "ERROR"})
                errors += 1
        else:
            results.append({"Setting": "EPG Data", "Value": "EPG models not available", "Status": "SKIP"})

        # Country mismatch warnings: when the selected lineup has a single
        # country code (e.g. US_Combined -> US), warn if the Channel Group Prefix
        # or the EPG Sources filter targets a DIFFERENT country code. This
        # catches setups like a US lineup with prefix "AU " or EPG filter "UK*",
        # which silently group/label or guide-match the wrong country.
        lineup_cc, _ = self._parse_lineup_filename(os.path.basename(lineup_file or ""))
        if lineup_cc:
            lineup_cc = lineup_cc.upper()

            gp_raw = (settings.get("group_prefix") or "")
            if gp_raw.strip().lower() != "none":
                gp_foreign = sorted(c for c in country_codes_in_text(gp_raw) if c != lineup_cc)
                if gp_foreign:
                    results.append({
                        "Setting": "Country Check: Group Prefix",
                        "Value": gp_raw.strip(),
                        "Status": (f"WARNING: prefix country {', '.join(gp_foreign)} does not match "
                                   f"lineup country {lineup_cc}. Channels may be grouped/labeled for "
                                   f"the wrong country."),
                    })

            epg_raw = (settings.get("epg_sources") or "")
            _, epg_is_all = self._parse_source_filter(epg_raw)
            if not epg_is_all:
                epg_foreign = sorted(c for c in country_codes_in_text(epg_raw) if c != lineup_cc)
                if epg_foreign:
                    results.append({
                        "Setting": "Country Check: EPG Sources",
                        "Value": epg_raw.strip(),
                        "Status": (f"WARNING: EPG source filter targets country {', '.join(epg_foreign)} "
                                   f"but the lineup is {lineup_cc}. EPG may match the wrong country's guide."),
                    })

        # DB connectivity
        try:
            ch_count = Channel.objects.count()
            gr_count = ChannelGroup.objects.count()
            st_count = Stream.objects.count()
            results.append({"Setting": "Database", "Value": "", "Status": f"OK ({ch_count} channels, {gr_count} groups, {st_count} streams)"})
        except Exception as e:
            results.append({"Setting": "Database", "Value": "", "Status": f"ERROR: {e}"})
            errors += 1

        warn_details = [r["Setting"] + ": " + r["Status"] for r in results if "WARNING" in r.get("Status", "")]
        status = "ok" if errors == 0 else "error"
        if errors == 0:
            msg = "✅ All settings valid."
        else:
            error_details = [r["Setting"] + ": " + r["Status"] for r in results if "ERROR" in r.get("Status", "")]
            msg = f"❌ {errors} error(s) found.\n" + "\n".join(error_details)
        if warn_details:
            msg += f"\n\n⚠️ {len(warn_details)} warning(s):\n" + "\n".join(warn_details)
        return action_result(status, msg, data=results)

    def _scan_lineups(self, settings, logger):
        """Discover available lineup JSON files."""
        plugin_dir = os.path.dirname(__file__)
        # Find all JSON files that look like lineups (contain "categories" key)
        lineup_files = glob(os.path.join(plugin_dir, "*.json"))
        # Exclude plugin.json
        lineup_files = [f for f in lineup_files if os.path.basename(f) != "plugin.json"]

        if not lineup_files:
            return {"status": "error", "error": "No lineup JSON files found in plugin directory."}

        results = []
        for f in lineup_files:
            fname = os.path.basename(f)
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                cats = data.get("categories", {})
                if not cats or not isinstance(cats, dict):
                    continue  # Not a lineup file
                results.append({
                    "File": fname,
                    "Package": data.get("package", "Unknown"),
                    "Date": data.get("date", "N/A"),
                    "Categories": len(cats),
                    "Channels": sum(len(v) for v in cats.values()),
                })
            except Exception as e:
                logger.error(f"{LOG_PREFIX} Error reading {fname}: {e}")

        return {"status": "ok", "message": f"Found {len(results)} lineup file(s).", "data": results}

    def _preview_groups(self, settings, logger):
        """Dry-run: show groups that would be created/updated."""
        lineup = self._load_lineup(settings, logger)
        prefix = self._get_group_prefix(settings, lineup)

        existing_groups = {g['name'] for g in ChannelGroup.objects.all().values('name')}

        results = []
        for category in lineup["categories"]:
            group_name = self._make_group_name(prefix, category)
            channel_count = len(lineup["categories"][category])
            status = "Exists" if group_name in existing_groups else "New"
            results.append({
                "Group Name": group_name,
                "Category": category,
                "Channels": channel_count,
                "Status": status,
            })

        new_count = sum(1 for r in results if r["Status"] == "New")
        return {
            "status": "ok",
            "message": f"{len(results)} groups ({new_count} new, {len(results) - new_count} existing).",
            "data": results,
        }

    def _preview_channels(self, settings, logger):
        """Dry-run: show channels that would be created."""
        lineup = self._load_lineup(settings, logger)
        prefix = self._get_group_prefix(settings, lineup)
        assigner = self._init_assigner_state(settings)

        # Build group name -> ID mapping
        existing_groups = {g['name']: g['id'] for g in ChannelGroup.objects.all().values('id', 'name')}
        # Build existing channels lookup: (name, group_id) -> channel
        existing_channels = {}
        for ch in Channel.objects.all().values('id', 'name', 'channel_number', 'channel_group_id'):
            existing_channels[(ch['name'], ch['channel_group_id'])] = ch

        results = []
        for category, channels in lineup["categories"].items():
            group_name = self._make_group_name(prefix, category)
            group_id = existing_groups.get(group_name)

            for entry in channels:
                ch_name = entry["name"]
                ch_number = self._get_channel_number(settings, entry, assigner)
                key = (ch_name, group_id) if group_id else None

                if key and key in existing_channels:
                    existing = existing_channels[key]
                    if existing['channel_number'] != ch_number:
                        status = f"Update (#{existing['channel_number']} -> #{ch_number})"
                    else:
                        status = "Exists"
                else:
                    status = "New"

                results.append({
                    "Channel": ch_name,
                    "Number": ch_number if ch_number else "",
                    "Category": category,
                    "Group": group_name,
                    "Status": status,
                })

        new_count = sum(1 for r in results if r["Status"] == "New")
        update_count = sum(1 for r in results if r["Status"].startswith("Update"))

        # Export CSV
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_name = f"lineuparr_preview_channels_{ts}.csv"
        self._export_csv(csv_name, results,
                         ["Channel", "Number", "Category", "Group", "Status"], logger, settings,
                         action_name="Preview Channels (dry run, nothing was written)",
                         summary=[
                             ("Channels that would be created", new_count),
                             ("Channels that would be renumbered", update_count),
                             ("Channels already correct",
                              len(results) - new_count - update_count),
                         ])
        self._write_html_report(
            "Channel sync preview",
            [("Channel", "Channel"), ("Number", "Number"), ("Category", "Category"),
             ("Group", "Group"), ("Status", "Status")],
            results, settings, logger, export_filename=csv_name)

        return {
            "status": "ok",
            "message": f"{len(results)} channels ({new_count} new, {update_count} updates, {len(results) - new_count - update_count} existing).",
            "data": results,
        }

    def _preview_stream_match(self, settings, logger):
        """Dry-run: show stream-to-channel matches with confidence scores.
        Runs as daemon thread to avoid HTTP timeout."""
        # Calculate ETA for the response message
        try:
            lineup = self._load_lineup(settings, logger)
            total_channels = sum(len(v) for v in lineup["categories"].values())
            stream_count = Stream.objects.count()
            eta_seconds = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_STREAM_MATCH
            eta_str = ProgressTracker._format_eta_static(eta_seconds)
        except Exception:
            total_channels = 0
            stream_count = 0
            eta_str = "unknown"

        if not self._try_start_thread(self._do_preview_stream_match, (dict(settings), logger)):
            return {"status": "error", "error": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Preview started: matching {total_channels} channels against {stream_count} streams. ETA: ~{eta_str}. Click 📊 Status to watch progress.",
            "background": True,
        }

    def _do_preview_stream_match(self, settings, logger):
        """Background thread for stream match preview."""
        try:
            numbering_mode = self._resolve_numbering_mode(settings)
            use_number_boost = (numbering_mode == "lineup")
            lineup = self._load_filtered_lineup(settings, logger)
            if lineup.get("status") == "error":
                return lineup
            matcher = self._init_fuzzy_matcher(settings, logger)
            alias_map_for = self._alias_map_provider(settings, logger)
            upgrade_twin_set = self._get_upgrade_twin_set(settings, lineup, matcher)
            streams = self._get_all_streams(settings, logger)
            assigner = self._init_assigner_state(settings)
            lineup_cc, _ = self._parse_lineup_filename(settings.get("lineup_file", ""))
            if lineup_cc:
                logger.info(f"{LOG_PREFIX} Filtering streams to country: {lineup_cc}")

            if not streams:
                logger.error(f"{LOG_PREFIX} No streams found. Check M3U sources.")
                send_websocket_update('updates', 'update', {
                    "type": "plugin", "plugin": "Lineuparr",
                    "message": "Preview failed: No streams found."
                })
                return

            # Deduplicate stream names for matching (performance: avoid redundant fuzzy comparisons)
            unique_stream_names = list(set(s['name'] for s in streams))
            stream_countries = self._stream_countries_by_name(streams)
            logger.info(f"{LOG_PREFIX} Matching against {len(unique_stream_names)} unique stream names (from {len(streams)} total)")

            # Pre-normalize stream names for performance
            matcher.precompute_normalizations(unique_stream_names)
            matcher.country_filter_drops = 0

            results = []
            matched_count = 0
            unmatched_count = 0
            perfect_count = 0
            type_counts = {}

            progress = ProgressTracker(
                sum(len(v) for v in lineup["categories"].values()),
                "preview_stream_match", logger
            )

            for category, channels in lineup["categories"].items():
                cat_cc = self._resolve_category_country(category, lineup_cc, logger)
                for entry in channels:
                    if self._stop_event.is_set():
                        logger.info(f"{LOG_PREFIX} Preview cancelled.")
                        return

                    ch_name = entry["name"]
                    entry_cc = self._resolve_entry_country(entry, cat_cc, logger)
                    ch_number = self._get_channel_number(settings, entry, assigner)
                    boost_number = self._parse_channel_number(entry.get("number")) if use_number_boost else None

                    matches = matcher.match_all_streams(
                        ch_name, unique_stream_names, alias_map_for(entry_cc),
                        channel_number=boost_number,
                        lineup_country=entry_cc,
                        quality_aware=(ch_name in upgrade_twin_set),
                        candidate_countries=stream_countries,
                    )

                    if matches:
                        best_name, best_score, best_type = matches[0]
                        matched_count += 1
                        if best_score == 100:
                            perfect_count += 1
                        # Track match type (strip score from "fuzzy (85)")
                        base_type = best_type.split(" (")[0] if " (" in best_type else best_type
                        type_counts[base_type] = type_counts.get(base_type, 0) + 1
                        all_matches_str = " | ".join(f"{m[0]} ({m[1]}%)" for m in matches[:5])
                        results.append({
                            "Channel": ch_name,
                            "Number": ch_number if ch_number else "",
                            "Category": category,
                            "Best Match": best_name,
                            "Score": best_score,
                            "Match Type": best_type,
                            "Total Matches": len(matches),
                            "Top Matches": all_matches_str,
                        })
                    else:
                        unmatched_count += 1
                        results.append({
                            "Channel": ch_name,
                            "Number": ch_number if ch_number else "",
                            "Category": category,
                            "Best Match": "NO MATCH",
                            "Score": 0,
                            "Match Type": "",
                            "Total Matches": 0,
                            "Top Matches": "",
                        })

                    progress.update()

            if matcher.country_filter_drops:
                logger.info(
                    f"{LOG_PREFIX} Country filter dropped "
                    f"{matcher.country_filter_drops} cross-country candidate(s)"
                )

            # Sort: unmatched first, then by score ascending
            results.sort(key=lambda r: (0 if r["Score"] == 0 else 1, r["Score"]))

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_name = f"lineuparr_preview_match_{ts}.csv"
            self._export_csv(csv_name, results,
                             ["Channel", "Number", "Category", "Best Match", "Score", "Match Type", "Total Matches", "Top Matches"],
                             logger, settings,
                             action_name="Preview Stream Match (dry run, no stream was attached)",
                             summary=[
                                 ("Channels matched", matched_count),
                                 ("Channels with no match", unmatched_count),
                                 ("Matches scoring 100", perfect_count),
                                 ("Cross-country candidates rejected",
                                  matcher.country_filter_drops),
                             ])
            report_path = self._write_html_report(
                "Preview stream match",
                [("Channel", "Channel"), ("Number", "Number"), ("Category", "Category"),
                 ("Best Match", "Best match"), ("Score", "Score"), ("Match Type", "Match type"),
                 ("Total Matches", "Total matches")],
                results, settings, logger, export_filename=csv_name)

            total = matched_count + unmatched_count
            type_breakdown = ", ".join(f"{v} {k}" for k, v in sorted(type_counts.items(), key=lambda x: -x[1]))
            msg = (
                f"Preview complete: {matched_count}/{total} matched "
                f"({perfect_count} perfect, {matched_count - perfect_count} partial), "
                f"{unmatched_count} unmatched. "
                f"Types: {type_breakdown}. CSV exported."
            )
            if report_path:
                msg += f" Report: {report_path}"

            progress.finish(summary=msg)
            logger.info(f"{LOG_PREFIX} {msg}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": msg
            })

        except Exception as e:
            logger.exception(f"{LOG_PREFIX} Preview stream match error: {e}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": f"Preview error: {e}"
            })

    # ========================================================================
    # DESTRUCTIVE ACTIONS
    # ========================================================================

    def _sync_groups(self, settings, logger):
        """Create/update ChannelGroups from lineup categories."""
        dry_run = settings.get("dry_run_mode", False)
        lineup = self._load_lineup(settings, logger)
        prefix = self._get_group_prefix(settings, lineup)
        rate_limiter = SmartRateLimiter(settings.get("rate_limiting", PluginConfig.DEFAULT_RATE_LIMITING))

        created = 0
        existed = 0

        for category in lineup["categories"]:
            group_name = self._make_group_name(prefix, category)

            if dry_run:
                exists = ChannelGroup.objects.filter(name=group_name).exists()
                if exists:
                    existed += 1
                else:
                    created += 1
                    logger.info(f"{LOG_PREFIX} [DRY RUN] Would create group: '{group_name}'")
            else:
                group, was_created = ChannelGroup.objects.get_or_create(name=group_name)
                if was_created:
                    created += 1
                    logger.info(f"{LOG_PREFIX} Created group: '{group_name}' (ID: {group.id})")
                else:
                    existed += 1

            rate_limiter.wait()

        if not dry_run:
            self._trigger_frontend_refresh(logger)

        prefix_str = f" (dry run)" if dry_run else ""
        return {
            "status": "ok",
            "message": f"Groups synced{prefix_str}: {created} created, {existed} already existed.",
        }

    def _sync_channels(self, settings, logger):
        """Create channels from lineup and assign to groups.
        Runs as daemon thread to avoid HTTP timeout."""
        # When called from _do_full_sync (already in a thread), run inline
        if threading.current_thread() is not threading.main_thread():
            return self._do_sync_channels(settings, logger)

        # Calculate initial ETA estimate
        total_channels = 0
        try:
            lineup = self._load_lineup(settings, logger)
            total_channels = sum(len(v) for v in lineup["categories"].values())
            eta_seconds = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_CHANNEL_SYNC
            eta_str = ProgressTracker._format_eta_static(eta_seconds)
        except Exception:
            eta_str = "unknown"

        if not self._try_start_thread(self._do_sync_channels_bg, (dict(settings), logger)):
            return {"status": "error", "error": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Sync Channels started: {total_channels} channels. ETA: ~{eta_str}. Click 📊 Status to watch progress.",
            "background": True,
        }

    def _do_sync_channels_bg(self, settings, logger):
        """Background wrapper that sends WebSocket updates on completion."""
        try:
            result = self._do_sync_channels(settings, logger)
            emoji = "✅" if result.get("status") == "ok" else "❌"
            msg = f"{emoji} SYNC CHANNELS COMPLETED: {result.get('message', '')}"
            logger.info(f"{LOG_PREFIX} {msg}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": msg
            })
        except Exception as e:
            logger.exception(f"{LOG_PREFIX} Sync channels error: {e}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": f"❌ Sync channels error: {e}"
            })

    def _do_sync_channels(self, settings, logger):
        """Core sync channels logic."""
        dry_run = settings.get("dry_run_mode", False)
        lineup = self._load_lineup(settings, logger)
        prefix = self._get_group_prefix(settings, lineup)
        rate_limiter = SmartRateLimiter(settings.get("rate_limiting", PluginConfig.DEFAULT_RATE_LIMITING))
        assigner = self._init_assigner_state(settings)

        # Ensure groups exist
        existing_groups = {g['name']: g['id'] for g in ChannelGroup.objects.all().values('id', 'name')}
        for category in lineup["categories"]:
            group_name = self._make_group_name(prefix, category)
            if group_name not in existing_groups:
                if dry_run:
                    logger.info(f"{LOG_PREFIX} [DRY RUN] Group '{group_name}' does not exist, would need sync_groups first")
                    return {"status": "error", "error": f"Group '{group_name}' does not exist. Run 'Sync Groups' first."}
                else:
                    group, _ = ChannelGroup.objects.get_or_create(name=group_name)
                    existing_groups[group_name] = group.id
                    logger.info(f"{LOG_PREFIX} Auto-created group: '{group_name}'")

        created = 0
        updated = 0
        unchanged = 0
        failed = 0
        synced_channel_ids = []

        total_channels = sum(len(v) for v in lineup["categories"].values())
        progress = ProgressTracker(total_channels, "sync_channels", logger)

        for category, channels in lineup["categories"].items():
            group_name = self._make_group_name(prefix, category)
            group_id = existing_groups[group_name]

            for entry in channels:
                ch_name = entry["name"]
                ch_number = self._get_channel_number(settings, entry, assigner)

                try:
                    if dry_run:
                        existing = Channel.objects.filter(name=ch_name, channel_group_id=group_id).values('id', 'channel_number').first()
                        if existing:
                            synced_channel_ids.append(existing['id'])
                            if ch_number is not None and existing['channel_number'] != ch_number:
                                updated += 1
                            else:
                                unchanged += 1
                        else:
                            created += 1
                    else:
                        defaults = {"channel_number": ch_number} if ch_number is not None else {}
                        ch, was_created = Channel.objects.get_or_create(
                            name=ch_name,
                            channel_group_id=group_id,
                            defaults=defaults
                        )
                        synced_channel_ids.append(ch.id)
                        if was_created:
                            created += 1
                            logger.debug(f"{LOG_PREFIX} Created channel: '{ch_name}' #{ch_number} in '{group_name}'")
                        else:
                            # Update channel number if different
                            if ch_number is not None and ch.channel_number != ch_number:
                                ch.channel_number = ch_number
                                ch.save(update_fields=['channel_number'])
                                updated += 1
                            else:
                                unchanged += 1

                except Exception as e:
                    logger.error(f"{LOG_PREFIX} Failed to sync channel '{ch_name}': {e}")
                    failed += 1

                rate_limiter.wait()
                progress.update()

        progress.finish()

        # Enable synced channels in configured profiles
        if not dry_run and synced_channel_ids:
            self._enable_channels_in_profiles(synced_channel_ids, settings, logger)

        if not dry_run:
            # Record what this run actually created, for the lifetime tally the
            # public badge sums. Guarded by dry_run because the preview branch
            # increments the same counter for channels it WOULD have made, so
            # recording unconditionally would inflate the badge on every Preview.
            self._record_channels_created(created, "sync_channels", logger)
            self._trigger_frontend_refresh(logger)

        prefix_str = " (dry run)" if dry_run else ""
        msg = f"Channels synced{prefix_str}: {created} created, {updated} updated, {unchanged} unchanged"
        if failed:
            msg += f", {failed} failed"
        total = created + updated + unchanged + failed
        if failed == 0 or total == 0:
            status = "ok"
        elif failed == total:
            status = "error"
        else:
            status = "warning"
        return action_result(status, msg)

    def _apply_stream_match(self, settings, logger):
        """Attach matched streams to channels with quality ordering.
        Runs as daemon thread to avoid HTTP timeout."""
        # When called from _do_full_sync (already in a thread), run inline
        if threading.current_thread() is not threading.main_thread():
            return self._do_apply_stream_match(settings, logger)

        try:
            lineup = self._load_lineup(settings, logger)
            total_channels = sum(len(v) for v in lineup["categories"].values())
            eta_seconds = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_STREAM_MATCH
            eta_str = ProgressTracker._format_eta_static(eta_seconds)
        except Exception:
            total_channels = 0
            eta_str = "unknown"

        if not self._try_start_thread(self._do_apply_stream_match_bg, (dict(settings), logger)):
            return {"status": "error", "error": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Stream matching started for {total_channels} channels. ETA: ~{eta_str}. Click 📊 Status to watch progress.",
            "background": True,
        }

    def _apply_epg_match(self, settings, logger):
        """Assign EPG data to channels via fuzzy matching.
        Runs as daemon thread to avoid HTTP timeout."""
        if not _EPG_AVAILABLE:
            return {"status": "error", "error": "EPG models not available in this Dispatcharr installation."}

        # When called from _do_full_sync (already in a thread), run inline
        if threading.current_thread() is not threading.main_thread():
            return self._do_apply_epg_match(settings, logger)

        try:
            lineup = self._load_lineup(settings, logger)
            total_channels = sum(len(v) for v in lineup["categories"].values())
            eta_seconds = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_EPG_MATCH
            eta_str = ProgressTracker._format_eta_static(eta_seconds)
        except Exception:
            total_channels = 0
            eta_str = "unknown"

        if not self._try_start_thread(self._do_apply_epg_match_bg, (dict(settings), logger)):
            return {"status": "error", "error": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"EPG matching started for {total_channels} channels. ETA: ~{eta_str}. Click 📊 Status to watch progress.",
            "background": True,
        }

    def _apply_logo_match(self, settings, logger):
        """Assign logos to channels via 3-tier fallback.
        Runs as daemon thread to avoid HTTP timeout."""
        if not _LOGO_AVAILABLE:
            return {"status": "error", "error": "Logo model not available in this Dispatcharr installation."}

        # When called from _do_full_sync (already in a thread), run inline
        if threading.current_thread() is not threading.main_thread():
            return self._do_apply_logo_match(settings, logger)

        try:
            lineup = self._load_lineup(settings, logger)
            total_channels = sum(len(v) for v in lineup["categories"].values())
            eta_seconds = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_LOGO_MATCH
            eta_str = ProgressTracker._format_eta_static(eta_seconds)
        except Exception:
            total_channels = 0
            eta_str = "unknown"

        if not self._try_start_thread(self._do_apply_logo_match_bg, (dict(settings), logger)):
            return {"status": "error", "error": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Logo assignment started for {total_channels} channels. ETA: ~{eta_str}. Click 📊 Status to watch progress.",
            "background": True,
        }

    def _do_apply_logo_match_bg(self, settings, logger):
        """Background wrapper for logo assignment."""
        try:
            result = self._do_apply_logo_match(settings, logger)
            msg = result_text(result, "Logo assignment complete.")
            logger.info(f"{LOG_PREFIX} LOGO MATCH COMPLETED: {msg}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": msg
            })
        except Exception as e:
            logger.exception(f"{LOG_PREFIX} Logo assignment error: {e}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": f"Logo assignment error: {e}"
            })

    def _do_apply_logo_match(self, settings, logger):
        """Core logo assignment logic: 3-tier fallback (EPG icon -> Logo Manager -> tv-logos)."""
        if not _LOGO_AVAILABLE:
            return {"status": "ok", "message": "Logo model not available. Skipping logo assignment."}

        if not self._acquire_lock(logger):
            return {"status": "error", "error": "Another operation is in progress. Try again later."}

        try:
            from .logo_matcher import (
                fetch_tv_logos_filelist, match_channel_to_logo,
                build_logo_url,
            )

            lineup = self._load_filtered_lineup(settings, logger)
            if lineup.get("status") == "error":
                return lineup
            prefix = self._get_group_prefix(settings, lineup)
            lineup_file = settings.get("lineup_file", "")
            cc, _ = self._parse_lineup_filename(lineup_file)
            country_suffix = cc.lower() if cc else "us"

            # Get existing groups and channels (with logo and epg_data)
            existing_groups = {g['name']: g['id'] for g in ChannelGroup.objects.all().values('id', 'name')}

            total_channels = sum(len(v) for v in lineup["categories"].values())
            progress = ProgressTracker(total_channels, "apply_logo_match", logger)

            # Pre-fetch: all channels with their current logo and EPG data
            channel_qs = Channel.objects.select_related('epg_data', 'logo').filter(
                channel_group_id__in=existing_groups.values()
            )
            channel_lookup = {}
            for ch in channel_qs:
                channel_lookup[(ch.name, ch.channel_group_id)] = ch

            # Pre-fetch: all existing logos by URL and by name (case-insensitive)
            existing_logos_by_url = {logo.url: logo for logo in Logo.objects.all()}
            existing_logos_by_name = {}
            for logo in existing_logos_by_url.values():
                existing_logos_by_name.setdefault(logo.name.lower(), logo)

            # Tier 3 prep: fetch tv-logos file list
            country_dir = PluginConfig.COUNTRY_DIR_MAP.get(cc, "") if cc else ""
            tv_logo_files = []
            if country_dir:
                logger.info(f"{LOG_PREFIX} Fetching tv-logos file list for {country_dir}...")
                tv_logo_files = fetch_tv_logos_filelist(
                    PluginConfig.TV_LOGOS_REPO,
                    PluginConfig.TV_LOGOS_BRANCH,
                    country_dir,
                )
                logger.info(f"{LOG_PREFIX} Found {len(tv_logo_files)} logos in tv-logos/{country_dir}")
            else:
                logger.warning(f"{LOG_PREFIX} No tv-logos directory mapping for country code '{cc}'. Tier 3 disabled.")

            assigned_epg = 0
            assigned_manager = 0
            assigned_tvlogos = 0
            skipped_has_logo = 0
            skipped_no_match = 0
            channels_to_update = []

            for category, channels in lineup["categories"].items():
                group_name = self._make_group_name(prefix, category)
                group_id = existing_groups.get(group_name)

                if not group_id:
                    for _ in channels:
                        progress.update()
                    continue

                for entry in channels:
                    if self._stop_event.is_set():
                        logger.info(f"{LOG_PREFIX} Logo assignment cancelled.")
                        return {"status": "ok", "message": "Logo assignment cancelled by user."}

                    ch_name = entry["name"]
                    ch = channel_lookup.get((ch_name, group_id))

                    if not ch:
                        progress.update()
                        continue

                    # Skip channels that already have a logo
                    if ch.logo_id is not None:
                        skipped_has_logo += 1
                        progress.update()
                        continue

                    logo = None
                    source = None

                    # Tier 1: EPG icon
                    if ch.epg_data and getattr(ch.epg_data, 'icon_url', None):
                        icon_url = ch.epg_data.icon_url.strip()
                        if icon_url:
                            logo = existing_logos_by_url.get(icon_url)
                            if not logo:
                                try:
                                    logo = Logo.objects.create(
                                        name=ch.epg_data.name or ch_name,
                                        url=icon_url,
                                    )
                                    existing_logos_by_url[icon_url] = logo
                                except Exception as e:
                                    logger.debug(f"{LOG_PREFIX} Failed to create logo from EPG icon for {ch_name}: {e}")
                                    logo = None
                            if logo:
                                source = "EPG"

                    # Tier 2: Logo Manager (case-insensitive exact match)
                    if not logo:
                        logo = existing_logos_by_name.get(ch_name.lower())
                        if logo:
                            source = "Logo Manager"

                    # Tier 3: tv-logos GitHub
                    if not logo and tv_logo_files:
                        matched_file = match_channel_to_logo(ch_name, tv_logo_files, country_suffix)
                        if matched_file:
                            raw_url = build_logo_url(
                                PluginConfig.TV_LOGOS_REPO,
                                PluginConfig.TV_LOGOS_BRANCH,
                                country_dir,
                                matched_file,
                            )
                            logo = existing_logos_by_url.get(raw_url)
                            if not logo:
                                try:
                                    logo = Logo.objects.create(name=ch_name, url=raw_url)
                                    existing_logos_by_url[raw_url] = logo
                                except Exception as e:
                                    logger.debug(f"{LOG_PREFIX} Failed to create logo from tv-logos for {ch_name}: {e}")
                                    logo = None
                            if logo:
                                source = "tv-logos"

                    # Assign logo if found
                    if logo:
                        ch.logo = logo
                        channels_to_update.append(ch)
                        if source == "EPG":
                            assigned_epg += 1
                        elif source == "Logo Manager":
                            assigned_manager += 1
                        elif source == "tv-logos":
                            assigned_tvlogos += 1
                        logger.debug(f"{LOG_PREFIX} Logo: {ch_name} <- {source} ({logo.name})")
                    else:
                        skipped_no_match += 1

                    progress.update()

            # Bulk update
            if channels_to_update:
                with transaction.atomic():
                    Channel.objects.bulk_update(channels_to_update, ['logo_id'])
                logger.info(f"{LOG_PREFIX} Bulk-updated logos for {len(channels_to_update)} channels")

            progress.finish()
            self._trigger_frontend_refresh(logger)

            total_assigned = assigned_epg + assigned_manager + assigned_tvlogos
            msg = (
                f"Logo assignment complete: {total_assigned} assigned "
                f"(EPG: {assigned_epg}, Logo Manager: {assigned_manager}, tv-logos: {assigned_tvlogos}), "
                f"{skipped_has_logo} already had logos, {skipped_no_match} no match"
            )
            logger.info(f"{LOG_PREFIX} {msg}")
            return {"status": "ok", "message": msg}

        finally:
            self._release_lock(logger)

    def _do_apply_stream_match_bg(self, settings, logger):
        """Background wrapper that sends WebSocket updates on completion."""
        try:
            result = self._do_apply_stream_match(settings, logger)
            msg = result_text(result, "Stream matching complete.")
            logger.info(f"{LOG_PREFIX} ✅ APPLY STREAM MATCH COMPLETED: {msg}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": msg
            })
        except Exception as e:
            logger.exception(f"{LOG_PREFIX} Apply stream match error: {e}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": f"Stream match error: {e}"
            })

    def _filter_lineup_to_channel(self, lineup, raw_name, logger):
        """Return a name-filtered copy of `lineup`, or an error result dict.

        Returns `{"status": "error", ...}` on an empty or unmatched
        `raw_name`; the caller must return that dict verbatim. Never
        mutates the input lineup.
        """
        target = raw_name.strip().casefold()
        if not target:
            return {"status": "error", "error": "Channel name must not be empty."}
        src_categories = lineup.get("categories", {})

        filtered_categories = {}
        match_count = 0
        for category, entries in src_categories.items():
            kept = [e for e in entries
                    if str(e.get("name", "")).strip().casefold() == target]
            if kept:
                filtered_categories[category] = kept
                match_count += len(kept)

        if match_count == 0:
            hints = []
            for entries in src_categories.values():
                for e in entries:
                    nm = str(e.get("name", "")).strip()
                    if target in nm.casefold():
                        hints.append(nm)
                        if len(hints) >= 10:
                            break
                if len(hints) >= 10:
                    break
            msg = f"No lineup channel named '{raw_name}' found."
            if hints:
                msg += " Did you mean: " + ", ".join(hints) + "?"
            logger.warning(f"{LOG_PREFIX} Single-channel filter: {msg}")
            return {"status": "error", "error": msg}

        suffix = "y" if match_count == 1 else "ies"
        logger.info(
            f"{LOG_PREFIX} Single-channel filter: processing "
            f"{match_count} entr{suffix} named '{raw_name}'"
        )
        new_lineup = dict(lineup)
        new_lineup["categories"] = filtered_categories
        return new_lineup

    def _load_filtered_lineup(self, settings, logger):
        """Load the lineup, then narrow it to `single_channel_name` if set.

        Returns the (possibly filtered) lineup dict, or the helper's
        error result dict on no match. Callers must return the result
        verbatim when it is an error dict (status == "error").
        """
        lineup = self._load_lineup(settings, logger)
        single_name = (settings.get("single_channel_name") or "").strip()
        if single_name:
            return self._filter_lineup_to_channel(lineup, single_name, logger)
        return lineup

    def _do_apply_stream_match(self, settings, logger):
        """Core stream matching logic (called from thread)."""
        dry_run = settings.get("dry_run_mode", False)
        use_number_boost = (self._resolve_numbering_mode(settings) == "lineup")
        prioritize_quality = settings.get("prioritize_quality", PluginConfig.DEFAULT_PRIORITIZE_QUALITY)
        preserve = settings.get("preserve_existing_streams", False)

        if not self._acquire_lock(logger):
            return {"status": "error", "error": "Another operation is in progress. Try again later."}

        try:
            lineup = self._load_filtered_lineup(settings, logger)
            if lineup.get("status") == "error":
                return lineup
            prefix = self._get_group_prefix(settings, lineup)
            matcher = self._init_fuzzy_matcher(settings, logger)
            alias_map_for = self._alias_map_provider(settings, logger)
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", PluginConfig.DEFAULT_RATE_LIMITING))
            lineup_cc, _ = self._parse_lineup_filename(settings.get("lineup_file", ""))
            if lineup_cc:
                logger.info(f"{LOG_PREFIX} Filtering streams to country: {lineup_cc}")

            # Get streams
            all_streams = self._get_all_streams(settings, logger)
            if not all_streams:
                return {"status": "error", "error": "No streams found. Check M3U sources."}

            # Build name -> stream objects lookup
            stream_by_name = {}
            for s in all_streams:
                stream_by_name.setdefault(s['name'], []).append(s)

            # Deduplicate stream names for matching performance
            unique_stream_names = list(stream_by_name.keys())
            stream_countries = self._stream_countries_by_name(all_streams)
            logger.info(f"{LOG_PREFIX} Matching against {len(unique_stream_names)} unique stream names (from {len(all_streams)} total)")

            # Pre-normalize stream names for performance
            matcher.precompute_normalizations(unique_stream_names)
            matcher.country_filter_drops = 0

            upgrade_twin_set = self._get_upgrade_twin_set(settings, lineup, matcher, logger)

            # Get existing groups and channels
            existing_groups = {g['name']: g['id'] for g in ChannelGroup.objects.all().values('id', 'name')}
            existing_channels = {}
            for ch in Channel.objects.all().values('id', 'name', 'channel_group_id'):
                existing_channels[(ch['name'], ch['channel_group_id'])] = ch['id']

            total_channels = sum(len(v) for v in lineup["categories"].values())
            progress = ProgressTracker(total_channels, "apply_stream_match", logger)

            channels_matched = 0
            channels_unmatched = 0
            total_streams_attached = 0
            csv_rows = []

            for category, channels in lineup["categories"].items():
                group_name = self._make_group_name(prefix, category)
                group_id = existing_groups.get(group_name)

                if not group_id:
                    logger.warning(f"{LOG_PREFIX} Group '{group_name}' not found, skipping category '{category}'")
                    for entry in channels:
                        channels_unmatched += 1
                        progress.update()
                    continue

                cat_cc = self._resolve_category_country(category, lineup_cc, logger)

                for entry in channels:
                    if self._stop_event.is_set():
                        logger.info(f"{LOG_PREFIX} Stream matching cancelled.")
                        return {"status": "ok", "message": "Stream matching cancelled by user."}

                    ch_name = entry["name"]
                    entry_cc = self._resolve_entry_country(entry, cat_cc, logger)
                    ch_number = self._parse_channel_number(entry.get("number")) if use_number_boost else None
                    channel_id = existing_channels.get((ch_name, group_id))

                    if not channel_id:
                        logger.debug(f"{LOG_PREFIX} Channel '{ch_name}' not in DB, skipping")
                        channels_unmatched += 1
                        progress.update()
                        continue

                    # Match streams using deduplicated names
                    matches = matcher.match_all_streams(
                        ch_name, unique_stream_names, alias_map_for(entry_cc),
                        channel_number=ch_number,
                        lineup_country=entry_cc,
                        quality_aware=(ch_name in upgrade_twin_set),
                        candidate_countries=stream_countries,
                    )

                    if matches:
                        # Expand matched names back to all stream objects
                        matched_stream_objs = []
                        for match_name, score, mtype in matches:
                            for stream_obj in stream_by_name.get(match_name, []):
                                stream_obj_copy = dict(stream_obj)
                                stream_obj_copy['_match_score'] = score
                                stream_obj_copy['_match_type'] = mtype
                                matched_stream_objs.append(stream_obj_copy)

                        sorted_streams = self._sort_streams_by_quality(matched_stream_objs, prioritize_quality)
                        attached_count = len(sorted_streams)

                        if not dry_run:
                            if preserve:
                                try:
                                    existing = list(
                                        ChannelStream.objects.filter(
                                            channel_id=channel_id
                                        ).values_list('stream_id', 'order')
                                    )
                                    existing_ids = {sid for sid, _ in existing}
                                    next_order = max((o for _, o in existing), default=-1) + 1
                                    new_streams = [
                                        s for s in sorted_streams
                                        if s['id'] not in existing_ids
                                    ]
                                    with transaction.atomic():
                                        for idx, stream in enumerate(new_streams):
                                            ChannelStream.objects.create(
                                                channel_id=channel_id,
                                                stream_id=stream['id'],
                                                order=next_order + idx
                                            )
                                    total_streams_attached += len(new_streams)
                                    attached_count = len(new_streams)
                                except Exception as e:
                                    attached_count = 0
                                    logger.error(f"{LOG_PREFIX} Failed to append streams to '{ch_name}': {e}")
                            else:
                                try:
                                    # Atomic: delete old + create new in single transaction
                                    with transaction.atomic():
                                        ChannelStream.objects.filter(channel_id=channel_id).delete()
                                        for idx, stream in enumerate(sorted_streams):
                                            ChannelStream.objects.create(
                                                channel_id=channel_id,
                                                stream_id=stream['id'],
                                                order=idx
                                            )
                                    total_streams_attached += len(sorted_streams)
                                except Exception as e:
                                    logger.error(f"{LOG_PREFIX} Failed to attach streams to '{ch_name}': {e}")
                        else:
                            if preserve:
                                # Read-only dedupe so the dry-run CSV reports the
                                # count a real preserve run would actually append.
                                existing_ids = set(
                                    ChannelStream.objects.filter(
                                        channel_id=channel_id
                                    ).values_list('stream_id', flat=True)
                                )
                                new_streams = [
                                    s for s in sorted_streams
                                    if s['id'] not in existing_ids
                                ]
                                total_streams_attached += len(new_streams)
                                attached_count = len(new_streams)
                            else:
                                total_streams_attached += len(sorted_streams)

                        channels_matched += 1
                        csv_rows.append({
                            "Channel": ch_name,
                            "Number": ch_number if ch_number else "",
                            "Category": category,
                            "Streams Attached": attached_count,
                            "Best Match": matches[0][0],
                            "Best Score": matches[0][1],
                            "Match Type": matches[0][2],
                        })
                    else:
                        channels_unmatched += 1
                        csv_rows.append({
                            "Channel": ch_name,
                            "Number": ch_number if ch_number else "",
                            "Category": category,
                            "Streams Attached": 0,
                            "Best Match": "NO MATCH",
                            "Best Score": 0,
                            "Match Type": "",
                        })

                    rate_limiter.wait()
                    progress.update()

            progress.finish()

            if matcher.country_filter_drops:
                logger.info(
                    f"{LOG_PREFIX} Country filter dropped "
                    f"{matcher.country_filter_drops} cross-country candidate(s)"
                )

            # Export CSV
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode = "dryrun" if dry_run else "applied"
            csv_name = f"lineuparr_match_{mode}_{ts}.csv"
            # The unmatched-channel cleanup runs after this export is written,
            # so its count is not known here and is deliberately not claimed.
            # The Preserve Existing Streams line in the preamble says whether
            # that cleanup can happen at all.
            self._export_csv(
                csv_name, csv_rows,
                ["Channel", "Number", "Category", "Streams Attached", "Best Match", "Best Score", "Match Type"],
                logger, settings,
                action_name=("Apply Stream Match (dry run, nothing was written)"
                             if dry_run else "Apply Stream Match"),
                summary=[
                    ("Channels matched", channels_matched),
                    ("Channels with no match", channels_unmatched),
                    ("Streams attached", total_streams_attached),
                    ("Cross-country candidates rejected", matcher.country_filter_drops),
                ]
            )
            self._write_html_report(
                "Stream match (dry run)" if dry_run else "Stream match applied",
                [("Channel", "Channel"), ("Number", "Number"), ("Category", "Category"),
                 ("Streams Attached", "Streams attached"), ("Best Match", "Best match"),
                 ("Best Score", "Best score"), ("Match Type", "Match type")],
                csv_rows, settings, logger, export_filename=csv_name)

            # Save state
            self._save_state({
                "last_sync": datetime.now().isoformat(),
                "lineup_file": settings.get("lineup_file", ""),
                "channels_matched": channels_matched,
                "channels_unmatched": channels_unmatched,
                "streams_attached": total_streams_attached,
                "dry_run": dry_run,
            }, logger)

            # Cleanup: remove channels with no streams in Lineuparr-managed groups.
            # Skipped in preserve mode: a no-match channel may still hold streams
            # from another source the user explicitly asked us not to disturb.
            cleanup_count = 0
            if not dry_run and channels_unmatched > 0 and not preserve:
                lineup = self._load_lineup(settings, logger)
                prefix = self._get_group_prefix(settings, lineup)
                lineuparr_group_names = [
                    self._make_group_name(prefix, cat) for cat in lineup["categories"]
                ]
                lineuparr_groups = ChannelGroup.objects.filter(name__in=lineuparr_group_names).values_list('id', flat=True)
                # Find channels in Lineuparr groups with zero streams
                from django.db.models import Count
                empty_channels = Channel.objects.filter(
                    channel_group_id__in=lineuparr_groups
                ).annotate(
                    stream_count=Count('channelstream')
                ).filter(stream_count=0)
                cleanup_count = empty_channels.count()
                if cleanup_count > 0:
                    empty_channels.delete()
                    logger.info(f"{LOG_PREFIX} Cleanup: removed {cleanup_count} channels with no streams")

            if not dry_run:
                self._trigger_frontend_refresh(logger)

        finally:
            self._release_lock(logger)

        prefix_str = " (dry run)" if dry_run else ""
        msg = (
            f"Stream matching{prefix_str}: {channels_matched} channels matched, "
            f"{channels_unmatched} unmatched, {total_streams_attached} streams attached."
        )
        if cleanup_count > 0:
            msg += f" Cleaned up {cleanup_count} unmatched channels."
        msg += " CSV exported."
        logger.info(f"{LOG_PREFIX} {msg}")
        return {"status": "ok", "message": msg}

    def _refresh_epg_sources(self, source_ids, logger):
        """Trigger a Dispatcharr EPG refresh for the given EPGSource IDs.

        Called after EPG matching so newly matched channels pick up program
        info. source_ids are the sources that were actually matched to lineup
        channels (and, by construction, are within the user's "EPG Sources for
        Matching" selection). Each refresh is dispatched as an async Celery task
        so it does not block the matching operation. Returns the count of
        sources for which a refresh was dispatched.
        """
        if not _EPG_AVAILABLE:
            return 0
        ids = sorted({sid for sid in source_ids if sid})
        if not ids:
            return 0
        try:
            from apps.epg.tasks import refresh_epg_data
        except Exception as e:
            logger.warning(f"{LOG_PREFIX} EPG refresh task unavailable, skipping EPG refresh: {e}")
            return 0
        # Map IDs to names for readable logging.
        names = {}
        try:
            for src in EPGSource.objects.filter(id__in=ids).values('id', 'name'):
                names[src['id']] = src['name']
        except Exception:
            pass
        triggered = 0
        for sid in ids:
            try:
                refresh_epg_data.delay(sid)
                triggered += 1
                logger.info(f"{LOG_PREFIX} Triggered EPG refresh for source '{names.get(sid, sid)}' (id={sid})")
            except Exception as e:
                logger.warning(f"{LOG_PREFIX} Failed to trigger EPG refresh for source id={sid}: {e}")
        logger.info(f"{LOG_PREFIX} EPG refresh dispatched for {triggered}/{len(ids)} matched source(s)")
        return triggered

    def _do_apply_epg_match_bg(self, settings, logger):
        """Background wrapper for EPG matching."""
        try:
            result = self._do_apply_epg_match(settings, logger)
            msg = result_text(result, "EPG matching complete.")
            logger.info(f"{LOG_PREFIX} EPG MATCH COMPLETED: {msg}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": msg
            })
        except Exception as e:
            logger.exception(f"{LOG_PREFIX} EPG match error: {e}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": f"EPG match error: {e}"
            })

    def _do_apply_epg_match(self, settings, logger):
        """Core EPG matching logic: fuzzy-match EPG entries to Lineuparr-managed channels."""
        if not _EPG_AVAILABLE:
            return {"status": "ok", "message": "EPG models not available. Skipping EPG matching."}

        if not self._acquire_lock(logger):
            return {"status": "error", "error": "Another operation is in progress. Try again later."}

        try:
            use_number_boost = (self._resolve_numbering_mode(settings) == "lineup")
            lineup = self._load_filtered_lineup(settings, logger)
            if lineup.get("status") == "error":
                return lineup
            prefix = self._get_group_prefix(settings, lineup)
            matcher = self._init_fuzzy_matcher(settings, logger)
            alias_map_for = self._alias_map_provider(settings, logger)
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", PluginConfig.DEFAULT_RATE_LIMITING))

            # Extract lineup country code for EPG country matching
            lineup_file = settings.get("lineup_file", "")
            lineup_cc, _ = self._parse_lineup_filename(os.path.basename(lineup_file))
            if lineup_cc:
                logger.info(f"{LOG_PREFIX} Lineup country code: {lineup_cc} (will prefer EPG entries with matching country)")

            # Fetch filtered EPG data
            epg_data = self._get_filtered_epg_data(settings, logger)
            if not epg_data:
                return {"status": "ok", "message": "No EPG data available. Skipping EPG matching."}

            # Pre-fetch program data availability
            all_epg_ids = [e['id'] for e in epg_data]
            epg_ids_with_programs = self._get_epg_ids_with_programs(all_epg_ids, logger)

            # Pre-filter: only match against EPG entries that have program data
            # This dramatically reduces the candidate pool (e.g., 575 vs 20,683)
            epg_data_with_programs = [e for e in epg_data if e['id'] in epg_ids_with_programs]
            logger.info(f"{LOG_PREFIX} Pre-filtered to {len(epg_data_with_programs)} EPG entries with program data (from {len(epg_data)} total)")

            if not epg_data_with_programs:
                # Do NOT stop here. Dispatcharr downloads programme data only for
                # EPG entries that are already mapped to a channel, and mapping
                # them is what this action does, so a source that has never been
                # matched always has zero programmes. Stopping would make such a
                # source permanently unmatchable. The second match pass below
                # searches every entry regardless of programme data, so let the
                # run continue and fall through to it.
                logger.warning(
                    f"{LOG_PREFIX} No EPG entry has programme data in the next 12 hours. "
                    f"Matching against all {len(epg_data)} entries instead. This is normal "
                    f"for an EPG source that has never been matched to a channel: Dispatcharr "
                    f"fetches programmes only for mapped entries."
                )

            # Build source ID -> name lookup for CSV
            epg_source_names = {}
            try:
                for src in EPGSource.objects.all().values('id', 'name'):
                    epg_source_names[src['id']] = src['name']
            except Exception:
                pass

            # Build EPG name list and name->entries lookup (only entries with program data)
            epg_by_name = {}
            for e in epg_data_with_programs:
                epg_by_name.setdefault(e['name'], []).append(e)
            unique_epg_names = list(epg_by_name.keys())

            # Build fallback lookup from ALL EPG entries (for channels with no program-data match)
            epg_by_name_all = {}
            for e in epg_data:
                epg_by_name_all.setdefault(e['name'], []).append(e)
            unique_epg_names_all = list(epg_by_name_all.keys())

            # Pre-normalize EPG names for matching performance
            matcher.precompute_normalizations(unique_epg_names_all)

            # Get existing groups and channels
            existing_groups = {g['name']: g['id'] for g in ChannelGroup.objects.all().values('id', 'name')}
            existing_channels = {}
            for ch in Channel.objects.all().values('id', 'name', 'channel_group_id', 'epg_data_id'):
                existing_channels[(ch['name'], ch['channel_group_id'])] = ch

            total_channels = sum(len(v) for v in lineup["categories"].values())
            progress = ProgressTracker(total_channels, "apply_epg_match", logger)

            matched = 0
            matched_fallback = 0
            skipped_no_match = 0
            skipped_existing = 0
            csv_rows = []
            epg_assignments = []
            # EPGSource IDs that were actually matched to a lineup channel. These
            # already come only from the selected "EPG Sources for Matching"
            # (see _get_filtered_epg_data), so they are exactly the sources to
            # refresh: matched AND user-selected.
            matched_epg_source_ids = set()

            # (pool, name->entries, has_program_data) in priority order.
            match_passes = [
                (unique_epg_names, epg_by_name, True),
                (unique_epg_names_all, epg_by_name_all, False),
            ]

            for category, channels in lineup["categories"].items():
                group_name = self._make_group_name(prefix, category)
                group_id = existing_groups.get(group_name)

                if not group_id:
                    for _ in channels:
                        skipped_no_match += 1
                        progress.update()
                    continue

                cat_cc = self._resolve_category_country(category, lineup_cc, logger)

                for entry in channels:
                    if self._stop_event.is_set():
                        logger.info(f"{LOG_PREFIX} EPG matching cancelled.")
                        return {"status": "ok", "message": "EPG matching cancelled by user."}

                    ch_name = entry["name"]
                    entry_cc = self._resolve_entry_country(entry, cat_cc, logger)
                    # Channel country first, then relaxed (None) if nothing matched.
                    countries = (entry_cc, None) if entry_cc else (None,)
                    ch_number = self._parse_channel_number(entry.get("number")) if use_number_boost else None
                    ch_data = existing_channels.get((ch_name, group_id))

                    if not ch_data:
                        skipped_no_match += 1
                        progress.update()
                        continue

                    channel_id = ch_data['id']

                    # Skip channels that already have a manually-assigned EPG entry
                    if ch_data.get('epg_data_id'):
                        skipped_existing += 1
                        progress.update()
                        continue

                    # Try program-data entries before all entries, and the lineup
                    # country before relaxing it, so a cross-border channel (e.g.
                    # an NL lineup's ZDF, tagged "┃DE┃") still gets its foreign EPG.
                    best_epg = None
                    best_score = 0
                    best_method = None
                    has_program_data = True

                    for country in countries:
                        for pool, lookup, has_prog in match_passes:
                            if not pool:
                                continue
                            ms = matcher.match_all_streams(
                                ch_name, pool, alias_map_for(entry_cc),
                                channel_number=ch_number,
                                lineup_country=country,
                            )
                            if not ms:
                                continue
                            top_name, top_score, top_method = ms[0]
                            top_entries = lookup.get(top_name, [])
                            if not top_entries:
                                continue
                            best_epg = self._pick_epg_by_country(top_entries, entry_cc)
                            best_score = top_score
                            best_method = top_method
                            has_program_data = has_prog
                            if not has_prog:
                                logger.debug(f"{LOG_PREFIX} EPG fallback (no program data): {ch_name} -> {best_epg['name']}")
                            if country is None and entry_cc:
                                logger.debug(f"{LOG_PREFIX} EPG country-relaxed match: {ch_name} -> {best_epg['name']}")
                            break
                        if best_epg:
                            break

                    # Build CSV row
                    row = {
                        "channel_name": ch_name,
                        "channel_number": ch_number or "",
                        "channel_group": group_name,
                        "matched_epg_name": best_epg['name'] if best_epg else "",
                        "epg_source": epg_source_names.get(best_epg.get('epg_source'), '') if best_epg else "",
                        "confidence_score": best_score if best_epg else 0,
                        "has_program_data": "Yes" if (best_epg and has_program_data) else ("Fallback" if best_epg else "No"),
                        "match_method": best_method or "",
                        "status": "",
                    }

                    if best_epg:
                        epg_assignments.append((channel_id, best_epg['id']))
                        if best_epg.get('epg_source'):
                            matched_epg_source_ids.add(best_epg.get('epg_source'))
                        matched += 1
                        if not has_program_data:
                            matched_fallback += 1
                        row["status"] = "Matched" if has_program_data else "Matched (no programs)"
                        logger.debug(f"{LOG_PREFIX} EPG match: {ch_name} -> {best_epg['name']} ({best_score}%, {best_method})")
                    else:
                        skipped_no_match += 1
                        row["status"] = "No match"

                    csv_rows.append(row)
                    progress.update()
                    rate_limiter.wait()

            # Apply EPG assignments in bulk
            if epg_assignments:
                channels_to_update = []
                ch_objects = {ch.id: ch for ch in Channel.objects.filter(id__in=[a[0] for a in epg_assignments])}
                for channel_id, epg_data_id in epg_assignments:
                    ch = ch_objects.get(channel_id)
                    if ch:
                        ch.epg_data_id = epg_data_id
                        channels_to_update.append(ch)
                if channels_to_update:
                    with transaction.atomic():
                        Channel.objects.bulk_update(channels_to_update, ['epg_data_id'])
                    logger.info(f"{LOG_PREFIX} Bulk-updated EPG for {len(channels_to_update)} channels")

            # Optionally refresh the EPG sources we matched against, so the newly
            # matched channels pick up program info. Defaults on.
            refresh_note = ""
            if settings.get("refresh_epg_after_match", True) and matched_epg_source_ids:
                refreshed = self._refresh_epg_sources(matched_epg_source_ids, logger)
                if refreshed:
                    refresh_note = f", refreshing EPG for {refreshed} source(s)"

            # Export CSV
            if csv_rows:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                csv_name = f"lineuparr_epg_match_{timestamp}.csv"
                self._export_csv(
                    csv_name,
                    csv_rows,
                    ["channel_name", "channel_number", "channel_group", "matched_epg_name",
                     "epg_source", "confidence_score", "has_program_data", "match_method", "status"],
                    logger, settings,
                    action_name="Apply EPG Match",
                    summary=[
                        ("Channels given a guide", matched),
                        ("Channels with no match", skipped_no_match),
                        ("Channels skipped, already had a guide", skipped_existing),
                        ("Matched without programme data yet", matched_fallback),
                    ],
                )
                self._write_html_report(
                    "EPG match",
                    [("channel_name", "Channel"), ("channel_number", "Number"),
                     ("channel_group", "Group"), ("matched_epg_name", "Matched EPG entry"),
                     ("epg_source", "EPG source"), ("confidence_score", "Score"),
                     ("has_program_data", "Has programme data"), ("match_method", "Match method"),
                     ("status", "Status")],
                    csv_rows, settings, logger, export_filename=csv_name)

            progress.finish()
            self._trigger_frontend_refresh(logger)

            fallback_note = f" ({matched_fallback} via fallback without program data)" if matched_fallback else ""
            existing_note = f", {skipped_existing} skipped (already assigned)" if skipped_existing else ""
            msg = f"EPG matching complete: {matched} matched{fallback_note}, {skipped_no_match} no match{existing_note} (from {len(epg_data_with_programs)} EPG entries with program data){refresh_note}"
            logger.info(f"{LOG_PREFIX} {msg}")
            return {"status": "ok", "message": msg}

        finally:
            self._release_lock(logger)

    def _resort_streams(self, settings, logger):
        """Re-order already-attached streams by quality using latest stream_stats data.
        Much faster than Apply Stream Match since it skips fuzzy matching."""
        prioritize_quality = settings.get("prioritize_quality", PluginConfig.DEFAULT_PRIORITIZE_QUALITY)
        lineup = self._load_lineup(settings, logger)
        prefix = self._get_group_prefix(settings, lineup)

        # Find all Lineuparr-managed groups
        lineuparr_group_names = [
            self._make_group_name(prefix, cat) for cat in lineup["categories"]
        ]
        lineuparr_group_ids = set(ChannelGroup.objects.filter(
            name__in=lineuparr_group_names
        ).values_list('id', flat=True))

        if not lineuparr_group_ids:
            return {"status": "error", "error": "No Lineuparr channel groups found. Run Full Sync first."}

        # Get all channels in Lineuparr groups that have streams attached
        channels_with_streams = Channel.objects.filter(
            channel_group_id__in=lineuparr_group_ids
        ).prefetch_related('channelstream_set__stream').all()

        resorted = 0
        for channel in channels_with_streams:
            cs_entries = list(channel.channelstream_set.select_related('stream').all())
            if len(cs_entries) <= 1:
                continue  # Nothing to sort

            # Build stream dicts with stats for sorting
            stream_dicts = []
            for cs in cs_entries:
                s = cs.stream
                stats = s.stream_stats if s.stream_stats else {}
                stream_dicts.append({
                    'cs_id': cs.id,
                    'name': s.name,
                    '_stream_stats': stats,
                    '_m3u_priority': 999,
                })

            # Sort by quality
            sorted_streams = self._sort_streams_by_quality(stream_dicts, prioritize_quality)

            # Check if order actually changed
            old_order = [sd['cs_id'] for sd in stream_dicts]
            new_order = [sd['cs_id'] for sd in sorted_streams]
            if old_order != new_order:
                # Update order fields
                for idx, sd in enumerate(sorted_streams):
                    ChannelStream.objects.filter(id=sd['cs_id']).update(order=idx)
                resorted += 1

        total_channels = len([c for c in channels_with_streams if c.channelstream_set.count() > 0])
        msg = f"Re-sorted streams for {resorted} channels (out of {total_channels} with streams)."
        if resorted == 0:
            msg = f"All {total_channels} channels already in optimal order."
        logger.info(f"{LOG_PREFIX} {msg}")
        return {"status": "ok", "message": msg}

    def _full_sync(self, settings, logger):
        """Run all steps: groups -> channels -> stream matching -> EPG matching -> logo assignment."""
        # Calculate initial ETA estimate for all 5 steps
        try:
            lineup = self._load_lineup(settings, logger)
            total_channels = sum(len(v) for v in lineup["categories"].values())
            total_categories = len(lineup["categories"])
            eta_groups = total_categories * 0.02
            eta_channels = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_CHANNEL_SYNC
            eta_streams = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_STREAM_MATCH
            eta_epg = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_EPG_MATCH
            eta_logos = total_channels * PluginConfig.ESTIMATED_SECONDS_PER_LOGO_MATCH
            eta_total = eta_groups + eta_channels + eta_streams + eta_epg + eta_logos
            eta_str = ProgressTracker._format_eta_static(eta_total)
            eta_msg = f" ETA: ~{eta_str} ({total_categories} groups, {total_channels} channels)."
        except Exception:
            eta_msg = ""

        if not self._try_start_thread(self._do_full_sync, (dict(settings), logger)):
            return {"status": "error", "error": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Full Sync started: groups, channels, stream matching, EPG matching, and logo assignment.{eta_msg} Click 📊 Status to watch progress.",
            "background": True,
        }

    def _do_full_sync(self, settings, logger):
        """Background thread for full sync."""
        # Sub-steps each call _trigger_frontend_refresh; suppress those so a
        # Full Sync emits exactly one channel-list refresh, at the end.
        self._suppress_refresh = True
        try:
            # Full Sync is whole-lineup by contract. Its sub-steps route
            # through the same _do_apply_* methods that honor
            # single_channel_name, so neutralize it on a local copy here
            # rather than threading a flag through four signatures.
            settings = {**settings, "single_channel_name": ""}
            logger.info(f"{LOG_PREFIX} === FULL SYNC STARTED ===")
            sync_start = time.time()
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": "🚀 Full sync started..."
            })

            # Step 1: Sync groups
            if self._stop_event.is_set():
                logger.info(f"{LOG_PREFIX} Full sync cancelled.")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Full sync cancelled."})
                return
            logger.info(f"{LOG_PREFIX} Step 1/5: Syncing groups...")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 1/5: Syncing groups..."})
            result = self._sync_groups(settings, logger)
            if result["status"] == "error":
                logger.error(f"{LOG_PREFIX} Full sync aborted at groups: {result_text(result)}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Full sync aborted: {result_text(result)}"})
                return
            logger.info(f"{LOG_PREFIX} Step 1/5 complete: {result_text(result)}")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 1/5 complete: {result_text(result)}"})

            # Step 2: Sync channels
            if self._stop_event.is_set():
                logger.info(f"{LOG_PREFIX} Full sync cancelled.")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Full sync cancelled."})
                return
            logger.info(f"{LOG_PREFIX} Step 2/5: Syncing channels...")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 2/5: Syncing channels..."})
            result = self._sync_channels(settings, logger)
            if result["status"] == "error":
                logger.error(f"{LOG_PREFIX} Full sync aborted at channels: {result_text(result)}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Full sync aborted: {result_text(result)}"})
                return
            if result["status"] == "warning":
                logger.warning(f"{LOG_PREFIX} Step 2/5 complete with warnings: {result_text(result)}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 2/5 complete (some failures): {result_text(result)}"})
            else:
                logger.info(f"{LOG_PREFIX} Step 2/5 complete: {result_text(result)}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 2/5 complete: {result_text(result)}"})

            # Step 3: Match streams
            if self._stop_event.is_set():
                logger.info(f"{LOG_PREFIX} Full sync cancelled.")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Full sync cancelled."})
                return
            logger.info(f"{LOG_PREFIX} Step 3/5: Matching streams...")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 3/5: Matching streams..."})
            result = self._apply_stream_match(settings, logger)
            logger.info(f"{LOG_PREFIX} Step 3/5 complete: {result_text(result)}")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 3/5 complete: {result_text(result)}"})

            # Step 4: Match EPG
            if self._stop_event.is_set():
                logger.info(f"{LOG_PREFIX} Full sync cancelled.")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Full sync cancelled."})
                return
            if _EPG_AVAILABLE:
                logger.info(f"{LOG_PREFIX} Step 4/5: Matching EPG data...")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 4/5: Matching EPG data..."})
                result = self._apply_epg_match(settings, logger)
                logger.info(f"{LOG_PREFIX} Step 4/5 complete: {result_text(result)}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 4/5 complete: {result_text(result)}"})
            else:
                logger.info(f"{LOG_PREFIX} Step 4/5: Skipped (EPG models not available)")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 4/5: Skipped (EPG not available)"})

            # Step 5: Assign logos
            if self._stop_event.is_set():
                logger.info(f"{LOG_PREFIX} Full sync cancelled.")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Full sync cancelled."})
                return
            if _LOGO_AVAILABLE:
                logger.info(f"{LOG_PREFIX} Step 5/5: Assigning logos...")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 5/5: Assigning logos..."})
                result = self._apply_logo_match(settings, logger)
                logger.info(f"{LOG_PREFIX} Step 5/5 complete: {result_text(result)}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 5/5 complete: {result_text(result)}"})
            else:
                logger.info(f"{LOG_PREFIX} Step 5/5: Skipped (Logo model not available)")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 5/5: Skipped (Logo model not available)"})

            elapsed = time.time() - sync_start
            elapsed_str = ProgressTracker._format_eta_static(elapsed)
            logger.info(f"{LOG_PREFIX} === FULL SYNC COMPLETE ({elapsed_str}) ===")
            self._suppress_refresh = False
            self._trigger_frontend_refresh(logger)
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": f"✅ FULL SYNC COMPLETE in {elapsed_str}!"
            })

        except Exception as e:
            logger.exception(f"{LOG_PREFIX} Full sync error: {e}")
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": f"❌ Full sync error: {e}"
            })
        finally:
            # Always clear the flag -- including on early returns (cancel/
            # abort) and exceptions -- so later operations refresh normally.
            self._suppress_refresh = False

    def _clear_csv_exports(self, settings, logger):
        """Delete all Lineuparr CSV export files.

        This deliberately ignores 'Delete CSV Exports Older Than (Days)':
        somebody pressing this button expects everything cleared.
        """
        export_dir = PluginConfig.EXPORTS_DIR
        if not os.path.exists(export_dir):
            return {"status": "ok", "message": "No exports directory found."}

        removed = 0
        for f in os.listdir(export_dir):
            if (f.startswith(PluginConfig.CSV_EXPORT_PREFIX)
                    and f.endswith(PluginConfig.CSV_EXPORT_SUFFIX)):
                try:
                    os.remove(os.path.join(export_dir, f))
                    removed += 1
                except Exception as e:
                    logger.error(f"{LOG_PREFIX} Failed to delete {f}: {e}")

        return {"status": "ok", "message": f"Removed {removed} CSV export file(s)."}
