from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class R2Object:
    key: str
    size: int
    etag: str | None = None
    last_modified: str | None = None
    http_metadata: dict[str, object] = field(default_factory=dict)
    custom_metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_api(cls, payload: dict[str, object]) -> "R2Object":
        return cls(
            key=str(payload["key"]),
            size=int(payload.get("size") or 0),
            etag=payload.get("etag") and str(payload["etag"]),
            last_modified=payload.get("last_modified")
            and str(payload["last_modified"]),
            http_metadata=dict(payload.get("http_metadata") or {}),
            custom_metadata=dict(payload.get("custom_metadata") or {}),
        )


@dataclass
class LocalFile:
    key: str
    size: int
    mtime_ns: int
    path: str
