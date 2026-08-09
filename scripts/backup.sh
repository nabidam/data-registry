#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: BACKUP_QUIET_CONFIRMED=YES MTREG_OBJECT_SOURCE=alias/bucket $0 BACKUP_DIRECTORY" >&2
  exit 2
fi

if [[ "${BACKUP_QUIET_CONFIRMED:-}" != "YES" ]]; then
  echo "refusing backup: finish imports, reservations, snapshots, and purges, then set BACKUP_QUIET_CONFIRMED=YES" >&2
  exit 2
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required" >&2
  exit 2
fi

if [[ -z "${MTREG_OBJECT_SOURCE:-}" ]]; then
  echo "MTREG_OBJECT_SOURCE is required, for example local/mtreg" >&2
  exit 2
fi

for command_name in pg_dump pg_restore mc sha256sum; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "required command not found: $command_name" >&2
    exit 2
  fi
done

backup_root=$1
if [[ -z "$backup_root" || "$backup_root" == "/" ]]; then
  echo "choose a dedicated backup directory" >&2
  exit 2
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_name="mtdataregistry-${timestamp}"
partial_path="${backup_root}/.${backup_name}.partial"
final_path="${backup_root}/${backup_name}"
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
database_url=${DATABASE_URL/postgresql+psycopg:/postgresql:}

mkdir -p "$backup_root"
if [[ -e "$partial_path" || -e "$final_path" ]]; then
  echo "backup path already exists for timestamp ${timestamp}" >&2
  exit 1
fi
mkdir -m 700 "$partial_path"

cleanup_partial() {
  if [[ -d "$partial_path" ]]; then
    echo "backup failed; incomplete files remain at $partial_path" >&2
  fi
}
trap cleanup_partial EXIT

echo "exporting PostgreSQL metadata"
pg_dump --format=custom --file "$partial_path/postgres.dump" "$database_url"
pg_restore --list "$partial_path/postgres.dump" > "$partial_path/postgres-contents.txt"

echo "mirroring current object versions from ${MTREG_OBJECT_SOURCE}"
mkdir "$partial_path/objects"
mc mirror "$MTREG_OBJECT_SOURCE" "$partial_path/objects"

git_commit=unknown
if command -v git >/dev/null 2>&1; then
  git_commit=$(git -C "$project_root" rev-parse HEAD 2>/dev/null || echo unknown)
fi

cat > "$partial_path/backup-manifest.txt" <<EOF
created_at_utc=${timestamp}
git_commit=${git_commit}
object_source=${MTREG_OBJECT_SOURCE}
object_versions=current-only
EOF

echo "computing checksums"
(
  cd "$partial_path"
  sha256sum postgres.dump postgres-contents.txt backup-manifest.txt
  find objects -type f -print0 | sort -z | xargs -0 -r sha256sum
) > "$partial_path/SHA256SUMS"

mv "$partial_path" "$final_path"
trap - EXIT
echo "backup complete: $final_path"
