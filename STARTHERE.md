# STARTHERE.md - AI Bootstrap Guide

> This file is designed for AI coding assistants. If you are a human,
> see [README.md](README.md) for the human-friendly guide.

## Quick Bootstrap

```bash
git clone https://github.com/GreyforgeLabs/atomic-json-store.git && cd atomic-json-store && ./scripts/setup.sh
```

## What This Project Does

Atomic, cross-process locked, schema-versioned JSON persistence for Python. One class (`AtomicJsonStore`) writes documents through temp-file-plus-rename, serializes read-modify-write cycles with an advisory sidecar lock, and upgrades old files through registered migrations. Standard library only.

## Project Structure

```text
atomic-json-store/
  src/atomic_json_store/
    __init__.py         # package version and public exports
    core.py             # AtomicJsonStore, lock, envelope, migrations
    cli.py              # argparse CLI (get/set/delete/dump/info/init)
    __main__.py         # python -m atomic_json_store support
  tests/
    test_core.py        # atomicity, locking, migrations, corruption, concurrency
    test_cli.py         # CLI behaviour and exit codes
  scripts/
    setup.sh            # idempotent install + verification
  .github/workflows/    # CI, tagged release, manual PyPI publish
  README.md             # human-facing docs
  STARTHERE.md          # this file
```

## Setup Prerequisites

- Python 3.11 or newer
- Linux or macOS (Windows locking is best-effort and not covered by CI)
- No system dependencies

## Installation Steps

1. Clone: `git clone https://github.com/GreyforgeLabs/atomic-json-store.git`
2. Enter directory: `cd atomic-json-store`
3. Run setup: `./scripts/setup.sh`

`setup.sh` creates `.venv`, installs the package in editable mode with dev extras, and runs the verification below.

## Verification

```bash
atomic-json-store --version
# Expected output: atomic-json-store 1.0.0
python -m pytest -q
# Expected output: all tests pass
```

## Key Entry Points

- `src/atomic_json_store/core.py` - `AtomicJsonStore` (`load`, `save`, `update`, `transaction`, `get`, `set`, `reset`, `info`)
- `src/atomic_json_store/cli.py` - `atomic-json-store` command

## Configuration

No configuration files or environment variables. All behaviour is set through constructor arguments (`schema_version`, `migrations`, `default`, `lock_timeout`, `on_corrupt`, `fsync`, `file_mode`) or CLI flags (`--lock-timeout`, `--schema-version`, `--json`, `--default`).

## Common Tasks

```bash
source .venv/bin/activate

# Run tests
python -m pytest

# Lint and format
python -m ruff check .
python -m ruff format .

# Build distribution
python -m build
```
