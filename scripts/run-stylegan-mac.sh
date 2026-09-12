#!/usr/bin/env bash
set -euo pipefail

# Kept so `bun run model:serve` and the demo script stay working. The shared
# script picks the device on its own; on this laptop that lands on MPS.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run-stylegan.sh" "$@"
