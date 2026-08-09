from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from core.errors import BadRequest
from services.batches.purge import inspect_batch_purge, purge_batch
from storage.local import LocalStorage
from storage.s3 import S3Storage


class _Rows:
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        return iter(self.values)

    def scalars(self):
        return iter(self.values)


class LocalStoragePrefixTests(TestCase):
    def test_delete_prefix_does_not_touch_similar_batch_id(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as root:
            storage = LocalStorage(root)
            source = Path(root) / "source.txt"
            source.write_text("safe")
            storage.put_file(source, "raw/batch_12/data.txt")
            storage.put_file(source, "raw/batch_12/nested/part.txt")
            storage.put_file(source, "raw/batch_120/keep.txt")

            self.assertEqual(storage.delete_prefix("raw/batch_12/"), 2)
            self.assertFalse((Path(root) / "raw/batch_12").exists())
            self.assertTrue((Path(root) / "raw/batch_120/keep.txt").exists())

    def test_delete_prefix_rejects_root(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as root:
            storage = LocalStorage(root)
            with self.assertRaises(ValueError):
                storage.delete_prefix("/")


class S3StoragePrefixTests(TestCase):
    def test_delete_prefix_removes_versions_and_delete_markers(self):
        version_paginator = MagicMock()
        version_paginator.paginate.return_value = [
            {
                "Versions": [{"Key": "raw/batch_12/data", "VersionId": "v1"}],
                "DeleteMarkers": [
                    {"Key": "raw/batch_12/old", "VersionId": "marker-1"}
                ],
            }
        ]
        current_paginator = MagicMock()
        current_paginator.paginate.return_value = []
        client = MagicMock()
        client.get_paginator.side_effect = [version_paginator, current_paginator]
        client.delete_objects.return_value = {}
        storage = S3Storage.__new__(S3Storage)
        storage.bucket = "mtreg"
        storage.client = client

        deleted = storage.delete_prefix("raw/batch_12/")

        self.assertEqual(deleted, 2)
        client.delete_objects.assert_called_once_with(
            Bucket="mtreg",
            Delete={
                "Objects": [
                    {"Key": "raw/batch_12/data", "VersionId": "v1"},
                    {"Key": "raw/batch_12/old", "VersionId": "marker-1"},
                ],
                "Quiet": True,
            },
        )
        version_paginator.paginate.assert_called_once_with(
            Bucket="mtreg", Prefix="raw/batch_12/"
        )


class BatchPurgeImpactTests(IsolatedAsyncioTestCase):
    @patch(
        "services.batches.purge._artifact_dependencies",
        new_callable=AsyncMock,
        return_value=([], []),
    )
    async def test_explicit_dataset_reference_blocks_purge(self, _artifacts):
        definition = SimpleNamespace(
            id=7,
            name="training",
            batch_ids=[12],
            batch_rules=[],
            filters={},
        )
        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[_Rows([]), _Rows([]), _Rows([definition]), _Rows([])]
        )
        session.scalar = AsyncMock(return_value=0)
        batch = SimpleNamespace(
            id=12,
            name="bad import",
            status="rejected",
            sample_count=100,
        )

        impact = await inspect_batch_purge(session, batch)

        self.assertFalse(impact.can_purge)
        self.assertIn("dataset #7 (training) explicitly references this batch", impact.blockers)

    @patch("services.batches.purge.inspect_batch_purge", new_callable=AsyncMock)
    @patch("services.batches.purge.lock_allocation_boundary", new_callable=AsyncMock)
    async def test_blocked_purge_does_not_delete_storage(self, _lock, inspect):
        inspect.return_value = SimpleNamespace(
            can_purge=False,
            blockers=["snapshot #4 contains this batch"],
        )
        batch = SimpleNamespace(id=12, name="bad import", status="rejected")
        session = MagicMock()
        session.scalar = AsyncMock(return_value=batch)

        with patch("services.batches.purge.get_storage") as get_storage:
            with self.assertRaises(BadRequest):
                await purge_batch(
                    session,
                    12,
                    confirm_name="bad import",
                    reason="the column mapping was wrong",
                )

        get_storage.assert_not_called()
