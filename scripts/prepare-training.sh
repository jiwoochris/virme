#!/usr/bin/env bash
set -euo pipefail

# Turns an image folder into the zip StyleGAN2-ADA trains on, then prints (or runs)
# the training command. The conversion works on this laptop, so the dataset can be
# ready before the GPU box exists; training itself needs CUDA.

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

source_dir=""
dest_zip=""
resolution=256
gpus=1
kimg=5000
snap=50
resume=""
run_training=0

usage() {
  cat <<'TXT'
usage: scripts/prepare-training.sh --source <이미지 폴더> [옵션]

  --source PATH      학습에 쓸 이미지가 든 폴더 (필수)
  --dest PATH        만들 데이터셋 zip. 기본값은 datasets/<폴더이름>-<해상도>.zip
  --resolution N     정사각 해상도. 기본값 256
  --gpus N           학습에 쓸 GPU 장수. 기본값 1
  --kimg N           학습 길이(천 장 단위). 기본값 5000
  --snap N           스냅샷 간격. 기본값 50
  --resume PATH      이어서 학습할 체크포인트
  --train            학습까지 바로 시작 (생략하면 명령만 출력)
TXT
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) source_dir="$2"; shift 2 ;;
    --dest) dest_zip="$2"; shift 2 ;;
    --resolution) resolution="$2"; shift 2 ;;
    --gpus) gpus="$2"; shift 2 ;;
    --kimg) kimg="$2"; shift 2 ;;
    --snap) snap="$2"; shift 2 ;;
    --resume) resume="$2"; shift 2 ;;
    --train) run_training=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ -z "$source_dir" ]]; then
  echo "--source 가 필요합니다" >&2
  usage >&2
  exit 1
fi
if [[ ! -d "$source_dir" ]]; then
  echo "이미지 폴더를 찾지 못했습니다: $source_dir" >&2
  exit 1
fi

stylegan_root="$project_root/.runtime/stylegan2-ada-pytorch"
if [[ ! -d "$stylegan_root" ]]; then
  echo "StyleGAN 런타임이 없습니다. scripts/setup-stylegan.sh 를 먼저 실행하세요" >&2
  exit 1
fi

python_bin="$project_root/.venv/bin/python"
if [[ ! -x "$python_bin" ]]; then
  echo "가상환경이 없습니다. scripts/setup-stylegan.sh 를 먼저 실행하세요" >&2
  exit 1
fi

if [[ -z "$dest_zip" ]]; then
  dest_zip="$project_root/datasets/$(basename "$source_dir")-${resolution}.zip"
fi
mkdir -p "$(dirname "$dest_zip")"

image_count=$(find "$source_dir" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) | wc -l | tr -d ' ')
echo "이미지 ${image_count}장 → ${dest_zip} (${resolution}x${resolution})"
if [[ "$image_count" -lt 1000 ]]; then
  echo "경고: StyleGAN2-ADA 는 적응형 증강을 쓰더라도 1,000장 미만에서는 결과가 크게 흔들립니다" >&2
fi

if [[ -s "$dest_zip" ]]; then
  echo "데이터셋이 이미 있어 변환을 건너뜁니다: $dest_zip"
else
  "$python_bin" "$stylegan_root/dataset_tool.py" \
    --source="$source_dir" \
    --dest="$dest_zip" \
    --width="$resolution" \
    --height="$resolution"
fi

outdir="$project_root/training-runs"
mkdir -p "$outdir"

train_command=("$python_bin" "$stylegan_root/train.py"
  "--outdir=$outdir"
  "--data=$dest_zip"
  "--gpus=$gpus"
  "--cfg=auto"
  "--mirror=1"
  "--snap=$snap"
  "--kimg=$kimg")
if [[ -n "$resume" ]]; then
  train_command+=("--resume=$resume")
fi

echo
echo "학습 명령:"
printf '  %q' "${train_command[@]}"
echo
echo

has_cuda=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  has_cuda=1
fi

if [[ "$run_training" -eq 1 ]]; then
  if [[ "$has_cuda" -eq 0 ]]; then
    echo "CUDA GPU 가 없어 학습을 시작하지 않습니다. 데이터셋은 준비됐으니 GPU 서버에서 위 명령을 실행하세요" >&2
    exit 2
  fi
  exec "${train_command[@]}"
fi

if [[ "$has_cuda" -eq 0 ]]; then
  echo "이 컴퓨터에는 CUDA GPU 가 없습니다. 데이터셋만 준비했고, 학습은 GPU 서버에서 위 명령으로 시작하면 됩니다"
else
  echo "--train 을 붙이면 바로 시작합니다"
fi
