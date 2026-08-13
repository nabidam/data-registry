"""Language and language-pair profiles for contamination-safe selection.

The contamination-safe pipeline was ported from an English-to-Persian test-set
builder, so its feature detectors hardcoded Latin acronyms, Latin/ASCII unit
abbreviations, and a "Latin in target or Persian in source" definition of mixed
script.  Applied to another pair those detectors do not fail loudly; they return
zero and the selector records the zero as a measurement.  A ``ru-fa`` import then
reports that it satisfied an acronym quota it never could satisfy.

A profile answers four questions about one language:

* how is its text normalized before hashing, tokenizing, and shingling;
* which Unicode script does it expect;
* how are acronyms and numeric units written in it;
* which feature detectors are meaningful for it at all.

That last question is the important one.  ``supported_features`` lets the
selector omit a quota it cannot honestly measure instead of trying to fill it
with a detector that does not apply.  Unknown languages resolve to a
conservative fallback supporting only the script-independent features, which is
safer than applying English detectors to text they were never written for.

Normalization is deliberately aggressive about *encoding* variants and silent
about *linguistic* ones.  Persian ``ي``/``ی`` and ``ك``/``ک`` are the same letter
typed from different keyboards, and Unicode NFKC does not unify them, so two
byte-different but identical Persian strings currently escape exact dedup, MinHash,
and the contamination prefilter.  Folding them is a correctness fix, not a
heuristic.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

import polars as pl

# Feature identifiers shared with contamination_safe. They are the keys of
# ``_Row.flags``, of ``selection.hard_phenomena_min_share``, and of the report.
FEATURE_MATH = "has_math"
FEATURE_NUMBERS_UNITS = "has_numbers_units"
FEATURE_ACRONYMS = "has_acronyms"
FEATURE_MIXED_SCRIPT = "has_mixed_script"
FEATURE_RARE_TERM = "is_rare_term"

ALL_FEATURES = (
    FEATURE_MATH,
    FEATURE_NUMBERS_UNITS,
    FEATURE_ACRONYMS,
    FEATURE_MIXED_SCRIPT,
    FEATURE_RARE_TERM,
)

UNKNOWN_LANGUAGE = "und"

TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# Script-neutral: LaTeX commands and mathematical operators carry no language.
MATH_RE = re.compile(
    r"(\$[^$]+\$|\\\(|\\\[|\\frac|\\sum|\\int|\\alpha|\\beta|\\gamma|\\lambda|\\sigma"
    r"|[=<>≤≥±∓×÷√∞∈∉⊂⊆∪∩∑∏∫∂∇Δ∆]|\b[a-zA-Z]\s*\^\s*[0-9n]|\b[xyz]\s*=)"
)
# Statistical notation is written in Latin even inside non-Latin prose.
STAT_RE = re.compile(r"\b[pP]\s*[<>=]\s*0?\.\d+|\bn\s*=\s*\d+|\bCI\b|\br\s*=\s*[-0.]|\bF\(\d")

# Expected-script character classes. A profile without one cannot participate in
# mixed-script detection, because "unexpected" has no meaning without "expected".
LATIN_SCRIPT_RE = re.compile(r"[A-Za-zÀ-ɏ]")
CYRILLIC_SCRIPT_RE = re.compile(r"[Ѐ-ӿ]")
ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

# Acronyms only exist in scripts that have case. Persian and Arabic do not.
LATIN_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}s?\b")
CYRILLIC_ACRONYM_RE = re.compile(r"\b[А-ЯЁ]{2,6}\b|\b[A-Z]{2,6}s?\b")

_LATIN_UNITS = (
    r"%|mm|cm|km|kg|mg|g|ml|l|s|ms|hz|khz|mhz|ghz|k|°c|°f|"
    r"kpa|mpa|mol|ppm|db|nm|µm|um|ev|kev|mev|gev|w|kw|mw|v|mv|a|ma"
)
_CYRILLIC_UNITS = (
    r"%|мм|см|км|м|кг|мг|г|мл|л|мс|с|мин|ч|тыс|млн|млрд|°с|гц|кгц|мгц|ггц|"
    r"вт|квт|мвт|в|мв|а|ма|моль"
)
# Persian unit words are written out rather than abbreviated.
_PERSIAN_UNITS = (
    r"%|درصد|میلیمتر|سانتیمتر|کیلومتر|متر|کیلوگرم|میلیگرم|گرم|میلیلیتر|لیتر|"
    r"ثانیه|دقیقه|ساعت|هرتز|ولت|وات|مول"
)


def _units_pattern(units: str) -> re.Pattern[str]:
    """A number immediately followed by one of this language's unit tokens."""
    return re.compile(rf"\b\d+(\.\d+)?\s*({units})\b", re.IGNORECASE)


# --- normalizers -----------------------------------------------------------
# Each folds encoding variants that denote the same text. None of them change
# which words are present; they only make identical text compare equal.

_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_PERSIAN_LETTERS = str.maketrans(
    {"ي": "ی", "ﻱ": "ی", "ﻲ": "ی", "ك": "ک", "ﻙ": "ک", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا"}
)
# Harakat (diacritics), tatweel (decorative elongation), ZWNJ and bidi marks are
# invisible or optional; two spellings that differ only here are the same string.
_ARABIC_MARKS_RE = re.compile(r"[ً-ْٰـ‌‍‎‏]")


def _normalize_default(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _normalize_persian(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ARABIC_INDIC_DIGITS).translate(_PERSIAN_LETTERS)
    return _ARABIC_MARKS_RE.sub("", text)


def _normalize_russian(text: str) -> str:
    # "ё" is optional in Russian orthography and routinely typed as "е".
    return unicodedata.normalize("NFKC", text).replace("ё", "е").replace("Ё", "Е")


# --- vectorized twins ------------------------------------------------------
# Ingestion de-duplicates every row of every import, which is the one hot path
# in this system, so it needs these normalizers as polars expressions rather
# than per-row Python calls. Each expression sits directly beside the scalar
# function it mirrors, and ``tests/test_language_profiles.py`` asserts the two
# agree: they are one rule with two evaluation strategies, not two rules.

_DIGIT_MAP = dict(zip("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789", strict=True))
_LETTER_MAP = {
    "ي": "ی", "ﻱ": "ی", "ﻲ": "ی", "ك": "ک", "ﻙ": "ک",
    "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا",
}


def _normalize_default_expr(expr: pl.Expr) -> pl.Expr:
    return expr.str.normalize("NFKC")


def _normalize_persian_expr(expr: pl.Expr) -> pl.Expr:
    return (
        expr.str.normalize("NFKC")
        .str.replace_many(_DIGIT_MAP | _LETTER_MAP)
        .str.replace_all(_ARABIC_MARKS_RE.pattern, "")
    )


def _normalize_russian_expr(expr: pl.Expr) -> pl.Expr:
    return expr.str.normalize("NFKC").str.replace_many({"ё": "е", "Ё": "Е"})


@dataclass(frozen=True)
class LanguageProfile:
    """Everything the selector needs to know about one language."""

    code: str
    normalize: Callable[[str], str]
    normalize_expr: Callable[[pl.Expr], pl.Expr]
    script: re.Pattern[str] | None = None
    acronym: re.Pattern[str] | None = None
    units: re.Pattern[str] | None = None

    def tokens(self, text: str) -> list[str]:
        return TOKEN_RE.findall(self.normalize(text).lower())

    def key(self, text: str) -> str:
        """Canonical whitespace-joined form used for exact duplicate detection."""
        return " ".join(self.tokens(text))

    def key_expr(self, expr: pl.Expr) -> pl.Expr:
        """Vectorized :meth:`key`, for de-duplicating a whole frame at once."""
        return (
            self.normalize_expr(expr)
            .str.to_lowercase()
            # TOKEN_RE splits on runs of non-word characters; collapsing those
            # runs to one space and trimming produces the same joined tokens.
            # Spelled out as "not a letter, number, or underscore" rather than
            # as `[^\w]`, because Rust's `\w` also matches combining marks and
            # join controls while Python's does not, and a stray Persian hamza
            # would then survive here but not in `key`.
            .str.replace_all(r"[^\p{L}\p{N}_]+", " ")
            .str.strip_chars()
        )


_FALLBACK_PROFILE = LanguageProfile(
    code=UNKNOWN_LANGUAGE,
    normalize=_normalize_default,
    normalize_expr=_normalize_default_expr,
)

_PROFILES: dict[str, LanguageProfile] = {
    "en": LanguageProfile(
        code="en",
        normalize=_normalize_default,
        normalize_expr=_normalize_default_expr,
        script=LATIN_SCRIPT_RE,
        acronym=LATIN_ACRONYM_RE,
        units=_units_pattern(_LATIN_UNITS),
    ),
    "ru": LanguageProfile(
        code="ru",
        normalize=_normalize_russian,
        normalize_expr=_normalize_russian_expr,
        script=CYRILLIC_SCRIPT_RE,
        acronym=CYRILLIC_ACRONYM_RE,
        units=_units_pattern(_CYRILLIC_UNITS),
    ),
    "fa": LanguageProfile(
        code="fa",
        normalize=_normalize_persian,
        normalize_expr=_normalize_persian_expr,
        script=ARABIC_SCRIPT_RE,
        # Persian is caseless: an acronym detector would be a fabrication.
        acronym=None,
        units=_units_pattern(_PERSIAN_UNITS),
    ),
    "ar": LanguageProfile(
        code="ar",
        normalize=_normalize_persian,
        normalize_expr=_normalize_persian_expr,
        script=ARABIC_SCRIPT_RE,
        acronym=None,
        units=_units_pattern(_PERSIAN_UNITS),
    ),
}


def normalize_tag(tag: str | None) -> str:
    """Reduce a language tag to the primary subtag: ``en-US`` and ``EN`` become ``en``."""
    if not tag:
        return UNKNOWN_LANGUAGE
    primary = re.split(r"[-_]", str(tag).strip().lower(), maxsplit=1)[0]
    return primary or UNKNOWN_LANGUAGE


def get_language_profile(tag: str | None) -> LanguageProfile:
    return _PROFILES.get(normalize_tag(tag), _FALLBACK_PROFILE)


def pair_key(src_lang: str | None, tgt_lang: str | None) -> str:
    return f"{normalize_tag(src_lang)}-{normalize_tag(tgt_lang)}"


@dataclass(frozen=True)
class PairProfile:
    """The resolved policy surface for one ``(src_lang, tgt_lang)`` pair."""

    pair_key: str
    src_lang: str
    tgt_lang: str
    source: LanguageProfile
    target: LanguageProfile
    supported_features: frozenset[str]

    def normalize_source(self, text: str) -> str:
        return self.source.normalize(text)

    def normalize_target(self, text: str) -> str:
        return self.target.normalize(text)

    def source_tokens(self, text: str) -> list[str]:
        return self.source.tokens(text)

    def target_tokens(self, text: str) -> list[str]:
        return self.target.tokens(text)

    def source_key(self, text: str) -> str:
        return self.source.key(text)

    def target_key(self, text: str) -> str:
        return self.target.key(text)

    def supports(self, feature: str) -> bool:
        return feature in self.supported_features

    def flags(self, source: str, target: str) -> dict[str, bool]:
        """Feature flags for one row.

        Every key is always present so callers can index ``flags`` by name, but a
        flag for an unsupported feature is always ``False`` and must never be
        turned into a quota.  ``supported_features`` is the authority on which of
        these values carry information.
        """
        return {
            FEATURE_MATH: bool(MATH_RE.search(source)),
            FEATURE_NUMBERS_UNITS: bool(
                (self.source.units.search(source) if self.source.units else False)
                or STAT_RE.search(source)
            ),
            FEATURE_ACRONYMS: bool(
                self.source.acronym.search(source) if self.source.acronym else False
            ),
            FEATURE_MIXED_SCRIPT: self._mixed_script(source, target),
            # Frequency-derived; filled in by the caller, which needs the corpus.
            FEATURE_RARE_TERM: False,
        }

    def _mixed_script(self, source: str, target: str) -> bool:
        """Either side carrying the other side's script.

        For ``en-fa`` this reduces exactly to the original "Latin in target or
        Persian in source" rule.  For ``ru-fa`` it becomes "Arabic in source or
        Cyrillic in target", which is the same phenomenon expressed for the
        languages actually involved.
        """
        if not self.supports(FEATURE_MIXED_SCRIPT):
            return False
        assert self.source.script is not None and self.target.script is not None
        return bool(self.target.script.search(source) or self.source.script.search(target))


def _supported_features(source: LanguageProfile, target: LanguageProfile) -> frozenset[str]:
    supported = {FEATURE_MATH, FEATURE_RARE_TERM}
    if source.units is not None:
        supported.add(FEATURE_NUMBERS_UNITS)
    if source.acronym is not None:
        supported.add(FEATURE_ACRONYMS)
    # Mixed script needs two known, different scripts. Same-script pairs such as
    # en-de have no cross-script signal to detect.
    if (
        source.script is not None
        and target.script is not None
        and source.script.pattern != target.script.pattern
    ):
        supported.add(FEATURE_MIXED_SCRIPT)
    return frozenset(supported)


@lru_cache(maxsize=256)
def resolve_pair(src_lang: str | None, tgt_lang: str | None) -> PairProfile:
    """Resolve one pair's profile. Cached: called once per row group, not per row."""
    src = normalize_tag(src_lang)
    tgt = normalize_tag(tgt_lang)
    source = get_language_profile(src)
    target = get_language_profile(tgt)
    return PairProfile(
        pair_key=f"{src}-{tgt}",
        src_lang=src,
        tgt_lang=tgt,
        source=source,
        target=target,
        supported_features=_supported_features(source, target),
    )
