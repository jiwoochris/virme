#!/usr/bin/env bash
set -euo pipefail

# Kept so `bun run model:setup` stays working. The shared script installs the CUDA
# wheels when it finds a GPU and the plain wheels otherwise.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/setup-stylegan.sh" "$@"
