class R2LocalFSError(Exception):
    """Base exception for expected r2-local-fs failures."""


class ApiError(R2LocalFSError):
    """Raised when Local Explorer returns an unsuccessful response."""


class ConflictError(R2LocalFSError):
    """Raised when local and remote changes conflict."""
