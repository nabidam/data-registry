# Contamination-safe integration review

Date: 2026-08-08

Reference implementation:
`/home/dev/projects/forgpt/codes/2026/mt/translategemma/build_test_set.py`

## Conclusion

The registry integrates the reference pipeline's main stages and has a sound safety model, but it
is a scalable adaptation rather than an exact full-corpus port. Evaluation rows are reserved before
a batch becomes ready, contamination-risk rows are permanently quarantined, and training builders
centrally exclude both groups.

The integration should not be described as fully equivalent to the reference script until the
intentional large-corpus approximations are stated clearly and measured against representative MT
corpora.

## What is integrated well

- Evaluation selection runs before an imported batch becomes available to dataset builders.
- Selected rows become permanently `RESERVED_EVALUATION`.
- Deduplication rejects, selected-document siblings, and detected near-duplicates become
  permanently `QUARANTINED`.
- Training snapshots use only `TRAINABLE` rows; evaluation sets use only
  `RESERVED_EVALUATION` rows.
- The pipeline includes feature annotation, exact/MinHash/embedding deduplication, domain and
  length quotas, document restriction, k-center selection, hard-phenomenon top-ups, document
  holdout, near-duplicate quarantine, dev/test assignment, and gold-subset flagging.
- Canonical Parquet stores document IDs, selection annotations, `evaluation_split`, and
  `human_verify` without creating cumulative dataset copies.
- Batch statistics retain the policy, seed, counts, selected-ID hash, and Parquet hashes.
- Large CSV/TSV imports use bounded memory and a durable PostgreSQL-backed worker.

## Findings

### 1. Generated dev/test and gold subsets are not first-class evaluation-set filters

Severity: high

The importer stores `evaluation_split` and `human_verify` in immutable batch Parquet, but the
evaluation-set creation contract cannot filter on either value. The reference script emits dev and
test outputs separately, while the registry requires researchers to discover and paste explicit
sample IDs to reproduce those subsets.

Impact:

- The generated dev set cannot be directly materialized as an evaluation set.
- The generated test set cannot be directly materialized as an evaluation set.
- The human-verification subset cannot be directly selected or excluded.
- Stages 9 and 10 affect stored metadata but are incomplete in the researcher workflow.

Recommendation: add evaluation-only filters for `evaluation_split` and `human_verify`, expose them
on the Evaluation Sets page, persist them in the evaluation-set specification, and show both fields
in evaluation-set row previews.

Status: addressed in the follow-up implementation accompanying this review.

### 2. Large-import selection and filtering are scalable approximations

Severity: high for equivalence claims; acceptable for scale when documented

For large CSV/TSV imports, expensive selection runs on a deterministic bounded candidate reservoir
rather than on every corpus row. Rare-term statistics, strata, MinHash deduplication, and embedding
deduplication therefore describe the candidate pool rather than the entire corpus.

After selection, every corpus row is checked for exact-source and selected-document overlap.
Semantic verification is limited to rows whose source or target shares at least two normalized
three-token shingles with the corresponding side of the reserved evaluation pool. This higher-recall
gate makes full-corpus scanning practical, but a semantically equivalent rewrite without enough
shared phrasing can remain trainable.

Embedding deduplication also uses random-hyperplane LSH, and k-center selection can use a
lower-dimensional random projection. Both are bounded, deterministic approximations of the
reference script's direct embedding comparisons.

Recommendation: retain the scalable adaptation as the normal large-corpus mode. Do not perform
unfiltered all-pairs LaBSE comparison over the complete corpus. The prefilter was strengthened in
the follow-up implementation from any source-side five-token shingle to at least two
source-or-target three-token shingles, with source-side LaBSE still making the final quarantine
decision. Add an optional stricter audit mode only if the remaining low-lexical-overlap risk is
unacceptable for a particular corpus.

### 3. Default selector and container dependencies disagree

Severity: high operationally

`contamination_safe` is the application default, while Docker images default to excluding the
evaluation dependency group. A default eligible import can therefore be accepted and later fail in
the worker when selection starts.

Recommendation: production/full images should install evaluation dependencies by default, or the
application should choose a lightweight default selector when those dependencies are absent.

### 4. Mixed-script annotation assumes an English-Persian direction

Severity: medium

The registry supports arbitrary language pairs, but mixed-script detection checks for Latin text on
the target side or Persian text on the source side without consulting `src_lang` and `tgt_lang`.
This makes the feature and its selection quota unreliable outside the assumed direction.

Recommendation: make the annotation language-aware or disable this quota for unsupported language
pairs.

### 5. Verification and policy validation are limited

Severity: medium confidence risk

There is no automated parity or integration suite for the contamination-safe path. The policy
loader verifies only that YAML contains a mapping and a description; malformed nested keys,
thresholds, bucket shares, or strategies fail later in the worker.

Recommendation: when verification work is prioritized, start with a frozen reference fixture and
policy-schema validation. This review does not require adding tests.

## Reference-to-registry configuration mapping

The following defaults are preserved: flattened domain allocation (`0.5`), minimum 50 rows per
domain, length buckets and shares, hard-phenomenon shares, rare-token settings, 30 candidate
documents, MinHash threshold `0.85`, embedding deduplication threshold `0.95`, LaBSE with batch size
64, contamination threshold `0.92`, 50/50 dev/test assignment, gold subset size 120, and seed 42.

Intentional registry differences:

- Reservation size comes from the import percentage and maximum instead of `selection.total_size`.
- Canonical source/target fields replace fixed `en` and `fa` columns.
- Immutable Parquet and allocation metadata replace standalone train/dev/test CSV outputs.
- Large imports add a bounded reservoir, candidate multiplier, embedding LSH, projection dimensions,
  and a two-match source-or-target three-token semantic prefilter.

## Workflow summary

1. Normalize imported translation pairs into the canonical schema.
2. Annotate source length, domain, difficult phenomena, and rare terminology.
3. Remove duplicate evaluation candidates using exact, MinHash, and embedding checks.
4. Allocate domain and length quotas and select diverse examples with k-center sampling.
5. Top up difficult phenomena that fall below their configured shares.
6. Mark selected examples `RESERVED_EVALUATION`.
7. Mark duplicate rejects, selected-document siblings, and detected near-duplicates `QUARANTINED`.
8. Assign reserved examples to dev or test and flag a human-verification subset.
9. Publish immutable Parquet and metadata; training can query only `TRAINABLE` rows.

In short, the pipeline chooses evaluation examples first and then permanently prevents those
examples and detected relatives from entering training.
