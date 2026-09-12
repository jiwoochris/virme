#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

packages=(torch torchvision pillow fastapi 'uvicorn[standard]' numpy scipy imageio imageio-ffmpeg 'dill>=0.4.1' 'timm==0.4.12' 'ftfy==6.1.1' regex requests tqdm)

# On a CUDA box the default wheel index gives a CPU-only torch, which loads the
# checkpoint and then runs the synthesis on the processor. Pick the CUDA wheels and
# add ninja, which builds the bias_act and upfirdn2d kernels the first time they run.
cuda_channel="${VIRME_CUDA_CHANNEL:-cu124}"
if [[ -n "${VIRME_FORCE_CPU_WHEELS:-}" ]]; then
  target="cpu"
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  target="cuda"
else
  target="cpu"
fi

uv venv --python 3.11 .venv
if [[ "$target" == "cuda" ]]; then
  echo "installing CUDA wheels (${cuda_channel}); the custom kernels compile on first use"
  uv pip install --python .venv/bin/python --index-url "https://download.pytorch.org/whl/${cuda_channel}" torch torchvision
  uv pip install --python .venv/bin/python ninja "${packages[@]:2}"
else
  uv pip install --python .venv/bin/python "${packages[@]}"
fi

mkdir -p .runtime models generated

if [[ ! -d .runtime/stylegan2-ada-pytorch ]]; then
  git clone --depth 1 https://github.com/NVlabs/stylegan2-ada-pytorch.git .runtime/stylegan2-ada-pytorch
fi

if [[ ! -d .runtime/stylegan-xl ]]; then
  git clone --depth 1 https://github.com/autonomousvision/stylegan_xl.git .runtime/stylegan-xl
fi

if [[ ! -s models/stylegan2-church-config-f.pkl ]]; then
  curl -L --fail --progress-bar \
    https://nvlabs-fi-cdn.nvidia.com/stylegan2/networks/stylegan2-church-config-f.pkl \
    -o models/stylegan2-church-config-f.pkl
fi

if [[ ! -s models/stylegan-xl-imagenet128.pkl ]]; then
  curl -L --fail --progress-bar \
    https://s3.eu-central-1.amazonaws.com/avg-projects/stylegan_xl/models/imagenet128.pkl \
    -o models/stylegan-xl-imagenet128.pkl
fi

echo "StyleGAN runtimes ready (${target}): StyleGAN2 Church + StyleGAN-XL ImageNet-128"
.venv/bin/python - <<'PY'
import sys
sys.path.insert(0, "model_service")
from device import device_report, resolve_device, resolve_precision

device = resolve_device()
print("resolved device:", device_report(device, resolve_precision(device)))
PY
