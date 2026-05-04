from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .paths import expand_path


CONFIG_FILE = ".r2-local-fs.json"


@dataclass(frozen=True)
class ProjectConfig:
    endpoint: str
    buckets: dict[str, Path]

    @classmethod
    def load(cls, path: Path) -> "ProjectConfig | None":
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        endpoint = str(data.get("endpoint") or "http://localhost:8787")
        raw_buckets = data.get("buckets") or {}
        if not isinstance(raw_buckets, dict):
            raise ValueError(f"{path} has invalid buckets config")

        buckets = {
            str(bucket): expand_path(str(directory))
            for bucket, directory in raw_buckets.items()
        }
        return cls(endpoint=endpoint, buckets=buckets)

    def save(self, path: Path) -> None:
        data = {
            "endpoint": self.endpoint,
            "buckets": {
                bucket: str(directory)
                for bucket, directory in sorted(self.buckets.items())
            },
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        tmp.replace(path)
