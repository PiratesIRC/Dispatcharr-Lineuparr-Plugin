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

# The pure matching primitives (normalize_name, calculate_similarity, the callsign
# ladder, ...) live in the vendored shared core. The rapidfuzz fast path and its
# pure-Python fallback are inside FuzzyMatcherCore.calculate_similarity. The decorative
# helpers are re-exported so tests/callers that reference them keep working.
try:
    from .matching_core import (
        FuzzyMatcherCore,
        _is_decorative_char,
        _normalize_emoji,
        _strip_stylized_tokens,
    )
except ImportError:  # script/test context without the package parent on sys.path
    from matching_core import (
        FuzzyMatcherCore,
        _is_decorative_char,
        _normalize_emoji,
        _strip_stylized_tokens,
    )

__version__ = "1.3.4"

LOGGER = logging.getLogger("plugins.lineuparr.fuzzy_matcher")
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    LOGGER.addHandler(_handler)
LOGGER.setLevel(logging.DEBUG)


# Matches "+1" / "+2" time-shift suffixes in the ORIGINAL (pre-normalization)
# channel name. Must be checked before normalization because "+" is in the Sm
# category and gets stripped, making "+1" indistinguishable from "1" afterward.
_PLUS_SHIFT_RE = re.compile(r'\+\s{0,2}\d{1,2}\b')


# Quality markers that distinguish an upgrade-tier channel from its standard twin.
# HD/FHD/HEVC are intentionally excluded - they don't create a separate twin channel.
_UPGRADE_QUALITY_RE = re.compile(r'\b(?:4K|8K|UHD|HDR)\b|ᵁᴴᴰ', re.IGNORECASE)


def has_upgrade_quality(name: str) -> bool:
    """Return True if name contains an upgrade quality marker (4K/8K/UHD/HDR)."""
    return bool(_UPGRADE_QUALITY_RE.search(name))


# Country tokens for the delimited provider-prefix patterns below; curated so a
# bare delimited word ("(SPORTS)") isn't misread as a country. Keep in sync with
# detect_stream_country().
_PREFIX_COUNTRY = r'US|USA|UK|CA|AU|FR|DE|ES|IT|NL|BR|MX|IN'

# (open, close) pairs; open and close must MATCH, so "(US]" / "│US)" are rejected.
_DELIM_PAIRS = ((r'\(', r'\)'), (r'\[', r'\]'), (r'\|', r'\|'), ('┃', '┃'), ('│', '│'))


def _balanced_delim(token):
    """Regex fragment matching `token` wrapped in one MATCHED delimiter pair:
    (token) [token] |token| ┃token┃ │token│. `token` may contain a capture group."""
    return '(?:' + '|'.join(
        o + r'\s*(?:' + token + r')\s*' + c for o, c in _DELIM_PAIRS
    ) + ')'


# Leading country tag in a matched delimiter pair; one capture group fires.
_BRACKETED_CC_RE = re.compile(r'^\s*' + _balanced_delim(r'([A-Za-z]{2,3})'))


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

    m = _BRACKETED_CC_RE.match(name)
    if m:
        tok = next((g for g in m.groups() if g), None)
        return _normalize_country_token(tok) if tok else None

    m = re.match(r'^\s*([A-Za-z]{2,3})\s*[-:|┃│]', name)
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
    m = re.match(r'^\s*([A-Za-z]{2,3})\s*[-:|┃│]', category_name)
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


class FuzzyMatcher(FuzzyMatcherCore):
    """Handles fuzzy matching for Lineuparr with alias support and channel number boosting."""

    # Lineuparr strips bare time-zone region words (Pacific/Central/Mountain/Atlantic) for
    # SCORING and enforces region correctness via its post-match region filter
    # (match_all_streams reads the ORIGINAL names). See REGIONAL_BARE_PATTERNS in the core.
    _STRIP_BARE_REGION = True

    def __init__(self, match_threshold=80, logger=None):
        # The core seeds match_threshold, logger, the four normalization/callsign
        # caches, and the known-callsign rescue slot (unused here - Lineuparr has no
        # channel DB). Only the country-filter counter is Lineuparr-specific.
        super().__init__(match_threshold=match_threshold, logger=logger or LOGGER)
        # Cumulative count of candidates dropped by the country filter across
        # match_all_streams calls. Callers reset this before a matching loop
        # and read it after, so they can log a summary.
        self.country_filter_drops = 0

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


    @staticmethod
    def _is_group_header(name):
        """Return True for M3U playlist group/section separators like '##### EUROSPORT #####'
        or '## 24/7 COMEDY ##'. These are not real stream names and must be
        excluded from matching. A run of 2+ #/=/* is decorative (no real channel
        name contains "##"/"=="/"**"); pipes need a run of 3+ since a single "|"
        is a common in-name separator ("001 | Team A vs Team B")."""
        return bool(re.search(r'[#=*]{2,}|\|{3,}', name or ""))


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

