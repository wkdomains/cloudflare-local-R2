from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from r2_local_fs.models import R2Object
from r2_local_fs.sync import SyncEngine, SyncOptions


class FakeClient:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def list_objects(self, bucket: str, prefix: str = "") -> list[R2Object]:
        return [
            self._object(key, body)
            for key, body in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    def get_object(self, bucket: str, key: str) -> tuple[bytes, str | None]:
        return self.objects[key], "application/octet-stream"

    def put_object(
        self,
        bucket: str,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> R2Object:
        self.objects[key] = body
        return self._object(key, body)

    def delete_objects(self, bucket: str, keys: list[str]) -> None:
        for key in keys:
            self.deleted.append(key)
            self.objects.pop(key, None)

    def _object(self, key: str, body: bytes) -> R2Object:
        return R2Object(
            key=key,
            size=len(body),
            etag=hashlib.md5(body, usedforsecurity=False).hexdigest(),
            last_modified=str(len(body)),
        )


class SyncTests(unittest.TestCase):
    def test_push_uploads_local_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blog").mkdir()
            (root / "blog/foo.json").write_text('{"ok": true}', encoding="utf-8")
            client = FakeClient()
            engine = make_engine(client, root)

            stats = engine.push()

            self.assertEqual(stats.uploaded, 1)
            self.assertEqual(client.objects["blog/foo.json"], b'{"ok": true}')

    def test_sync_deletes_remote_when_local_file_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "asset.txt"
            path.write_text("one", encoding="utf-8")
            client = FakeClient()
            engine = make_engine(client, root)
            engine.push()

            path.unlink()
            stats = engine.sync_once()

            self.assertEqual(stats.deleted_remote, 1)
            self.assertNotIn("asset.txt", client.objects)
            self.assertEqual(client.deleted, ["asset.txt"])

    def test_local_delete_wins_even_if_remote_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "asset.txt"
            path.write_text("one", encoding="utf-8")
            client = FakeClient()
            engine = make_engine(client, root)
            engine.push()

            client.objects["asset.txt"] = b"remote changed"
            path.unlink()
            stats = engine.sync_once()

            self.assertEqual(stats.deleted_remote, 1)
            self.assertNotIn("asset.txt", client.objects)

    def test_sync_downloads_remote_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            client = FakeClient()
            client.objects["blog/bar.txt"] = b"remote"
            engine = make_engine(client, root)

            stats = engine.sync_once()

            self.assertEqual(stats.downloaded, 1)
            self.assertEqual((root / "blog/bar.txt").read_bytes(), b"remote")


def make_engine(client: FakeClient, root: Path) -> SyncEngine:
    options = SyncOptions(
        endpoint="http://localhost:8787",
        bucket="wk-prod",
        root=root,
        stable_file_ms=0,
    )
    return SyncEngine(client, options, printer=lambda _message: None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
