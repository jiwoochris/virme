# virtueme-core: 기록 처리와 키워드 검색

카카오톡·인스타그램·사진 기록을 한 형식으로 모으고, 키워드를 넣으면 관련 기록을 찾아 근거 등급을 매긴 뒤
서사 오브젝트(JSON)로 돌려주는 로컬 파이썬 패키지입니다. Storyseed 노벨·대화 화면은 이 패키지의 로컬 API 에
연결되면 합성 페르소나 대신 **내 기록**으로 이야기를 쓰고, 이야기 속 사실 주장을 기록과 대조합니다(근거 계약 v2).
같은 서사 오브젝트를 비주얼 모드와 인사이트(시각화) 화면이 함께 읽고, 정서 곡선으로 음악(ACE-Step 1.5)과
그 음악에 맞춰 움직이는 잠재공간 영상을 만듭니다.

모든 계산은 이 컴퓨터에서 합니다. 밖으로 나가는 것은 가명·개인정보 가리기를 거친 텍스트 일부뿐이며,
사진 파일과 GPS 좌표는 나가지 않습니다.

## 5분 안에 샘플로 돌려 보기

가상 인물 윤서연의 90일 기록(카카오톡 방 3개, 인스타그램, 휴대폰 사진)이 `examples/seoyeon/` 에 들어 있습니다.
실존 인물이 아닙니다.

```bash
cd core
uv sync                                     # 파이썬 3.12 환경 (torch 포함, 처음 한 번)
export OPENROUTER_API_KEY=sk-or-v1-...      # 없으면 LLM 단계를 건너뛰고 로컬 계산만 한다

uv run python examples/seoyeon/fetch_photos.py   # 샘플 사진을 위키미디어 공용에서 받는다 (저장소에는 없음)

P="uv run virtueme -p seoyeon"
$P ingest kakao examples/seoyeon/kakao/대학친구들.txt examples/seoyeon/kakao/엄마.txt examples/seoyeon/kakao/정우진.csv --me 윤서연
$P ingest instagram examples/seoyeon/instagram
$P ingest photos examples/seoyeon/photos
$P people --relation "지인A=대학 친구" --relation "지인B=대학 친구" --relation "지인C=대학 친구" \
          --relation "지인D=회사 동기" --relation "지인E=러닝 크루 친구"
$P build                                    # 이름 가리기·임베딩·정서·묶기·관계 추출 (모델 첫 다운로드 제외)
$P query 러닝                                # 근거 등급과 근거 기록 출력
$P serve                                    # 로컬 API http://127.0.0.1:8767
```

다른 터미널에서 웹 앱을 띄우고 노벨·대화 모드에서 **기록 → 내 기록**을 고릅니다. 인사이트 모드는 서버가 떠 있으면
바로 정서 곡선·주제 묶음·관계 그래프를 그립니다.

```bash
bun run dev      # 또는 npm run dev. Vite 가 /api/core 를 8767 로 넘긴다
```

노벨 화면의 AI 집필은 지금처럼 브라우저에 넣은 OpenRouter 키로 돌아갑니다 (⚙ 설정).

## 내 기록 가져오기

명령줄 대신 웹 화면으로도 넣을 수 있습니다. `virtueme -p <프로필> serve` 를 켠 뒤 `http://127.0.0.1:8767/import` 를 열면
파일 올리기 → 기록 처리하기 → 사람 관계 붙이기를 한 화면에서 합니다(카카오톡 발화 335건 기준 처리 57초).
이 화면은 서버가 뜬 컴퓨터에서만 열리고, 원래 이름·번호는 이 화면과 로컬 DB 에만 보입니다.

| 출처 | 내보내는 방법 | 명령 |
|---|---|---|
| 카카오톡 (PC) | 대화방 → 메뉴 → 대화 내용 → 대화 내보내기 → 텍스트 | `ingest kakao 파일.txt --me 내이름` |
| 카카오톡 (모바일) | 대화방 → ≡ → 설정 → 대화 내용 내보내기 → 텍스트만 보내기 | 같은 명령 (점 날짜·날짜 먼저·시각 먼저 세 형식 자동 인식) |
| 카카오톡 (Mac) | 대화방 → 대화 내보내기 → csv | 같은 명령 |
| 문자 (안드로이드) | "SMS Backup & Restore" 앱으로 백업 → XML 파일 | `ingest sms 백업.xml` |
| 문자·iMessage (아이폰) | 아이클라우드 메시지를 켠 맥에서 `~/Library/Messages/chat.db` 를 복사 | `ingest sms chat.db` |
| 인스타그램 | 설정 → 계정 센터 → 내 정보 다운로드 → **JSON** 형식 | `ingest instagram 압축푼폴더` |
| 휴대폰 사진 | 사진 폴더 그대로 (jpg·png·heic) | `ingest photos 폴더` |

- `--me` 는 카카오톡에 표시되는 본인 이름입니다. 틀리면 보낸 사람 목록을 보여 주고 멈춥니다.
- 대화 상대 실명은 가져올 때 `지인A`, `지인B` … 로 바뀝니다. `엄마`·`팀장님`처럼 관계로 저장된 연락처는 그대로 둡니다.
- 문자는 인증번호·광고처럼 자동 발송된 것(발신 전용 번호, [Web발신]·(광고) 표시)은 빼고, 이름 없이 번호로만 온 상대는
  번호를 가명으로 바꿉니다. 카카오톡과 연락처 이름이 같으면 같은 가명을 씁니다.
- `people --relation "지인D=회사 동기"` 로 가명에 관계를 붙이면 이야기에서 그 관계로 부릅니다. 실명은 쓰지 마세요.
- 같은 파일을 다시 가져와도 기록 ID 가 같아 중복되지 않습니다. 새 기록만 `build` 가 계산합니다.
- 프로필(`-p`) 하나가 사람 한 명입니다. DB 는 `core/data/<프로필>.db` 에 생기고 저장소에는 올라가지 않습니다.

## 처리 단계

| 단계 | 모듈 | 하는 일 | 쓰는 것 |
|---|---|---|---|
| 가져오기 | `ingest/` | 카카오톡·문자·인스타그램·사진을 공통 레코드 `virtueme.record/1` 로 변환. 같은 사람이 3분 안에 연달아 보낸 메시지는 한 발화로 합침 | 직접 구현 |
| 개인정보 | `privacy.py` | 참여자 가명화, 본문 속 이름·전화·이메일·계좌·카드·주민번호·URL·계정 가리기 | 직접 구현 |
| 제3자 이름 | `pii.py` | 참여자 목록에 없고 본문에만 나오는 사람 이름을 "(누군가)"로 가림. 관계어("엄마")와 반려동물 이름은 둠 | GLiNER `gliner_multi_pii-v1` (Apache-2.0) |
| 형태소 | `textproc.py` | 명사·용언 원형 추출 ("외로웠다" → "외롭다") | Kiwi |
| 임베딩 | `embed.py` | 텍스트 bge-m3, 사진 SigLIP 2. 사진은 자동 태그("사진: 바다, 해변")로 텍스트 공간에도 넣음 | sentence-transformers, transformers |
| 정서 | `emotion.py` | 기록마다 정서가(-1..1)·각성도(0..1). 텍스트는 LLM 채점, 사진은 SigLIP 비교 | OpenRouter (gemini-2.5-flash) |
| 패턴 | `patterns.py` | 날짜별 정서 곡선, 변화점, 주 단위 주기, 시간대 | ruptures (PELT), statsmodels (STL) |
| 묶기 | `clusters.py` | 주제 묶음(형태소 명사로 BERTopic, LLM 이름 붙이기), 기분 묶음, 혼자/함께 묶음, 의미 지도(2차원 좌표) | BERTopic, UMAP, HDBSCAN |
| 개체·관계 | `entities.py` | 기록마다 사람·장소·활동·물건과 관계를 뽑아 그래프로. 습관을 단정한 자기 서술("요즘 운동할 시간도 없어")과 그 뒤 기록 수의 간극 찾기 | OpenRouter (추출 gemini-2.5-flash, 자기 서술 claude-haiku-4.5), networkx |
| 검색 | `query.py` | 키워드 확장 → 직접 일치 + 의미 유사도 → LLM 판정 → 근거 3등급 | OpenRouter (확장 gemini-2.5-flash, 판정 claude-haiku-4.5) |
| 서사 오브젝트 | `narrative.py` | `virtueme.narrative/1` JSON, 근거 기록 목록, 주제·기분·혼자/함께 비율, 비주얼 신호 7개 | `schema/narrative.v1.schema.json` |
| 이야기 프롬프트 | `story.py` | 번호 붙인 취재 노트와 근거 계약 v2 집필 규칙. 노벨과 대화가 같은 함수를 씀 | Storyseed 원문 프롬프트 (`prompts_storyseed.py`) |
| 근거 검사 | `verify.py` | `<em ref>` 앵커의 날짜·요일·시각·횟수·낱말을 기록과 대조, 문맥 검사, 강등 | OpenRouter (문맥 검사 claude-haiku-4.5) |
| 음악 | `music.py` | 정서 곡선을 구간으로 나눠 BPM·조성·편성을 정하고, 구간마다 생성해 무음을 자르고 크로스페이드로 이음. 프레임별 음량·온셋 | ACE-Step 1.5 (MIT, 로컬 서버), ffmpeg |
| 오디오 반응 영상 | `../scripts/render-audio-reactive.py` | 음량으로 잠재공간 이동 속도, 온셋으로 윤곽 박동, 구간 분위기로 색을 정해 렌더. 장소는 StyleGAN2, 사물·상황은 StyleGAN-XL(`--mode object\|situation`) | 팀 모델 환경 (`bun run model:setup`) |
| API·CLI | `api.py`, `cli.py` | 로컬 HTTP API, 명령줄 | FastAPI |

변화점·주기·코사인 유사도는 AI 모델이 아니라 통계·알고리즘 기법입니다. 발표에서 구분해서 설명합니다.

### 근거 등급

- 기록마다 관련도(0..1)를 매깁니다. 형태소 원형이 일치하면 0.9(키워드)·0.75(동의어), 의미 유사도는 공간별
  배경 질의 대비 z 점수를 로지스틱으로 바꾼 값이고, 둘을 `1 - (1-직접)(1-의미)` 로 합칩니다.
- 키가 있으면 관련도 0.2 이상 후보(최대 240건)를 판정 모델이 2(직접)·1(간접)·0(무관)으로 다시 봅니다.
  2 는 강한 근거, 1 은 약한 근거가 됩니다.
- 기본값은 **본인이 쓴 기록만** 근거로 셉니다. 상대가 보낸 메시지는 앞뒤 20분 안의 인접 근거로만 씁니다.
- 강한 근거: 강한 기록 8건·5일 이상, 기록이 있는 날의 5% 이상
- 약한 근거: 강한 기록 3건·2일 이상, 또는 강한·약한을 합쳐 8건·4일 이상
- 근거 부족: 그 밖. 서사 오브젝트에 `message: "기록이 충분하지 않습니다"` 만 담고, 노벨 화면은 이야기를 짓지 않습니다.

기준값은 전부 `config.py` 에 있고, 서사 오브젝트의 `grade_basis` 에 그대로 실립니다.

### 근거 계약 v2

이야기의 창작 부분은 자유롭게 두고, 기록에서 가져온 사실 주장만 검사합니다.

- 취재 노트의 기록마다 번호를 붙이고, LLM 은 기록에서 가져온 값을 `<em ref="3">7월 13일 5km 완주</em>` 처럼
  번호와 함께 감쌉니다. 날짜·시각은 그때 한 일과 한 앵커에 넣게 합니다.
- 검사기는 앵커 안의 값을 날짜, 요일, 시각(분까지 적으면 ±15분, 시만 적으면 ±90분), 횟수, 낱말로 나누고,
  모두 ref 기록 한 건에 있는지 봅니다. 날짜·시각이 둘 이상이면 ref 기록 여러 건에 나눠 있어도 됩니다.
- 이야기에서는 규칙을 통과한 앵커를 판정 모델이 앵커가 든 문장과 ref 기록을 비교해 한 번 더 봅니다(`context_mismatch`).
  기록을 쓴 사람까지 함께 보내므로, 친구가 한 일을 "나"가 한 일처럼 쓴 문장도 여기서 걸립니다. 대화 답은 규칙 검사만 합니다.
- 통과하지 못한 앵커는 강조만 벗기거나(`plain`), 날짜·시각·횟수를 흐리게 바꾸거나(`blur`, 기본값),
  그 문장만 다시 쓰게(`rewrite`) 합니다.
- 화면에서는 통과한 앵커만 금색으로 표시하고, 누르면 인용한 기록(가린 텍스트)을 보여 줍니다.

## 로컬 API

| 요청 | 응답 |
|---|---|
| `GET /health` | 프로필, 기록 수, 기간, 쓰는 모델, 빌드 여부 |
| `POST /query` `{"keyword": "러닝"}` | `narrative_object`, `evidence_records`(최대 48건), `adjacent_records`, `people`(가명 → 관계) |
| `POST /story/prompt` `{"keyword", "genre", "version"}` | 노벨 집필용 system·user 프롬프트, 번호 순 근거 기록 ID, 서사 오브젝트 |
| `POST /verify` `{"paragraphs", "evidence_ids", "mode"}` | 검사한 본문 HTML, 앵커별 판정과 이유, 통계 |
| `POST /talk/context` `{"message", "history"}` | 대화 모드 system 프롬프트와 이번 질문에 가까운 기록 ID |
| `GET /talk/greeting` | 대화 첫 인사 |
| `GET /records/{id}` | 기록 한 건 (가린 텍스트) |
| `GET /emotion` | 날짜별 정서 곡선 |
| `GET /profile` | 전체 요약: 변화점, 주기, 시간대 분포 |
| `GET /clusters` | 주제·기분·혼자/함께 묶음과 비율 |
| `GET /graph?keyword=` | 개체·관계 그래프 (노드·엣지, 근거 기록 수) |
| `GET /gaps` | 자기 서술과 기록 비율이 어긋나는 곳 |
| `GET /map?keyword=` | 의미 지도: 본인 기록의 2차원 좌표, 주제, 키워드 근거 표시 |
| `GET /import` | 기록 넣기 화면 (`POST /import/{kakao,sms,instagram,photos}`, `POST /build`, `GET /build/status`, `GET /people/local`, `POST /people/relation`) |
| `GET /label` | 정답 라벨 붙이는 화면 (`/label/candidates`, `POST /label`, `/label/export`) |
| `POST /music/plan` `{"keyword"}` | 음악 계획: 구간별 날짜·조성·분위기·설명, BPM, 매핑 설명 (만들지는 않음) |
| `POST /music` `{"keyword"}` | 음악 만들기 시작(캐시가 있으면 바로 ready). ACE-Step 서버가 꺼져 있으면 `unavailable` |
| `GET /music/{id}` · `/music/{id}/music.m4a` · `features.json` · `video.mp4` | 진행 상태와 결과 파일 |

응답의 텍스트는 가려진 것이고, 좌표와 원본 파일 경로는 들어 있지 않습니다.

## 평가 (2026-09-11)

| 무엇을 | 결과 | 다시 돌리기 |
|---|---|---|
| 합성 데이터 이식 | Storyseed 원본 JS 와 시드 7개 × 페르소나 4명, 28개 조합 모두 기록이 같음 | `uv run pytest tests/test_synthetic.py` |
| 텍스트 임베딩 모델 | 한국어 66문장 검색 MAP (코사인 그대로): bge-m3 0.514, multilingual-e5-base 0.413, SigLIP 2 0.20~0.22 | `uv run python eval/embed_bench.py` |
| 사진 검색 | 한국어 키워드 → 사진 15장, SigLIP 2 top-1 0.818 | 〃 |
| 검색 점수 설계 | bge-m3 코사인 0.514 → 배경 질의 z 점수 + 직접 일치 0.715 → + 키워드 확장 0.741 (강한 근거 F1 0.404 → 0.615) | `uv run python eval/retrieval_eval.py --grid` |
| LLM 판정 | 윤서연 본인 기록 345건·키워드 10개: 강한 근거 F1 임베딩만 0.49 → 판정 추가 0.74 (정밀도 0.39 → 0.79, 재현율 0.73). 판정 기준 v4 는 사람 키워드(친구·엄마)를 단둘이 나눈 대화와 단체방 잡담으로 나누고, 한 글자 키워드가 다른 낱말의 일부에 걸리지 않게 함 | `uv run python eval/profile_eval.py -p seoyeon` |
| 변화점 | 윤서연 정서 곡선에서 6월 23일 상승(-0.07 → 0.26) 1건. 줄거리상 회사 동기가 러닝을 권한 주(6/22~)이고, "러닝" 첫 근거가 6월 24일 | `uv run virtueme -p seoyeon query 러닝` |
| 제3자 이름 가리기 | 한국어 40문장(이름 21개, 대명사·관계어·반려동물만 있는 문장 20개): 이름 21개 모두 가림, 잘못 가린 낱말 0개. 윤서연 기록 695건에서 이름 3개와 본인 이름 1개를 가림 | `uv run python eval/pii_eval.py` |

- 교차 인코더 재순위 모델(bge-reranker-v2-m3)도 시험했지만 주제 키워드 검색에서 MAP 0.41 로 오히려 낮아 쓰지 않았습니다.
- LLM 판정 평가의 정답은 claude-sonnet-4.5 가 붙인 은(silver) 라벨입니다. 판정 모델과 같은 회사 모델이라
  일치율이 부풀 수 있습니다. 서버의 `/label` 화면에서 사람이 라벨을 붙이면 `eval/profile_eval.py --gold` 로
  그 라벨 기준 수치를 냅니다.

### 근거 계약 v2 평가

가상 인물 윤서연과 합성 페르소나, 키워드 5개씩, 장르 둘(미스터리·코미디)로 이야기를 20편씩 만들고, 대화 모드에는
두 사람에게 질문 20개씩을 물었습니다. claude-sonnet-4.5 가 앵커마다 기록과 맞는지 판정한 값을 정답으로 삼았습니다.

| 화면 · 집필 규칙 · 검사 | 편(답)당 앵커 | 날조 앵커 | 검사 정밀도 | 검사 재현율 |
|---|---|---|---|---|
| 이야기 · v1 규칙 · v1 값 목록 대조 | 6.9 | 20.3% | 81.4% | 63.6% |
| 이야기 · v2 규칙 · 규칙 검사 | 5.65 | 14.2% | 90.1% | 93.8% |
| 이야기 · v2 규칙 · 규칙 + 문맥 검사 (기본값) | 5.65 | 14.2% | 97.3% | 74.2% |
| 대화 · v1 규칙 · v1 값 목록 대조 | 2.4 | 15.5% | 82.2% | 45.1% |
| 대화 · v2 규칙 · 규칙 검사 (기본값) | 2.2 | 9.0% | 95.8% | 85.2% |
| 대화 · v2 규칙 · 규칙 + 문맥 검사 | 2.2 | 9.0% | 96.4% | 65.4% |

- 정밀도는 금색으로 통과한 앵커 가운데 실제로 기록과 맞는 비율, 재현율은 맞는 앵커 가운데 통과한 비율입니다.
- 날조 앵커 16개 가운데 규칙 검사가 6개, 문맥 검사까지 14개를 걸러 냅니다.
- 짝 오류(값 하나하나는 어느 기록에든 있는데 조합이 틀린 주장)가 v1 이야기 날조 앵커 28개 가운데 20개(71%)였고,
  v1 값 목록 대조는 그중 14개를 통과시켰습니다. 같은 이야기에 v2 규칙 + 문맥 검사를 돌리면 20개 가운데 12개를 잡습니다.
- 대화 답에서는 문맥 검사가 정밀도를 0.6%p 올리는 대신 재현율을 20%p 깎아서, 대화는 규칙 검사만 합니다(`/verify` 의
  `kind: "talk"`). 대화 표본은 v2 날조 앵커가 8개라 이야기보다 오차가 큽니다.
- ref 번호가 실제 출처와 맞은 비율은 94.8%입니다.
- 걸러진 앵커를 강조만 벗기면 틀린 날짜가 본문에 12건 남고, `blur`·`rewrite` 로는 0건입니다.
- 다시 돌리기: `uv run python eval/grounding_eval.py generate --versions v1 v2`, `... talk` 후 `... score --rewrite`

### 정서 → 음악 매핑

| 음악 값 | 정하는 방법 |
|---|---|
| 구간 | 근거 기간의 정서 곡선을 변화점에서 자름. 변화점이 없으면 기간을 셋(도입·전개·마무리)으로 |
| BPM | 62 + 60 × 근거 기록 날의 평균 각성도 + 45 × 활동량 신호 (60~140) |
| 조성 | 키워드로 으뜸음을 고름(근거가 조금 바뀌어도 같은 조). 정서가가 -0.05 이하인 구간은 나란한조(단조), 0.05 이상은 장조 |
| 분위기 | 정서가·각성도 사분면: 밝고 힘찬 / 따뜻하고 잔잔한 / 긴장된 / 쓸쓸하고 느린 |
| 셈여림 | 구간 각성도 + 근거가 몰린 정도. 근거가 몰린 구간(예: 10km 완주 주간)이 세게 들림 |
| 편성 | 근거 기록이 대부분 혼자면 피아노 독주, 함께가 많으면 현악·타악 합주 |
| 장르 바탕 | 안정 신호가 높으면 앰비언트, 새로움이 높으면 로파이 전자음, 그 밖에는 시네마틱 |

숫자는 전부 `music.py` 가 정하고, 음악 모델에는 그 값과 영어 설명(예: `instrumental lo-fi electronic, solo piano,
warm, gentle, hopeful, moderate groove, gentle intro, 108 bpm, no vocals`)만 넘깁니다. 같은 서사면 같은 계획과 시드가
나오므로 같은 음악이 다시 만들어집니다. 비주얼 모드의 "정서 → 음악 매핑 보기"가 이 근거를 문장으로 보여 줍니다.

## 시연 준비 (음악·영상 미리 만들기)

음악 한 곡(3구간, 약 53초)은 M4 맥에서 약 4분, 오디오 반응 영상(512px, 24fps)은 약 7분 걸립니다. 발표 전에 키워드마다
미리 만들어 두면, 발표 때는 ACE-Step 서버 없이 캐시에서 바로 재생합니다.

```bash
# 1) ACE-Step 1.5 (virme 와 같은 폴더에 두는 것을 기준으로 적음, 모델 약 9.4GB)
git clone https://github.com/ace-step/ACE-Step-1.5.git ../../acestep && cd ../../acestep
uv sync && uv run acestep-download
# 127.0.0.1:8001. 가사·계획용 LM 은 쓰지 않으므로 끈다 (start_api_server_macos.sh 는 이 값을 auto 로 덮어씀)
ACESTEP_LM_BACKEND=mlx ACESTEP_INIT_LLM=false uv run acestep-api --host 127.0.0.1 --port 8001

# 2) 팀 StyleGAN 환경 (영상용, virme 폴더에서 한 번)
bun run model:setup

# 3) 발표용 키워드 미리 만들기 (core 폴더). 간극 계산도 같이 해 둔다. 이미 만든 키워드는 건너뛴다
uv run virtueme -p seoyeon demo 러닝 콩이 외로움 --video

# 4) 발표 때 한 번에 켜기 (virme 폴더). Ctrl+C 로 이 스크립트가 켠 서버만 함께 끈다
export OPENROUTER_API_KEY=sk-or-v1-...
VIRTUEME_PROFILE=seoyeon ./scripts/start-demo-mac.sh            # core + 웹 앱
VIRTUEME_PROFILE=seoyeon ./scripts/start-demo-mac.sh --music    # 새 키워드 음악도 만들 때 ACE-Step 까지
```

- 결과는 `$VIRTUEME_DATA/music/<프로필>/<음악 번호>/` 에 `music.m4a`, `features.json`, `video.mp4` 로 남습니다.
- 비주얼 모드 왼쪽의 "기록의 음악"에서 재생하면 잠재 루프 영상이 음량에 맞춰 빨라지고 온셋마다 밝아집니다.
  "오디오 반응 영상 보기"는 미리 렌더한 영상을 소리와 함께 보여 줍니다.
- `demo` 는 모든 키워드의 음악을 먼저 만들고 영상은 그다음에 렌더합니다. 24GB 맥에서 ACE-Step 과 영상 렌더를 함께 돌리면
  메모리가 모자라 시스템이 작업을 끊을 수 있으니, 음악 단계가 끝났다는 안내가 나오면 ACE-Step 서버를 꺼 두세요.
  끊겨도 다시 돌리면 만든 구간·음악·영상은 건너뛰고 이어서 합니다. 사물·상황 영상은 `--video-modes place object situation`.
- ACE-Step 서버가 꺼져 있고 캐시도 없으면 화면에 켜는 방법을 안내합니다. 이야기·대화·인사이트는 음악 없이도 동작합니다.
- 노벨은 검사를 마친 이야기를 브라우저에 키워드별로 남겨 둡니다. 발표 때 키나 네트워크 문제로 새로 짓지 못하면 리허설 때 만든
  이야기를 만든 날짜와 함께 보여 줍니다. 리허설에서 발표용 키워드를 한 번씩 돌려 두세요.
- `bun run build` 로 만든 정적 파일로 띄워도 화면이 `127.0.0.1:8767` 의 core 서버를 직접 찾습니다.

## 설정 (환경 변수)

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `OPENROUTER_API_KEY` | 없음 | 없으면 키워드 확장·LLM 판정·LLM 정서 채점을 건너뛰고 로컬 계산만 한다 |
| `VIRTUEME_DATA` | `core/data` | 프로필 DB 위치 |
| `VIRTUEME_LLM_MODEL` | `google/gemini-2.5-flash` | 키워드 확장·정서 채점 |
| `VIRTUEME_JUDGE_MODEL` | `anthropic/claude-haiku-4.5` | 근거 판정 |
| `VIRTUEME_TEXT_MODEL` / `VIRTUEME_VISION_MODEL` | `BAAI/bge-m3` / `google/siglip2-base-patch16-224` | 임베딩 |
| `VIRTUEME_TZ` | `Asia/Seoul` | 시간대가 없는 기록의 시간대 |
| `VIRTUEME_ACESTEP_URL` | `http://127.0.0.1:8001` | ACE-Step REST 서버 |
| `VIRTUEME_MUSIC_DIR` | `$VIRTUEME_DATA/music` | 음악·영상 캐시 |

## 테스트

```bash
uv run pytest -q     # 54개. 모델을 내려받지 않는 해시 임베더와 가짜 음악 서버로 돈다
```

## 알려진 한계

- "비"처럼 한 글자 키워드는 판정 모델이 "비빔국수"를 관련으로 보는 식의 오판정이 남습니다.
- 감정 키워드(피곤, 행복)는 직접·간접 경계가 모델마다 달라 강한 근거 재현율이 낮습니다.
- 90일 기록으로는 연 단위 계절성을 계산할 수 없습니다 (주 단위만).
- 근거 검사는 앵커 안의 값만 봅니다. 앵커 밖 문장에서 지어낸 날짜는 검사 대상이 아니므로, 집필 규칙으로
  날짜·시각을 앵커 안에 넣게 합니다.
- 문맥 검사는 맞는 앵커의 약 26%를 떨어뜨립니다(재현율 74%). 금색이 줄어드는 대신 틀린 금색이 3% 안쪽입니다.
- 제3자 이름 가리기는 사전에 있는 낱말과 같은 이름("하늘", "보람")을 이름으로 보지 않습니다.
- 개체 이름 합치기는 규칙 기반입니다("제주도"→"제주", "우리집"→"집", "크루"→"러닝 크루"). "뜀"과 "러닝"처럼
  다른 낱말로 적은 같은 활동은 따로 셉니다.
