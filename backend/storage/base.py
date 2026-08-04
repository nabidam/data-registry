"""Small storage interface.

Only what the registry actually needs: put a local file, fetch a file back,
and tell DuckDB how to read an object. Everything else is out of scope.
"""

from abc import ABC, abstractmethod
from pathlib import Path

import duckdb


class Storage(ABC):
    @abstractmethod
    def uri(self, key: str) -> str:
        """Canonical URI stored in Postgres for a given object key."""

    @abstractmethod
    def put_file(self, local_path: Path, key: str) -> str:
        """Upload a local file, return its URI."""

    @abstractmethod
    def create_multipart_upload(self, key: str) -> str:
        """Start a resumable upload and return its opaque upload id."""

    @abstractmethod
    def upload_multipart_part(self, key: str, upload_id: str, part_number: int, file) -> str:
        """Stream one part and return the storage ETag needed to complete it."""

    @abstractmethod
    def complete_multipart_upload(
        self, key: str, upload_id: str, parts: list[dict[str, object]]
    ) -> str:
        """Finalize a multipart upload and return its canonical URI."""

    @abstractmethod
    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """Discard an incomplete multipart upload."""

    @abstractmethod
    def get_file(self, key: str, local_path: Path) -> Path:
        """Download an object to a local path."""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def configure_duckdb(self, con: duckdb.DuckDBPyConnection) -> None:
        """Give a DuckDB connection whatever it needs to read our URIs."""

    def key_of(self, uri: str) -> str:
        """Inverse of :meth:`uri`."""
        raise NotImplementedError
