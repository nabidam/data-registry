import random
from unittest import TestCase

from services.evaluation.contamination_safe import (
    _candidate_documents,
    document_key,
    holdout_budget,
)
from services.evaluation.reservation_config import load_contamination_safe_config


class _Row:
    """The attributes `_candidate_documents` reads, and nothing else."""

    def __init__(self, document_id: str, domain: str = "general") -> None:
        self.document_id = document_id
        self.domain = domain
        self.pair_key = "en-fa"
        self.length_bucket = "medium"
        self.flags = {"has_math": False, "has_numbers_units": True}


def _config(**selection) -> dict:
    base = {
        "selection": {
            "max_test_documents": 200,
            "max_holdout_share": 0.01,
            "max_holdout_rows": None,
        },
        "contamination": {"document_namespace": "source"},
        "input": {"id_separator": ":"},
    }
    base["selection"].update(selection)
    return base


class HoldoutBudgetTests(TestCase):
    def test_share_of_corpus(self):
        self.assertEqual(holdout_budget(_config(), 6_274_184), 62_741)

    def test_absolute_cap_wins_when_lower(self):
        config = _config(max_holdout_rows=1_000)
        self.assertEqual(holdout_budget(config, 6_274_184), 1_000)

    def test_unbounded_without_corpus_size(self):
        # A share of an unknown corpus is not a number.
        self.assertIsNone(holdout_budget(_config(), None))

    def test_unbounded_when_policy_sets_neither(self):
        config = _config(max_holdout_share=None, max_holdout_rows=None)
        self.assertIsNone(holdout_budget(config, 6_274_184))


class CandidateDocumentTests(TestCase):
    def _rows(self, sizes: dict[str, int], per_document: int = 5):
        rows, indexes = [], []
        for document_id in sizes:
            for _ in range(per_document):
                indexes.append(len(rows))
                rows.append(_Row(document_id))
        return rows, indexes

    def test_budget_prefers_small_documents(self):
        """The failure this exists for: 30 documents at 8,051 rows each."""
        sizes = {"src1:huge": 8_051, "src1:small_a": 40, "src1:small_b": 40}
        rows, indexes = self._rows(sizes)
        kept, report = _candidate_documents(
            rows,
            indexes,
            _config(max_holdout_share=0.01),
            random.Random(42),
            document_sizes=sizes,
            corpus_rows=100_000,  # budget = 1,000 rows
        )
        held = {rows[index].document_id for index in kept}
        self.assertEqual(held, {"src1:small_a", "src1:small_b"})
        self.assertEqual(report["estimated_rows"], 80)
        self.assertLessEqual(report["estimated_rows"], report["budget_rows"])

    def test_without_sizes_cost_falls_back_to_pool_counts(self):
        sizes = {"src1:huge": 8_051, "src1:small": 40}
        rows, indexes = self._rows(sizes)
        _, report = _candidate_documents(
            rows, indexes, _config(), random.Random(42), None, 100_000
        )
        # Both documents look identical from inside the pool, which is exactly
        # why the caller is expected to supply real sizes.
        self.assertEqual(report["document_sizes"], "candidate_pool")

    def test_reports_when_everything_fits(self):
        sizes = {"src1:a": 10, "src1:b": 10}
        rows, indexes = self._rows(sizes)
        kept, report = _candidate_documents(
            rows, indexes, _config(), random.Random(42), sizes, 100_000
        )
        self.assertEqual(len(kept), len(indexes))
        self.assertFalse(report["applied"])
        self.assertEqual(report["estimated_rows"], 20)

    def test_deterministic_across_runs(self):
        sizes = {f"src1:doc{i}": 100 + i for i in range(40)}
        rows, indexes = self._rows(sizes)
        first, _ = _candidate_documents(
            rows, indexes, _config(max_holdout_rows=500), random.Random(42), sizes, 100_000
        )
        second, _ = _candidate_documents(
            rows, indexes, _config(max_holdout_rows=500), random.Random(42), sizes, 100_000
        )
        self.assertEqual(first, second)

    def test_synthetic_document_ids_are_left_alone(self):
        rows = [_Row(f"sample:{i}") for i in range(20)]
        kept, report = _candidate_documents(
            rows, list(range(20)), _config(), random.Random(42), None, 100_000
        )
        self.assertEqual(len(kept), 20)
        self.assertEqual(report["reason"], "no document metadata")


class DocumentKeyTests(TestCase):
    """Sizing must key documents exactly as selection does."""

    def test_namespaced_by_source_and_split_on_separator(self):
        config = _config()
        record = {"document_id": "book7:chunk12", "source_id": 3, "sample_id": 1}
        self.assertEqual(document_key(record, config), "src3:book7")

    def test_missing_document_id_is_unique_per_row(self):
        config = _config()
        self.assertEqual(
            document_key({"document_id": None, "sample_id": 42}, config), "sample:42"
        )

    def test_shipped_policy_is_loadable_and_bounded(self):
        config = load_contamination_safe_config()
        self.assertIsNotNone(holdout_budget(config, 1_000_000))
