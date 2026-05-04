from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import STATE_DIR_NAME


MANIFEST_VERSION = 1


@dataclass
class ManifestObject:
    etag: str | None = None
    size: int | None = None
    last_modified: str | None = None
    local_mtime_ns: int | None = None
    local_size: int | None = None
    synced_at: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ManifestObject":
        return cls(
            etag=data.get("etag") and str(data["etag"]),
            size=int(data["size"]) if data.get("size") is not None else None,
            last_modified=data.get("last_modified")
            and str(data["last_modified"]),
            local_mtime_ns=int(data["local_mtime_ns"])
            if data.get("local_mtime_ns") is not None
            else None,
            local_size=int(data["local_size"])
            if data.get("local_size") is not None
            else None,
            synced_at=float(data["synced_at"])
            if data.get("synced_at") is not None
            else None,
        )


@dataclass
class Manifest:
    bucket: str
    endpoint: str
    objects: dict[str, ManifestObject] = field(default_factory=dict)
    version: int = MANIFEST_VERSION

    @classmethod
    def load(cls, path: Path, bucket: str, endpoint: str) -> "Manifest":
        if not path.exists():
            return cls(bucket=bucket, endpoint=endpoint)

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        objects = {
            key: ManifestObject.from_dict(value)
            for key, value in dict(data.get("objects") or {}).items()
        }
        return cls(
            bucket=str(data.get("bucket") or bucket),
            endpoint=str(data.get("endpoint") or endpoint),
            objects=objects,
            version=int(data.get("version") or MANIFEST_VERSION),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data = {
            "version": self.version,
            "bucket": self.bucket,
            "endpoint": self.endpoint,
            "saved_at": time.time(),
            "objects": {
                key: asdict(value)
                for key, value in sorted(self.objects.items())
            },
        }
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        tmp.replace(path)


def manifest_path(root: Path) -> Path:
    return root / STATE_DIR_NAME / "manifest.json"
