#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

# Defaults keep the laptop behaviour: loopback only. A rented GPU box needs
# VIRME_MODEL_HOST=0.0.0.0 so the browser on another machine can reach it.
host="${VIRME_MODEL_HOST:-127.0.0.1}"
port="${VIRME_MODEL_PORT:-8766}"
workers="${VIRME_MODEL_WORKERS:-1}"

export PYTHONPATH="$project_root/model_service"
export PYTORCH_ENABLE_MPS_FALLBACK="${PYTORCH_ENABLE_MPS_FALLBACK:-1}"

python_bin="$project_root/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  echo "virtualenv missing; run scripts/setup-stylegan.sh first" >&2
  exit 1
fi

echo "serving StyleGAN on ${host}:${port} (device=${VIRME_DEVICE:-auto}, precision=${VIRME_PRECISION:-auto})"
exec "$project_root/.venv/bin/uvicorn" app:app \
  --app-dir model_service \
  --host "$host" \
  --port "$port" \
  --workers "$workers"
