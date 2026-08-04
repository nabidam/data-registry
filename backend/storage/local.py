import shutil
from pathlib import Path

import duckdb

from storage.base import Storage


class LocalStorage(Storage):
    """Filesystem storage. Useful for single-node deployments and local dev."""

    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / key.lstrip("/")

    def uri(self, key: str) -> str:
        return str(self._path(key))

    def key_of(self, uri: str) -> str:
        return str(Path(uri).relative_to(self.root))

    def put_file(self, local_path: Path, key: str) -> str:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(local_path).resolve() != dest.resolve():
            shutil.copyfile(local_path, dest)
        return str(dest)

    def create_multipart_upload(self, key: str) -> str:
        raise RuntimeError("resumable imports require S3-compatible object storage")

    def upload_multipart_part(self, key: str, upload_id: str, part_number: int, file) -> str:
        raise RuntimeError("resumable imports require S3-compatible object storage")

    def complete_multipart_upload(
        self, key: str, upload_id: str, parts: list[dict[str, object]]
    ) -> str:
        raise RuntimeError("resumable imports require S3-compatible object storage")

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        return None

    def get_file(self, key: str, local_path: Path) -> Path:
        src = self._path(key)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, local_path)
        return Path(local_path)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def configure_duckdb(self, con: duckdb.DuckDBPyConnection) -> None:
        return None
