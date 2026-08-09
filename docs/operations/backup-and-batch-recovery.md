# Backup and Batch Recovery

## Protection model

The registry uses two recovery levels:

1. Reject or purge one erroneous, unused batch.
2. Restore PostgreSQL and object storage from a coordinated backup after wider
   corruption or operator error.

Batch rejection should handle most import mistakes. Full restore rewinds the
whole registry and can discard unrelated work created after the backup.

## Reject before purge

A rejected batch remains in PostgreSQL and object storage, but dataset builders
stop reading it because they query ready batches. Existing snapshots keep their
files and manifests.

The Batches page requires the batch name and a reason before rejection. You can
restore a rejected batch while its Parquet objects remain present.

Use rejection while investigating an import. Purge only after the dry run shows
no blockers.

## Purge rules

The purge API accepts failed or rejected batches. A ready batch must go through
rejection first. The dry run blocks deletion when it finds:

- an explicit dataset-definition reference;
- selected samples in a dataset reservation;
- rows from the batch in a snapshot or evaluation-set Parquet artifact;
- an active dataset reservation or snapshot build;
- an artifact that the service could not inspect.

Datasets configured to use all ready batches appear as warnings. Rejection has
already removed the target batch from their query results.

After a clean dry run, purge deletes the raw upload, every import-attempt prefix,
annotations, reservation membership, sample metadata, batch statistics, and the
batch row. A small audit row records the batch ID, name, reason, and purge time.
It stores no sample text, allocation statistics, or object locations.

For S3-compatible versioned buckets, purge removes every object version and
delete marker below the batch prefixes. The operation stops before deleting
PostgreSQL metadata if its credentials cannot list or delete those versions.

Purge cannot run as one transaction across PostgreSQL and S3-compatible
storage. The service deletes the two batch-specific storage prefixes first,
then removes metadata in one PostgreSQL transaction. A storage failure stops
the metadata deletion. Retrying the purge is safe because prefix deletion is
idempotent.

## Create a coordinated backup

The backup consists of a PostgreSQL custom-format dump and a mirror of the
current object versions. Store it on another disk or host. A copy inside the
same Docker volume does not protect against host or volume loss.

Before running the script:

1. Finish active imports, dataset reservations, snapshot builds, and purges.
2. Prevent administrators from starting a purge until the script finishes.
3. Configure an `mc` alias for the source MinIO, S3, or GCS bucket.
4. Load `DATABASE_URL` without printing it to the terminal.

Run:

```bash
BACKUP_QUIET_CONFIRMED=YES \
MTREG_OBJECT_SOURCE=production/mtreg \
./scripts/backup.sh /mnt/registry-backups
```

The script writes into a hidden partial directory and renames it after the
database dump, object mirror, and checksums succeed. Each completed directory
contains:

```text
postgres.dump
postgres-contents.txt
objects/
backup-manifest.txt
SHA256SUMS
```

`mc mirror` copies current object versions. Use bucket replication when the
backup must preserve MinIO version history.

## Verify and restore

Test restoration in a separate environment:

1. Run `sha256sum -c SHA256SUMS` from the backup directory.
2. Create an empty PostgreSQL database.
3. Restore `postgres.dump` with `pg_restore`.
4. Mirror `objects/` into an empty object bucket.
5. Start the backend at the Git commit from `backup-manifest.txt`.
6. Run migrations only after confirming the restored application starts at
   that commit.
7. Open several batches, snapshots, and evaluation sets and read their Parquet
   rows.

Restore into the live database or bucket only after the isolated restore passes
these checks. Keep multiple dated backups and test a restore on a schedule.
