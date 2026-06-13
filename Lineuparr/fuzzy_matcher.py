"""
Fuzzy Matcher Module for Lineuparr
Forked from Stream-Mapparr's fuzzy_matcher.py (v26.018.0100) with enhancements:
  - Stage 0: Alias-aware matching
  - Channel number boost for tiebreaking
  - Enhanced provider prefix normalization
"""

import re
import logging
import unicodedata

__version__ = "1.3.4"

LOGGER = logging.getLogger("plugins.lineuparr.fuzzy_matcher")
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    LOGGER.addHandler(_handler)
LOGGER.setLevel(logging.DEBUG)

# --- Pattern categories for normalization ---

# Unicode categories considered decorative/badge characters by IPTV providers.
# So = Other Symbol (◉), No = Other Number (², ³), Lm = Modifier Letter
# (ᴿᴬᵂ, ᴴᴰ, ⱽᴵᴾ superscripts), Sk = Modifier Symbol.
# Accented letters (é, î, ü…) are Ll/Lu and are NOT in this set.
# Sm (Math Symbol) is intentionally EXCLUDED: it contains "+", which is a
# meaningful, channel-distinguishing character (Canal+, Three Stooges+,
# Comedy Central+). Stripping it regresses those matches.
_DECORATOR_CATS = frozenset({'So', 'No', 'Lm', 'Sk'})

# Tokens that are non-distinctive stream-label variants (e.g. "ABC News Live"
# should still match "ABC News"). Used by the subset/divergent guards.
_NON_DISTINCTIVE_TOKENS = frozenset({"live", "now", "new"})

def _is_distinctive(t):
    """Return True if token t is distinctive enough to matter in subset/divergent guards."""
    return t not in _NON_DISTINCTIVE_TOKENS and (len(t) >= 4 or (t.isdigit() and len(t) >= 2))

# Matches "+1" / "+2" time-shift suffixes in the ORIGINAL (pre-normalization)
# channel name. Must be checked before normalization because "+" is in the Sm
# category and gets stripped, making "+1" indistinguishable from "1" afterward.
_PLUS_SHIFT_RE = re.compile(r'\+\s{0,2}\d{1,2}\b')

QUALITY_PATTERNS = [
    r'\s*\[(4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead|Backup)\]\s*',
    r'\s*\((4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead|Backup)\)\s*',
    r'^\s*(4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead)\b\s*',
    r'\s*\b(4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead)$',
    r'\s+\b(4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead)\b\s+',
]

# Quality markers that distinguish an upgrade-tier channel from its standard twin.
# HD/FHD/HEVC are intentionally excluded - they don't create a separate twin channel.
_UPGRADE_QUALITY_RE = re.compile(r'\b(?:4K|8K|UHD|HDR)\b|ᵁᴴᴰ', re.IGNORECASE)


def has_upgrade_quality(name: str) -> bool:
    """Return True if name contains an upgrade quality marker (4K/8K/UHD/HDR)."""
    return bool(_UPGRADE_QUALITY_RE.search(name))


REGIONAL_PATTERNS = [
    # East/West are intentionally NOT stripped - they distinguish separate channel feeds
    # (e.g., "HBO East" and "HBO West" are different channels)
    r'\s[Pp][Aa][Cc][Ii][Ff][Ii][Cc]',
    r'\s[Cc][Ee][Nn][Tt][Rr][Aa][Ll]',
    r'\s[Mm][Oo][Uu][Nn][Tt][Aa][Ii][Nn]',
    r'\s[Aa][Tt][Ll][Aa][Nn][Tt][Ii][Cc]',
    r'\s*\([Pp][Aa][Cc][Ii][Ff][Ii][Cc]\)\s*',
    r'\s*\([Cc][Ee][Nn][Tt][Rr][Aa][Ll]\)\s*',
    r'\s*\([Mm][Oo][Uu][Nn][Tt][Aa][Ii][Nn]\)\s*',
    r'\s*\([Aa][Tt][Ll][Aa][Nn][Tt][Ii][Cc]\)\s*',
]

GEOGRAPHIC_PATTERNS = [
    r'\b[A-Z]{2,3}:\s*',
    r'\b[A-Z]{2,3}\s*-\s*',
    r'\|[A-Z]{2,3}\|\s*',
    r'\[[A-Z]{2,3}\]\s*',
]

# Enhanced provider prefix patterns for IPTV-specific naming
PROVIDER_PREFIX_PATTERNS = [
    r'^(?:US|USA|UK|CA|AU|FR|DE|ES|IT|NL|BR|MX|IN)\s*[:\-\|]\s*',
    # Bare country tag + whitespace, no separator (e.g. "US Racer Network",
    # "FR beIN SPORTS MAX", "MEX Bein Sports"). Restricted to a curated set so
    # it cannot eat a real channel name: "USA Network" (USA != US + space),
    # "In Country Television" ("IN") and "IT Crowd" ("IT") are all safe too.
    # Keep this set in sync with detect_stream_country()'s bare-space branch.
    r'^(?:US|UK|CA|AU|FR|DE|MX|MEX|FRA|GER)\s+',
    # "USA " space prefix as a US country tag ("USA  ABC", "USA BET"). A
    # negative lookahead for NETWORK protects the real channel "USA Network"
    # (these feeds tag that one as "US ..."/"US: ...", never bare "USA ").
    # Keep in sync with detect_stream_country()'s USA branch.
    r'^USA\s+(?!NETWORK\b)',
    # Country code glued directly to a quality tag with no separator
    # ("UKSD: Sky Sports", "UKHD ESPN", "USFHD ..."). Detection mirrors this
    # in detect_stream_country() so these can't leak as wildcards.
    r'^(?:US|UK)(?:SD|HD|FHD|UHD|FD|HEVC|4K|8K)\b\s*[:\-\|]?\s*',
    r'^\s*\((?:US|USA|UK|CA|AU|FR|DE|ES|IT|NL|BR|MX|IN)\)\s*',
    r'\s*\|\s*(?:US|USA|UK|CA|AU|FR|DE|ES|IT|NL|BR|MX|IN)\s*$',
    # Content-category group prefixes used by some IPTV providers.
    r'^(?:ADULT|EROTIC|PRIME|GOLD)\s*[:\-\|]\s*',
    # FAST streaming-platform source tags (Roku, Tubi, Pluto, etc.). These mark
    # the distribution platform, not the channel or its country, so strip them
    # for matching ("RK: beIN Sports Xtra" -> "beIN Sports Xtra"). A separator
    # is required so this can't eat real names like "GOLF" or "PLEX TV Movies".
    # NOT a country signal: detect_stream_country() ignores these (correctly,
    # since a platform like Roku spans US/CA/UK).
    r'^(?:RK|GO|TUBI|PLUTO|XUMO|PLEX|STIRR|FREEVEE|GLANCE)\s*[:\-\|]\s*',
]

MISC_PATTERNS = [
    r'\s*\([^)]*\)\s*',
]

# ISO-2 country codes Lineuparr lineup filenames use (keep in sync with
# PROVIDER_PREFIX_PATTERNS above and PluginConfig.COUNTRY_DIR_MAP).
_KNOWN_COUNTRY_CODES = {
    "US", "UK", "CA", "AU", "DE", "FR", "IT", "ES", "NL", "BR", "MX", "IN",
    "IE", "SE", "NO", "DK", "PT", "PL", "AT", "CH", "BE", "FI",
    # Codes seen as colon-prefixes in provider feeds (e.g. "TR: 24 TV",
    # "GR: ...", "IR: IRIB ...", "AL: DigitalB"). normalize_name() already
    # strips a 2-3 letter colon prefix via GEOGRAPHIC_PATTERNS, so detection
    # must recognize these too or the streams match cleanly yet evade the
    # country filter and leak as wildcards (the bug-064 asymmetry). "AR" is
    # deliberately excluded: in these feeds it tags Arabic-language channels,
    # not Argentina, so mapping it to a country would be wrong.
    "TR", "GR", "IR", "AL",
    # Second batch (2026-06-07): foreign feeds that were leaking in as BACKUP
    # streams on globally-named channels (CNN, BBC) in a US lineup preview.
    # Each was confirmed against real stream content (e.g. RO=Acasa, RU=2X2,
    # AZ=Baku TV, MK=24 Vesti, IL=Kan 11/13/14, CO=Cable Noticias, CR=FUTV).
    # Provider tags (HUB/AMP/STC/OSN/MEO/MXC) and regions (LA/AFR/AF) are NOT
    # added: HUB/AMP carry US channels, the rest are not single countries.
    "BG", "RO", "RU", "AZ", "HR", "TH", "RS", "MK", "IL", "CO", "CR", "CY",
    # Lower-volume but unambiguous ISO-2 countries also seen as colon-prefixes
    # (JP=Animax/BS, KR=Arirang, CZ/HU=European feeds, PH=GMA/Manila, NZ).
    "JP", "KR", "CZ", "HU", "NZ", "PH",
    # Singletons confirmed from feed content (VN=Vietnam, PK=92 News/8XM,
    # SI=Arena Sport, ET=Ethiopia via "ETH: Addis TV"). "MT" is NOT added: in
    # these feeds it tags theme channels (Cooking/Clubbing 4K), not Malta.
    "VN", "PK", "SI", "ET",
}

# ISO-3 or colloquial codes seen in M3U streams → ISO-2.
_ISO3_TO_ISO2 = {
    "USA": "US", "MEX": "MX", "IRE": "IE", "GER": "DE", "FRA": "FR",
    "ITA": "IT", "ESP": "ES", "NLD": "NL", "BRA": "BR", "IND": "IN",
    "ETH": "ET",
}

# (PLUTO <COUNTRY>) full-name variants seen in the M3U.
_PLUTO_COUNTRY_MAP = {
    "USA": "US", "US": "US", "UK": "UK",
    "BRAZIL": "BR", "SWEDEN": "SE", "NORWAY": "NO", "DENMARK": "DK",
    "GERMANY": "DE", "SPAIN": "ES", "FRANCE": "FR", "ITALY": "IT",
    "CANADA": "CA", "MEXICO": "MX", "INDIA": "IN", "IRELAND": "IE",
    "AUSTRALIA": "AU", "NETHERLANDS": "NL",
    # "LATIN"/"EUROPE" etc. intentionally omitted - ambiguous region.
}

# Cross-border country matching is STRICT by default: a lineup accepts only
# streams tagged with its own country (plus untagged streams, which can't be
# proven wrong). Blanket compatibility was removed - it wrongly merged
# channels that merely share a name across a border (Food Network US != Food
# Network CA; ESPN US != ESPN MX).
#
# The ONLY cross-border exceptions are specific channels that are genuinely the
# SAME feed on both sides. Keyed by the unordered country pair (frozenset), so
# the rule applies in both directions. Values are comparison keys produced by
# _fold_key() (accent-folded, lowercased, alphanumerics only).
#
# US<->MX: the US Spanish-language networks, whose M3U feeds are frequently
# tagged MEX/(MX) even though they are the US feed. To add a genuinely-shared
# channel, fold its name the same way (e.g. "NBC Universo" -> "nbcuniverso").
_CROSS_BORDER_SHARED = {
    frozenset({"US", "MX"}): frozenset({
        "univision", "unimas", "telemundo", "galavision", "universo",
        "nbcuniverso", "tudn", "telexitos", "bandamax", "tlnovelas",
        "univisiontlnovelas", "telemundodeportes", "universonbc",
    }),
}


def _fold_key(name):
    """Accent-fold, lowercase, and strip to alphanumerics for set membership.

    "Univisión" -> "univision", "NBC Universo" -> "nbcuniverso". Used to test a
    channel name against _CROSS_BORDER_SHARED regardless of accents/spacing.
    """
    if not name:
        return ""
    name = unicodedata.normalize('NFD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]', '', name.lower())


def _normalize_country_token(tok):
    """Map a raw prefix token to a whitelisted ISO-2 code, else None."""
    tok = tok.upper()
    if tok in _KNOWN_COUNTRY_CODES:
        return tok
    mapped = _ISO3_TO_ISO2.get(tok)
    return mapped if mapped in _KNOWN_COUNTRY_CODES else None


def detect_stream_country(name):
    """Detect ISO-2 country code from a stream name's leading prefix marker.

    Recognizes: `(US) ...`, `(USA) ...`, `US: ...`, `MEX: ...`, `(PLUTO UK) ...`
    (case-insensitive).
    Returns None when no recognized marker is present (so callers can accept
    unlabeled streams). Also returns None for look-alike prefixes like `NBC`
    or `FOX` that match the shape but aren't in the country whitelist.
    """
    if not name:
        return None

    # Pluto path uses its own full-country-name map rather than _normalize_country_token
    # (which reads _ISO3_TO_ISO2). The whitelist intersection is still enforced here.
    m = re.match(r'^\s*\(\s*PLUTO\s+([A-Za-z]+)\s*\)', name, re.IGNORECASE)
    if m:
        mapped = _PLUTO_COUNTRY_MAP.get(m.group(1).upper())
        return mapped if mapped in _KNOWN_COUNTRY_CODES else None

    m = re.match(r'^\s*\(\s*([A-Za-z]{2,3})\s*\)', name)
    if m:
        return _normalize_country_token(m.group(1))

    m = re.match(r'^\s*([A-Za-z]{2,3})\s*[-:|]', name)
    if m:
        return _normalize_country_token(m.group(1))

    # Bare country tag + whitespace, no separator (e.g. "US beIN SPORTS (S)",
    # "CA TSN 1 HD", "FR beIN SPORTS MAX", "MEX Bein Sports"). normalize_name()
    # strips this exact prefix via PROVIDER_PREFIX_PATTERNS, so the country
    # filter must recognize it too, otherwise these match a foreign lineup
    # cleanly but can't be proven wrong-country, and leak in as backup streams.
    # Restricted to a curated set of unambiguous codes (same set as the prefix
    # stripper) so it can't eat "USA Network" (USA, not US+space), "IN Country
    # Television" (IN), or "IT Crowd" (IT). MEX/FRA/GER 3-letter aliases are
    # included and folded to ISO-2 via _normalize_country_token.
    m = re.match(r'^\s*(US|UK|CA|AU|FR|DE|MX|MEX|FRA|GER)\s+', name, re.IGNORECASE)
    if m:
        return _normalize_country_token(m.group(1))

    # Country code glued directly to a quality tag, no separator ("UKSD: Sky
    # Sports", "UKHD ESPN", "USFHD ..."). normalize_name() strips this via
    # PROVIDER_PREFIX_PATTERNS, so detection must match it too, otherwise these
    # match a foreign lineup cleanly but can't be proven wrong-country and leak
    # in as backup streams (same asymmetry as bug-064).
    m = re.match(r'^\s*(US|UK)(?:SD|HD|FHD|UHD|FD|HEVC|4K|8K)\b', name, re.IGNORECASE)
    if m:
        return _normalize_country_token(m.group(1))

    # "USA " as a US country tag ("USA  ABC", "USA BET"). The real channel
    # "USA Network" is tagged "US ..."/"US: ..." in these feeds, never bare
    # "USA ", so a NETWORK lookahead keeps it from being misread as country=US.
    m = re.match(r'^\s*USA\s+(?!NETWORK\b)', name, re.IGNORECASE)
    if m:
        return "US"

    return None


def detect_category_country(category_name):
    """Detect an ISO-2 country code from a lineup category-name prefix.

    Single-country lineups carry one country code in the filename and use plain
    theme categories ("News", "Sports", "Movies"). Mixed-country lineups instead
    encode each channel's country in the category name, e.g. "AU| AUSTRALIA VIP",
    "UK: Sports", "NZ Movies", "US News". This lets the country filter run
    per-category when the filename has no single country (e.g.
    "AU-NZ-UK_Test_Mixed_lineup.json").

    Returns a whitelisted ISO-2 code, or None for ordinary theme categories so
    the caller falls back to the lineup-level code. Only the curated
    _KNOWN_COUNTRY_CODES are accepted, so a category like "Sci-Fi" (token "SCI")
    or "On-Demand" (token "ON") is not misread as a country.

    Two branches with deliberately different breadth:
      - delimiter branch ("XX:"/"XX-"/"XX|"): accepts ANY code in the full
        _KNOWN_COUNTRY_CODES set, so a mixed lineup can prefix categories with
        ES/IT/etc. A theme category that happens to start "XX<delim>" where XX
        is a real country code (e.g. "IT: ...") IS read as that country - this
        is intended for the mixed-lineup use case, and such category names do
        not occur in single-country theme lineups.
      - bare-space branch ("XX Movies"): restricted to a small curated set so a
        plain word cannot be eaten by a space-separated token.
    """
    if not category_name:
        return None

    # Country code followed by a delimiter: "AU| ...", "UK: ...", "US-...".
    m = re.match(r'^\s*([A-Za-z]{2,3})\s*[-:|]', category_name)
    if m:
        return _normalize_country_token(m.group(1))

    # Bare country tag + whitespace ("NZ Movies", "US News"). Restricted to a
    # curated set so an ordinary theme word can't be eaten.
    m = re.match(r'^\s*(US|UK|CA|AU|NZ|FR|DE|MX|MEX|FRA|GER)\s+', category_name, re.IGNORECASE)
    if m:
        return _normalize_country_token(m.group(1))

    return None


def country_codes_in_text(text):
    """Return the set of whitelisted ISO-2 country codes that appear as whole
    tokens anywhere in `text`.

    A "token" is a maximal run of letters bounded by anything non-alpha (start,
    end, whitespace, separators like - : |, or wildcards * ?). Only 2-3 letter
    tokens are considered, and only those that resolve to a known country code
    (via _normalize_country_token, which folds ISO-3 like USA->US). This is used
    to warn when a group prefix or EPG source filter targets a country other
    than the lineup's, e.g. "UK*", "UK-*", "*-UK", "UK Jesmann" all yield {"UK"};
    "AU:" / "AU " yield {"AU"}; "EPG Share", "Jessman", "DTV-" yield set().
    """
    codes = set()
    for tok in re.split(r'[^A-Za-z]+', text or ""):
        if 2 <= len(tok) <= 3:
            cc = _normalize_country_token(tok)
            if cc:
                codes.add(cc)
    return codes


class FuzzyMatcher:
    """Handles fuzzy matching for Lineuparr with alias support and channel number boosting."""

    def __init__(self, match_threshold=80, logger=None):
        self.match_threshold = match_threshold
        self.logger = logger or LOGGER
        # Cache for pre-normalized stream names (performance optimization)
        self._norm_cache = {}  # raw_name -> normalized_lower
        self._norm_nospace_cache = {}  # raw_name -> normalized_nospace
        self._processed_cache = {}  # raw_name -> processed_for_matching
        # Cumulative count of candidates dropped by the country filter across
        # match_all_streams calls. Callers reset this before a matching loop
        # and read it after, so they can log a summary.
        self.country_filter_drops = 0
        # Cache for callsign extraction: raw_name -> (callsign|None, is_high_conf)
        self._callsign_cache = {}

    def precompute_normalizations(self, names, user_ignored_tags=None):
        """
        Pre-normalize a list of names and cache the results.
        Dramatically improves performance by avoiding redundant normalization
        when matching many lineup channels against the same stream list.
        """
        self._norm_cache.clear()
        self._norm_nospace_cache.clear()
        self._processed_cache.clear()
        self._callsign_cache.clear()

        for name in names:
            if self._is_group_header(name):
                continue
            norm = self.normalize_name(name, user_ignored_tags)
            if norm and len(norm) >= 2:
                norm_lower = norm.lower()
                self._norm_cache[name] = norm_lower
                self._norm_nospace_cache[name] = re.sub(r'[\s&\-]+', '', norm_lower)
                self._processed_cache[name] = self.process_string_for_matching(norm)

        self.logger.info(f"Pre-normalized {len(self._norm_cache)} stream names (from {len(names)} total)")

    def _get_cached_norm(self, name, user_ignored_tags=None):
        """Get cached normalization or compute on the fly."""
        if name in self._norm_cache:
            return self._norm_cache[name], self._norm_nospace_cache[name]
        norm = self.normalize_name(name, user_ignored_tags)
        if not norm or len(norm) < 2:
            return None, None
        norm_lower = norm.lower()
        return norm_lower, re.sub(r'[\s&\-]+', '', norm_lower)

    def _get_cached_processed(self, name, user_ignored_tags=None):
        """Get cached processed string or compute on the fly."""
        if name in self._processed_cache:
            return self._processed_cache[name]
        norm = self.normalize_name(name, user_ignored_tags)
        if not norm or len(norm) < 2:
            return None
        return self.process_string_for_matching(norm)

    def normalize_name(self, name, user_ignored_tags=None, ignore_quality=True, ignore_regional=True,
                       ignore_geographic=True, ignore_misc=True):
        """
        Normalize channel or stream name for matching by removing tags, prefixes, and noise.
        """
        if user_ignored_tags is None:
            user_ignored_tags = []

        original_name = name

        # Strip IPTV provider prefixes BEFORE hyphen normalization so that
        # "FR - Canal+ FHD" loses its "FR - " while the hyphen is still a
        # hyphen. After hyphen normalization the separator would become a space
        # and the pattern would fail to match, leaving "FR" as a stray token.
        for pattern in PROVIDER_PREFIX_PATTERNS:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Quality patterns (before space normalization). Loop until stable so
        # chained suffixes like "4K HDR" or "UHD HDR" are fully stripped in
        # successive passes (each pass may expose a token for the next).
        if ignore_quality:
            prev = None
            while prev != name:
                prev = name
                for pattern in QUALITY_PATTERNS:
                    name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Normalize spacing around numbers
        name = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', name)
        name = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', name)

        # Normalize hyphens to spaces
        name = re.sub(r'-', ' ', name)

        # Replace dots between word chars with spaces (e.g. "JusticeCentral.TV"
        # → "JusticeCentral TV"). Keeps the dot-suffix variant equivalent to
        # the spaced form for matching purposes.
        name = re.sub(r'(?<=\w)\.(?=\w)', ' ', name)

        # Normalize number-words to digits so "BBC Three" and "BBC 3" share
        # tokens. Critical for cases like "Three Angels Broadcasting Network"
        # vs "3 Angels Broadcasting Network", and for BBC One/Two/Three/Four
        # vs BBC 1/2/3/4. Bounded by word boundaries so brand names with
        # embedded letters (e.g. "Onesimus") aren't corrupted.
        _NUM_WORDS = {
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
            "eleven": "11", "twelve": "12",
        }
        def _num_repl(m):
            return _NUM_WORDS[m.group(0).lower()]
        name = re.sub(
            r'\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b',
            _num_repl, name, flags=re.IGNORECASE,
        )

        # Split CamelCase boundaries so "JusticeCentral" becomes "Justice
        # Central" and "DangerTV" becomes "Danger TV". Two separate patterns:
        #   1. lower → Upper followed by lower  (Justice|Central, Danger|Iq → no, etc.)
        #   2. lower(4+) → UPPER acronym at word boundary  (Danger|TV, Beauty|IQ)
        # The 4-char floor on rule 2 protects short brand names like "MeTV" and
        # "truTV" whose existing EPG matches rely on the un-split form.
        name = re.sub(r'([a-z])([A-Z][a-z])', r'\1 \2', name)
        name = re.sub(r'([a-z]{4,})([A-Z]{2,})\b', r'\1 \2', name)

        # Strip region tokens (East / Eastern / West and the (E)/(W)
        # abbreviations) for SCORING. Region CORRECTNESS - East vs West vs
        # Pacific - is enforced separately by the post-match region filter in
        # match_all_streams, which reads the ORIGINAL un-normalized names, not
        # this output. Stripping here lets a regionless lineup channel
        # ("Food Network") still score-match a region-tagged stream
        # ("Food Network Eastern" / "... East") instead of being dragged below
        # threshold by the extra token; the filter then accepts it (regionless
        # defaults to East). "Western"/"Westerns" is a movie genre, NOT a
        # region, so bare "west" is stripped only on a word boundary (\bwest\b
        # does not match inside "western") and "western" is never touched.
        name = re.sub(r'\(\s*(?:eastern|east|e|west|w)\s*\)', ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'\b(?:eastern|east|west)\b', ' ', name, flags=re.IGNORECASE)

        # Remove leading parenthetical prefixes
        while name.lstrip().startswith('('):
            new_name = re.sub(r'^\s*\([^\)]+\)\s*', '', name)
            if new_name == name:
                break
            name = new_name

        # Build pattern list based on flags
        patterns_to_apply = []
        if ignore_regional:
            patterns_to_apply.extend(REGIONAL_PATTERNS)
        if ignore_geographic:
            patterns_to_apply.extend(GEOGRAPHIC_PATTERNS)
        if ignore_misc and ignore_regional:
            patterns_to_apply.extend(MISC_PATTERNS)

        for pattern in patterns_to_apply:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Apply user-configured ignored tags
        for tag in user_ignored_tags:
            escaped_tag = re.escape(tag)
            if '[' in tag or ']' in tag or '(' in tag or ')' in tag:
                name = re.sub(escaped_tag + r'\s*', '', name, flags=re.IGNORECASE)
            else:
                if re.match(r'^\w+$', tag):
                    name = re.sub(r'\b' + escaped_tag + r'\b', '', name, flags=re.IGNORECASE)
                else:
                    name = re.sub(escaped_tag + r'\s*', '', name, flags=re.IGNORECASE)

        # Remove callsigns in parentheses
        if ignore_regional:
            name = re.sub(r'\([KW][A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?\)', '', name, flags=re.IGNORECASE)
        else:
            name = re.sub(r'\([KW](?!EST\)|ACIFIC\)|ENTRAL\)|OUNTAIN\)|TLANTIC\))[A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?\)', '', name, flags=re.IGNORECASE)

        if ignore_regional:
            name = re.sub(r'\([A-Z0-9]+\)', '', name)

        # Remove common suffixes/prefixes
        name = re.sub(r'^The\s+', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Network\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Channel\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+TV\s*$', '', name, flags=re.IGNORECASE)

        # Strip decorative Unicode markers (◉, ², superscript letters ᴿᴬᵂ…)
        # that some IPTV providers append as quality/status badges. Only
        # characters in decorator categories are removed; accented letters
        # (é, î, ü…) are in Ll/Lu and are preserved.
        name = ''.join(
            ' ' if unicodedata.category(c) in _DECORATOR_CATS else c
            for c in name
        )

        # Clean up whitespace
        name = re.sub(r'\s+', ' ', name).strip()

        if not name:
            self.logger.debug(f"normalize_name returned empty for: '{original_name}'")

        return name

    def calculate_similarity(self, str1, str2, min_ratio=0.0):
        """Levenshtein distance-based similarity ratio (0.0 to 1.0).
        If min_ratio > 0, returns 0.0 early when the result can't reach it."""
        if len(str1) < len(str2):
            str1, str2 = str2, str1
        len1, len2 = len(str1), len(str2)
        if len2 == 0 or len1 == 0:
            return 0.0

        total_len = len1 + len2
        # Length-difference pre-check: even with 0 substitutions, the distance
        # is at least (len1 - len2), so the max possible ratio is bounded.
        if min_ratio > 0:
            max_possible = (total_len - (len1 - len2)) / total_len
            if max_possible < min_ratio:
                return 0.0
            # Max allowed distance to still meet min_ratio
            max_distance = int(total_len * (1.0 - min_ratio))

        previous_row = list(range(len2 + 1))
        for i, c1 in enumerate(str1):
            current_row = [i + 1]
            for j, c2 in enumerate(str2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            # Early termination: if the minimum value in this row already
            # exceeds max_distance, no subsequent row can produce a valid result
            if min_ratio > 0 and min(current_row) > max_distance:
                return 0.0
            previous_row = current_row

        distance = previous_row[-1]
        return (total_len - distance) / total_len

    @staticmethod
    def _length_scaled_threshold(base_threshold, shorter_len):
        """Require higher similarity for shorter strings to avoid false positives."""
        if shorter_len <= 4:
            return max(base_threshold, 95)
        elif shorter_len <= 8:
            return max(base_threshold, 90)
        return base_threshold

    @staticmethod
    def _has_token_overlap(str_a, str_b, min_token_len=4, require_majority=False):
        """Check that distinctive tokens are shared between two strings.

        Basic mode: at least one token (>= min_token_len) must be shared.
        Majority mode: uses all tokens (>= 2 chars) and requires that more than
        half of the smaller set overlaps. Catches false positives like
        "america racing" vs "america bbc" while allowing single-token matches.
        """
        # "network"/"channel"/"television" are generic brand-suffix words, not
        # distinctive - treat as common so the subset guard does not reject
        # cases like "FanDuel Sports Cincinnati" vs "FanDuel Sports Network
        # Cincinnati". (They're already stripped from end-of-string by
        # normalize_name; this catches mid-string occurrences.)
        common_words = {
            "the", "and", "of", "in", "on", "at", "to", "for", "a", "an",
            "network", "channel", "television",
        }

        if require_majority:
            # Use all meaningful tokens (>= 2 chars) for stricter checking.
            # Single-digit tokens (1, 2, 3, ...) are kept because they're
            # channel-distinguishing (BBC 1 vs BBC 2, ESPN 1 vs ESPN 2 etc.)
            # even though they're only 1 char.
            def _meaningful(t):
                if t in common_words:
                    return False
                return len(t) >= 2 or t.isdigit()
            tokens_a = {t for t in str_a.split() if _meaningful(t)}
            tokens_b = {t for t in str_b.split() if _meaningful(t)}
            if not tokens_a or not tokens_b:
                return True
            shared = tokens_a & tokens_b
            if not shared:
                return False
            smaller = min(len(tokens_a), len(tokens_b))
            if not len(shared) > smaller / 2:
                return False

            unique_a = tokens_a - tokens_b
            unique_b = tokens_b - tokens_a

            # Subset guard: when one side is a strict subset of the other and
            # the larger side has a distinctive token the smaller lacks, the
            # candidate is a more specific channel than the query and the high
            # fuzzy score is a false positive. Catches e.g. "Nickelodeon" vs
            # "Nickelodeon Teen". Threshold is >=4 chars; "live"/"now" are
            # explicitly non-distinctive (stream label variants) so "ABC News"
            # still matches "ABC News Live". Pure-digit tokens >=2 chars
            # (e.g. "360") are also distinctive: "Canal+Sport" must not match
            # "Canal+Sport 360".
            if not unique_a:
                if any(_is_distinctive(t) for t in unique_b):
                    return False
            elif not unique_b:
                if any(_is_distinctive(t) for t in unique_a):
                    return False

            # Divergent guard: when BOTH sides have unique tokens AND at least
            # one of those unique tokens is a distinctive (>=4 char) word, the
            # strings describe different brands and the fuzzy score is
            # misleading. Catches "Sky Cinema Disney" vs "Sky Cinema Decades"
            # (decades = 7 chars), "Sky Cinema Fast" vs "Sky Cinema Family",
            # and "BBC One vs BBC Two" once number-words become digits earlier.
            # Number-word→digit normalization (in normalize_name) reduces this
            # guard's risk: "Three Angels" / "3 Angels" both become "3 angels"
            # before reaching here, so they never trigger this case.
            if unique_a and unique_b:
                if any(len(t) >= 4 for t in unique_a | unique_b):
                    return False

            # Numeric/ordinal divergent guard: when BOTH sides have a numeric
            # or ordinal token unique to them, they are sibling channels
            # distinguished by number (BBC One vs BBC Two; ESPN 1 vs ESPN 2).
            # Short tokens like "one"/"two" wouldn't trip the divergent guard
            # above so they need their own check.
            _NUMERIC = {
                "one", "two", "three", "four", "five", "six", "seven", "eight",
                "nine", "ten", "eleven", "twelve",
                "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
                "first", "second", "third", "fourth", "fifth",
            }
            if (unique_a & _NUMERIC) and (unique_b & _NUMERIC):
                return False

            return True

        # Basic mode: at least one long token shared
        tokens_a = {t for t in str_a.split() if t not in common_words and len(t) >= min_token_len}
        tokens_b = {t for t in str_b.split() if t not in common_words and len(t) >= min_token_len}
        if not tokens_a or not tokens_b:
            return True
        return bool(tokens_a & tokens_b)

    def process_string_for_matching(self, s):
        """Normalize for token-sort matching: lowercase, remove accents, sort tokens."""
        s = unicodedata.normalize('NFD', s)
        s = ''.join(char for char in s if unicodedata.category(char) != 'Mn')
        s = s.lower()
        s = re.sub(r'([a-z])(\d)', r'\1 \2', s)
        cleaned_s = ""
        for char in s:
            if 'a' <= char <= 'z' or '0' <= char <= '9':
                cleaned_s += char
            else:
                cleaned_s += ' '
        tokens = sorted([token for token in cleaned_s.split() if token])
        return " ".join(tokens)

    @staticmethod
    def _is_group_header(name):
        """Return True for M3U playlist group/section separators like '##### EUROSPORT #####'
        or '## 24/7 COMEDY ##'. These are not real stream names and must be
        excluded from matching. A run of 2+ #/=/* is decorative (no real channel
        name contains "##"/"=="/"**"); pipes need a run of 3+ since a single "|"
        is a common in-name separator ("001 | Team A vs Team B")."""
        return bool(re.search(r'[#=*]{2,}|\|{3,}', name or ""))

    @staticmethod
    def _trailing_number(name):
        """Return the integer value of a space-separated, purely-numeric
        trailing token, or None. 'HBO 2' -> 2, 'DIRECTV 4K Live 1' -> 1,
        'ESPN' -> None, 'ESPN2' -> None (digit not space-separated). Used to
        reject 'Foo 1' vs 'Foo 2' false positives -- different channels that
        otherwise fuzzy-match almost perfectly."""
        m = re.search(r'(?:^|\s)(\d{1,4})\s*$', name or "")
        return int(m.group(1)) if m else None

    def _channel_number_boost(self, stream_name, expected_number):
        """
        Check if a stream name contains the expected channel number.
        Returns 5-point boost if found, 0 otherwise.
        Only boosts for 3+ digit numbers to avoid false positives on short numbers.
        """
        if expected_number is None:
            return 0
        number_str = str(expected_number)
        # Only boost for 3+ digit numbers (avoids "ESPN2" matching channel 2)
        if len(number_str) < 3:
            return 0
        # Require number to appear with clear delimiters (space, bracket, or string boundary)
        if re.search(r'(?:^|[\s\[\(])' + re.escape(number_str) + r'(?:$|[\s\]\)])', stream_name):
            return 5
        return 0

    # Words that match the callsign regex shape but are never US broadcast
    # callsigns. A US callsign (K/W + 2-4 letters) is shape-identical to many
    # common English words, so the loose Priority-4 pattern mis-extracts them -
    # e.g. "with" in "Bizarre Foods with Andrew Zimmern" becomes callsign
    # "WITH". Regex alone cannot tell "WITH" from "WABC"; frequent K/W-initial
    # words are denied explicitly so they never extract as a callsign. WWE/WWF/
    # WCW stop wrestling show names extracting as false-positive callsigns.
    _CALLSIGN_DENYLIST = frozenset({
        'WWE', 'WWF', 'WCW', 'EAST',
        'WAR', 'WARS', 'WARM', 'WASH', 'WATCH', 'WAVE', 'WAVES', 'WAY', 'WAYS',
        'WEB', 'WEEK', 'WELL', 'WENT', 'WERE', 'WEST', 'WHAT', 'WHEN', 'WHERE',
        'WHICH', 'WHILE', 'WHITE', 'WHO', 'WHY', 'WIDE', 'WIFE', 'WILD', 'WILL',
        'WIND', 'WINE', 'WING', 'WINGS', 'WINS', 'WIRE', 'WISE', 'WISH', 'WITH',
        'WOLF', 'WOMAN', 'WOMEN', 'WOOD', 'WORD', 'WORDS', 'WORK', 'WORKS',
        'WORLD', 'WORM', 'WORN', 'WRAP',
        'KEEN', 'KEEP', 'KEPT', 'KEY', 'KEYS', 'KICK', 'KID', 'KIDS', 'KILL',
        'KIND', 'KING', 'KINGS', 'KISS', 'KITE', 'KNEE', 'KNEW', 'KNOW', 'KNOWN',
    })

    def _compute_callsign_with_confidence(self, channel_name):
        """
        Extract US TV callsign with a confidence flag.

        Returns (callsign, is_high_confidence). High confidence =
        Priorities 1-3 (parenthesized / suffixed-paren / end-of-name).
        Priority 4 (any loose word) is low confidence. (None, False)
        when nothing extractable.
        """
        # Remove common provider prefixes
        channel_name = re.sub(r'^D\d+-', '', channel_name)
        channel_name = re.sub(r'^USA?\s*[^a-zA-Z0-9]*\s*', '', channel_name, flags=re.IGNORECASE)

        # Priority 1: Callsigns in parentheses (most reliable)
        paren_match = re.search(r'\(([KW][A-Z]{3})(?:-[A-Z\s]+)?\)', channel_name, re.IGNORECASE)
        if paren_match:
            callsign = paren_match.group(1).upper()
            if callsign not in self._CALLSIGN_DENYLIST:
                return callsign, True

        # Priority 2: Callsigns with suffix in parentheses
        paren_suffix_match = re.search(r'\(([KW][A-Z]{2,4}-(?:TV|CD|LP|DT|LD))\)', channel_name, re.IGNORECASE)
        if paren_suffix_match:
            return paren_suffix_match.group(1).upper(), True

        # Priority 3: Callsigns at the end
        end_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\s*(?:\.[a-z]+)?\s*$', channel_name, re.IGNORECASE)
        if end_match:
            callsign = end_match.group(1).upper()
            if callsign not in self._CALLSIGN_DENYLIST:
                return callsign, True

        # Priority 4: Any word matching callsign pattern (low confidence)
        word_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\b', channel_name, re.IGNORECASE)
        if word_match:
            callsign = word_match.group(1).upper()
            if callsign not in self._CALLSIGN_DENYLIST:
                return callsign, False

        return None, False

    def _extract_callsign_with_confidence(self, channel_name):
        """
        Cached wrapper around _compute_callsign_with_confidence.

        Extraction is pure in channel_name, so results are memoized - the
        anchor calls this once per stream over a fixed candidate list, which
        is otherwise massively redundant across the per-channel matching
        loop. Cache is cleared by precompute_normalizations.
        """
        cached = self._callsign_cache.get(channel_name)
        if cached is not None:
            return cached
        result = self._compute_callsign_with_confidence(channel_name)
        self._callsign_cache[channel_name] = result
        return result

    def extract_callsign(self, channel_name):
        """
        Extract US TV callsign from channel name with priority order.
        Returns None if common false positives appear alone.
        """
        callsign, _ = self._extract_callsign_with_confidence(channel_name)
        return callsign

    def normalize_callsign(self, callsign):
        """Remove the broadcast suffix (-TV/-CD/-LP/-DT/-LD) from a callsign."""
        if callsign:
            callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
        return callsign

    def alias_match(self, lineup_name, candidate_names, alias_map, user_ignored_tags=None):
        """
        Stage 0: Alias-aware matching.
        For each known alias of the lineup channel name, check if any candidate stream
        name matches after normalization.

        Args:
            lineup_name: Official channel name from lineup JSON
            candidate_names: List of stream names to match against
            alias_map: Dict mapping lineup names to lists of known aliases
            user_ignored_tags: Tags to strip during normalization

        Returns:
            List of (stream_name, score, "alias") tuples for all matches, sorted by score desc.
            Empty list if no alias matches found.
        """
        if user_ignored_tags is None:
            user_ignored_tags = []

        aliases = alias_map.get(lineup_name, [])
        if not aliases:
            return []

        matches = []

        # Normalize all aliases - track spaced and nospace versions separately
        alias_lookup = {}  # normalized_lower -> alias (for exact matching, includes both forms)
        alias_spaced = []  # only the spaced (original) normalized forms (for similarity matching)
        for alias in aliases:
            norm = self.normalize_name(alias, user_ignored_tags)
            if norm:
                norm_lower = norm.lower()
                alias_lookup[norm_lower] = alias
                alias_spaced.append(norm_lower)
                # Also add space-stripped version for exact matching only
                nospace = re.sub(r'[\s&\-]+', '', norm_lower)
                if nospace != norm_lower:
                    alias_lookup[nospace] = alias

        if not alias_lookup:
            return []

        for candidate in candidate_names:
            if self._is_group_header(candidate):
                continue
            candidate_lower, candidate_nospace = self._get_cached_norm(candidate, user_ignored_tags)
            if not candidate_lower:
                continue

            # Check exact match against any alias (spaced or nospace)
            if candidate_lower in alias_lookup or candidate_nospace in alias_lookup:
                matches.append((candidate, 100, "alias"))
                continue

            # Check high-similarity match against spaced alias forms only
            best_alias_score = 0
            best_alias_len = 0
            best_alias_norm = ""
            threshold_ratio = self.match_threshold / 100.0
            for norm_alias in alias_spaced:
                ratio = self.calculate_similarity(norm_alias, candidate_lower, min_ratio=threshold_ratio)
                if ratio > best_alias_score:
                    best_alias_score = ratio
                    best_alias_len = min(len(norm_alias), len(candidate_lower))
                    best_alias_norm = norm_alias

            score = int(best_alias_score * 100)
            effective_threshold = self._length_scaled_threshold(self.match_threshold, best_alias_len)

            if score >= effective_threshold and score < 100:
                # Aliases are short/curated - a fuzzy alias match must share a
                # majority of tokens, not just one. This rejects false positives
                # like alias "ABC News" vs stream "BBC News" (93%, shares only
                # "news"; the 3-char call sign "abc"/"bbc" is below the basic
                # overlap guard's 4-char token floor).
                # Use process_string_for_matching (NFD) so accented tokens like
                # "chérie" match their unaccented stream form "cherie".
                if not self._has_token_overlap(
                    self.process_string_for_matching(best_alias_norm),
                    self.process_string_for_matching(candidate_lower),
                    require_majority=True,
                ):
                    continue

            if score >= effective_threshold:
                matches.append((candidate, score, "alias"))

        # Sort by score descending
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    def fuzzy_match(self, query_name, candidate_names, user_ignored_tags=None,
                    ignore_quality=True, ignore_regional=True, ignore_geographic=True, ignore_misc=True):
        """
        3-stage fuzzy matching: exact → substring → fuzzy token-sort.
        Uses precomputed normalization cache when available for performance.
        (Alias matching is handled separately in alias_match for Lineuparr's pipeline.)

        Returns:
            Tuple of (matched_name, score, match_type) or (None, 0, None)
        """
        if not candidate_names:
            return None, 0, None
        if user_ignored_tags is None:
            user_ignored_tags = []

        normalized_query = self.normalize_name(query_name, user_ignored_tags,
                                               ignore_quality=ignore_quality,
                                               ignore_regional=ignore_regional,
                                               ignore_geographic=ignore_geographic,
                                               ignore_misc=ignore_misc)
        if not normalized_query:
            return None, 0, None

        normalized_query_lower = normalized_query.lower()
        normalized_query_nospace = re.sub(r'[\s&\-]+', '', normalized_query_lower)
        processed_query = None  # Lazy-compute for stage 3

        best_match = None
        best_ratio = 0
        best_match_type = None
        best_match_candidate_lower = ""

        for candidate in candidate_names:
            # Use cache if available, otherwise normalize on the fly
            candidate_lower, candidate_nospace = self._get_cached_norm(candidate, user_ignored_tags)
            if not candidate_lower:
                continue

            # Stage 1: Exact match
            if normalized_query_nospace == candidate_nospace:
                return candidate, 100, "exact"

            ratio = self.calculate_similarity(normalized_query_lower, candidate_lower, min_ratio=0.97)
            if ratio >= 0.97 and ratio > best_ratio and self._has_token_overlap(normalized_query_lower, candidate_lower, require_majority=True):
                best_match = candidate
                best_ratio = ratio
                best_match_type = "exact"
                best_match_candidate_lower = candidate_lower
                continue

            # Stage 2: Substring match (only if no exact found yet)
            if not best_match_type or best_match_type != "exact":
                if normalized_query_lower in candidate_lower or candidate_lower in normalized_query_lower:
                    length_ratio = min(len(normalized_query_lower), len(candidate_lower)) / max(len(normalized_query_lower), len(candidate_lower))
                    if length_ratio >= 0.75:
                        sub_ratio = self.calculate_similarity(normalized_query_lower, candidate_lower, min_ratio=self.match_threshold / 100.0)
                        if sub_ratio > best_ratio:
                            sub_score = int(sub_ratio * 100)
                            shorter_len = min(len(normalized_query_lower), len(candidate_lower))
                            effective_threshold = self._length_scaled_threshold(self.match_threshold, shorter_len)
                            if sub_score >= effective_threshold and self._has_token_overlap(normalized_query_lower, candidate_lower, require_majority=True):
                                best_match = candidate
                                best_ratio = sub_ratio
                                best_match_type = "substring"

        # Return exact/substring match if found
        if best_match and best_match_type == "exact":
            return best_match, int(best_ratio * 100), best_match_type
        if best_match and best_match_type == "substring" and int(best_ratio * 100) >= self.match_threshold:
            return best_match, int(best_ratio * 100), best_match_type

        # Stage 3: Fuzzy token-sort matching
        processed_query = self.process_string_for_matching(normalized_query)
        best_score = -1.0
        best_fuzzy = None
        best_fuzzy_proc_candidate = ""

        for candidate in candidate_names:
            processed_candidate = self._get_cached_processed(candidate, user_ignored_tags)
            if not processed_candidate:
                continue

            score = self.calculate_similarity(processed_query, processed_candidate, min_ratio=self.match_threshold / 100.0)
            if score > best_score:
                best_score = score
                best_fuzzy = candidate
                best_fuzzy_proc_candidate = processed_candidate

        percentage_score = int(best_score * 100)
        if percentage_score >= self.match_threshold:
            shorter_len = min(len(processed_query), len(best_fuzzy_proc_candidate))
            effective_threshold = self._length_scaled_threshold(self.match_threshold, shorter_len)
            if percentage_score >= effective_threshold and self._has_token_overlap(processed_query, best_fuzzy_proc_candidate, require_majority=True):
                return best_fuzzy, percentage_score, f"fuzzy ({percentage_score})"

        return None, 0, None

    def match_all_streams(self, lineup_name, candidate_names, alias_map, channel_number=None,
                          user_ignored_tags=None, lineup_country=None, quality_aware=False):
        """
        Full matching pipeline for Lineuparr: alias → exact → substring → fuzzy, with number boost.
        Returns ALL matching streams sorted by score.

        Args:
            lineup_name: Official channel name from lineup
            candidate_names: List of stream names
            alias_map: Alias dict
            channel_number: Expected channel number for boost
            user_ignored_tags: Tags to strip
            quality_aware: When True, upgrade streams (4K/8K/UHD/HDR) are excluded
                from standard channels. Streams listed in alias_map for this channel bypass
                the filter (e.g. "France 2": ["FRANCE 2 4K HDR"] allows that specific stream).

        Returns:
            List of (stream_name, score, match_type) tuples sorted by score desc.
        """
        if not candidate_names:
            return []

        if user_ignored_tags is None:
            user_ignored_tags = []

        # Quality-aware pre-filtering (opt-in via quality_aware).
        # Both tiers are gated: upgrade channels only match upgrade streams,
        # standard channels only match standard streams. Streams listed in
        # alias_map for either tier bypass the filter.
        if quality_aware:
            lineup_is_upgrade = has_upgrade_quality(lineup_name or "")
            explicit_bypass = set()
            if alias_map:
                raw = alias_map.get(lineup_name, [])
                alias_list = [raw] if isinstance(raw, str) else list(raw)
                norm_aliases = {
                    self.normalize_name(a, ignore_quality=False).lower()
                    for a in alias_list
                    if self.normalize_name(a, ignore_quality=False)
                }
                for c in candidate_names:
                    norm_c = self.normalize_name(c, ignore_quality=False)
                    if norm_c and norm_c.lower() in norm_aliases:
                        explicit_bypass.add(c)
            candidate_names = [
                c for c in candidate_names
                if has_upgrade_quality(c) == lineup_is_upgrade or c in explicit_bypass
            ]
            if not candidate_names:
                return []

        # Callsign anchor (asymmetric): extract the lineup channel's US
        # broadcast callsign up front. Used after the fuzzy stages to floor
        # high-confidence callsign agreement and hard-reject disagreement.
        query_callsign, query_cs_hc = self._extract_callsign_with_confidence(lineup_name or "")
        query_callsign_norm = self.normalize_callsign(query_callsign) if query_callsign else None
        callsign_anchored = set()  # candidate names exempt from the region filter

        all_matches = {}  # stream_name -> (score, match_type)

        # Stage 0: Alias matching
        alias_results = self.alias_match(lineup_name, candidate_names, alias_map, user_ignored_tags)
        for stream_name, score, mtype in alias_results:
            if stream_name not in all_matches or score > all_matches[stream_name][0]:
                all_matches[stream_name] = (score, mtype)

        # Stages 1-3: Standard fuzzy matching
        # We need to collect ALL matches above threshold, not just the best
        normalized_query = self.normalize_name(lineup_name, user_ignored_tags)
        if normalized_query:
            normalized_query_lower = normalized_query.lower()
            normalized_query_nospace = re.sub(r'[\s&\-]+', '', normalized_query_lower)
            processed_query = self.process_string_for_matching(normalized_query)
            # A differing space-separated trailing number means a different
            # channel ("DIRECTV 4K Live 1" vs "... Live 2") -- used to skip
            # those near-identical false positives in the fuzzy stages below.
            query_trailing_num = self._trailing_number(normalized_query_lower)
            # Detect "+1"/"+2" time-shift suffix so shift channels never match
            # non-shift streams and vice versa ("Nickelodeon+1" vs "Nickelodeon").
            # Must check the ORIGINAL name: normalize_name strips "+" (Sm category)
            # so "+1" becomes "1" post-normalization and would be undetectable.
            query_is_shift = bool(_PLUS_SHIFT_RE.search(lineup_name or ""))

            for candidate in candidate_names:
                if candidate in all_matches:
                    continue  # Already matched via alias
                if self._is_group_header(candidate):
                    continue  # Skip M3U group/section separators

                # Use cached normalizations for performance
                candidate_lower, candidate_nospace = self._get_cached_norm(candidate, user_ignored_tags)
                if not candidate_lower:
                    continue

                cand_is_shift = bool(_PLUS_SHIFT_RE.search(candidate))
                if query_is_shift != cand_is_shift:
                    continue  # Shift channel (+1/+2) must only match shift streams

                if query_trailing_num is not None:
                    cand_trailing_num = self._trailing_number(candidate_lower)
                    if cand_trailing_num is not None and cand_trailing_num != query_trailing_num:
                        continue  # "Foo 1" vs "Foo 2" -- different channel

                score = 0
                mtype = None

                # Exact
                if normalized_query_nospace == candidate_nospace:
                    score = 100
                    mtype = "exact"
                else:
                    ratio = self.calculate_similarity(normalized_query_lower, candidate_lower, min_ratio=0.97)
                    if ratio >= 0.97 and self._has_token_overlap(normalized_query_lower, candidate_lower, require_majority=True):
                        score = int(ratio * 100)
                        mtype = "exact"

                # Substring
                if not mtype:
                    if normalized_query_lower in candidate_lower or candidate_lower in normalized_query_lower:
                        length_ratio = min(len(normalized_query_lower), len(candidate_lower)) / max(len(normalized_query_lower), len(candidate_lower))
                        if length_ratio >= 0.75:
                            ratio = self.calculate_similarity(normalized_query_lower, candidate_lower, min_ratio=self.match_threshold / 100.0)
                            sub_score = int(ratio * 100)
                            shorter_len = min(len(normalized_query_lower), len(candidate_lower))
                            sub_threshold = self._length_scaled_threshold(self.match_threshold, shorter_len)
                            if sub_score >= sub_threshold and self._has_token_overlap(normalized_query_lower, candidate_lower, require_majority=True):
                                score = sub_score
                                mtype = "substring"

                # Fuzzy token-sort
                if not mtype:
                    processed_candidate = self._get_cached_processed(candidate, user_ignored_tags)
                    if processed_candidate:
                        ratio = self.calculate_similarity(processed_query, processed_candidate, min_ratio=self.match_threshold / 100.0)
                        fuzzy_score = int(ratio * 100)
                        shorter_len = min(len(processed_query), len(processed_candidate))
                        fuzzy_threshold = self._length_scaled_threshold(self.match_threshold, shorter_len)
                        if fuzzy_score >= fuzzy_threshold and self._has_token_overlap(processed_query, processed_candidate, require_majority=True):
                            score = fuzzy_score
                            mtype = f"fuzzy ({fuzzy_score})"

                if mtype and score > 0:
                    # Apply channel number boost
                    boost = self._channel_number_boost(candidate, channel_number)
                    all_matches[candidate] = (min(score + boost, 100), mtype)

        # Callsign anchor: a shared high-confidence callsign rescues an
        # otherwise-unmatched stream (floored at 95) and a disagreeing one
        # hard-rejects a false positive. BOTH the floor and the reject require
        # BOTH callsigns to be high-confidence (parenthesized or end-of-name).
        # A loose mid-name word that merely has callsign shape (e.g. "WITH")
        # is not a reliable callsign and must not floor or reject.
        if query_callsign_norm and query_cs_hc:
            for candidate in candidate_names:
                cand_cs, cand_hc = self._extract_callsign_with_confidence(candidate)
                if not cand_cs or not cand_hc:
                    continue
                if self.normalize_callsign(cand_cs) == query_callsign_norm:
                    existing = all_matches.get(candidate)
                    if existing is None or existing[0] < 95:
                        all_matches[candidate] = (95, "callsign")
                    callsign_anchored.add(candidate)
                else:
                    # High-confidence callsign disagreement → hard reject.
                    all_matches.pop(candidate, None)

        # Filter out wrong-country matches. A stream whose name carries a
        # recognized country marker (e.g. "UK: Discovery Channel", "(IN) Bloomberg",
        # "(PLUTO Brazil) MTV") and that marker differs from the lineup's country
        # is dropped. Streams without a country marker are kept - we can't prove
        # they're wrong and over-filtering breaks lineups whose M3U sources don't
        # tag country at all.
        #
        # Matching is STRICT by default: only the lineup's own country passes.
        # The single exception is a channel that is genuinely the same feed
        # across a border (e.g. US Spanish networks tagged MEX) - see
        # _CROSS_BORDER_SHARED. This deliberately rejects same-name-different-
        # channel cases like "(CA) Food Network" or "(MX) ESPN" for a US lineup.
        if lineup_country:
            lc = lineup_country.upper()
            # Defensive: if caller passes an unrecognized code, skip filtering
            # rather than drop every country-marked candidate.
            if lc in _KNOWN_COUNTRY_CODES:
                nq_fold = _fold_key(normalized_query)
                kept = {}
                for name, val in all_matches.items():
                    sc = detect_stream_country(name)
                    if sc is None or sc == lc:
                        kept[name] = val
                        continue
                    shared = _CROSS_BORDER_SHARED.get(frozenset({lc, sc}))
                    if shared and nq_fold in shared:
                        kept[name] = val  # genuinely-shared cross-border feed
                        continue
                    self.country_filter_drops += 1
                all_matches = kept

        # Filter out wrong-region matches (East vs West vs Pacific).
        # Detect the lineup channel's region from the ORIGINAL un-normalized
        # name: normalize_name now strips East/West/Eastern (and Pacific via
        # REGIONAL_PATTERNS) for scoring, so the normalized form no longer
        # carries the region word. "east" as a substring also catches the
        # adjective "Eastern"; for west we require the word but exclude the
        # "western"/"westerns" genre.
        original_lower = (lineup_name or "").lower()
        # Detect (e)/(w)/(p) abbreviations in the original name
        _has_abbrev_east = bool(re.search(r'\(\s*e\s*\)', original_lower))
        _has_abbrev_west = bool(re.search(r'\(\s*w\s*\)', original_lower))
        _has_abbrev_pacific = bool(re.search(r'\(\s*p\s*\)', original_lower))
        query_has_east = "east" in original_lower or _has_abbrev_east
        query_has_west = ("west" in original_lower and "western" not in original_lower) or _has_abbrev_west
        query_has_pacific = ("pacific" in original_lower) or _has_abbrev_pacific

        if query_has_east or query_has_west or query_has_pacific:
            filtered = {}
            for stream_name, (score, mtype) in all_matches.items():
                if stream_name in callsign_anchored:
                    filtered[stream_name] = (score, mtype)
                    continue
                sn_lower = stream_name.lower()
                stream_has_east = "east" in sn_lower
                stream_has_west = "west" in sn_lower and "western" not in sn_lower
                stream_has_pacific = "pacific" in sn_lower
                stream_has_region = stream_has_east or stream_has_west or stream_has_pacific

                if query_has_east:
                    # East channel: match East streams or regionless (assume East)
                    if stream_has_west and not stream_has_east:
                        continue  # Skip West-only streams
                    if stream_has_pacific and not stream_has_east:
                        continue  # Skip Pacific-only streams
                elif query_has_west:
                    # West channel: match West or Pacific streams (Pacific is West-coast)
                    if stream_has_east and not stream_has_west and not stream_has_pacific:
                        continue  # Skip East-only streams
                    if not stream_has_region:
                        continue  # Skip regionless streams (they default to East)
                elif query_has_pacific:
                    # Pacific channel: only match Pacific streams
                    if stream_has_east and not stream_has_pacific:
                        continue  # Skip East-only streams
                    if stream_has_west and not stream_has_pacific:
                        continue  # Skip West-only streams
                    if not stream_has_region:
                        continue  # Skip regionless streams (they default to East)

                filtered[stream_name] = (score, mtype)
            all_matches = filtered

        else:
            # Regionless channel: prefer regionless EPG entries, reject Pacific/West
            filtered = {}
            for stream_name, (score, mtype) in all_matches.items():
                if stream_name in callsign_anchored:
                    filtered[stream_name] = (score, mtype)
                    continue
                sn_lower = stream_name.lower()
                stream_has_pacific = "pacific" in sn_lower
                stream_has_west = "west" in sn_lower and "western" not in sn_lower
                if stream_has_pacific or stream_has_west:
                    continue  # Skip Pacific/West for regionless channels (default East)
                filtered[stream_name] = (score, mtype)
            # Only apply filter if it doesn't eliminate all matches
            if filtered:
                all_matches = filtered

        # Convert to sorted list. Primary key is score (desc); secondary key is
        # the overlap between the candidate's ORIGINAL tokens and the lineup
        # channel's ORIGINAL tokens. The secondary key disambiguates ties caused
        # by normalize_name collapsing brand-name timezones (e.g. "Comedy TV"
        # and "Comedy Central" both normalize to "comedy") - without it, the
        # winner is whichever candidate happened to appear first in the EPG
        # source list. The original-token overlap correctly prefers
        # "USA: Comedy TV" over "(US) Comedy Central (S)" when matching
        # lineup "Comedy TV".
        lineup_tokens = set(re.findall(r'[a-z0-9]+', (lineup_name or "").lower()))

        def _orig_overlap(candidate_name):
            cand_tokens = set(re.findall(r'[a-z0-9]+', candidate_name.lower()))
            return len(lineup_tokens & cand_tokens)

        # Secondary sort key: prefer streams whose country marker matches the
        # lineup country (score 1) over streams with an unrecognized prefix like
        # AF:, TS:, MEO: that pass the hard country filter but should rank below
        # FR: streams (score 0). Streams with a recognized but wrong country
        # (already dropped by the filter) would score -1.
        _sort_lc = lineup_country.upper() if lineup_country else None
        _sort_active = bool(_sort_lc and _sort_lc in _KNOWN_COUNTRY_CODES)

        def _country_key(candidate_name):
            if _sort_active:
                sc = detect_stream_country(candidate_name)
                # Streams tagged with the lineup's own country rank first; any
                # surviving cross-border-shared or untagged stream ranks below.
                return 1 if sc == _sort_lc else 0
            return 0

        results = [(name, score, mtype) for name, (score, mtype) in all_matches.items()]
        results.sort(
            key=lambda x: (x[1], _country_key(x[0]), _orig_overlap(x[0])),
            reverse=True,
        )
        return results

