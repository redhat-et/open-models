#!/usr/bin/env bash
# Run a script with the web app's venv (it has pyyaml, fastapi, etc).
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$DIR/web/.venv/bin/python" "$@"
