#!/usr/bin/env bash
# Pass-through wrapper: ./run.sh --target local --tests full --tag nightly
set -euo pipefail
cd "$(dirname "$0")"
exec python3 bench.py "$@"
