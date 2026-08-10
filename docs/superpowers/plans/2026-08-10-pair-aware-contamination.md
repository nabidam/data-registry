# Pair-Aware Contamination Framework Implementation Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. This
> project does not use TDD (see `AGENTS.md`); tasks end with a verification step
> that imports and exercises the changed module instead of a test suite.

**Goal:** Make contamination-safe selection and contamination scanning operate per
language pair by default, so a `ru→fa` reservation cannot silently mis-measure its
own rows or accidentally quarantine `en→fa` training data.

**Architecture:** A new `language_profiles` module owns every language-specific
decision (normalization, expected script, acronym/unit detection, feature
capability). `contamination_safe` threads a `pair_key` through every internal row
and reference, groups contamination comparisons by an explicit `comparison_scope`,
and builds quotas hierarchically (pair → domain → length bucket). The existing
`contamination_scope` field keeps its meaning (which corpus to scan); the new
`comparison_scope` field answers a separate question (which rows are comparable).

**Tech Stack:** Python 3.13, Polars, SQLAlchemy 2, Alembic, FastAPI, Pydantic v2,
sentence-transformers (LaBSE), scikit-learn, datasketch. React + TypeScript frontend.

## Global Constraints

- Backward compatible: existing API payloads, existing rows, and an *older*
  `evaluation_reservation.yaml` must all keep working. Every new config key is read
  with `.get(key, default)`.
- Minimum user-workflow change: the only new UI control is one optional select.
  Everything else changes underneath.
- Public function signatures in `contamination_safe.py` keep their existing
  positional parameters; new parameters are keyword-only with defaults.
- Follow `AGENTS.md`: no new abstraction layers, no enterprise patterns, keep it
  maintainable by one engineer.
- No `unwrap`-style silent failures: an unsupported feature is recorded in the
  report, never reported as a measured zero.

---

### Task 1: Language profile layer

**Files:**
- Create: `backend/services/evaluation/language_profiles.py`

**Interfaces:**
- Produces:
  - `normalize_tag(tag: str | None) -> str`
  - `pair_key(src_lang, tgt_lang) -> str` returning `"ru-fa"`
  - `resolve_pair(src_lang, tgt_lang) -> PairProfile`
  - `PairProfile.tokens(text, *, side) -> list[str]`
  - `PairProfile.normalize_source/normalize_target(text) -> str`
  - `PairProfile.flags(source, target) -> dict[str, bool]` with all five feature keys
  - `PairProfile.supported_features: frozenset[str]`
  - Feature name constants `FEATURE_MATH`, `FEATURE_NUMBERS_UNITS`,
    `FEATURE_ACRONYMS`, `FEATURE_MIXED_SCRIPT`, `FEATURE_RARE_TERM`

- [ ] **Step 1: Write the module** — profiles for `en`, `fa`, `ru`, plus a
      conservative `und` fallback. Persian normalizer folds Arabic-Indic digits,
      `ي→ی`, `ك→ک`, `ة→ه`, and strips harakat/tatweel/ZWNJ. Russian normalizer folds
      `ё→е`. Mixed-script is defined as "source carries target's script or target
      carries source's script", supported only when both scripts are known and differ.
- [ ] **Step 2: Verify** — `uv run python -c "from services.evaluation.language_profiles import resolve_pair; p=resolve_pair('ru','fa'); print(p.pair_key, sorted(p.supported_features))"`
- [ ] **Step 3: Commit** — `feat(evaluation): add language profiles for pair-aware features`

---

### Task 2: Per-pair policy resolution in the reservation config

**Files:**
- Modify: `backend/services/evaluation/reservation_config.py`
- Modify: `backend/config/evaluation_reservation.yaml`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `resolve_pair_policy(config: dict, pair_key: str) -> dict` deep-merging
  `config["pairs"][pair_key]` over the base policy.

- [ ] **Step 1:** Add `resolve_pair_policy` with a bounded recursive mapping merge.
- [ ] **Step 2:** Add new YAML keys with defaults that preserve current behaviour:
      `contamination.comparison_scope: pair`, `contamination.cross_lingual_cosine_threshold: 0.97`,
      `contamination.exact_target_duplication: true`, `contamination.document_namespace: source`,
      and an empty `pairs: {}` section documenting the override shape.
- [ ] **Step 3: Commit** — `feat(evaluation): support per-language-pair policy overrides`

---

### Task 3: Pair-aware annotation, quotas, and top-up

**Files:**
- Modify: `backend/services/evaluation/contamination_safe.py`

**Interfaces:**
- Consumes: Task 1 profiles, Task 2 `resolve_pair_policy`.
- Produces: `_Row` carrying `src_lang`, `tgt_lang`, `pair_key`, `source_key`,
  `target_key`; `_quotas` keyed by `(pair_key, domain, bucket)`.

- [ ] **Step 1:** Thread `src_lang`/`tgt_lang`/`source_id`/`batch_id` through the
      frame selection with a helper that tolerates frames missing those columns.
- [ ] **Step 2:** Rewrite `_annotate` to group by pair, compute the rare-token
      frequency table and rare threshold *within* each pair, and resolve length
      buckets from that pair's merged policy.
- [ ] **Step 3:** Namespace `_document_id` by `source_id` (configurable).
- [ ] **Step 4:** Add `_pair_targets`, make `_quotas`/`_select` hierarchical, and clamp
      `min_per_domain` so a small pair cannot be forced past its own target.
- [ ] **Step 5:** Restrict `_top_up_hard_phenomena` to features the pair supports and
      to rows within the supporting pairs, so top-up cannot undo pair allocation.
- [ ] **Step 6:** Extend the report with `language_pairs`, `pair_targets`,
      `unsupported_features`, and `document_namespace`.
- [ ] **Step 7: Commit** — `feat(evaluation): make selection quotas language-pair aware`

---

### Task 4: Scoped contamination reference and scan

**Files:**
- Modify: `backend/services/evaluation/contamination_safe.py`

**Interfaces:**
- Produces:
  - `prepare_contamination_reference(selected_rows, config, progress=None, *, comparison_scope=None)`
  - `scan_full_corpus_contamination(rows, reference, config, progress=None)` unchanged positionally
  - `ContaminationScanResult` gains `exact_target_rows`, keeps existing fields.

- [ ] **Step 1:** Replace the flat reference with `groups: dict[str, _ReferenceGroup]`
      keyed by the comparison scope, each holding source *and* target keys, shingles,
      and embeddings. Keep a `global_target_keys` set outside the grouping.
- [ ] **Step 2:** Fix the gate/decision asymmetry — a source-gated candidate is
      resolved against source embeddings, a target-gated candidate against target
      embeddings.
- [ ] **Step 3:** Add exact normalized target duplication as an always-on cross-pair
      check, independent of `comparison_scope`.
- [ ] **Step 4:** Select the cosine threshold per side from the scope
      (`same` when that side's language is pinned by the scope, `cross_lingual` otherwise).
- [ ] **Step 5: Commit** — `feat(evaluation): scope contamination comparisons by language pair`

---

### Task 5: Wire the comparison scope through policy, model, schema, and API

**Files:**
- Modify: `backend/core/config.py`
- Modify: `backend/services/evaluation/reservation.py`
- Modify: `backend/models/entities.py:159`
- Create: `backend/alembic/versions/<rev>_reservation_comparison_scope.py`
- Modify: `backend/schemas/__init__.py:186-208`
- Modify: `backend/services/evaluation/dataset_reservation.py`
- Modify: `backend/services/ingestion/service.py`

- [ ] **Step 1:** Add `evaluation_comparison_scope: str = "pair"` to `Settings`.
- [ ] **Step 2:** Add `comparison_scope` to `ReservationPolicy` (resolved default) and
      to its `as_dict()` report.
- [ ] **Step 3:** Add the `comparison_scope` column (`String(32)`, `server_default="pair"`,
      `nullable=False`) with an Alembic migration on top of head `7d3c1a9b5e20`.
- [ ] **Step 4:** Add the field to `DatasetReservationIn` (defaulted, so existing
      payloads still validate) and `DatasetReservationOut`.
- [ ] **Step 5:** Pass the scope into `prepare_contamination_reference` from both the
      import path and the dataset-reservation path, and record it in both reports.
- [ ] **Step 6: Commit** — `feat(evaluation): add comparison scope to reservations`

---

### Task 6: Frontend control

**Files:**
- Modify: `frontend/src/types.ts:157`
- Modify: `frontend/src/pages/Datasets.tsx:62,697,757-770`

- [ ] **Step 1:** Add `comparison_scope` to the `DatasetReservation` type.
- [ ] **Step 2:** Add one `Select` beside the existing scan-scope control, defaulting
      to `pair`, with option labels naming the tradeoff.
- [ ] **Step 3: Commit** — `feat(frontend): expose contamination comparison scope`

---

### Task 7: Documentation

**Files:**
- Create: `docs/adr/003-pair-aware-contamination.md`
- Modify: `docs/workflows/extending-a-fine-tuning-dataset-with-a-new-language-pair.md`

- [ ] **Step 1:** Write ADR 003 recording the two-axis scope model, the exact/semantic
      target split, the document namespace, and the capability-flag rule.
- [ ] **Step 2:** Update the workflow doc's step 4 to describe the new default.
- [ ] **Step 3: Commit** — `docs: record pair-aware contamination decisions`
