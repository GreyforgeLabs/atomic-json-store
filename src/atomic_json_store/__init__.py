"""Atomic, cross-process locked, schema-versioned JSON persistence."""

from atomic_json_store.core import (
    FORMAT,
    LEGACY_VERSION,
    AtomicJsonStore,
    CorruptStoreError,
    LockTimeoutError,
    SchemaVersionError,
    StoreError,
    StoreInfo,
)

__version__ = "1.0.0"

__all__ = [
    "FORMAT",
    "LEGACY_VERSION",
    "AtomicJsonStore",
    "CorruptStoreError",
    "LockTimeoutError",
    "SchemaVersionError",
    "StoreError",
    "StoreInfo",
    "__version__",
]
