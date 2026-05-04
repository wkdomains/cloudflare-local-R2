from __future__ import annotations

import os
from pathlib import Path


STATE_DIR_NAME = ".r2-local-fs"


def expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def key_to_relative_path(key: str) -> Path:
    """Convert an R2 key to a safe relative filesystem path."""
    if key.startswith("/") or "\x00" in key:
        raise ValueError(f"unsafe R2 key: {key!r}")

    parts = []
    for part in key.split("/"):
        if part in {"", ".", ".."}:
            raise ValueError(f"unsafe R2 key segment in {key!r}")
        parts.append(part)
    return Path(*parts)


def relative_path_to_key(path: Path) -> str:
    """Convert a relative filesystem path to an R2 key."""
    if path.is_absolute():
        raise ValueError(f"path must be relative: {path}")

    parts = []
    for part in path.parts:
        if part in {"", ".", "..", STATE_DIR_NAME}:
            raise ValueError(f"unsafe local path segment in {path}")
        parts.append(part)
    return "/".join(parts)


def is_internal_path(root: Path, path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] == STATE_DIR_NAME
