from __future__ import annotations

import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path

from .client import LocalExplorerClient
from .errors import ConflictError
from .manifest import Manifest, ManifestObject, manifest_path
from .models import LocalFile, R2Object
from .paths import is_internal_path, key_to_relative_path, relative_path_to_key


@dataclass
class SyncOptions:
    endpoint: str
    bucket: str
    root: Path
    stable_file_ms: int = 1000
    remote_poll_ms: int = 5000
    full_scan_ms: int = 60000
    dry_run: bool = False


@dataclass
class SyncStats:
    uploaded: int = 0
    downloaded: int = 0
    deleted_remote: int = 0
    deleted_local: int = 0
    skipped: int = 0
    conflicts: int = 0

    def changed(self) -> bool:
        return any(
            [
                self.uploaded,
                self.downloaded,
                self.deleted_remote,
                self.deleted_local,
                self.conflicts,
            ]
        )


class SyncEngine:
    def __init__(
        self,
        client: LocalExplorerClient,
        options: SyncOptions,
        *,
        printer=print,
    ) -> None:
        self.client = client
        self.options = options
        self.root = options.root
        self.printer = printer
        self.manifest_file = manifest_path(self.root)
        self.manifest = Manifest.load(
            self.manifest_file,
            bucket=options.bucket,
            endpoint=options.endpoint,
        )

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest.save(self.manifest_file)

    def pull(self) -> SyncStats:
        self.init()
        stats = SyncStats()
        remote = {
            obj.key: obj
            for obj in self.client.list_objects(self.options.bucket)
            if _is_file_object(obj)
        }
        local = scan_local(self.root)

        for key, obj in sorted(remote.items()):
            local_file = local.get(key)
            if local_file and _local_matches_manifest(
                local_file,
                self.manifest.objects.get(key),
            ):
                if self._download(key, obj):
                    stats.downloaded += 1
            elif local_file and key not in self.manifest.objects:
                self._write_conflict(key)
                stats.conflicts += 1
            else:
                if self._download(key, obj):
                    stats.downloaded += 1

        self._remove_manifest_entries_not_in(remote)
        self._save_manifest()
        return stats

    def push(self) -> SyncStats:
        self.init()
        stats = SyncStats()
        local = scan_local(self.root)
        remote = {
            obj.key: obj
            for obj in self.client.list_objects(self.options.bucket)
            if _is_file_object(obj)
        }

        for key, local_file in sorted(local.items()):
            manifest_obj = self.manifest.objects.get(key)
            remote_obj = remote.get(key)
            if (
                remote_obj
                and manifest_obj
                and _remote_changed(remote_obj, manifest_obj)
                and _local_changed(local_file, manifest_obj)
            ):
                raise ConflictError(f"local and remote both changed: {key}")
            if key not in remote or _local_changed(local_file, manifest_obj):
                if self._upload(local_file):
                    stats.uploaded += 1

        remote_deletes = [
            key
            for key in self.manifest.objects
            if key not in local and key in remote
        ]
        if remote_deletes:
            self._delete_remote(remote_deletes)
            stats.deleted_remote += len(remote_deletes)

        for key in list(self.manifest.objects):
            if key not in local:
                self.manifest.objects.pop(key, None)

        self._save_manifest()
        return stats

    def sync_once(self) -> SyncStats:
        self.init()
        stats = SyncStats()
        local = scan_local(self.root)
        remote = {
            obj.key: obj
            for obj in self.client.list_objects(self.options.bucket)
            if _is_file_object(obj)
        }

        keys = sorted(set(local) | set(remote) | set(self.manifest.objects))
        remote_deletes: list[str] = []

        for key in keys:
            local_file = local.get(key)
            remote_obj = remote.get(key)
            manifest_obj = self.manifest.objects.get(key)

            local_exists = local_file is not None
            remote_exists = remote_obj is not None
            known = manifest_obj is not None

            if local_exists and remote_exists:
                local_changed = _local_changed(local_file, manifest_obj)
                remote_changed = _remote_changed(remote_obj, manifest_obj)
                if local_changed and remote_changed and known:
                    self._write_conflict(key)
                    stats.conflicts += 1
                elif local_changed:
                    if self._upload(local_file):
                        stats.uploaded += 1
                elif remote_changed:
                    if self._download(key, remote_obj):
                        stats.downloaded += 1
                else:
                    self._remember(key, remote_obj, local_file)
                continue

            if local_exists and not remote_exists:
                if known and not _local_changed(local_file, manifest_obj):
                    self._delete_local(key)
                    self.manifest.objects.pop(key, None)
                    stats.deleted_local += 1
                else:
                    if self._upload(local_file):
                        stats.uploaded += 1
                continue

            if remote_exists and not local_exists:
                if known:
                    remote_deletes.append(key)
                else:
                    if self._download(key, remote_obj):
                        stats.downloaded += 1
                continue

            if known:
                self.manifest.objects.pop(key, None)

        if remote_deletes:
            self._delete_remote(remote_deletes)
            stats.deleted_remote += len(remote_deletes)

        self._save_manifest()
        return stats

    def watch(self) -> None:
        self.init()
        self.printer(
            f"Watching {self.root} <-> {self.options.bucket} "
            f"at {self.options.endpoint}"
        )
        while True:
            try:
                stats = self.sync_once()
                if stats.changed():
                    self.printer(format_stats(stats))
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.printer(f"error: {exc}")
            time.sleep(max(self.options.remote_poll_ms, 250) / 1000)

    def _upload(self, local_file: LocalFile) -> bool:
        path = Path(local_file.path)
        if not wait_until_stable(path, self.options.stable_file_ms):
            self.printer(f"skip unstable file: {path}")
            return False
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if self.options.dry_run:
            self.printer(f"would upload {local_file.key}")
            return True
        body = path.read_bytes()
        obj = self.client.put_object(
            self.options.bucket,
            local_file.key,
            body,
            content_type=content_type,
        )
        stat = path.stat()
        self._remember(
            local_file.key,
            obj,
            LocalFile(
                key=local_file.key,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                path=str(path),
            ),
        )
        return True

    def _download(self, key: str, obj: R2Object) -> bool:
        rel = key_to_relative_path(key)
        target = self.root / rel
        if self.options.dry_run:
            self.printer(f"would download {key}")
            return True
        body, _content_type = self.client.get_object(self.options.bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".r2tmp")
        tmp.write_bytes(body)
        tmp.replace(target)
        stat = target.stat()
        self._remember(
            key,
            obj,
            LocalFile(
                key=key,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                path=str(target),
            ),
        )
        return True

    def _delete_remote(self, keys: list[str]) -> None:
        if self.options.dry_run:
            self.printer(f"would delete remote: {', '.join(keys)}")
            return
        self.client.delete_objects(self.options.bucket, keys)
        for key in keys:
            self.manifest.objects.pop(key, None)

    def _delete_local(self, key: str) -> None:
        path = self.root / key_to_relative_path(key)
        if self.options.dry_run:
            self.printer(f"would delete local {path}")
            return
        if path.exists():
            path.unlink()
            _prune_empty_dirs(path.parent, self.root)

    def _write_conflict(self, key: str) -> None:
        if self.options.dry_run:
            self.printer(f"would preserve conflict for {key}")
            return
        body, _content_type = self.client.get_object(self.options.bucket, key)
        conflict_path = (
            self.root
            / ".r2-local-fs"
            / "conflicts"
            / f"{int(time.time())}"
            / key_to_relative_path(key)
        )
        conflict_path.parent.mkdir(parents=True, exist_ok=True)
        conflict_path.write_bytes(body)
        self.printer(f"conflict preserved remote copy at {conflict_path}")

    def _remember(self, key: str, obj: R2Object, local_file: LocalFile) -> None:
        self.manifest.objects[key] = ManifestObject(
            etag=obj.etag,
            size=obj.size,
            last_modified=obj.last_modified,
            local_mtime_ns=local_file.mtime_ns,
            local_size=local_file.size,
            synced_at=time.time(),
        )

    def _remove_manifest_entries_not_in(self, remote: dict[str, R2Object]) -> None:
        for key in list(self.manifest.objects):
            if key not in remote:
                self.manifest.objects.pop(key, None)

    def _save_manifest(self) -> None:
        if not self.options.dry_run:
            self.manifest.save(self.manifest_file)


def scan_local(root: Path) -> dict[str, LocalFile]:
    root = root.resolve()
    result: dict[str, LocalFile] = {}
    if not root.exists():
        return result
    for path in root.rglob("*"):
        if path.is_dir() or is_internal_path(root, path):
            continue
        rel = path.relative_to(root)
        try:
            key = relative_path_to_key(rel)
        except ValueError:
            continue
        stat = path.stat()
        result[key] = LocalFile(
            key=key,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            path=str(path),
        )
    return result


def wait_until_stable(path: Path, stable_file_ms: int) -> bool:
    try:
        before = path.stat()
    except FileNotFoundError:
        return False
    if stable_file_ms <= 0:
        return True
    time.sleep(stable_file_ms / 1000)
    try:
        after = path.stat()
    except FileNotFoundError:
        return False
    return (
        before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
    )


def format_stats(stats: SyncStats) -> str:
    parts = []
    if stats.uploaded:
        parts.append(f"uploaded={stats.uploaded}")
    if stats.downloaded:
        parts.append(f"downloaded={stats.downloaded}")
    if stats.deleted_remote:
        parts.append(f"deleted_remote={stats.deleted_remote}")
    if stats.deleted_local:
        parts.append(f"deleted_local={stats.deleted_local}")
    if stats.conflicts:
        parts.append(f"conflicts={stats.conflicts}")
    if not parts:
        parts.append("no changes")
    return "sync: " + " ".join(parts)


def _is_file_object(obj: R2Object) -> bool:
    return not obj.key.endswith("/")


def _local_changed(local_file: LocalFile, manifest_obj: ManifestObject | None) -> bool:
    if manifest_obj is None:
        return True
    return (
        manifest_obj.local_size != local_file.size
        or manifest_obj.local_mtime_ns != local_file.mtime_ns
    )


def _remote_changed(remote_obj: R2Object, manifest_obj: ManifestObject | None) -> bool:
    if manifest_obj is None:
        return True
    if manifest_obj.etag or remote_obj.etag:
        return manifest_obj.etag != remote_obj.etag or manifest_obj.size != remote_obj.size
    return (
        manifest_obj.size != remote_obj.size
        or manifest_obj.last_modified != remote_obj.last_modified
    )


def _local_matches_manifest(
    local_file: LocalFile,
    manifest_obj: ManifestObject | None,
) -> bool:
    return manifest_obj is not None and not _local_changed(local_file, manifest_obj)


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    current = start
    stop = stop.resolve()
    while current.resolve() != stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
