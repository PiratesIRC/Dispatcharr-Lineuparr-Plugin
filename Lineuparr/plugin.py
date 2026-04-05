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
import threading
from datetime import datetime, timedelta
from glob import glob

from django.db import transaction

from .fuzzy_matcher import FuzzyMatcher
from .aliases import CHANNEL_ALIASES

from apps.channels.models import Channel, ChannelGroup, ChannelProfile, ChannelProfileMembership, ChannelStream, Stream
from apps.m3u.models import M3UAccount
from core.utils import send_websocket_update

# EPG models (optional — EPG matching disabled if not available)
try:
    from apps.epg.models import EPGData, EPGSource, ProgramData
    _EPG_AVAILABLE = True
except ImportError:
    _EPG_AVAILABLE = False

# Logo model (optional — logo assignment disabled if not available)
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


class PluginConfig:
    PLUGIN_VERSION = "1.26.9520"

    DEFAULT_FUZZY_MATCH_THRESHOLD = 80
    DEFAULT_PRIORITIZE_QUALITY = True
    DEFAULT_RATE_LIMITING = "none"

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
    STATE_FILE = "/data/lineuparr_state.json"
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

    def finish(self):
        elapsed = time.time() - self.start_time
        eta_str = self._format_eta(elapsed)
        self.logger.info(f"{LOG_PREFIX} [{self.action_id}] Complete: {self.processed_items}/{self.total_items} in {eta_str}")
        send_websocket_update('updates', 'update', {
            "type": "plugin", "plugin": "Lineuparr",
            "message": f"✅ {self.action_id}: Complete ({self.processed_items}/{self.total_items}) in {eta_str}"
        })

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
        if not lineup_options:
            lineup_options = [{"value": "_none", "label": "No lineup files found"}]

        # Discover M3U sources
        m3u_options = [{"value": "_all", "label": "All sources"}]
        try:
            for acc in M3UAccount.objects.all().values('id', 'name'):
                m3u_options.append({"value": acc['name'], "label": acc['name']})
        except Exception:
            pass

        # Discover EPG sources
        epg_source_options = [{"value": "_all", "label": "All EPG sources"}]
        if _EPG_AVAILABLE:
            try:
                for src in EPGSource.objects.all().values('id', 'name').order_by('name'):
                    epg_source_options.append({"value": src['name'], "label": src['name']})
            except Exception:
                pass

        # Discover channel profiles
        profile_options = [{"value": "_none", "label": "None (don't enable in profiles)"}]
        try:
            for p in ChannelProfile.objects.all().values('id', 'name'):
                profile_options.append({"value": p['name'], "label": p['name']})
        except Exception:
            pass

        return [
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
                "id": "channel_profiles",
                "label": "Channel Profile",
                "type": "select",
                "default": "_none",
                "options": profile_options,
                "help_text": "Automatically enable matched channels in this profile after sync.",
            },
            {
                "id": "group_prefix",
                "label": "Channel Group Prefix",
                "type": "string",
                "default": "",
                "help_text": "Blank = auto from lineup name. 'none' = no prefix. Add trailing separator to control format (e.g. 'US ' or 'DTV-').",
            },
            {
                "id": "category_detail",
                "label": "Category Detail",
                "type": "select",
                "default": "normal",
                "options": [
                    {"value": "none", "label": "None - single group (prefix only, no categories)"},
                    {"value": "simple", "label": "Simple - merged categories (News & Info, Sports & Outdoors, etc.)"},
                    {"value": "normal", "label": "Normal - all individual categories"},
                ],
                "help_text": "Controls how lineup categories are grouped. Simple merges 16 categories into 7.",
            },
            {
                "id": "match_sensitivity",
                "label": "Match Sensitivity",
                "type": "select",
                "default": "normal",
                "options": [
                    {"value": "relaxed", "label": "Relaxed - more matches, more false positives"},
                    {"value": "normal", "label": "Normal - balanced"},
                    {"value": "strict", "label": "Strict - fewer matches, high confidence"},
                    {"value": "exact", "label": "Exact - near-exact matches only"},
                ],
                "help_text": "How closely stream and EPG names must match channel names. Lower = more matches but more errors.",
            },
            {
                "id": "prioritize_quality",
                "label": "\u2b50 Order Matched Streams by Quality",
                "type": "boolean",
                "default": True,
                "help_text": "Sort attached streams by quality (4K > UHD > FHD > HD > SD). Uses probed resolution if available.",
            },
            {
                "id": "rate_limiting",
                "label": "\u23f3 Rate Limiting",
                "type": "select",
                "default": "none",
                "options": [
                    {"value": "none", "label": "None - fastest"},
                    {"value": "low", "label": "Low - slight delay"},
                    {"value": "medium", "label": "Medium - moderate delay"},
                    {"value": "high", "label": "High - slowest, gentlest on API"},
                ],
                "help_text": "Add delays between API calls during sync. Use if Dispatcharr becomes unresponsive during operations.",
            },
            {
                "id": "custom_aliases",
                "label": "Custom Channel Aliases (advanced)",
                "type": "string",
                "default": "",
                "help_text": "JSON mapping extra names to lineup channels. Leave blank to use built-in aliases only.",
            },
            {
                "id": "epg_sources",
                "label": "EPG Sources for Matching",
                "type": "select",
                "default": "_all",
                "options": epg_source_options,
                "help_text": "Select which EPG source to match against, or 'All' to use every available source.",
            },
        ]

    def run(self, action, params, context):
        logger = context.get("logger", LOGGER)
        settings = context.get("settings", {})

        try:
            action_map = {
                "validate_settings": self._validate_settings,
                "scan_lineups": self._scan_lineups,
                "preview_stream_match": self._preview_stream_match,
                "full_sync": self._full_sync,
                "sync_channels": self._sync_channels,
                "apply_stream_match": self._apply_stream_match,
                "apply_epg_match": self._apply_epg_match,
                "apply_logo_match": self._apply_logo_match,
                "resort_streams": self._resort_streams,
                "clear_csv_exports": self._clear_csv_exports,
                # Legacy actions (no UI buttons, kept for API compatibility)
                "preview_groups": self._preview_groups,
                "sync_groups": self._sync_groups,
                "preview_channels": self._preview_channels,
            }

            handler = action_map.get(action)
            if not handler:
                logger.warning(f"{LOG_PREFIX} Unknown action: {action}")
                return {"status": "error", "message": f"Unknown action: {action}"}

            logger.info(f"{LOG_PREFIX} ▶ Action triggered: {action}")
            result = handler(settings, logger)
            status = result.get("status", "?") if isinstance(result, dict) else "ok"
            msg = result.get("message", "")[:200] if isinstance(result, dict) else ""
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
            return {"status": "error", "message": f"Internal error: {str(e)}"}

    def stop(self, context):
        logger = context.get("logger", LOGGER)
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info(f"{LOG_PREFIX} Plugin stopped.")

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

        # Apply category detail level transformation
        data = self._apply_category_detail(data, detail)

        self._lineup_cache = data
        self._lineup_cache_file = cache_key
        logger.info(f"{LOG_PREFIX} Loaded lineup: {data.get('package', 'Unknown')} ({sum(len(v) for v in data['categories'].values())} channels, {len(data['categories'])} categories, detail={detail})")
        return data

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

        if detail == "simple":
            # Merge into simplified categories
            simplified = {}
            mapped_originals = set()
            for simple_name, originals in PluginConfig.SIMPLIFIED_CATEGORIES.items():
                merged = []
                for orig in originals:
                    if orig in original_cats:
                        merged.extend(original_cats[orig])
                        mapped_originals.add(orig)
                if merged:
                    simplified[simple_name] = merged
            # Preserve any categories not in the mapping
            for orig_name, channels in original_cats.items():
                if orig_name not in mapped_originals and channels:
                    simplified[orig_name] = channels
            result["categories"] = simplified
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

    def _get_group_prefix(self, settings, lineup_data):
        """Get the group prefix (user override or auto from lineup package name).
        'none' = no prefix (category names only). Blank = auto from package.
        Trailing separators preserved so 'US ' gives 'US News', not 'US: News'."""
        prefix = settings.get("group_prefix", "").lstrip()
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
        m3u_str = settings.get("m3u_sources", "").strip()
        if not m3u_str or m3u_str == "_all":
            return None, {}, []

        all_accounts = {a['name']: a['id'] for a in M3UAccount.objects.all().values('id', 'name')}
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

        qs = Stream.objects.all().values('id', 'name', 'm3u_account', 'stream_stats')
        if valid_ids:
            qs = qs.filter(m3u_account__in=valid_ids)

        streams = list(qs)
        for s in streams:
            s['_m3u_priority'] = priority_map.get(s.get('m3u_account'), 999)
            s['_stream_stats'] = s.pop('stream_stats', None) or {}

        logger.info(f"{LOG_PREFIX} Loaded {len(streams)} streams" + (f" from {len(valid_ids)} M3U sources" if valid_ids else ""))
        return streams

    def _build_alias_map(self, settings, logger):
        """Merge built-in aliases with user custom aliases."""
        alias_map = dict(CHANNEL_ALIASES)

        custom_str = settings.get("custom_aliases", "").strip()
        if custom_str:
            try:
                custom = json.loads(custom_str)
                if isinstance(custom, dict):
                    for k, v in custom.items():
                        if isinstance(v, list):
                            if k in alias_map:
                                alias_map[k] = list(dict.fromkeys(alias_map[k] + v))
                            else:
                                alias_map[k] = v
                    logger.info(f"{LOG_PREFIX} Merged {len(custom)} custom aliases")
            except json.JSONDecodeError as e:
                logger.warning(f"{LOG_PREFIX} Failed to parse custom_aliases JSON: {e}")

        return alias_map

    def _get_filtered_epg_data(self, settings, logger):
        """Fetch EPG data, optionally filtered and prioritized by selected sources."""
        if not _EPG_AVAILABLE:
            logger.warning(f"{LOG_PREFIX} EPG models not available. Skipping EPG matching.")
            return []

        all_epg = list(EPGData.objects.all().values('id', 'name', 'epg_source'))
        logger.info(f"{LOG_PREFIX} Fetched {len(all_epg)} EPG data entries")

        epg_sources_str = settings.get("epg_sources", "").strip()
        if not epg_sources_str or epg_sources_str == "_all":
            return all_epg

        # Map source names to IDs (case-insensitive)
        available = {}
        for src in EPGSource.objects.all().values('id', 'name'):
            available[src['name'].strip().upper()] = src['id']

        source_names = [s.strip() for s in epg_sources_str.split(",") if s.strip()]
        valid_ids = []
        priority_map = {}

        for idx, name in enumerate(source_names):
            src_id = available.get(name.upper())
            if src_id:
                valid_ids.append(src_id)
                priority_map[src_id] = idx
            else:
                logger.warning(f"{LOG_PREFIX} EPG source not found: '{name}'")

        if not valid_ids:
            logger.warning(f"{LOG_PREFIX} No valid EPG sources matched. Using all EPG data.")
            return all_epg

        filtered = [e for e in all_epg if e.get('epg_source') in valid_ids]
        filtered.sort(key=lambda e: priority_map.get(e.get('epg_source'), 999))
        logger.info(f"{LOG_PREFIX} Filtered to {len(filtered)} EPG entries from {len(valid_ids)} source(s)")
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

    def _init_fuzzy_matcher(self, settings, logger):
        """Create a configured FuzzyMatcher instance."""
        # Support both new select-based sensitivity and legacy numeric threshold
        sensitivity = settings.get("match_sensitivity", "normal")
        threshold = self.SENSITIVITY_MAP.get(sensitivity)
        if threshold is None:
            # Fallback: try legacy numeric field
            threshold = int(settings.get("fuzzy_match_threshold", PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD))
        threshold = max(0, min(100, threshold))
        return FuzzyMatcher(match_threshold=threshold, logger=logger)

    def _resolve_channel_profiles(self, settings, logger):
        """Resolve comma-separated profile names to profile objects.
        Returns list of {'id': int, 'name': str} dicts, or empty list if not configured."""
        profiles_str = settings.get("channel_profiles", "").strip()
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

    def _export_csv(self, filename, rows, fieldnames, logger, settings=None):
        """Export data to CSV in the exports directory with settings header."""
        try:
            os.makedirs(PluginConfig.EXPORTS_DIR, exist_ok=True)
            filepath = os.path.join(PluginConfig.EXPORTS_DIR, filename)
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                # Write settings header as comment lines
                if settings:
                    f.write(f"# Lineuparr v{PluginConfig.PLUGIN_VERSION}\n")
                    f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"# Lineup: {settings.get('lineup_file', '')}\n")
                    f.write(f"# Category Detail: {settings.get('category_detail', 'normal')}\n")
                    f.write(f"# Group Prefix: {settings.get('group_prefix', '') or '(auto)'}\n")
                    f.write(f"# Match Sensitivity: {settings.get('match_sensitivity', 'normal')}\n")
                    f.write(f"# Quality Ordering: {settings.get('prioritize_quality', True)}\n")
                    f.write(f"# Rate Limiting: {settings.get('rate_limiting', 'none')}\n")
                    # M3U source names (not URLs)
                    m3u_val = settings.get('m3u_sources', '_all')
                    try:
                        if m3u_val == '_all':
                            m3u_names = [acc['name'] for acc in M3UAccount.objects.all().values('name')]
                            f.write(f"# M3U Sources: {', '.join(m3u_names) or '(none)'} (all)\n")
                        else:
                            f.write(f"# M3U Sources: {m3u_val}\n")
                    except Exception:
                        f.write(f"# M3U Sources: {m3u_val}\n")
                    # EPG source names
                    epg_val = settings.get('epg_sources', '_all')
                    try:
                        if _EPG_AVAILABLE:
                            if epg_val == '_all':
                                epg_names = [src['name'] for src in EPGSource.objects.all().values('name')]
                                f.write(f"# EPG Sources: {', '.join(epg_names) or '(none)'} (all)\n")
                            else:
                                f.write(f"# EPG Sources: {epg_val}\n")
                        else:
                            f.write(f"# EPG Sources: (unavailable)\n")
                    except Exception:
                        f.write(f"# EPG Sources: {epg_val}\n")
                    # Profile
                    f.write(f"# Channel Profile: {settings.get('channel_profiles', '_none')}\n")
                    f.write(f"#\n")
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"{LOG_PREFIX} Exported CSV: {filepath} ({len(rows)} rows)")
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
        """Notify frontend of channel updates via WebSocket."""
        try:
            send_websocket_update('updates', 'update', {
                "type": "plugin", "plugin": "Lineuparr",
                "message": "Channels updated"
            })
        except Exception as e:
            logger.error(f"{LOG_PREFIX} WebSocket update failed: {e}")

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

        # Check M3U sources
        m3u_str = settings.get("m3u_sources", "").strip()
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
        custom_str = settings.get("custom_aliases", "").strip()
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
        profiles_str = settings.get("channel_profiles", "").strip()
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

                epg_sources_str = settings.get("epg_sources", "").strip()
                if epg_sources_str and epg_sources_str != "_all":
                    filtered = self._get_filtered_epg_data(settings, logger)
                    results.append({"Setting": "EPG Sources Filter", "Value": epg_sources_str, "Status": f"OK ({len(filtered)} entries after filtering)"})
                else:
                    results.append({"Setting": "EPG Sources Filter", "Value": "All sources", "Status": "OK"})
            except Exception as e:
                results.append({"Setting": "EPG Data", "Value": str(e), "Status": "ERROR"})
                errors += 1
        else:
            results.append({"Setting": "EPG Data", "Value": "EPG models not available", "Status": "SKIP"})

        # DB connectivity
        try:
            ch_count = Channel.objects.count()
            gr_count = ChannelGroup.objects.count()
            st_count = Stream.objects.count()
            results.append({"Setting": "Database", "Value": "", "Status": f"OK ({ch_count} channels, {gr_count} groups, {st_count} streams)"})
        except Exception as e:
            results.append({"Setting": "Database", "Value": "", "Status": f"ERROR: {e}"})
            errors += 1

        status = "ok" if errors == 0 else "error"
        if errors == 0:
            msg = "✅ All settings valid."
        else:
            error_details = [r["Setting"] + ": " + r["Status"] for r in results if "ERROR" in r.get("Status", "")]
            msg = f"❌ {errors} error(s) found.\n" + "\n".join(error_details)
        return {"status": status, "message": msg, "data": results}

    def _scan_lineups(self, settings, logger):
        """Discover available lineup JSON files."""
        plugin_dir = os.path.dirname(__file__)
        # Find all JSON files that look like lineups (contain "categories" key)
        lineup_files = glob(os.path.join(plugin_dir, "*.json"))
        # Exclude plugin.json
        lineup_files = [f for f in lineup_files if os.path.basename(f) != "plugin.json"]

        if not lineup_files:
            return {"status": "error", "message": "No lineup JSON files found in plugin directory."}

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
                ch_number = self._parse_channel_number(entry.get("number"))
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
        self._export_csv(f"lineuparr_preview_channels_{ts}.csv", results,
                         ["Channel", "Number", "Category", "Group", "Status"], logger, settings)

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
            return {"status": "error", "message": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Preview started: matching {total_channels} channels against {stream_count} streams. ETA: ~{eta_str}. Check notifications for progress.",
            "background": True,
        }

    def _do_preview_stream_match(self, settings, logger):
        """Background thread for stream match preview."""
        try:
            lineup = self._load_lineup(settings, logger)
            matcher = self._init_fuzzy_matcher(settings, logger)
            alias_map = self._build_alias_map(settings, logger)
            streams = self._get_all_streams(settings, logger)

            if not streams:
                logger.error(f"{LOG_PREFIX} No streams found. Check M3U sources.")
                send_websocket_update('updates', 'update', {
                    "type": "plugin", "plugin": "Lineuparr",
                    "message": "Preview failed: No streams found."
                })
                return

            # Deduplicate stream names for matching (performance: avoid redundant fuzzy comparisons)
            unique_stream_names = list(set(s['name'] for s in streams))
            logger.info(f"{LOG_PREFIX} Matching against {len(unique_stream_names)} unique stream names (from {len(streams)} total)")

            # Pre-normalize stream names for performance
            matcher.precompute_normalizations(unique_stream_names)

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
                for entry in channels:
                    if self._stop_event.is_set():
                        logger.info(f"{LOG_PREFIX} Preview cancelled.")
                        return

                    ch_name = entry["name"]
                    ch_number = self._parse_channel_number(entry.get("number"))

                    matches = matcher.match_all_streams(
                        ch_name, unique_stream_names, alias_map,
                        channel_number=ch_number
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

            progress.finish()

            # Sort: unmatched first, then by score ascending
            results.sort(key=lambda r: (0 if r["Score"] == 0 else 1, r["Score"]))

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._export_csv(f"lineuparr_preview_match_{ts}.csv", results,
                             ["Channel", "Number", "Category", "Best Match", "Score", "Match Type", "Total Matches", "Top Matches"],
                             logger, settings)

            total = matched_count + unmatched_count
            type_breakdown = ", ".join(f"{v} {k}" for k, v in sorted(type_counts.items(), key=lambda x: -x[1]))
            msg = (
                f"Preview complete: {matched_count}/{total} matched "
                f"({perfect_count} perfect, {matched_count - perfect_count} partial), "
                f"{unmatched_count} unmatched. "
                f"Types: {type_breakdown}. CSV exported."
            )
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
            return {"status": "error", "message": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Sync Channels started: {total_channels} channels. ETA: ~{eta_str}. Check notifications for progress.",
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

        # Ensure groups exist
        existing_groups = {g['name']: g['id'] for g in ChannelGroup.objects.all().values('id', 'name')}
        for category in lineup["categories"]:
            group_name = self._make_group_name(prefix, category)
            if group_name not in existing_groups:
                if dry_run:
                    logger.info(f"{LOG_PREFIX} [DRY RUN] Group '{group_name}' does not exist, would need sync_groups first")
                    return {"status": "error", "message": f"Group '{group_name}' does not exist. Run 'Sync Groups' first."}
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
                ch_number = self._parse_channel_number(entry.get("number"))

                try:
                    if dry_run:
                        existing = Channel.objects.filter(name=ch_name, channel_group_id=group_id).values('id', 'channel_number').first()
                        if existing:
                            synced_channel_ids.append(existing['id'])
                            if ch_number and existing['channel_number'] != ch_number:
                                updated += 1
                            else:
                                unchanged += 1
                        else:
                            created += 1
                    else:
                        ch, was_created = Channel.objects.get_or_create(
                            name=ch_name,
                            channel_group_id=group_id,
                            defaults={"channel_number": ch_number}
                        )
                        synced_channel_ids.append(ch.id)
                        if was_created:
                            created += 1
                            logger.debug(f"{LOG_PREFIX} Created channel: '{ch_name}' #{ch_number} in '{group_name}'")
                        else:
                            # Update channel number if different
                            if ch_number and ch.channel_number != ch_number:
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
        return {"status": status, "message": msg}

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
            return {"status": "error", "message": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Stream matching started for {total_channels} channels. ETA: ~{eta_str}. Check notifications for progress.",
            "background": True,
        }

    def _apply_epg_match(self, settings, logger):
        """Assign EPG data to channels via fuzzy matching.
        Runs as daemon thread to avoid HTTP timeout."""
        if not _EPG_AVAILABLE:
            return {"status": "error", "message": "EPG models not available in this Dispatcharr installation."}

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
            return {"status": "error", "message": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"EPG matching started for {total_channels} channels. ETA: ~{eta_str}. Check notifications for progress.",
            "background": True,
        }

    def _apply_logo_match(self, settings, logger):
        """Assign logos to channels via 3-tier fallback.
        Runs as daemon thread to avoid HTTP timeout."""
        if not _LOGO_AVAILABLE:
            return {"status": "error", "message": "Logo model not available in this Dispatcharr installation."}

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
            return {"status": "error", "message": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Logo assignment started for {total_channels} channels. ETA: ~{eta_str}. Check notifications for progress.",
            "background": True,
        }

    def _do_apply_logo_match_bg(self, settings, logger):
        """Background wrapper for logo assignment."""
        try:
            result = self._do_apply_logo_match(settings, logger)
            msg = result.get("message", "Logo assignment complete.")
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
            return {"status": "error", "message": "Another operation is in progress. Try again later."}

        try:
            from .logo_matcher import (
                fetch_tv_logos_filelist, match_channel_to_logo,
                build_logo_url,
            )

            lineup = self._load_lineup(settings, logger)
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
            msg = result.get("message", "Stream matching complete.")
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

    def _do_apply_stream_match(self, settings, logger):
        """Core stream matching logic (called from thread)."""
        dry_run = settings.get("dry_run_mode", False)
        prioritize_quality = settings.get("prioritize_quality", PluginConfig.DEFAULT_PRIORITIZE_QUALITY)

        if not self._acquire_lock(logger):
            return {"status": "error", "message": "Another operation is in progress. Try again later."}

        try:
            lineup = self._load_lineup(settings, logger)
            prefix = self._get_group_prefix(settings, lineup)
            matcher = self._init_fuzzy_matcher(settings, logger)
            alias_map = self._build_alias_map(settings, logger)
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", PluginConfig.DEFAULT_RATE_LIMITING))

            # Get streams
            all_streams = self._get_all_streams(settings, logger)
            if not all_streams:
                return {"status": "error", "message": "No streams found. Check M3U sources."}

            # Build name -> stream objects lookup
            stream_by_name = {}
            for s in all_streams:
                stream_by_name.setdefault(s['name'], []).append(s)

            # Deduplicate stream names for matching performance
            unique_stream_names = list(stream_by_name.keys())
            logger.info(f"{LOG_PREFIX} Matching against {len(unique_stream_names)} unique stream names (from {len(all_streams)} total)")

            # Pre-normalize stream names for performance
            matcher.precompute_normalizations(unique_stream_names)

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

                for entry in channels:
                    if self._stop_event.is_set():
                        logger.info(f"{LOG_PREFIX} Stream matching cancelled.")
                        return {"status": "ok", "message": "Stream matching cancelled by user."}

                    ch_name = entry["name"]
                    ch_number = self._parse_channel_number(entry.get("number"))
                    channel_id = existing_channels.get((ch_name, group_id))

                    if not channel_id:
                        logger.debug(f"{LOG_PREFIX} Channel '{ch_name}' not in DB, skipping")
                        channels_unmatched += 1
                        progress.update()
                        continue

                    # Match streams using deduplicated names
                    matches = matcher.match_all_streams(
                        ch_name, unique_stream_names, alias_map,
                        channel_number=ch_number
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

                        if not dry_run:
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
                            total_streams_attached += len(sorted_streams)

                        channels_matched += 1
                        csv_rows.append({
                            "Channel": ch_name,
                            "Number": ch_number if ch_number else "",
                            "Category": category,
                            "Streams Attached": len(sorted_streams),
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

            # Export CSV
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            mode = "dryrun" if dry_run else "applied"
            self._export_csv(
                f"lineuparr_match_{mode}_{ts}.csv", csv_rows,
                ["Channel", "Number", "Category", "Streams Attached", "Best Match", "Best Score", "Match Type"],
                logger, settings
            )

            # Save state
            self._save_state({
                "last_sync": datetime.now().isoformat(),
                "lineup_file": settings.get("lineup_file", ""),
                "channels_matched": channels_matched,
                "channels_unmatched": channels_unmatched,
                "streams_attached": total_streams_attached,
                "dry_run": dry_run,
            }, logger)

            # Cleanup: remove channels with no streams in Lineuparr-managed groups
            cleanup_count = 0
            if not dry_run and channels_unmatched > 0:
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

    def _do_apply_epg_match_bg(self, settings, logger):
        """Background wrapper for EPG matching."""
        try:
            result = self._do_apply_epg_match(settings, logger)
            msg = result.get("message", "EPG matching complete.")
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
            return {"status": "error", "message": "Another operation is in progress. Try again later."}

        try:
            lineup = self._load_lineup(settings, logger)
            prefix = self._get_group_prefix(settings, lineup)
            matcher = self._init_fuzzy_matcher(settings, logger)
            alias_map = self._build_alias_map(settings, logger)
            rate_limiter = SmartRateLimiter(settings.get("rate_limiting", PluginConfig.DEFAULT_RATE_LIMITING))

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
                return {"status": "ok", "message": "No EPG entries have program data in the next 12 hours. Skipping EPG matching."}

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

            for category, channels in lineup["categories"].items():
                group_name = self._make_group_name(prefix, category)
                group_id = existing_groups.get(group_name)

                if not group_id:
                    for _ in channels:
                        skipped_no_match += 1
                        progress.update()
                    continue

                for entry in channels:
                    if self._stop_event.is_set():
                        logger.info(f"{LOG_PREFIX} EPG matching cancelled.")
                        return {"status": "ok", "message": "EPG matching cancelled by user."}

                    ch_name = entry["name"]
                    ch_number = self._parse_channel_number(entry.get("number"))
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

                    # Fuzzy match channel name against EPG names
                    # (all candidates already have program data — pre-filtered above)
                    matches = matcher.match_all_streams(
                        ch_name, unique_epg_names, alias_map,
                        channel_number=ch_number
                    )

                    # Take best match (all candidates have program data)
                    best_epg = None
                    best_score = 0
                    best_method = None
                    has_program_data = True

                    if matches:
                        top_name, top_score, top_method = matches[0]
                        top_entries = epg_by_name.get(top_name, [])
                        if top_entries:
                            best_epg = top_entries[0]
                            best_score = top_score
                            best_method = top_method

                    # Fallback: if no program-data match, try ALL EPG entries
                    if not best_epg and unique_epg_names_all:
                        fallback_matches = matcher.match_all_streams(
                            ch_name, unique_epg_names_all, alias_map,
                            channel_number=ch_number
                        )
                        if fallback_matches:
                            top_name, top_score, top_method = fallback_matches[0]
                            top_entries = epg_by_name_all.get(top_name, [])
                            if top_entries:
                                best_epg = top_entries[0]
                                best_score = top_score
                                best_method = top_method
                                has_program_data = False
                                logger.debug(f"{LOG_PREFIX} EPG fallback (no program data): {ch_name} -> {best_epg['name']}")

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

            # Export CSV
            if csv_rows:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                self._export_csv(
                    f"lineuparr_epg_match_{timestamp}.csv",
                    csv_rows,
                    ["channel_name", "channel_number", "channel_group", "matched_epg_name",
                     "epg_source", "confidence_score", "has_program_data", "match_method", "status"],
                    logger, settings,
                )

            progress.finish()
            self._trigger_frontend_refresh(logger)

            fallback_note = f" ({matched_fallback} via fallback without program data)" if matched_fallback else ""
            existing_note = f", {skipped_existing} skipped (already assigned)" if skipped_existing else ""
            msg = f"EPG matching complete: {matched} matched{fallback_note}, {skipped_no_match} no match{existing_note} (from {len(epg_data_with_programs)} EPG entries with program data)"
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
            return {"status": "error", "message": "No Lineuparr channel groups found. Run Full Sync first."}

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
            return {"status": "error", "message": "An operation is already running. Please wait for it to finish."}
        return {
            "status": "ok",
            "message": f"Full Sync started: groups, channels, stream matching, EPG matching, and logo assignment.{eta_msg} Check notifications for progress.",
            "background": True,
        }

    def _do_full_sync(self, settings, logger):
        """Background thread for full sync."""
        try:
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
                logger.error(f"{LOG_PREFIX} Full sync aborted at groups: {result['message']}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Full sync aborted: {result['message']}"})
                return
            logger.info(f"{LOG_PREFIX} Step 1/5 complete: {result['message']}")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 1/5 complete: {result['message']}"})

            # Step 2: Sync channels
            if self._stop_event.is_set():
                logger.info(f"{LOG_PREFIX} Full sync cancelled.")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Full sync cancelled."})
                return
            logger.info(f"{LOG_PREFIX} Step 2/5: Syncing channels...")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 2/5: Syncing channels..."})
            result = self._sync_channels(settings, logger)
            if result["status"] == "error":
                logger.error(f"{LOG_PREFIX} Full sync aborted at channels: {result['message']}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Full sync aborted: {result['message']}"})
                return
            if result["status"] == "warning":
                logger.warning(f"{LOG_PREFIX} Step 2/5 complete with warnings: {result['message']}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 2/5 complete (some failures): {result['message']}"})
            else:
                logger.info(f"{LOG_PREFIX} Step 2/5 complete: {result['message']}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 2/5 complete: {result['message']}"})

            # Step 3: Match streams
            if self._stop_event.is_set():
                logger.info(f"{LOG_PREFIX} Full sync cancelled.")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Full sync cancelled."})
                return
            logger.info(f"{LOG_PREFIX} Step 3/5: Matching streams...")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 3/5: Matching streams..."})
            result = self._apply_stream_match(settings, logger)
            logger.info(f"{LOG_PREFIX} Step 3/5 complete: {result['message']}")
            send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 3/5 complete: {result['message']}"})

            # Step 4: Match EPG
            if self._stop_event.is_set():
                logger.info(f"{LOG_PREFIX} Full sync cancelled.")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Full sync cancelled."})
                return
            if _EPG_AVAILABLE:
                logger.info(f"{LOG_PREFIX} Step 4/5: Matching EPG data...")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 4/5: Matching EPG data..."})
                result = self._apply_epg_match(settings, logger)
                logger.info(f"{LOG_PREFIX} Step 4/5 complete: {result['message']}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 4/5 complete: {result['message']}"})
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
                logger.info(f"{LOG_PREFIX} Step 5/5 complete: {result['message']}")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": f"Step 5/5 complete: {result['message']}"})
            else:
                logger.info(f"{LOG_PREFIX} Step 5/5: Skipped (Logo model not available)")
                send_websocket_update('updates', 'update', {"type": "plugin", "plugin": "Lineuparr", "message": "Step 5/5: Skipped (Logo model not available)"})

            elapsed = time.time() - sync_start
            elapsed_str = ProgressTracker._format_eta_static(elapsed)
            logger.info(f"{LOG_PREFIX} === FULL SYNC COMPLETE ({elapsed_str}) ===")
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

    def _clear_csv_exports(self, settings, logger):
        """Delete all Lineuparr CSV export files."""
        export_dir = PluginConfig.EXPORTS_DIR
        if not os.path.exists(export_dir):
            return {"status": "ok", "message": "No exports directory found."}

        removed = 0
        for f in os.listdir(export_dir):
            if f.startswith("lineuparr_") and f.endswith(".csv"):
                try:
                    os.remove(os.path.join(export_dir, f))
                    removed += 1
                except Exception as e:
                    logger.error(f"{LOG_PREFIX} Failed to delete {f}: {e}")

        return {"status": "ok", "message": f"Removed {removed} CSV export file(s)."}
