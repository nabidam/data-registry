# ADR 003: scope contamination-safe selection by language pair

## Context

`contamination_safe` was ported from an English-to-Persian test-set builder. Its
feature detectors, thresholds, and quotas encode that pair, but nothing in the
pipeline records which pair a row belongs to. Importing a second pair does not
fail; it produces measurements that are wrong in ways the report presents as
correct.

Three concrete defects motivated this decision.

Feature detectors were language-hardcoded. `has_acronyms` matched only Latin
uppercase runs, so a Cyrillic source scored zero. `has_numbers_units` listed Latin
unit abbreviations, so `5 км` did not count while `5 km` did. `has_mixed_script`
was literally "Latin in target or Persian in source". Each unfillable quota was
silently abandoned by the hard-phenomena top-up, and the batch report recorded the
resulting zero as a measurement rather than as an unmeasurable feature.

The full-corpus scan admitted candidates on source *or* target shingle overlap but
resolved every one of them against source embeddings. In a Persian-target registry
a Russian reservation would therefore flag every English row sharing two Persian
trigrams, pay full LaBSE inference on each, and then compare a Russian source with
an English one — a decision that could not fire. Expensive and ineffective at once.

Nothing scoped comparisons by language. LaBSE is cross-lingual by construction, so
a dataset reservation with `registry` contamination scope compared reserved rows
against every other pair using `near_dup_cosine_threshold: 0.92`, a value
calibrated for monolingual near-duplicates. LaBSE places a translation and its
source at roughly 0.85-0.95, so ordinary translation equivalence was one threshold
away from permanently quarantining valid training data.

## Decision

Every row carries a `pair_key` derived from its `src_lang` and `tgt_lang`.
Deduplication, quotas, document holdout, dev/test stratification, the gold subset,
and contamination comparison all group by it. Each language pair protects itself
by default; comparing across pairs is an explicit choice.

**Two orthogonal scopes, not one.** `contamination_scope` keeps its existing
meaning — which corpus is scanned, `composition` or `registry`. The new
`comparison_scope` answers a different question: which rows inside that corpus may
be compared. It takes `pair` (default), `source_language`, `target_language`, or
`any`. Collapsing these into one enum would have made `composition` and `pair`
ambiguous and would have made combinations such as "scan the whole registry, but
compare within pairs" unreachable.

**Exact and semantic target checks are split.** Exact normalized target
duplication is checked across every pair regardless of `comparison_scope`: a
training row producing a reserved row's exact output leaks that output whatever
language its source was written in, and the check is a hash-set lookup that costs
nothing to widen. Semantic target similarity stays inside the comparison scope,
because it is expensive, definitional, and removes rows another pair considers
valid. Bundling both behind one opt-in would have shipped the safe half disabled.

**Thresholds are chosen per side.** A side whose language the scope pins is a
monolingual comparison and keeps `near_dup_cosine_threshold`. A side the scope
leaves free uses `cross_lingual_cosine_threshold`, defaulting to 0.97, so
translation equivalence is not mistaken for duplication.

**Candidates are resolved on the side that admitted them.** Source-gated
candidates are compared against reserved source embeddings, target-gated ones
against reserved target embeddings. The reference embeds both sides.

**Language profiles own language-specific behaviour.** A profile supplies
normalization, expected script, acronym and unit detection, and — the part that
matters most — `supported_features`. A quota is built only from features the pair
can measure. An acronym quota is not applied to a caseless source language; it is
recorded in the report under `unsupported_features`. Unknown languages resolve to
a conservative fallback supporting only script-independent features, which is
safer than applying English detectors to text they were never written for.

Persian normalization folds Arabic-Indic digits, `ي`/`ی`, `ك`/`ک`, and
harakat/tatweel/ZWNJ. NFKC does not unify these, so byte-different but identical
Persian strings previously escaped exact dedup, MinHash, and the contamination
prefilter — a live defect in `en-fa`, not only a multilingual one.

**Document identity is namespaced by `source_id`.** Importers rarely emit globally
unique document IDs, so a registry-wide scan treated `doc:1` from two unrelated
corpora as one document. Namespacing by batch was rejected: it would disable
cross-batch holdout, which is the case the holdout exists for when one corpus is
re-imported.

**Quotas are hierarchical: pair, then domain, then length bucket.** Each pair's
slice is computed first and everything below resolves inside it, using that pair's
merged policy. `min_per_domain` is clamped to an even split of the pair's own
target, so a small pair is not asked for more rows than it was allocated. The
hard-phenomena top-up draws its replacements and its evictions from the same
restricted set, so topping up one pair cannot spend another pair's allocation.

Per-pair policy overrides live under a `pairs:` section in
`evaluation_reservation.yaml`, deep-merged over the base policy. Length buckets
count source tokens, and languages differ in token density, so a deployment can
retune edges for one pair without forking the whole policy.

## Operational consequences

Defaults change behaviour, deliberately. A dataset reservation that previously
compared across the whole registry now compares within pairs unless an operator
selects a wider `comparison_scope`. This is the intended correction: the previous
behaviour was uncalibrated rather than deliberate. Setting `comparison_scope: any`
reproduces the old comparison breadth, now with the cross-lingual threshold applied.

An older `evaluation_reservation.yaml` keeps working. Every new key is read with a
default, and a config with no `pairs:` section resolves to the base policy
unchanged. Existing `dataset_reservations` rows take `comparison_scope = 'pair'`
via server default; reservations are never re-run, so this only labels history.

Reference preparation now embeds both sides of the reserved pool, roughly doubling
that cost. The reserved pool is bounded by the requested evaluation size, and the
saving on the scan side is far larger: under `pair` scope, rows from other pairs
never reach the embedding stage at all.

Registry-wide scanning is no longer the dangerous default it was, but
`comparison_scope: any` still carries the original warning — a new reservation can
quarantine another pair's training data.

## Failure handling

A policy mistake must not be discovered after an hour of work. `validate_policy`
runs at the start of selection, at reference preparation, and when a reservation is
requested, and reports every problem at once. It covers the scope and namespace
enumerations, cosine ranges, the shape of `pairs`, and — for the base policy and
every merged pair override — that length buckets ascend and that the share list
matches the bucket count. Before this, a mismatched override surfaced as a
`zip()` length error inside quota allocation, after LaBSE had encoded the pool;
a misspelled `document_namespace` did not surface at all, silently returning
un-namespaced IDs.

Annotation refuses to return a frame with unassigned slots. Callers index the
annotated list positionally against their records, so a short list would shift
every later index onto a different sample — a silent corruption worse than a crash.

Imports are already atomic: sample rows are copied inside the same transaction
that marks the batch ready, so a failure rolls the whole attempt back and the batch
is retried or marked failed with its error. Nothing partial survives.

## Reversibility

Reservation and quarantine stay permanent for ordinary operation — that invariant
is what stops a snapshot from ever seeing an evaluated row. But permanence with no
recovery path means a wrong reservation is unfixable, and a wrong reservation is
exactly what a change of this size risks.

A run therefore records `applied_at`, the single instant stamped onto every row it
protected. Because `_set_allocations` only promotes rows that are still
`TRAINABLE`, that timestamp identifies precisely the rows this run changed and
none that an earlier run had already claimed — attribution without storing
millions of sample ids.

`revert_dataset_reservation` returns those rows to `TRAINABLE`. It refuses when
the reservation is not the most recent completed one, when an evaluation set was
built from it, when another reservation or a snapshot build is in flight, or when
`applied_at` is absent. It matches on both the timestamp and the allocation the run
assigned, so a row since re-classified by a human is never overwritten; an ignored
row keeps `IGNORED` but loses the protection it would otherwise be restored to.
The run is marked `reverted`, which the evaluation-set route already rejects, and
the reversal is recorded in the reservation report.

Batch-level mistakes are not covered here. They keep their existing reversal:
reject the batch and purge it.
