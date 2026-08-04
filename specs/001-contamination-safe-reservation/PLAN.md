# Contamination-safe evaluation reservation

## 1. Goal & kernel journey

Use every selection, deduplication, embedding, split, gold-subset, and audit stage in the supplied test-set builder during import, while preventing selected evaluation material, document siblings, and detected near duplicates from entering training snapshots.

KJ1. A researcher opens Imports and chooses `contamination_safe`.
KJ2. They optionally map the source document-id column and submit an immutable batch.
KJ3. The importer derives the configured target size, runs exact/MinHash/embedding deduplication and LaBSE selection, reserves selected rows, quarantines leakage-risk rows, and records immutable split, gold, and audit annotations.
KJ4. The batch is visible with selected, trainable, and contamination-quarantined counts; an evaluation export retains dev/test and human-verification annotations, while snapshots continue to use only trainable rows.

## 2. Scope

In:

- Port every applicable stage and knob from the supplied configuration into the backend reservation service.
- Add `contamination_safe` as an import-time selector with exact/MinHash/embedding deduplication, LaBSE embeddings with TF-IDF fallback, deterministic stratification, feature quotas, diversity selection, document holdout, and near-duplicate purge.
- Preserve the selected evaluation rows separately from contamination-only exclusions.
- Persist selected rows' dev/test label and human-verification flag in immutable batch Parquet, and record a SHA-256 batch manifest in batch statistics.
- Expose selector controls, document-id mapping, configuration summary, and result counts in the import UI.
- Update the README and add a concise ADR.

Out:

- Rewriting historical batches or snapshots.
- Duplicating train/dev/test files outside the immutable batch and evaluation-set materializations.

Backlog:

- Making every test-builder knob editable per import.

## 3. Requirements

- R1: Choosing `contamination_safe` applies the source script's exact, MinHash, and embedding near-duplicate stages, then reserves the configured target count through deterministic domain × length-bucket quotas, diversity selection, and hard-phenomenon top-ups. Example: identical rows, config, model, and seed select the same sample IDs.
- R2: A mapped source document-id is retained in batch Parquet and, when document holdout is enabled, siblings of selected rows cannot reach a training snapshot. Example: rows from `paper-1:0` and `paper-1:1` cannot be split between evaluation and trainable pools.
- R3: Cross-document rows above the configured embedding cosine threshold are excluded from training but are not presented as evaluation examples. Example: a duplicate abstract in another document is contamination-quarantined.
- R4: Selected rows retain a stratified dev/test assignment and configurable human-verification flag in batch Parquet. Example: an evaluation-set materialization includes `evaluation_split` and `human_verify`.
- R5: Import results record the policy, feature/quota/dedup results, allocation counts, selected-ID hash, and immutable Parquet SHA-256 in batch statistics. Example: the Batches list shows selected and quarantined values.
- R6: Researchers can select the mode and document-id field from Imports without changing deployment configuration. Example: blank document-id treats each row as its own document.
- R7: Existing `heuristic` and `random` reservation modes retain their current behavior.

## 4. Active risk modules

External system. LaBSE is loaded through Hugging Face by sentence-transformers at import time; its model identifier, device selection, cache behavior, and failure semantics are part of the policy contract. The package/model boundary is lazy: non-`contamination_safe` imports never load it, and a missing or unavailable model causes that import to fail before its batch becomes ready. The lightweight import form is not UI-heavy.

## 5. Stack & dependencies

Use the existing FastAPI, Polars, React, and TypeScript stack plus `numpy`, `datasketch`, `scikit-learn`, `sentence-transformers`, and CPU-compatible `torch`. `backend/config/evaluation_reservation.yaml` is the imported policy source; `EVALUATION_RESERVATION_CONFIG` can point to a deployment-specific copy. The configured LaBSE model is downloaded on first eligible import unless already present in its Hugging Face cache; no Docker image is involved.

## 6. Units

### U1 — selectable policy skeleton

Outcome: the imported configuration and `contamination_safe` selector are discoverable through the existing import contract.

Deps: none.

Files: `backend/config/evaluation_reservation.yaml`, `backend/services/evaluation/reservation_config.py`, `backend/services/evaluation/selectors.py`, `backend/api/routes/imports.py`, `frontend/src/pages/Imports.tsx`, `frontend/src/types.ts`.

Criteria: R5 and R6; the defaults endpoint returns a description and safe-policy configuration, the import form maps `document_id`, and selecting another existing strategy remains valid.

### U2 — contamination-safe selection and allocation

Outcome: imports selected with the policy keep evaluation rows, quarantine leakage rows, and write a detailed report.

Deps: U1.

Files: `backend/services/evaluation/contamination_safe.py`, `backend/services/evaluation/reservation.py`, `backend/services/ingestion/normalize.py`, `backend/services/ingestion/service.py`, `backend/core/allocation.py`, `backend/models/entities.py`, `backend/alembic/versions/*`.

Criteria: R1–R5; selected rows are `RESERVED_EVALUATION`, contamination-only rows are `QUARANTINED`, batch Parquet retains dev/test and gold annotations, and no non-trainable allocation reaches a training build.

### U3 — documentation sync

Outcome: the policy and its operational limits are documented at the point of use.

Deps: U2.

Files: `README.md`, `docs/adr/001-contamination-safe-reservation.md`, `.brana/ledger.md`.

Criteria: R1–R7 are explained, including the model-cache behavior, full-source-pipeline mapping, and reason for quarantine allocation.

## 7. Verification contract

Per the user instruction, do not run tests, Docker, builds, or model/image downloads. Completion is a manual static review of the request/response shapes, allocation paths, and documented configuration. The normal future verification command is `cd backend && uv run ruff check .` and `cd frontend && yarn build`.

## 8. Walkthrough script

1. Open Imports and upload a file with an ID column such as `document:chunk`.
2. Select `contamination_safe`, map that ID as Document ID, and import.
3. Confirm the recent-batch reservation report separates selected evaluation rows, dedup/quarantine rows, dev/test counts, and gold rows.
4. Create an evaluation set from the reserved pool and a training snapshot from the same batch; confirm the evaluation export retains `evaluation_split`/`human_verify`, and that the selected evaluation document and its sibling do not appear in the snapshot.
5. Repeat with the same data and seed; compare the recorded selected IDs/counts for reproducibility.
