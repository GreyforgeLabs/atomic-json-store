# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-09-06

### Added

- `AtomicJsonStore` with atomic temp-file-plus-`os.replace()` writes, fsync of file and directory, and private `0600` default file mode
- Cross-process advisory locking on a sidecar `<file>.lock` (shared for reads, exclusive for writes), re-entrant per thread, with configurable timeout
- Schema-versioned envelope (`format`, `schema_version`, `updated_at`, `data`) with ordered migrations, refusal of newer files, and version-0 adoption of plain JSON files
- `load`, `save`, `update`, `transaction`, `get`, `set`, `reset`, and `info`
- Corrupt-file policy: `raise` (default) or `quarantine`
- `atomic-json-store` CLI with `init`, `info`, `dump`, `get`, `set`, `delete`, dotted key paths, and distinct exit codes
- Test suite covering atomicity under failed replace, unserializable input, thread and multi-process increment counts, concurrent reader consistency, lock timeouts, migrations, and corruption handling
- GitHub Actions CI on Linux and macOS for Python 3.11, 3.12, and 3.13; tagged release workflow; manual PyPI publish workflow
- README, STARTHERE bootstrap, and idempotent setup script
