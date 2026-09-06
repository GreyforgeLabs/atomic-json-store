"""Command-line interface for atomic-json-store."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from atomic_json_store import __version__
from atomic_json_store.core import LEGACY_VERSION, AtomicJsonStore, StoreError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_MISSING = 3


class KeyPathError(StoreError):
    """A dotted key path does not resolve inside the document."""


def _split_path(key: str) -> list[str]:
    parts = key.split(".") if key else []
    if not parts or any(part == "" for part in parts):
        raise KeyPathError(f"invalid key path {key!r}")
    return parts


def _descend(node: Any, part: str, key: str) -> Any:
    if isinstance(node, dict):
        if part not in node:
            raise KeyPathError(f"key path {key!r} not found")
        return node[part]
    if isinstance(node, list):
        try:
            return node[int(part)]
        except (ValueError, IndexError) as exc:
            raise KeyPathError(f"key path {key!r} not found") from exc
    raise KeyPathError(f"key path {key!r} does not resolve through a scalar")


def get_path(data: Any, key: str) -> Any:
    node = data
    for part in _split_path(key):
        node = _descend(node, part, key)
    return node


def set_path(data: Any, key: str, value: Any) -> None:
    parts = _split_path(key)
    node = data
    for part in parts[:-1]:
        if isinstance(node, dict):
            child = node.get(part)
            if child is None:
                child = node[part] = {}
            node = child
        elif isinstance(node, list):
            node = _descend(node, part, key)
        else:
            raise KeyPathError(f"key path {key!r} does not resolve through a scalar")
    last = parts[-1]
    if isinstance(node, dict):
        node[last] = value
    elif isinstance(node, list):
        try:
            node[int(last)] = value
        except (ValueError, IndexError) as exc:
            raise KeyPathError(f"key path {key!r} not found") from exc
    else:
        raise KeyPathError(f"key path {key!r} does not resolve through a scalar")


def delete_path(data: Any, key: str) -> None:
    parts = _split_path(key)
    node = data
    for part in parts[:-1]:
        node = _descend(node, part, key)
    last = parts[-1]
    if isinstance(node, dict):
        if last not in node:
            raise KeyPathError(f"key path {key!r} not found")
        del node[last]
    elif isinstance(node, list):
        try:
            del node[int(last)]
        except (ValueError, IndexError) as exc:
            raise KeyPathError(f"key path {key!r} not found") from exc
    else:
        raise KeyPathError(f"key path {key!r} does not resolve through a scalar")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atomic-json-store",
        description="Inspect and edit an atomic-json-store document from the shell.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=10.0,
        help="seconds to wait for the store lock (default: 10)",
    )
    parser.add_argument("file", help="path to the JSON store")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create the store if it does not exist")
    init.add_argument("--schema-version", type=int, default=1)

    commands.add_parser("info", help="print envelope metadata as JSON")
    commands.add_parser("dump", help="print the document as JSON")

    get = commands.add_parser("get", help="print the value at a dotted key path")
    get.add_argument("key")
    get.add_argument("--default", help="JSON value to print when the key is missing")

    set_ = commands.add_parser("set", help="write a value at a dotted key path")
    set_.add_argument("key")
    set_.add_argument("value")
    set_.add_argument("--json", action="store_true", help="parse VALUE as JSON instead of text")

    delete = commands.add_parser("delete", help="remove a dotted key path")
    delete.add_argument("key")
    return parser


def _open(path: str, lock_timeout: float, *, create_version: int | None = None) -> AtomicJsonStore:
    probe = AtomicJsonStore(path, lock_timeout=lock_timeout)
    info = probe.info()
    if info.exists:
        if info.schema_version is None:
            raise StoreError(f"{path} is not a readable atomic-json-store document")
        version = info.schema_version
    elif create_version is not None:
        version = create_version
    else:
        raise StoreError(f"{path} does not exist")
    if version == LEGACY_VERSION and info.format is None and info.exists:
        # Adopt a plain JSON file at version 0 without forcing a migration.
        return AtomicJsonStore(path, schema_version=LEGACY_VERSION, lock_timeout=lock_timeout)
    return AtomicJsonStore(path, schema_version=version, lock_timeout=lock_timeout)


def _emit(value: Any) -> None:
    sys.stdout.write(json.dumps(value, indent=2, ensure_ascii=False))
    sys.stdout.write("\n")


def run(args: argparse.Namespace) -> int:
    if args.command == "init":
        if args.schema_version < 0:
            raise StoreError("--schema-version must be >= 0")
        store = AtomicJsonStore(
            args.file, schema_version=args.schema_version, lock_timeout=args.lock_timeout
        )
        if not store.exists():
            store.save({})
        _emit(asdict(store.info()) | {"path": str(store.path)})
        return EXIT_OK

    if args.command == "info":
        info = AtomicJsonStore(args.file, lock_timeout=args.lock_timeout).info()
        _emit(asdict(info) | {"path": str(info.path)})
        return EXIT_OK

    if args.command == "dump":
        _emit(_open(args.file, args.lock_timeout).load())
        return EXIT_OK

    if args.command == "get":
        data = _open(args.file, args.lock_timeout).load()
        try:
            _emit(get_path(data, args.key))
        except KeyPathError:
            if args.default is None:
                sys.stderr.write(f"atomic-json-store: {args.key}: not found\n")
                return EXIT_MISSING
            _emit(json.loads(args.default))
        return EXIT_OK

    if args.command == "set":
        value: Any = json.loads(args.value) if args.json else args.value
        store = _open(args.file, args.lock_timeout, create_version=1)

        def apply(data: Any) -> None:
            set_path(data, args.key, value)

        store.update(apply)
        return EXIT_OK

    if args.command == "delete":
        store = _open(args.file, args.lock_timeout)
        try:

            def apply(data: Any) -> None:
                delete_path(data, args.key)

            store.update(apply)
        except KeyPathError:
            sys.stderr.write(f"atomic-json-store: {args.key}: not found\n")
            return EXIT_MISSING
        return EXIT_OK

    raise StoreError(f"unknown command {args.command}")  # pragma: no cover


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"atomic-json-store: invalid JSON value: {exc}\n")
        return EXIT_USAGE
    except (StoreError, TypeError, OSError) as exc:
        sys.stderr.write(f"atomic-json-store: {exc}\n")
        return EXIT_ERROR
