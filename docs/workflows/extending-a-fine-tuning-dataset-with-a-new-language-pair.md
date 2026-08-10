# Extending a Fine-Tuning Dataset with a New Language Pair

## Scenario

A researcher has four English-to-Persian batches. They created a custom dataset
composition, ran a contamination-safe reservation, built reproducible
train/validation/test splits, and exported an immutable snapshot for fine-tuning.

Later, the researcher imports one or more Russian-to-Persian batches. The next
fine-tuning run should use:

- the same English samples in the same splits as the first run;
- new Russian samples assigned to train, validation, and test;
- a separate Russian-to-Persian evaluation set selected with
  `contamination_safe`.

The registry supports this workflow. Exact preservation depends on the meaning
of "same splits": preserving split assignments is reproducible from the split
recipe, while preserving the old files byte for byte requires keeping the old
snapshot as a separate training input.

## Recommended route

### 1. Keep the first snapshot unchanged

Treat the English-to-Persian snapshot as the record of the first fine-tuning
run. Do not rebuild or replace it. Its manifest, Parquet checksums, and split
counts describe the data used for that run.

Use the snapshot manifest as the source for the old dataset recipe. Copy these
fields when preparing the expanded dataset:

- included batch IDs or per-batch rules;
- composition seed;
- filters;
- split ratios and split seed.

Copy from the manifest instead of the current dataset definition. Dataset
definitions can be edited after snapshot creation, while the manifest records
the recipe used for that snapshot.

### 2. Import the Russian-to-Persian batches

Set `src_lang` to `ru` and `tgt_lang` to `fa` for each import. Keep
`contamination_safe` as the import selector.

The selector resolves a language profile from those two tags. That profile
decides how Russian text is normalized and tokenized, how its acronyms and
numeric units are detected, and which hard-phenomena quotas apply at all. A
feature Russian cannot express is dropped from the quota rather than pursued with
an English detector, and the batch report lists it under `unsupported_features`.
Nothing needs configuring for `ru`; unknown languages fall back to a conservative
profile that measures only script-independent features.

Each import reserves an evaluation slice before the batch can contribute to a
training snapshot. The import also quarantines document matches and other
contamination risks found within that batch. Those reserved and quarantined
rows cannot enter the expanded training dataset.

### 3. Create a Russian-only dataset

Create a logical dataset containing the new Russian batches. Set its language
filters to `src_langs=["ru"]` and `tgt_langs=["fa"]`, or rely on the batch
membership if those batches contain no other pairs.

This dataset gives the Russian reservation a clear boundary and leaves the old
English composition out of candidate selection.

### 4. Reserve a pooled Russian evaluation set if needed

Import-time reservation creates a reservation inside each batch. That may be
enough: create an evaluation set from reserved samples and filter it to
Russian-to-Persian.

For one selection across all new Russian batches, run a dataset-level
`contamination_safe` reservation on the Russian-only dataset. Select the target
count or percentage, seed, and `composition` contamination scope.

Two independent scopes control the contamination scan.

`contamination_scope` decides which corpus is scanned. `composition` scans the
Russian dataset. `registry` scans all ready batches.

`comparison_scope` decides which rows inside that corpus may be compared with
the reserved Russian rows. It defaults to `pair`, so Russian evaluation rows are
compared against Russian training rows and English rows are left alone. This is
the right setting for almost every expansion: similarity thresholds are
calibrated for same-language duplicates, and LaBSE scores an ordinary
translation and its source high enough that a wider scope can quarantine valid
English data.

Widen it only for a specific reason:

- `target_language` also compares the Persian side across source languages. Use
  it when you intend to treat the same Persian material arriving via several
  source languages as contamination. It can quarantine English rows.
- `source_language` compares pairs sharing the Russian source, such as `ru-fa`
  against `ru-en`.
- `any` compares everything. Advanced, and it can quarantine English rows.

Regardless of scope, a training row whose target is an exact duplicate of a
reserved row's target is always quarantined, in any language pair. That check is
cheap and unambiguous: the model would otherwise be trained on output it is
later evaluated on.

Dataset-level reservation selects from rows that remain `TRAINABLE` after
import-time reservation. It does not select the rows that the imports already
reserved.

After the reservation finishes, create an evaluation set and choose its
reservation ID. This materializes the exact Russian benchmark chosen by that
reservation, including its generated dev/test and human-verification metadata.

### 5. Create a new combined dataset

Create a new dataset definition. Do not edit the English dataset used for the
first experiment.

Start with the English composition recorded in the old snapshot manifest, then
add the Russian batches or their per-batch rules. Keep the old composition seed.
If the old filters contain `src_langs=["en"]`, change that filter to
`src_langs=["en", "ru"]`. Keep `tgt_langs=["fa"]`.

Per-batch percentage and count selection ranks samples inside each batch.
Adding a Russian batch does not change the rank or membership of an English
batch when the composition seed and English batch rules stay the same.

### 6. Create a split definition for the combined dataset

Create a new split definition attached to the combined dataset. Copy the old
snapshot's ratios and split seed.

The snapshot builder assigns each sample with a deterministic hash of:

```text
sample_id + split_seed
```

It maps that hash into the train, validation, or test ratio range. The assignment
does not depend on row order, total dataset size, or the presence of the Russian
batches. An eligible English sample therefore returns to its old split when the
new definition uses the same ratios and seed. Russian samples receive their own
assignments through the same function.

Create a new split definition instead of attaching the old dataset's split
record to the combined dataset. The copied recipe expresses the relationship
without crossing dataset ownership.

### 7. Build and export a new snapshot

Build a snapshot from the combined dataset and its new split definition. The
snapshot contains both language pairs in each split file:

```text
source_text | target_text | src_lang | tgt_lang
Hello       | سلام        | en       | fa
Привет      | سلام        | ru       | fa
```

The builder includes rows whose current allocation is `TRAINABLE`. It excludes
reserved, quarantined, and ignored rows from both language pairs.

Use this snapshot for the new fine-tuning experiment and attach the separate
Russian evaluation set to the experiment record.

## Preservation guarantees and limits

Using the same composition and split recipes preserves an old English sample's
split assignment. The combined snapshot contains the same set of English rows
only while those rows keep the same eligibility.

An English row from the old snapshot will be absent from the combined snapshot
if someone later:

- reserves or quarantines it;
- marks it ignored;
- changes the English batch rules, composition seed, or filters.

A dataset-level reservation can cause the first case when its `comparison_scope`
is widened past `pair`, or when a Russian row's Persian target exactly duplicates
an English row's Persian target.

## Recovering from a reservation that went wrong

A reservation that quarantined more than intended can be reverted, returning its
samples to trainable. Open the dataset's Reservation tab and choose **Revert** on
the run. The impact panel shows how many reserved and quarantined samples still
carry that run's protection, and lists anything that blocks the revert.

The revert is restricted on purpose:

- only the most recent completed reservation may be reverted; revert newer runs
  first;
- an evaluation set built from the reservation blocks it — delete the set first;
- no reservation or snapshot build may be in flight;
- reservations that ran before this feature existed cannot be attributed to their
  rows and must be corrected by hand.

Rows that someone has re-classified since the run are left untouched. A reverted
reservation can no longer be used to create an evaluation set.

This covers dataset-level reservations. A bad *import* is undone differently:
reject the batch and purge it, which removes its samples and objects together.
The registry records overlap between a later reservation and an old snapshot as
historical contamination. It does not rewrite the old snapshot.

The split algorithm does not stratify by language pair. Large Russian batches
should follow the configured ratios within normal hash variation. A small
Russian batch may contribute few rows, or no rows, to validation or test.

## Strongest option for byte-for-byte preservation

Some experiments require the English inputs to match the first run byte for
byte. Keep the old English snapshot and build a separate Russian snapshot. The
training loader can combine corresponding splits:

```text
new train      = old English train      + Russian train
new validation = old English validation + Russian validation
new test       = old English test       + Russian test
```

This route guarantees the old English files and membership. The current
registry cannot represent two snapshots as one snapshot or attach multiple
snapshot IDs to one experiment. The training configuration must record both
snapshot references outside the single `snapshot_id` field.

For routine multilingual expansion, prefer the combined-dataset route. It
produces one registry snapshot, preserves stable English assignments, and keeps
the full composition recipe in one manifest. Use the two-snapshot route when
byte identity or protection from later allocation changes takes priority over a
single registry artifact.
