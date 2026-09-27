"""Atomic, cross-process locked, schema-versioned JSON persistence.

Every write serializes the document first, then writes a temporary file in the
target directory, fsyncs it, and publishes it with ``os.replace``. Readers
therefore see either the previous complete document or the new one, never a
partial file. Writers coordinate through an advisory lock on a sidecar
``<name>.lock`` file so read-modify-write cycles from separate processes do not
lose updates.
"""

from __future__ import annotations

import contextlib
import copy
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

FORMAT = "atomic-json-store/1"
LEGACY_VERSION = 0

Migration = Callable[[Any], Any]
CorruptPolicy = Literal["raise", "quarantine"]


class StoreError(Exception):
    """Base class for atomic-json-store failures."""


class LockTimeoutError(StoreError, TimeoutError):
    """The store lock could not be acquired before the timeout elapsed."""


class CorruptStoreError(StoreError):
    """The store file exists but is not a readable document."""


class SchemaVersionError(StoreError):
    """The file's schema version cannot be reconciled with the store's version."""


@dataclass(frozen=True)
class StoreInfo:
    """Envelope metadata read without touching the payload."""

    path: Path
    exists: bool
    format: str | None
    schema_version: int | None
    updated_at: str | None
    size_bytes: int


if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
    import msvcrt

    def _try_lock(fd: int, exclusive: bool) -> bool:
        del exclusive  # msvcrt offers only exclusive byte-range locks.
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    def _fsync_directory(path: Path) -> None:
        del path

else:
    import fcntl

    def _try_lock(fd: int, exclusive: bool) -> bool:
        flag = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flag)
        except (BlockingIOError, PermissionError):
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class _LockState(threading.local):
    depth = 0
    exclusive = False


class _FileLock:
    """Re-entrant (per thread) advisory lock backed by a sidecar file."""

    def __init__(self, path: Path, timeout: float | None) -> None:
        self._path = path
        self._timeout = timeout
        self._state = _LockState()

    @contextlib.contextmanager
    def held(self, *, exclusive: bool) -> Iterator[None]:
        state = self._state
        if state.depth:
            if exclusive and not state.exclusive:
                raise StoreError("a shared lock cannot be upgraded to exclusive while held")
            state.depth += 1
            try:
                yield
            finally:
                state.depth -= 1
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            self._acquire(fd, exclusive)
            state.depth = 1
            state.exclusive = exclusive
            try:
                yield
            finally:
                state.depth = 0
                state.exclusive = False
                _unlock(fd)
        finally:
            os.close(fd)

    def _acquire(self, fd: int, exclusive: bool) -> None:
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        delay = 0.002
        while not _try_lock(fd, exclusive):
            if deadline is not None and time.monotonic() >= deadline:
                kind = "exclusive" if exclusive else "shared"
                raise LockTimeoutError(
                    f"timed out after {self._timeout}s waiting for {kind} lock on {self._path}"
                )
            time.sleep(delay)
            delay = min(delay * 2, 0.05)


class _NeedsExclusive(Exception):
    """Internal signal: a shared read discovered work that requires a write."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class AtomicJsonStore:
    """A JSON document on disk with atomic writes, locking, and schema migrations.

    ``schema_version`` is the version this program expects. Files written at an
    older version are upgraded through ``migrations`` (a mapping from a version to
    the function that produces the next version) when they are next loaded.
    Files written at a newer version are refused so an old binary never silently
    downgrades data it does not understand.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        schema_version: int = 1,
        migrations: Mapping[int, Migration] | None = None,
        default: Any = dict,
        lock_timeout: float | None = 10.0,
        indent: int | None = 2,
        sort_keys: bool = False,
        ensure_ascii: bool = False,
        fsync: bool = True,
        on_corrupt: CorruptPolicy = "raise",
        file_mode: int | None = None,
        encoder: type[json.JSONEncoder] | None = None,
        decoder: type[json.JSONDecoder] | None = None,
    ) -> None:
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise TypeError("schema_version must be an int")
        if schema_version < 0:
            raise ValueError("schema_version must be >= 0")
        if lock_timeout is not None and lock_timeout < 0:
            raise ValueError("lock_timeout must be >= 0 or None")
        if on_corrupt not in ("raise", "quarantine"):
            raise ValueError("on_corrupt must be 'raise' or 'quarantine'")
        self._migrations: dict[int, Migration] = {}
        for key, fn in (migrations or {}).items():
            if isinstance(key, bool) or not isinstance(key, int) or key < 0:
                raise TypeError(f"migration keys must be non-negative ints, got {key!r}")
            if not callable(fn):
                raise TypeError(f"migration for version {key} is not callable")
            self._migrations[key] = fn

        self._path = Path(path)
        self._lock_path = self._path.with_name(self._path.name + ".lock")
        self._lock = _FileLock(self._lock_path, lock_timeout)
        self._schema_version = schema_version
        self._default = default
        self._indent = indent
        self._sort_keys = sort_keys
        self._ensure_ascii = ensure_ascii
        self._fsync = fsync
        self._on_corrupt: CorruptPolicy = on_corrupt
        self._file_mode = file_mode
        self._encoder = encoder
        self._decoder = decoder

    # ── Introspection ────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @property
    def schema_version(self) -> int:
        return self._schema_version

    def exists(self) -> bool:
        return self._path.is_file()

    def info(self) -> StoreInfo:
        """Read envelope metadata. Never migrates, never writes."""
        # Writers replace the whole file atomically, so an opened descriptor sees
        # one complete generation without creating the sidecar lock or parent.
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return StoreInfo(self._path, False, None, None, None, 0)
        try:
            document = json.loads(raw, cls=self._decoder)
        except ValueError:
            return StoreInfo(self._path, True, None, None, None, len(raw))
        if not self._is_envelope(document):
            return StoreInfo(self._path, True, None, LEGACY_VERSION, None, len(raw))
        version = document.get("schema_version")
        updated = document.get("updated_at")
        return StoreInfo(
            self._path,
            True,
            FORMAT,
            version if self._valid_version(version) else None,
            updated if isinstance(updated, str) else None,
            len(raw),
        )

    # ── Read / write ─────────────────────────────────────────────────────

    def load(self) -> Any:
        """Return the current document, migrating and persisting it if needed."""
        with self._lock.held(exclusive=False):
            try:
                return self._read(repair=False)
            except _NeedsExclusive:
                pass
        with self._lock.held(exclusive=True):
            return self._read(repair=True)

    def save(self, data: Any) -> None:
        """Replace the document atomically."""
        with self._lock.held(exclusive=True):
            self._write(data)

    def update(self, fn: Callable[[Any], Any]) -> Any:
        """Apply ``fn`` to the current document under the exclusive lock.

        ``fn`` may mutate its argument in place and return ``None``, or return a
        replacement document. The stored result is returned.
        """
        with self._lock.held(exclusive=True):
            data = self._read(repair=True)
            result = fn(data)
            data = data if result is None else result
            self._write(data)
            return data

    @contextlib.contextmanager
    def transaction(self) -> Iterator[Any]:
        """Yield the document for in-place mutation; commit on clean exit only."""
        with self._lock.held(exclusive=True):
            data = self._read(repair=True)
            yield data
            self._write(data)

    def get(self, key: str, default: Any = None) -> Any:
        """Read one top-level key of a mapping document."""
        data = self.load()
        if not isinstance(data, Mapping):
            raise TypeError("get() requires a mapping document")
        return data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Write one top-level key of a mapping document."""

        def apply(data: Any) -> None:
            if not isinstance(data, dict):
                raise TypeError("set() requires a mapping document")
            data[key] = value

        self.update(apply)

    def reset(self) -> Any:
        """Replace the document with a fresh default and return it."""
        with self._lock.held(exclusive=True):
            data = self._fresh()
            self._write(data)
            return data

    # ── Internals (call only while holding the lock) ─────────────────────

    def _fresh(self) -> Any:
        if callable(self._default):
            return self._default()
        return copy.deepcopy(self._default)

    @staticmethod
    def _valid_version(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    @staticmethod
    def _is_envelope(document: Any) -> bool:
        return isinstance(document, dict) and document.get("format") == FORMAT

    def _read(self, *, repair: bool) -> Any:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return self._fresh()
        try:
            document = json.loads(raw, cls=self._decoder)
        except ValueError as exc:
            return self._corrupt(f"invalid JSON: {exc}", repair)
        if self._is_envelope(document):
            version = document.get("schema_version")
            if not self._valid_version(version) or "data" not in document:
                return self._corrupt("envelope is missing schema_version or data", repair)
            data = document["data"]
        else:
            version = LEGACY_VERSION
            data = document
        if version == self._schema_version:
            return data
        if version > self._schema_version:
            raise SchemaVersionError(
                f"{self._path} is at schema version {version}, newer than the supported "
                f"version {self._schema_version}"
            )
        if not repair:
            raise _NeedsExclusive
        migrated = self._migrate(data, version)
        self._write(migrated)
        return migrated

    def _corrupt(self, reason: str, repair: bool) -> Any:
        if self._on_corrupt == "raise":
            raise CorruptStoreError(f"{self._path}: {reason}")
        if not repair:
            raise _NeedsExclusive
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        os.replace(self._path, quarantine)
        data = self._fresh()
        self._write(data)
        return data

    def _migrate(self, data: Any, version: int) -> Any:
        for step in range(version, self._schema_version):
            fn = self._migrations.get(step)
            if fn is None:
                raise SchemaVersionError(
                    f"{self._path} is at schema version {version} and no migration is "
                    f"registered for version {step} -> {step + 1}"
                )
            data = fn(data)
            if data is None:
                raise SchemaVersionError(
                    f"migration {step} -> {step + 1} returned None; return the migrated document"
                )
        return data

    def _write(self, data: Any) -> None:
        document = {
            "format": FORMAT,
            "schema_version": self._schema_version,
            "updated_at": _utc_now(),
            "data": data,
        }
        # Serialize before touching the filesystem so a TypeError leaves the store intact.
        text = json.dumps(
            document,
            indent=self._indent,
            sort_keys=self._sort_keys,
            ensure_ascii=self._ensure_ascii,
            cls=self._encoder,
        )
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        mode = self._file_mode
        if mode is None:
            try:
                mode = self._path.stat().st_mode & 0o777
            except FileNotFoundError:
                mode = 0o600
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=".tmp", dir=parent)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.write("\n")
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise
        if self._fsync:
            _fsync_directory(parent)
