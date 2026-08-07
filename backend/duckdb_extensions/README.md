# Offline DuckDB extensions

Download the signed `httpfs` extension that exactly matches the DuckDB version
and container platform, and save it in this directory as:

```text
httpfs.duckdb_extension.gz
```

The current lockfile uses DuckDB 1.5.5 and the production image shown in the
error is Linux AMD64, so download:

```text
https://extensions.duckdb.org/v1.5.5/linux_amd64/httpfs.duckdb_extension.gz
```

The Docker build decompresses and installs this local artifact. If DuckDB or the
production architecture changes, replace the artifact with the matching build.
