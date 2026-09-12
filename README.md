# Virtueme Generative Portrait MVP

개인 기록을 노벨·채팅·비주얼의 세 모드로 경험하는 Virtueme 통합 POC입니다. 노벨과 채팅은 Storyseed 원본 기능을 포함하며, 비주얼은 장소·사물·상황의 잠재공간을 각각 12초 폐루프 영상으로 보여줍니다.

공개 페이지: [dexa.art/virme](https://dexa.art/virme/)

## Run

```bash
bun install
bun run model:setup
bun run model:serve
```

두 번째 터미널에서 웹 앱을 실행합니다.

```bash
bun run dev
```

`dexa.art/virme/`용 정적 빌드는 다음 명령으로 생성합니다.

```bash
bun run build:public
```

## 내 기록으로 쓰기 (core)

`core/` 는 카카오톡·인스타그램·사진 기록을 가져와 키워드별 근거 등급과 서사 오브젝트를 만드는 로컬 파이썬 패키지입니다.
core 서버를 켜 두면 노벨 모드에 **기록 → 내 기록** 선택이 나타나고, 합성 페르소나 대신 내 기록으로 이야기를 씁니다.
서버가 없으면 화면은 이전과 같습니다. 설치와 샘플 실행 방법은 [core/README.md](core/README.md) 에 있습니다.

```bash
bun run core:setup                          # 처음 한 번 (uv 필요)
VIRTUEME_PROFILE=seoyeon bun run core:serve # 로컬 API http://127.0.0.1:8767
bun run dev                                 # Vite 가 /api/core 를 core 서버로 넘긴다
```

두 모드 모두 근거가 부족한 키워드는 이야기를 짓지 않고 기준과 함께 안내합니다. 사용자가 "그래도 지어 보기"를 누르면
등급을 "근거 부족"으로 그대로 표시한 채 짓습니다.

## Scope

- 한 페이지 상단에서 전환하는 `NOVEL`·`TALK`·`VISUAL` 경험 모드
- 노벨: 씨앗 단어, 장르, 90일 기록 합성, AI 집필, 오프라인 규칙 조립, 근거 기록과 컨텍스트 기능을 포함한 Storyseed v9
- 채팅: 기록 기반 페르소나, 시작 질문, 대화 이력, 스트리밍 응답과 모델 설정을 포함한 Storyseed Talk v4
- 기본값 `OFF`인 선택형 중앙 초상 1개
- `PLACE`·`OBJECT`·`SITUATION`으로 구분한 세 가지 잠재공간 모드
- 장소는 기존 StyleGAN2 LSUN Church 건물 도메인으로 고정
- 사물은 백팩·머그·접이식 의자·폴라로이드 카메라·벽시계·컵 등 여러 일상 사물 사이를 이동
- 상황은 이발소·서점·식료품점·도서관·식당·무대·해변 등 여러 장면 사이를 이동
- [Lucid Sonic Dreams](https://github.com/mikael-alafriz-deel/lucid-sonic-dreams)의 누적 latent controller 구조를 개인 데이터용으로 TypeScript 포팅
- [Raspberry - Saje 레퍼런스 영상](https://www.youtube.com/watch?v=iEFqcMrszH0)의 고밀도 GAN 질감·프레임 용융감을 시각 기준으로 사용
- 개인 데이터, 실제 StyleGAN2 생성 프레임, 사전 렌더 잠재 루프의 시각적 라우팅 MVP
- Apple Silicon의 PyTorch MPS에서 로컬 추론하고 StyleGAN 모드에서는 생성 프레임 자체를 주 화면으로 표시

중앙 초상은 잠재 영상과 분리된 선택 레이어이며, 사용자가 `PORTRAIT` 토글로 표시 여부를 정합니다.

## Storyseed import and API safety

원격 원본의 전체 HTML·CSS·JavaScript 기능을 로컬 공개 문서로 가져옵니다.

```bash
bun run storyseed:import -- /path/to/Storyseed_v9.html /path/to/Storyseed_Talk_v4.html
```

가져오기 스크립트는 공개 저장소에 포함하면 안 되는 `EMBED_KEY`만 빈 값으로 바꾸고 안내문을 공개 버전에 맞게 수정합니다. 모델 선택과 사용자의 OpenRouter 키 입력·브라우저 `localStorage` 저장 기능은 유지됩니다. 키 없이도 원본이 제공하는 로컬 기능과 안내 화면을 사용할 수 있습니다.

## Lucid Sonic Dreams mapping

- 개인 데이터의 안정·새로움·충돌·불확실성·가능성 값을 0..1 파생 신호로 정규화
- `generate_vectors` → `stepLucidState`: 데이터 활동량과 상태 변화량을 latent에 합성
- 원본의 0.75 시간 평활 계수를 활동량·변화량에 적용
- `update_motion_signs` → truncation 경계에서 이동 방향 반전
- `generate_class_vec` → 다섯 개인 상태 신호로 latent 축과 색상군 변조
- 개인 데이터가 없을 때만 익명 데모 프로필로 controller 구동
- 개인 데이터의 다섯 신호로 여러 StyleGAN `W` latent를 혼합하고 activity·change·confidence로 truncation과 변이 seed를 제어
- StyleGAN 모드에서는 완성 RGB 초상을 출력하지 않는다. 여섯 `W` latent를 서로 다른 공간 영역에 배치하고 32×32 중간 feature의 가중 분산, 즉 latent 영역 사이의 구조를 RGB로 투영한다.
- latent curl flow field는 명시적으로 procedural fallback을 선택했을 때만 렌더링
- 중앙 초상은 모델 영상 위의 독립 레이어로 유지되며 기본값은 숨김

## Mac model runtime

- 장소 런타임: NVIDIA `stylegan2-ada-pytorch`, LSUN Church `stylegan2-church-config-f.pkl`, 256×256
- 사물·상황 런타임: `StyleGAN-XL` ImageNet 128×128 체크포인트. 클래스 조건과 latent를 함께 보간해 단일 대상이 아닌 여러 범주의 중간 영역을 만든다.
- 장치: `VIRME_DEVICE` 가 없으면 `cuda → mps → cpu` 순으로 고른다. 이 맥에서는 `mps` 로 잡히고 지원되지 않는 연산만 CPU 로 넘어간다. GPU 서버로 옮기는 방법은 아래 「GPU 서버에 모델 올리기」 참고
- 렌더 모드: `latent-structure`; 여러 잠재점의 128×128 중간 RGB 합성 단계에서 공통 윤곽·차이·비선형 잔차를 추출하고 이를 명확한 면·능선 단계로 분절한다. 완성 이미지는 직접 노출하지 않는다.
- 배경 모드: 잠재점을 폐루프 보간한 12초·144프레임 H.264 영상을 효과 없이 직접 재생한다. 장소는 `bun run model:video`, 사물·상황은 `bun run model:video:modes`로 재생성한다.
- 실제 측정 smoke output: `generated/stylegan-mac-smoke.jpg`
- 체크포인트 교체: `VIRTUEME_STYLEGAN_MODEL=/absolute/model.pkl bun run model:serve`
- 속도 측정: `bun run model:bench -- --batch 1,4,8`. 결과는 `generated/stylegan-benchmark.json` 에 남는다

Lucid Sonic Dreams 예제의 `Abstract Photos` 가중치는 기존 MEGA 배포 링크가 응답하지 않고, `Abstract art` 가중치의 Google Drive 공개 다운로드도 현재 사용할 수 없었습니다. 따라서 지금 화면은 **Lucid의 latent 보간 방식을 적용한 실제 StyleGAN2 파이프라인**이지만, 동일한 추상 체크포인트라고 주장하지 않습니다. 해당 `.pkl`을 확보하면 서비스 코드 변경 없이 교체할 수 있습니다.

## GPU 서버에 모델 올리기

모델 서비스는 장치를 스스로 고르고 주소를 환경변수로 받기 때문에, GPU 서버를 빌리면 코드를 고치지 않고 옮겨 붙습니다.

GPU 서버에서 설치하고 띄웁니다. `scripts/setup-stylegan.sh` 는 `nvidia-smi` 가 보이면 CUDA 빌드 torch 와 ninja 를 설치합니다. ninja 는 `bias_act`·`upfirdn2d` 커널을 처음 쓸 때 컴파일하는 데 필요하고, 없으면 느린 파이썬 폴백으로 돕니다.

```bash
./scripts/setup-stylegan.sh                  # CUDA 휠 설치 (채널 고정은 VIRME_CUDA_CHANNEL=cu121)
VIRME_MODEL_HOST=0.0.0.0 ./scripts/run-stylegan.sh
```

이 맥에서 웹 앱을 그 서버로 붙입니다.

```bash
VITE_STYLEGAN_TARGET=http://<서버주소>:8766 bun run dev
```

붙었는지는 `/health` 응답과 화면의 `data-device`·`data-precision` 속성으로 확인합니다. `device` 가 `cuda` 로 나오면 실시간 생성 경로가 열린 것이고, `cpu` 로 나오면 CPU 전용 torch 가 깔린 상태입니다.

| 환경변수 | 기본값 | 쓰임 |
| --- | --- | --- |
| `VIRME_DEVICE` | `auto` | `cuda`·`mps`·`cpu` 중 하나로 고정. 없는 장치를 지정하면 서버가 뜨지 않고 그 사실을 알립니다 |
| `VIRME_PRECISION` | `auto` | `fp16` 은 CUDA 에서만 켜집니다. 구조 계산은 항상 fp32 로 되돌려 맥과 같은 프레임을 그립니다 |
| `VIRME_MODEL_HOST` | `127.0.0.1` | 원격에서 붙으려면 `0.0.0.0` |
| `VIRME_MODEL_PORT` | `8766` | 모델 서버 포트 |
| `VIRME_MODEL_WORKERS` | `1` | CUDA 에서 여러 프로세스로 받을 때만 올립니다 |
| `VITE_STYLEGAN_TARGET` | `http://127.0.0.1:8766` | 개발 서버가 `/api/stylegan` 을 넘길 주소 |
| `VITE_CORE_TARGET` | `http://127.0.0.1:8767` | 개발 서버가 `/api/core` 를 넘길 주소 |

CUDA 에서는 요청 잠금이 풀립니다. MPS 는 여러 요청이 동시에 명령 인코더를 만들면 서버가 죽어서 모든 연산을 한 줄로 세워 두는데, CUDA 는 스트림이 동시 작업을 처리하므로 같은 잠금이 처리량 상한으로만 남기 때문입니다.

여러 프레임을 한 번에 만들려면 `POST /generate/batch` 에 `{"frames": [ ...generate 본문... ]}` 를 보냅니다. 한 번에 최대 64장이고, truncation 이 같은 요청끼리 묶어 생성기를 한 번만 통과합니다. 응답은 프레임마다 base64 JPEG 과 메타데이터입니다.

### 지금 이 맥의 실측

`bun run model:bench -- --batch 1,4,8 --frames 8` 결과입니다. 장소 체크포인트(256×256, `latent-structure` 모드) 기준입니다.

| 배치 | 프레임당 | 초당 프레임 |
| --- | --- | --- |
| 1 | 0.548초 | 1.83 |
| 4 | 0.623초 | 1.61 |
| 8 | 0.810초 | 1.24 |

MPS 에서는 배치를 키울수록 프레임당 시간이 늘어납니다. 배치가 이득이 되는 것은 CUDA 에서이고, 그 수치는 서버를 빌린 뒤 같은 명령으로 재어 비교하면 됩니다.

배치 경로는 부동소수점 재결합 때문에 단건 생성과 픽셀값이 uint8 기준 1 단계까지 달라질 수 있습니다. 화면에서 구분되는 차이는 아니지만, 발표용으로 미리 렌더할 때는 배치 크기를 고정해야 같은 프레임이 다시 나옵니다.

### 학습 준비

`scripts/prepare-training.sh` 가 이미지 폴더를 StyleGAN2-ADA 학습용 zip 으로 바꾸고 학습 명령을 만들어 줍니다. 변환은 이 맥에서도 되므로 GPU 서버가 생기기 전에 데이터셋을 준비해 둘 수 있습니다.

```bash
./scripts/prepare-training.sh --source ~/이미지폴더 --resolution 256      # 데이터셋 준비 + 명령 출력
./scripts/prepare-training.sh --source ~/이미지폴더 --gpus 4 --train      # GPU 서버에서 학습 시작
```


원시 개인 데이터는 렌더러에 전달하지 않고 정규화된 파생 신호만 사용합니다. 외부 시스템은 `virtueme:personal-data` CustomEvent 또는 JSON 파일로 연결할 수 있습니다. 원본 프로젝트는 MIT 라이선스입니다.
