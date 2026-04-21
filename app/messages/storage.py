"""Raw .eml file storage abstraction.

Stores messages on the local filesystem using a two-level directory structure
derived from the SHA-256 hash:

    <root>/<first-2-hex-chars>/<full-sha256>.eml

This layout avoids large flat directories while keeping the storage reference
(a relative path string) stable and human-readable.

Design choices for Phase 3:
  - Plain filesystem storage (no encryption yet).
  - Phase 5 (quarantine) will extend this with Fernet-encrypted blobs.
  - Writes are idempotent: if a message with the same SHA-256 already exists,
    the write is a no-op and the same path is returned.
  - A tmp-file rename pattern prevents partial writes.

Callers receive (sha256_hex, relative_path) so both values can be persisted
in the database without further computation.
"""

import hashlib
from pathlib import Path


class RawMessageStore:
    """Local filesystem store for raw .eml files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(self, raw_bytes: bytes) -> tuple[str, str]:
        """Persist raw message bytes and return (sha256_hex, relative_path).

        If a message with the same SHA-256 already exists the write is skipped
        and the existing path is returned (idempotent).
        """
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        relative_path = f"{sha256[:2]}/{sha256}.eml"
        full_path = self.root / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        if not full_path.exists():
            # Write via a temporary file to avoid a partial write being visible.
            tmp = full_path.with_suffix(".tmp")
            tmp.write_bytes(raw_bytes)
            tmp.replace(full_path)
        return sha256, relative_path

    def read(self, relative_path: str) -> bytes:
        """Read and return raw message bytes from the store."""
        return (self.root / relative_path).read_bytes()

    def exists(self, sha256: str) -> bool:
        """Return True if a message with the given SHA-256 is already stored."""
        return (self.root / sha256[:2] / f"{sha256}.eml").exists()


def get_raw_message_store(settings) -> RawMessageStore:  # type: ignore[no-untyped-def]
    """Create a RawMessageStore rooted at the path configured in settings."""
    return RawMessageStore(settings.raw_message_store_path)
