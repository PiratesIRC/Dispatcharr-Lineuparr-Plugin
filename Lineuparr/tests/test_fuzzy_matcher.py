"""Unit tests for fuzzy_matcher normalization, country detection, and the
Unicode-robustness fixes (box-drawing bar delimiters, matched-delimiter pairs,
and NFKD/isalnum non-ASCII handling).

Run from the repo root with:  python -m unittest discover -s Lineuparr/tests
or directly:                  python Lineuparr/tests/test_fuzzy_matcher.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fuzzy_matcher import (  # noqa: E402
    FuzzyMatcher,
    detect_stream_country,
    detect_category_country,
)


class DetectStreamCountryBars(unittest.TestCase):
    """Box-drawing vertical bars (┃ U+2503, │ U+2502) act as delimiters."""

    def test_heavy_bar_after_code(self):
        self.assertEqual(detect_stream_country("US┃ ESPN"), "US")

    def test_light_bar_after_code(self):
        self.assertEqual(detect_stream_country("UK│ Sky Sports"), "UK")

    def test_heavy_bar_pair(self):
        self.assertEqual(detect_stream_country("┃US┃ ESPN"), "US")

    def test_light_bar_pair(self):
        self.assertEqual(detect_stream_country("│FR│ Canal"), "FR")

    def test_iso3_alias_in_bars(self):
        self.assertEqual(detect_stream_country("│MEX│ Bein Sports"), "MX")


class DetectStreamCountryMatchedDelimiters(unittest.TestCase):
    """Bracket/pipe delimiters must be a MATCHED pair; mismatched -> None."""

    def test_parens(self):
        self.assertEqual(detect_stream_country("(US) ESPN"), "US")

    def test_square_brackets(self):
        self.assertEqual(detect_stream_country("[US] ESPN"), "US")

    def test_pipes(self):
        self.assertEqual(detect_stream_country("|US| ESPN"), "US")

    def test_mismatched_paren_bracket_is_none(self):
        self.assertIsNone(detect_stream_country("(US] ESPN"))

    def test_mismatched_pipe_paren_is_none(self):
        self.assertIsNone(detect_stream_country("|US) ESPN"))

    def test_mismatched_bracket_bar_is_none(self):
        self.assertIsNone(detect_stream_country("[US┃ ESPN"))


class DetectStreamCountryRegression(unittest.TestCase):
    """Pre-existing recognition must keep working after the regex refactor."""

    def test_colon_prefix(self):
        self.assertEqual(detect_stream_country("US: ESPN"), "US")

    def test_dash_prefix(self):
        self.assertEqual(detect_stream_country("FR - Canal+"), "FR")

    def test_bare_space_country(self):
        self.assertEqual(detect_stream_country("CA TSN 1 HD"), "CA")

    def test_usa_network_not_country(self):
        self.assertIsNone(detect_stream_country("USA Network"))

    def test_usa_bet_is_us(self):
        self.assertEqual(detect_stream_country("USA BET"), "US")

    def test_lookalike_brand_not_country(self):
        self.assertIsNone(detect_stream_country("FOX News"))

    def test_glued_quality_tag(self):
        self.assertEqual(detect_stream_country("UKHD ESPN"), "UK")

    def test_unlabeled_returns_none(self):
        self.assertIsNone(detect_stream_country("Discovery Channel"))


class DetectCategoryCountryBars(unittest.TestCase):
    def test_heavy_bar(self):
        self.assertEqual(detect_category_country("UK┃ Sports"), "UK")

    def test_light_bar(self):
        self.assertEqual(detect_category_country("FR│ Cinema"), "FR")

    def test_existing_pipe(self):
        self.assertEqual(detect_category_country("AU| AUSTRALIA VIP"), "AU")

    def test_theme_category_is_none(self):
        self.assertIsNone(detect_category_country("Sports"))


class NormalizeNameStripsBars(unittest.TestCase):
    def setUp(self):
        self.fm = FuzzyMatcher()

    def _tokens(self, name):
        return self.fm.normalize_name(name).lower().split()

    def test_strip_heavy_bar_prefix(self):
        toks = self._tokens("US┃ ESPN")
        self.assertIn("espn", toks)
        self.assertNotIn("us", toks)

    def test_strip_matched_light_bar_pair(self):
        toks = self._tokens("│US│ ESPN")
        self.assertIn("espn", toks)
        self.assertNotIn("us", toks)

    def test_strip_matched_heavy_bar_pair(self):
        toks = self._tokens("┃FR┃ Canal")
        self.assertIn("canal", toks)
        self.assertNotIn("fr", toks)


class NormalizeNameLeadingBarTag(unittest.TestCase):
    """A leading box-bar bouquet/source tag is stripped even when its inner
    text is not a country code (e.g. Dispatcharr stores '┃CANAL+┃ NPO 1 HD' as
    the EPG name for npo1.nl). Regression for the EPG no-match investigation."""

    def setUp(self):
        self.fm = FuzzyMatcher()

    def norm(self, s):
        return self.fm.normalize_name(s)

    def test_canalplus_tag_stripped(self):
        # The "+" must not leave a "CANAL" token behind.
        self.assertEqual(self.norm("┃CANAL+┃ NPO 1 HD").lower().split(), ["npo", "1"])

    def test_nlziet_tag_stripped(self):
        self.assertEqual(self.norm("┃NLZIET┃ NPO 2 HD").lower().split(), ["npo", "2"])

    def test_multitoken_tag_stripped(self):
        toks = self.norm("┃CA EN┃ BBC WORLD NEWS").lower().split()
        self.assertNotIn("ca", toks)
        self.assertIn("bbc", toks)

    def test_country_tag_still_works(self):
        # The existing 2-letter country case keeps stripping cleanly.
        self.assertEqual(self.norm("┃NL┃ SBS 9 HD").lower().split(), ["sbs", "9"])

    def test_light_bar_tag_stripped(self):
        self.assertEqual(self.norm("│PLUTO│ Comedy").lower().split(), ["comedy"])

    def test_canalplus_matches_lineup_name(self):
        # End-to-end: the stored EPG name now matches the lineup channel.
        res = self.fm.match_all_streams(
            "NPO 1", ["┃CANAL+┃ NPO 1 HD"], alias_map={}, lineup_country="NL"
        )
        self.assertTrue(res, "stored '┃CANAL+┃ NPO 1 HD' should match lineup 'NPO 1'")
        self.assertEqual(res[0][1], 100)

    def test_no_bar_name_untouched(self):
        self.assertEqual(self.norm("Discovery Channel"), self.norm("Discovery Channel"))
        self.assertIn("discovery", self.norm("Discovery Channel").lower())


class ProcessStringNFKD(unittest.TestCase):
    """NFKD compatibility folding + accent folding."""

    def setUp(self):
        self.fm = FuzzyMatcher()

    def proc(self, s):
        return self.fm.process_string_for_matching(s)

    def test_fullwidth_folds_to_ascii(self):
        self.assertEqual(self.proc("ＨＢＯ ２"), self.proc("HBO 2"))

    def test_superscript_digit_folds(self):
        self.assertEqual(self.proc("H²O"), self.proc("H2O"))

    def test_ligature_folds(self):
        self.assertEqual(self.proc("ﬁlm"), self.proc("film"))

    def test_ordinal_folds(self):
        self.assertEqual(self.proc("Canalº"), self.proc("Canalo"))

    def test_accent_folds(self):
        self.assertEqual(self.proc("Canalé"), self.proc("canale"))


class ProcessStringNonLatinPreserved(unittest.TestCase):
    """Non-Latin scripts must survive instead of being erased to ''."""

    def setUp(self):
        self.fm = FuzzyMatcher()

    def proc(self, s):
        return self.fm.process_string_for_matching(s)

    def test_cyrillic_not_erased(self):
        self.assertEqual(self.proc("Россия"), "россия")

    def test_cjk_not_erased(self):
        self.assertEqual(self.proc("東京"), "東京")

    def test_cyrillic_letter_digit_split(self):
        toks = self.proc("канал2").split()
        self.assertIn("канал", toks)
        self.assertIn("2", toks)

    def test_mixed_script_tokens_sorted(self):
        # Latin + Cyrillic both retained as separate tokens.
        self.assertEqual(self.proc("ESPN Спорт"), "espn спорт")


class ProcessStringRegression(unittest.TestCase):
    def setUp(self):
        self.fm = FuzzyMatcher()

    def proc(self, s):
        return self.fm.process_string_for_matching(s)

    def test_token_sort(self):
        self.assertEqual(self.proc("Sports Center"), "center sports")

    def test_punctuation_to_space(self):
        self.assertEqual(self.proc("A.B-C"), "a b c")

    def test_ascii_letter_digit_split(self):
        self.assertEqual(self.proc("HBO2"), "2 hbo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
