"""Regression: parenthesized callsign confidence (bug-062) + bug-014 guard.

bug-062 (recall gap): grandfathered 3-letter US callsigns in parentheses
without a suffix - (WWL)/(WJZ)/(KYW)/(WRC) - fell through Priority 1's 4-char
regex to the low-confidence loose-word path, so the callsign anchor never fired
and e.g. "CBS - LA New Orleans (WWL)" never matched "WWL-DT". A new Priority 1b
clause extracts a bare 3-letter parenthesized callsign at HIGH confidence.

bug-014 guard: never blanket-promote a denylisted English-word callsign. This
repo has NO allowlist, so denylisted word-callsigns like KING/WAVE in parens
must stay blocked (Priority 1b honors the denylist, like Priority 1).
"""
from Lineuparr.fuzzy_matcher import FuzzyMatcher


class TestParenThreeLetterCallsignConfidence:
    """bug-062: bare 3-letter parenthesized callsigns are high confidence."""

    def setup_method(self):
        self.m = FuzzyMatcher(match_threshold=85)

    def test_paren_three_letter_callsign_is_high_confidence(self):
        # bug-062: (WWL)/(WJZ)/(KYW)/(WRC) are real grandfathered 3-letter US
        # callsigns. Before the fix they extracted at low confidence via the
        # loose-word path, so the callsign anchor never fired.
        cases = [
            ("CBS - LA New Orleans (WWL)", "WWL"),
            ("CBS - MD Baltimore (WJZ)", "WJZ"),
            ("CBS - PA Philadelphia (KYW)", "KYW"),
            ("NBC - DC Washington (WRC)", "WRC"),
        ]
        for name, expected in cases:
            cs, hc = self.m._extract_callsign_with_confidence(name)
            assert (cs, hc) == (expected, True), f"{name!r} -> {(cs, hc)!r}"

    def test_paren_four_letter_callsign_still_high_confidence(self):
        # Priority 1 unchanged.
        cs, hc = self.m._extract_callsign_with_confidence("ABC (WABC) New York")
        assert (cs, hc) == ("WABC", True)

    def test_paren_suffixed_three_letter_falls_to_priority2(self):
        # Suffixed forms must NOT be caught by Priority 1b (it requires an
        # immediate close-paren after exactly two letters); they fall to
        # Priority 2's suffix rule.
        cs, hc = self.m._extract_callsign_with_confidence("CNN (KAB-TV)")
        assert (cs, hc) == ("KAB-TV", True)

    def test_common_word_paren_callsign_stays_blocked(self):
        # bug-014 guard: this repo has NO allowlist, so denylisted English-word
        # callsigns (KING/WAVE) in parentheses must stay blocked. They must not
        # be promoted to high confidence by Priority 1b.
        for name in ("NBC - WA Seattle (KING)", "NBC - KY Louisville (WAVE)"):
            cs, hc = self.m._extract_callsign_with_confidence(name)
            assert hc is False, f"{name!r} wrongly promoted -> {(cs, hc)!r}"
