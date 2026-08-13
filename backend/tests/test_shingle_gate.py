from collections import defaultdict
from unittest import TestCase

from services.evaluation.contamination_safe import _shingle_gate


def _index(rows: list[set[str]]) -> tuple[set[str], dict[str, set[int]]]:
    """Build the union set and the per-reserved-row index the scan uses."""
    union: set[str] = set()
    by_row: dict[str, set[int]] = defaultdict(set)
    for ordinal, shingles in enumerate(rows):
        union |= shingles
        for shingle in shingles:
            by_row[shingle].add(ordinal)
    return union, by_row


class ShingleGateTests(TestCase):
    def test_scattered_matches_no_longer_reach_labse(self):
        """One shingle from row A and one from row B is evidence about neither."""
        union, by_row = _index([{"a b c"}, {"x y z"}])
        gated, confirmed = _shingle_gate({"a b c", "x y z"}, union, by_row, 2, True)
        self.assertTrue(gated, "the cheap union gate still admits it")
        self.assertFalse(confirmed, "the precise gate rejects it")

    def test_genuine_near_duplicate_still_caught(self):
        union, by_row = _index([{"a b c", "b c d", "c d e"}, {"x y z"}])
        gated, confirmed = _shingle_gate({"a b c", "b c d"}, union, by_row, 2, True)
        self.assertTrue(gated)
        self.assertTrue(confirmed, "two shingles from one reserved row is real evidence")

    def test_below_minimum_is_rejected_by_the_cheap_gate(self):
        union, by_row = _index([{"a b c", "b c d"}])
        gated, confirmed = _shingle_gate({"a b c"}, union, by_row, 2, True)
        self.assertFalse(gated)
        self.assertFalse(confirmed)

    def test_no_overlap_at_all(self):
        union, by_row = _index([{"a b c"}])
        self.assertEqual(_shingle_gate({"q r s"}, union, by_row, 2, True), (False, False))

    def test_union_behaviour_restorable(self):
        """Operators can put the old, looser gate back."""
        union, by_row = _index([{"a b c"}, {"x y z"}])
        self.assertEqual(
            _shingle_gate({"a b c", "x y z"}, union, by_row, 2, False), (True, True)
        )

    def test_minimum_of_one_is_unaffected_by_the_second_gate(self):
        # A single shared shingle always belongs to whichever row supplied it, so
        # the two gates agree and recall is identical.
        union, by_row = _index([{"a b c"}, {"x y z"}])
        self.assertEqual(_shingle_gate({"x y z"}, union, by_row, 1, True), (True, True))

    def test_higher_minimum_needs_all_from_one_row(self):
        union, by_row = _index([{"a b c", "b c d"}, {"c d e"}])
        self.assertEqual(
            _shingle_gate({"a b c", "b c d", "c d e"}, union, by_row, 3, True), (True, False)
        )
        union, by_row = _index([{"a b c", "b c d", "c d e"}])
        self.assertEqual(
            _shingle_gate({"a b c", "b c d", "c d e"}, union, by_row, 3, True), (True, True)
        )
