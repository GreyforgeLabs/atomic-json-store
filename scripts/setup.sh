#!/usr/bin/env bash
set -euo pipefail

# atomic-json-store setup script
# Idempotent - safe to run multiple times

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== atomic-json-store Setup ==="

check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo "ERROR: $1 is required but not installed."
        exit 1
    fi
}

check_command python3

PYTHON_OK="$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))')"
if [ "$PYTHON_OK" != "1" ]; then
    echo "ERROR: Python 3.11 or newer is required (found $(python3 --version))."
    exit 1
fi

cd "$PROJECT_DIR"

if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e ".[dev]"

echo "=== Setup complete ==="

echo "Running verification..."
atomic-json-store --version
python -m pytest -q
echo "OK: atomic-json-store is installed in .venv and the test suite passes."
