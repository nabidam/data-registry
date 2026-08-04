# Conventions

- Python modules use explicit, typed functions and Ruff's configured `E`, `F`, `I`, `UP`, and `B` rules.
- Import-time policies are deterministic: every random decision derives from the supplied seed and batch rows.
- Canonical Parquet may carry selection annotations; Postgres remains metadata-only.
- Training builders include only `TRAINABLE`; evaluation builders include only `RESERVED_EVALUATION`.
- Normal verification is `cd backend && uv run ruff check .` and `cd frontend && yarn build`. Do not run verification when a user explicitly requests implementation only.
- Commit subjects use `<type>: <summary> [cycle/U#]`.
