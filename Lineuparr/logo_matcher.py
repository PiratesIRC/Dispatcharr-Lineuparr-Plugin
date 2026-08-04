"""
Logo matching utilities for matching channel names to tv-logo/tv-logos filenames.

Uses simple fuzzy matching (not the full FuzzyMatcher pipeline) since we're
comparing clean channel names against structured filenames.
"""

import re
import urllib.request
import urllib.parse
import json
import logging
import unicodedata

try:
    from rapidfuzz import fuzz
except ImportError:
    try:
        from thefuzz import fuzz
    except ImportError:
        # Pure-Python fallback: Levenshtein ratio via difflib
        import difflib

        class _FuzzFallback:
            @staticmethod
            def ratio(a, b):
                return difflib.SequenceMatcher(None, a, b).ratio() * 100

        fuzz = _FuzzFallback()

LOGGER = logging.getLogger("plugins.lineuparr.logo_matcher")

# Threshold for fuzzy matching channel names to logo filenames
LOGO_MATCH_THRESHOLD = 85


def normalize_logo_filename(filename, country_suffix):
    """Normalize a tv-logos filename for comparison.

    'cnn-us.png' with country_suffix='us' -> 'cnn'
    'fox-news-us.png' with country_suffix='us' -> 'fox news'
    """
    # Strip extension
    name = re.sub(r'\.(png|svg|jpg|jpeg|gif|webp)$', '', filename, flags=re.IGNORECASE)
    # Strip country suffix (e.g., '-us' at end)
    suffix_pattern = rf'-{re.escape(country_suffix)}$'
    name = re.sub(suffix_pattern, '', name, flags=re.IGNORECASE)
    # Replace hyphens with spaces
    name = name.replace('-', ' ')
    return name.lower().strip()


def normalize_channel_name(name):
    """Normalize a channel name for comparison against logo filenames.

    'A&E' -> 'a and e'
    'Fox News' -> 'fox news'
    'CNN HD' -> 'cnn'
    'Discovery Channel' -> 'discovery'
    """
    name = name.lower().strip()
    # Replace & with 'and'
    name = name.replace('&', ' and ')
    # Remove special characters except spaces
    name = re.sub(r'[^\w\s]', '', name)
    # Strip common suffixes that tv-logos filenames don't include
    name = re.sub(r'\s+(hd|sd|uhd|4k|fhd|network|channel|tv)\s*$', '', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def match_channel_to_logo(channel_name, logo_filenames, country_suffix, threshold=LOGO_MATCH_THRESHOLD):
    """Match a channel name against a list of tv-logos filenames.

    Returns the matching filename or None if no match meets the threshold.
    """
    normalized_channel = normalize_channel_name(channel_name)
    if not normalized_channel:
        return None

    best_score = 0
    best_file = None

    for filename in logo_filenames:
        normalized_logo = normalize_logo_filename(filename, country_suffix)
        if not normalized_logo:
            continue

        score = fuzz.ratio(normalized_channel, normalized_logo)
        if score > best_score:
            best_score = score
            best_file = filename
            if best_score == 100:
                break

    if best_score >= threshold:
        return best_file
    return None


def fetch_tv_logos_filelist(repo, branch, country_dir):
    """Fetch the list of logo filenames from the GitHub API.

    Args:
        repo: GitHub repo path, e.g. 'tv-logo/tv-logos'
        branch: Branch name, e.g. 'main'
        country_dir: Directory name, e.g. 'united-states'

    Returns:
        List of filenames (e.g. ['cnn-us.png', 'espn-us.png', ...])
        or empty list on failure.
    """
    url = f"https://api.github.com/repos/{repo}/contents/countries/{country_dir}?ref={branch}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if not isinstance(data, list):
            LOGGER.warning(f"Unexpected API response for {country_dir}: {type(data)}")
            return []
        if len(data) >= 1000:
            LOGGER.warning(f"GitHub API returned {len(data)} entries for {country_dir} - results may be truncated")
        # Filter to image files only
        image_exts = ('.png', '.svg', '.jpg', '.jpeg', '.gif', '.webp')
        return [item['name'] for item in data if item.get('name', '').lower().endswith(image_exts)]
    except Exception as e:
        LOGGER.warning(f"Failed to fetch tv-logos file list for {country_dir}: {e}")
        return []


def build_logo_url(repo, branch, country_dir, filename):
    """Build a raw GitHub URL for a logo file."""
    return f"https://raw.githubusercontent.com/{repo}/{branch}/countries/{country_dir}/{filename}"


# The helpers below are used only by the standalone custom-logo action. The
# original tv-logos matching functions above intentionally remain unchanged.
def _normalize_repository_logo_name(name, country_suffix=None):
    """Normalize a custom repository filename or Dispatcharr channel name."""
    name = unicodedata.normalize('NFC', str(name)).lower().strip()
    name = re.sub(r'\.(png|svg|jpg|jpeg|gif|webp)$', '', name, flags=re.IGNORECASE)
    if country_suffix:
        name = re.sub(
            rf'[-._]{re.escape(country_suffix)}$', '', name, flags=re.IGNORECASE
        )
    name = name.replace('-', ' ').replace('_', ' ')
    name = name.replace('&', ' and ').replace('+', ' plus ')
    name = re.sub(r'[^\w\s]', '', name)
    # East is treated as the standard feed; Pacific remains a distinct name.
    name = re.sub(r'\s+east\s*$', '', name)
    name = re.sub(r'\s+(hd|sd|uhd|4k|fhd)\s*$', '', name)
    return re.sub(r'\s+', ' ', name).strip()


def match_channel_to_repository_logo(
    channel_name, logo_filenames, country_suffix, threshold=90
):
    """Match a channel only against the standalone repository logo library."""
    normalized_channel = _normalize_repository_logo_name(channel_name)
    if not normalized_channel:
        return None

    country_pattern = re.compile(
        rf'[-._]{re.escape(country_suffix)}\.(png|svg|jpg|jpeg|gif|webp)$',
        flags=re.IGNORECASE,
    )
    country_files = [name for name in logo_filenames if country_pattern.search(name)]
    candidates = country_files or logo_filenames
    best_score = 0
    best_file = None
    for filename in candidates:
        normalized_logo = _normalize_repository_logo_name(filename, country_suffix)
        if not normalized_logo:
            continue
        score = fuzz.ratio(normalized_channel, normalized_logo)
        if score > best_score:
            best_score = score
            best_file = filename
            if best_score == 100:
                break
    return best_file if best_score >= threshold else None


def fetch_logo_filelist(repo, branch, directory):
    """Fetch image filenames from an arbitrary public GitHub directory."""
    repo = (repo or "").strip().strip("/")
    branch = (branch or "").strip().strip("/")
    directory = (directory or "").strip().strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        LOGGER.warning("Invalid GitHub repository name: %r", repo)
        return []
    if not branch or not directory or any(
        part in ("", ".", "..") for part in directory.split("/")
    ):
        LOGGER.warning("Invalid GitHub branch or logo directory")
        return []

    encoded_dir = urllib.parse.quote(directory, safe="/")
    query = urllib.parse.urlencode({"ref": branch})
    url = f"https://api.github.com/repos/{repo}/contents/{encoded_dir}?{query}"
    try:
        req = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github.v3+json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if not isinstance(data, list):
            LOGGER.warning("Unexpected GitHub API response for %s", directory)
            return []
        if len(data) >= 1000:
            LOGGER.warning(
                "GitHub API returned %s entries for %s; results may be truncated",
                len(data), directory,
            )
        image_exts = ('.png', '.svg', '.jpg', '.jpeg', '.gif', '.webp')
        return [
            item['name'] for item in data
            if item.get('type') == 'file'
            and item.get('name', '').lower().endswith(image_exts)
        ]
    except Exception as e:
        LOGGER.warning("Failed to fetch custom logo list for %s: %s", directory, e)
        return []


def build_repository_logo_url(repo, branch, directory, filename):
    """Build a raw GitHub URL for an image in a repository directory."""
    path = "/".join(
        urllib.parse.quote(part, safe="")
        for part in (
            branch.strip("/") + "/" + directory.strip("/") + "/" + filename
        ).split("/")
    )
    return f"https://raw.githubusercontent.com/{repo.strip().strip('/')}/{path}"
