#!/bin/zsh
set -euo pipefail

project_root=${0:A:h:h}
cd "$project_root"

uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python torch torchvision pillow fastapi 'uvicorn[standard]' numpy scipy imageio imageio-ffmpeg 'dill>=0.4.1' 'timm==0.4.12' 'ftfy==6.1.1' regex requests tqdm
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

echo "StyleGAN Mac runtimes ready: StyleGAN2 Church + StyleGAN-XL ImageNet-128"
