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

# Uncomment and modify as needed:
# check_command python3
# check_command node
# check_command cargo

# Install dependencies
# Uncomment the relevant section:

# Python:
# cd "$PROJECT_DIR"
# python3 -m venv .venv
# source .venv/bin/activate
# pip install -r requirements.txt

# Node.js:
# cd "$PROJECT_DIR"
# npm install

# Rust:
# cd "$PROJECT_DIR"
# cargo build

# Setup environment
if [ ! -f "$PROJECT_DIR/.env" ] && [ -f "$PROJECT_DIR/.env.example" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "Created .env from .env.example - edit with your values."
fi

echo "=== Setup complete ==="

# Verification
# Uncomment and modify:
# echo "Running verification..."
# python3 -c "import atomic-json-store; print('OK')"
# npm test
# cargo test
