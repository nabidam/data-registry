# ADR 001: reserve evaluation data and quarantine contamination risks

## Decision

Imports using `contamination_safe` run the complete reservation pipeline from
`build_test_set.py`: feature annotation; exact, MinHash, and embedding deduplication; LaBSE
embeddings; domain and length quotas; candidate-document restriction; k-center selection;
hard-phenomena top-ups; document holdout; cross-document embedding purge; stratified dev/test
assignment; gold-subset flagging; and batch reporting.

Selected benchmark rows use `RESERVED_EVALUATION`. Rows removed by deduplication, document holdout,
or the post-selection near-duplicate purge use `QUARANTINED`. A quarantined row belongs to neither
the training pool nor an evaluation set. Both allocations remain protected after annotations change.

The importer's canonical Parquet records `document_id`, selection features, `evaluation_split`, and
`human_verify`. Its batch statistics record the frozen policy, random seed, quota and feature
outcomes, deduplication and allocation counts, selected-ID hash, and the Parquet SHA-256. Those
records replace standalone dev/test files and retain the script's audit trail without duplicating
the dataset.

## Operational consequences

The backend isolates `numpy`, `scikit-learn`, `datasketch`, `sentence-transformers`, and
CPU-compatible `torch` in its `evaluation` dependency group. This keeps an ordinary local API
installation lightweight; `contamination_safe` fails with an install instruction until the group is
present. The Docker build defaults to a slim local image; production passes
`--build-arg INSTALL_EVALUATION=true` to install the group. The policy enables MinHash and LaBSE by
default. Both variants install only the committed `uv.lock` resolution. During the first eligible
import, sentence-transformers retrieves
`sentence-transformers/LaBSE` into the Hugging Face model cache when it is absent; later imports
reuse the cache. This is a model download, not a Docker image download. Operators can set
`embeddings.enabled: false` only when they intend to use the recorded TF-IDF fallback.

Document holdout can remove a large fraction of corpora with many chunks per document. Configure
`selection.max_test_documents` when the expected holdout would make the trainable pool too small.
The importer preserves deterministic decisions by deriving them from the configured seed and input
rows.
