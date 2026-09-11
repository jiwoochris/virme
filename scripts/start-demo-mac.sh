#!/bin/zsh
# 시연용으로 한 번에 켜기: core 서버(8767) + 웹 앱(5173), 필요하면 ACE-Step(8001)과 팀 StyleGAN 서비스(8766).
# 이미 떠 있는 서버는 그대로 쓰고, 이 스크립트가 켠 것만 Ctrl+C 때 함께 끈다.
#
#   export OPENROUTER_API_KEY=sk-or-v1-...                          # 근거 판정·문맥 검사용 (없으면 로컬 계산만)
#   VIRTUEME_PROFILE=seoyeon ./scripts/start-demo-mac.sh            # 음악은 캐시만 재생
#   VIRTUEME_PROFILE=seoyeon ./scripts/start-demo-mac.sh --music    # 새 키워드 음악도 만든다 (ACE-Step 필요)
#   VIRTUEME_PROFILE=seoyeon ./scripts/start-demo-mac.sh --stylegan # 장소 모드 실시간 프레임
set -euo pipefail
setopt null_glob

project_root=${0:A:h:h}
profile=${VIRTUEME_PROFILE:-default}
acestep_dir=${ACESTEP_DIR:-$project_root/../acestep}
logs=$project_root/generated/demo-logs
mkdir -p "$logs"

want_music=0
want_stylegan=0
for arg in "$@"; do
  case $arg in
    --music) want_music=1 ;;
    --stylegan) want_stylegan=1 ;;
    *) echo "모르는 옵션: $arg" >&2; exit 2 ;;
  esac
done

pids=()
cleanup() {
  (( ${#pids} )) || return 0
  echo "\n이 스크립트가 켠 서버를 끕니다"
  for pid in $pids; do
    pkill -TERM -P $pid 2>/dev/null || true   # uv run·npm exec 가 띄운 실제 서버(자식)부터
    kill $pid 2>/dev/null || true
  done
  pids=()
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM   # Ctrl+C 로 끝내는 것은 정상 종료로 본다

up() { curl -s -o /dev/null "$1" }

wait_for() {  # 주소가 응답할 때까지 최대 $2초 기다린다
  local url=$1 limit=$2 name=$3 i=0
  until up "$url"; do
    if (( i++ >= limit )); then
      echo "  $name 이(가) ${limit}초 안에 응답하지 않습니다. 마지막 로그:" >&2
      tail -5 "$logs/$name.log" >&2 || true
      return 1
    fi
    sleep 1
  done
}

start() {  # 이름, 확인 주소, 기다릴 초, 명령...
  local name=$1 url=$2 limit=$3
  shift 3
  if up "$url"; then
    echo "$name: 이미 떠 있어서 그대로 씁니다 ($url)"
    return 0
  fi
  echo "$name 켜는 중"
  "$@" > "$logs/$name.log" 2>&1 &
  pids+=$!
  wait_for "$url" "$limit" "$name"
}

start core http://127.0.0.1:8767/health 90 zsh -c "cd '$project_root/core' && exec uv run virtueme -p '$profile' serve"

if (( want_music )); then
  if [[ -d $acestep_dir ]]; then
    start acestep http://127.0.0.1:8001/health 180 zsh -c "cd '$acestep_dir' && ACESTEP_LM_BACKEND=mlx ACESTEP_INIT_LLM=false TOKENIZERS_PARALLELISM=false exec uv run acestep-api --host 127.0.0.1 --port 8001" || true
  else
    echo "ACE-Step 폴더가 없습니다 ($acestep_dir). 캐시한 음악만 재생합니다. 설치는 core/README.md 시연 준비 참고"
  fi
fi

if (( want_stylegan )); then
  start stylegan http://127.0.0.1:8766/health 120 "$project_root/scripts/run-stylegan-mac.sh" || true
fi

start web http://127.0.0.1:5173/ 60 zsh -c "cd '$project_root' && exec npx vite --host 127.0.0.1 --port 5173 --strictPort"

cached=(${VIRTUEME_DATA:-$project_root/core/data}/music/$profile/*/features.json)
echo "\n준비됐습니다: http://127.0.0.1:5173  (프로필 $profile, 캐시한 음악 ${#cached}곡, 로그 $logs)"
if (( ${#pids} )); then
  echo "끝내려면 Ctrl+C"
  wait
fi
