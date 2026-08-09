import os
from pathlib import Path
from urllib.parse import urlparse

import boto3
import duckdb
from botocore.config import Config

from storage.base import Storage


class S3Storage(Storage):
    """S3-compatible object storage.

    Covers Amazon S3, MinIO, and Google Cloud Storage through its S3
    interoperability endpoint (https://storage.googleapis.com + HMAC keys),
    so one implementation serves all three backends named in the spec.
    """

    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        access_key: str,
        secret_key: str,
        use_ssl: bool = True,
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.use_ssl = use_ssl
        endpoint_host = urlparse(endpoint_url).hostname if endpoint_url else None
        # Docker service names are private network hosts. Some deployment
        # environments inject HTTP(S)_PROXY globally; routing `minio` through
        # that proxy makes it unresolvable and turns every S3 call into a 502.
        # Keep proxy support for public S3/GCS endpoints.
        bypass_proxy = endpoint_host in {"minio", "localhost", "127.0.0.1", "::1"}
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                proxies={} if bypass_proxy else None,
            ),
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except Exception:
                # Bucket may be managed externally with no create permission.
                pass

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key.lstrip('/')}"

    def key_of(self, uri: str) -> str:
        return urlparse(uri).path.lstrip("/")

    def put_file(self, local_path: Path, key: str) -> str:
        self.client.upload_file(str(local_path), self.bucket, key.lstrip("/"))
        return self.uri(key)

    def create_multipart_upload(self, key: str) -> str:
        response = self.client.create_multipart_upload(Bucket=self.bucket, Key=key.lstrip("/"))
        return response["UploadId"]

    def upload_multipart_part(self, key: str, upload_id: str, part_number: int, file) -> str:
        response = self.client.upload_part(
            Bucket=self.bucket,
            Key=key.lstrip("/"),
            UploadId=upload_id,
            PartNumber=part_number,
            Body=file,
        )
        return response["ETag"]

    def complete_multipart_upload(
        self, key: str, upload_id: str, parts: list[dict[str, object]]
    ) -> str:
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key.lstrip("/"),
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
        return self.uri(key)

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        self.client.abort_multipart_upload(
            Bucket=self.bucket, Key=key.lstrip("/"), UploadId=upload_id
        )

    def get_file(self, key: str, local_path: Path) -> Path:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key.lstrip("/"), str(local_path))
        return Path(local_path)

    def read_bytes(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key.lstrip("/"))
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key.lstrip("/"))

    def delete_prefix(self, prefix: str) -> int:
        if not prefix.strip("/") or ".." in Path(prefix).parts:
            raise ValueError("refusing unsafe storage prefix")
        normalized = prefix.strip("/") + "/"
        deleted = 0

        def delete_objects(objects: list[dict[str, str]]) -> int:
            count = 0
            for start in range(0, len(objects), 1_000):
                chunk = objects[start : start + 1_000]
                if not chunk:
                    continue
                response = self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": chunk, "Quiet": True},
                )
                errors = response.get("Errors", [])
                if errors:
                    details = ", ".join(
                        f"{error.get('Key')}: {error.get('Message')}" for error in errors
                    )
                    raise RuntimeError(f"could not delete storage objects: {details}")
                count += len(chunk)
            return count

        # Listing versions works for versioned and unversioned S3-compatible
        # buckets. Delete markers count as versions and must be removed too, or
        # a purge would only hide old corpus objects instead of erasing them.
        version_paginator = self.client.get_paginator("list_object_versions")
        for page in version_paginator.paginate(Bucket=self.bucket, Prefix=normalized):
            versions = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]
            ]
            deleted += delete_objects(versions)

        # Some unversioned S3-compatible stores return no Versions entries.
        # Delete their current objects after the version pass.
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=normalized):
            current = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            deleted += delete_objects(current)
        return deleted

    def configure_duckdb(self, con: duckdb.DuckDBPyConnection) -> None:
        # DuckDB's httpfs extension parses the process's HTTP(S)_PROXY env vars as
        # soon as LOAD httpfs runs. Corporate proxies (often
        # `user:pass@host`) are not parseable by httpfs and abort every
        # connection, even to internal MinIO. Hide those vars precisely around
        # extension load, then restore them so boto3 (and anything else) still
        # sees the original proxy for public S3/GCS.
        proxy_keys = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
        saved_proxy = {k: os.environ[k] for k in proxy_keys if k in os.environ}
        for k in proxy_keys:
            os.environ.pop(k, None)
        try:
            # httpfs is installed into the image at build time. INSTALL checks
            # the remote extension repository even when the extension is already
            # present, so doing it per connection makes every DuckDB-backed API
            # request depend on extensions.duckdb.org being available.
            con.execute("LOAD httpfs;")
        finally:
            os.environ.update(saved_proxy)
        con.execute(f"SET s3_region='{self.region}';")
        con.execute(f"SET s3_access_key_id='{self.access_key}';")
        con.execute(f"SET s3_secret_access_key='{self.secret_key}';")
        con.execute(f"SET s3_use_ssl={'true' if self.use_ssl else 'false'};")
        con.execute("SET s3_url_style='path';")
        if self.endpoint_url:
            host = self.endpoint_url.split("://", 1)[-1].rstrip("/")
            con.execute(f"SET s3_endpoint='{host}';")
