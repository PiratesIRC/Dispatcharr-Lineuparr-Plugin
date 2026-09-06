"""Tests for logo_matcher module: GitHub filename normalization and matching."""
import pytest
from Lineuparr.logo_matcher import normalize_logo_filename, match_channel_to_logo


class TestNormalizeLogoFilename:
    def test_basic_us_logo(self):
        assert normalize_logo_filename("cnn-us.png", "us") == "cnn"

    def test_multi_word(self):
        assert normalize_logo_filename("fox-news-us.png", "us") == "fox news"

    def test_with_hd_suffix(self):
        assert normalize_logo_filename("espn-hd-us.png", "us") == "espn hd"

    def test_no_country_suffix(self):
        """Filenames without country suffix are handled gracefully."""
        assert normalize_logo_filename("cnn.png", "us") == "cnn"

    def test_svg_extension(self):
        assert normalize_logo_filename("hbo-us.svg", "us") == "hbo"

    def test_complex_name(self):
        assert normalize_logo_filename("abc-news-live-us.png", "us") == "abc news live"

    def test_and_in_name(self):
        """'a-and-e' normalizes keeping 'and' as a word."""
        assert normalize_logo_filename("a-and-e-us.png", "us") == "a and e"


class TestMatchChannelToLogo:
    """Test matching channel names against a list of normalized logo filenames."""

    @pytest.fixture
    def logo_files(self):
        return [
            "cnn-us.png",
            "fox-news-us.png",
            "espn-us.png",
            "abc-news-live-us.png",
            "a-and-e-us.png",
            "hbo-us.png",
            "comedy-central-us.png",
        ]

    def test_exact_match(self, logo_files):
        result = match_channel_to_logo("CNN", logo_files, "us")
        assert result == "cnn-us.png"

    def test_fuzzy_match(self, logo_files):
        result = match_channel_to_logo("Fox News", logo_files, "us")
        assert result == "fox-news-us.png"

    def test_ampersand_match(self, logo_files):
        """A&E should match a-and-e."""
        result = match_channel_to_logo("A&E", logo_files, "us")
        assert result == "a-and-e-us.png"

    def test_no_match(self, logo_files):
        result = match_channel_to_logo("Discovery Channel", logo_files, "us")
        assert result is None

    def test_threshold_prevents_bad_match(self, logo_files):
        """Low-similarity names should not match."""
        result = match_channel_to_logo("The Weather Channel", logo_files, "us")
        assert result is None

    def test_case_insensitive(self, logo_files):
        result = match_channel_to_logo("espn", logo_files, "us")
        assert result == "espn-us.png"

    def test_comedy_central(self, logo_files):
        result = match_channel_to_logo("Comedy Central", logo_files, "us")
        assert result == "comedy-central-us.png"
