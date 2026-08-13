from unittest import TestCase

import polars as pl

from services.evaluation.language_profiles import resolve_pair
from services.ingestion.normalize import ColumnMapping, normalize

# Text whose variants are byte-different but denote the same string. Each entry
# is a spelling a real corpus actually contains.
PERSIAN_VARIANTS = [
    "ترجمه شده به فارسی",  # baseline
    "ترجمه شده به فارسي",  # Arabic yeh
    "ترجمهٔ شده به فارسی",  # with a diacritic
    "ترجمه شده به فارسی ",  # trailing space
    "۱۲۳ ترجمه",  # Persian digits
    "١٢٣ ترجمه",  # Arabic-Indic digits
]


class KeyExpressionTests(TestCase):
    """The scalar and vectorized normalizers are one rule, so they must agree."""

    def test_key_expr_matches_key_for_every_profile(self):
        samples = [
            *PERSIAN_VARIANTS,
            "Hello,   World!",
            "MRI scan (T2-weighted) 3.5 mm",
            "Ёлка и ёж",
            "",
            "   ",
        ]
        for language in ("en", "fa", "ar", "ru", "de", None):
            profile = resolve_pair(language, language).source
            frame = pl.DataFrame({"text": samples})
            vectorized = frame.select(profile.key_expr(pl.col("text")))["text"].to_list()
            scalar = [profile.key(text) for text in samples]
            self.assertEqual(scalar, vectorized, f"mismatch for language {language!r}")


class NormalizeDedupTests(TestCase):
    def _normalize(self, sources: list[str], targets: list[str]) -> pl.DataFrame:
        return normalize(
            pl.DataFrame({"source": sources, "target": targets}),
            ColumnMapping(),
            batch_id=1,
            source_id=None,
            src_lang="en",
            tgt_lang="fa",
            domain=None,
        )

    def test_collapses_variants_raw_dedup_missed(self):
        out = self._normalize(
            ["A sentence."] * len(PERSIAN_VARIANTS[:4]),
            PERSIAN_VARIANTS[:4],
        )
        self.assertEqual(out.height, 1)
        # The first spelling in the input survives, as before.
        self.assertEqual(out["target_text"][0], PERSIAN_VARIANTS[0])

    def test_keeps_genuinely_different_pairs(self):
        out = self._normalize(
            ["First source.", "Second source.", "First source."],
            ["ترجمه یک", "ترجمه دو", "ترجمه سه"],
        )
        self.assertEqual(out.height, 3)

    def test_preserves_input_order(self):
        sources = [f"Sentence number {i}." for i in range(20)]
        targets = [f"جمله شماره {i}" for i in range(20)]
        out = self._normalize(sources, targets)
        self.assertEqual(out["source_text"].to_list(), sources)

    def test_handles_mixed_language_pairs_in_one_import(self):
        frame = pl.DataFrame(
            {
                "source": ["Same source.", "Same source.", "Тот же источник."],
                "target": ["ترجمه يك", "ترجمه یک", "ترجمه یک"],
                "src": ["en", "en", "ru"],
                "tgt": ["fa", "fa", "fa"],
            }
        )
        out = normalize(
            frame,
            ColumnMapping(src_lang="src", tgt_lang="tgt"),
            batch_id=1,
            source_id=None,
            src_lang="en",
            tgt_lang="fa",
            domain=None,
        )
        # The two en-fa rows collapse; the ru-fa row is a different pair entirely.
        self.assertEqual(out.height, 2)
        self.assertEqual(sorted(out["src_lang"].to_list()), ["en", "ru"])
