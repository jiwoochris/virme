# virtueme-core: 기록 처리와 키워드 검색

카카오톡·인스타그램·사진 기록을 한 형식으로 모으고, 키워드를 넣으면 관련 기록을 찾아 근거 등급을 매긴 뒤
서사 오브젝트(JSON)로 돌려주는 로컬 파이썬 패키지입니다. Storyseed 노벨 화면은 이 패키지의 로컬 API 에
연결되면 합성 페르소나 대신 **내 기록**으로 이야기를 씁니다.

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
$P build                                    # 임베딩·정서 계산, M4 맥 기준 약 50초 (모델 첫 다운로드 제외)
$P query 러닝                                # 근거 등급과 근거 기록 출력
$P serve                                    # 로컬 API http://127.0.0.1:8767
```

다른 터미널에서 웹 앱을 띄우고 노벨 모드에서 **기록 → 내 기록**을 고릅니다.

```bash
bun run dev      # 또는 npm run dev. Vite 가 /api/core 를 8767 로 넘긴다
```

노벨 화면의 AI 집필은 지금처럼 브라우저에 넣은 OpenRouter 키로 돌아갑니다 (⚙ 설정).

## 내 기록 가져오기

| 출처 | 내보내는 방법 | 명령 |
|---|---|---|
| 카카오톡 (PC) | 대화방 → 메뉴 → 대화 내용 → 대화 내보내기 → 텍스트 | `ingest kakao 파일.txt --me 내이름` |
| 카카오톡 (모바일) | 대화방 → ≡ → 설정 → 대화 내용 내보내기 → 텍스트만 보내기 | 같은 명령 (점 날짜·날짜 먼저·시각 먼저 세 형식 자동 인식) |
| 카카오톡 (Mac) | 대화방 → 대화 내보내기 → csv | 같은 명령 |
| 인스타그램 | 설정 → 계정 센터 → 내 정보 다운로드 → **JSON** 형식 | `ingest instagram 압축푼폴더` |
| 휴대폰 사진 | 사진 폴더 그대로 (jpg·png·heic) | `ingest photos 폴더` |

- `--me` 는 카카오톡에 표시되는 본인 이름입니다. 틀리면 보낸 사람 목록을 보여 주고 멈춥니다.
- 대화 상대 실명은 가져올 때 `지인A`, `지인B` … 로 바뀝니다. `엄마`·`팀장님`처럼 관계로 저장된 연락처는 그대로 둡니다.
- `people --relation "지인D=회사 동기"` 로 가명에 관계를 붙이면 이야기에서 그 관계로 부릅니다. 실명은 쓰지 마세요.
- 같은 파일을 다시 가져와도 기록 ID 가 같아 중복되지 않습니다. 새 기록만 `build` 가 계산합니다.
- 프로필(`-p`) 하나가 사람 한 명입니다. DB 는 `core/data/<프로필>.db` 에 생기고 저장소에는 올라가지 않습니다.

## 처리 단계

| 단계 | 모듈 | 하는 일 | 쓰는 것 |
|---|---|---|---|
| 가져오기 | `ingest/` | 출처마다 공통 레코드 `virtueme.record/1` 로 변환. 같은 사람이 3분 안에 연달아 보낸 메시지는 한 발화로 합침 | 직접 구현 |
| 개인정보 | `privacy.py` | 참여자 가명화, 본문 속 이름·전화·이메일·계좌·카드·주민번호·URL·계정 가리기 | 직접 구현 |
| 형태소 | `textproc.py` | 명사·용언 원형 추출 ("외로웠다" → "외롭다") | Kiwi |
| 임베딩 | `embed.py` | 텍스트 bge-m3, 사진 SigLIP 2. 사진은 자동 태그("사진: 바다, 해변")로 텍스트 공간에도 넣음 | sentence-transformers, transformers |
| 정서 | `emotion.py` | 기록마다 정서가(-1..1)·각성도(0..1). 텍스트는 LLM 채점, 사진은 SigLIP 비교 | OpenRouter (gemini-2.5-flash) |
| 패턴 | `patterns.py` | 날짜별 정서 곡선, 변화점, 주 단위 주기, 시간대 | ruptures (PELT), statsmodels (STL) |
| 검색 | `query.py` | 키워드 확장 → 직접 일치 + 의미 유사도 → LLM 판정 → 근거 3등급 | OpenRouter (확장 gemini-2.5-flash, 판정 claude-haiku-4.5) |
| 서사 오브젝트 | `narrative.py` | `virtueme.narrative/1` JSON, 근거 기록 목록, 비주얼 신호 7개 | `schema/narrative.v1.schema.json` |
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

## 로컬 API

| 요청 | 응답 |
|---|---|
| `GET /health` | 프로필, 기록 수, 기간, 쓰는 모델, 빌드 여부 |
| `POST /query` `{"keyword": "러닝"}` | `narrative_object`, `evidence_records`(최대 48건), `adjacent_records`, `people`(가명 → 관계) |
| `GET /records/{id}` | 기록 한 건 (가린 텍스트) |
| `GET /emotion` | 날짜별 정서 곡선 |
| `GET /profile` | 전체 요약: 변화점, 주기, 시간대 분포 |

응답의 텍스트는 가려진 것이고, 좌표와 원본 파일 경로는 들어 있지 않습니다.

## 평가 (2026-09-11)

| 무엇을 | 결과 | 다시 돌리기 |
|---|---|---|
| 합성 데이터 이식 | Storyseed 원본 JS 와 시드 7개 × 페르소나 4명, 28개 조합 모두 기록이 같음 | `uv run pytest tests/test_synthetic.py` |
| 텍스트 임베딩 모델 | 한국어 66문장 검색 MAP (코사인 그대로): bge-m3 0.514, multilingual-e5-base 0.413, SigLIP 2 0.20~0.22 | `uv run python eval/embed_bench.py` |
| 사진 검색 | 한국어 키워드 → 사진 15장, SigLIP 2 top-1 0.818 | 〃 |
| 검색 점수 설계 | bge-m3 코사인 0.514 → 배경 질의 z 점수 + 직접 일치 0.715 → + 키워드 확장 0.741 (강한 근거 F1 0.404 → 0.615) | `uv run python eval/retrieval_eval.py --grid` |
| LLM 판정 | 윤서연 본인 기록 345건·키워드 10개: 강한 근거 F1 0.48 → 0.68 (정밀도 0.38 → 0.71), 약한 근거 이상 F1 0.48 → 0.74 | `uv run python eval/profile_eval.py -p seoyeon` |
| 변화점 | 윤서연 정서 곡선에서 7월 5일 상승(0.01 → 0.28) 1건. 줄거리상 첫 러닝(6/29~7/5) 직후 | `uv run virtueme -p seoyeon query 러닝` |

- 교차 인코더 재순위 모델(bge-reranker-v2-m3)도 시험했지만 주제 키워드 검색에서 MAP 0.41 로 오히려 낮아 쓰지 않았습니다.
- LLM 판정 평가의 정답은 claude-sonnet-4.5 가 붙인 은(silver) 라벨입니다. 판정 모델과 같은 회사 모델이라
  일치율이 부풀 수 있으므로, 사람이 붙인 라벨로 2단계에서 다시 잽니다.

## 설정 (환경 변수)

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `OPENROUTER_API_KEY` | 없음 | 없으면 키워드 확장·LLM 판정·LLM 정서 채점을 건너뛰고 로컬 계산만 한다 |
| `VIRTUEME_DATA` | `core/data` | 프로필 DB 위치 |
| `VIRTUEME_LLM_MODEL` | `google/gemini-2.5-flash` | 키워드 확장·정서 채점 |
| `VIRTUEME_JUDGE_MODEL` | `anthropic/claude-haiku-4.5` | 근거 판정 |
| `VIRTUEME_TEXT_MODEL` / `VIRTUEME_VISION_MODEL` | `BAAI/bge-m3` / `google/siglip2-base-patch16-224` | 임베딩 |
| `VIRTUEME_TZ` | `Asia/Seoul` | 시간대가 없는 기록의 시간대 |

## 테스트

```bash
uv run pytest -q     # 25개. 모델을 내려받지 않는 해시 임베더로 돈다
```

## 알려진 한계 (2단계 이후 과제)

- 참여자 목록에 없는 제3자 이름(본문에만 나오는 동료 이름 등)은 가리지 못합니다. GLiNER 개인정보 탐지로 보완합니다.
- "비"처럼 한 글자 키워드는 판정 모델이 "비빔국수"를 관련으로 보는 식의 오판정이 남습니다.
- 감정 키워드(피곤, 행복)는 직접·간접 경계가 모델마다 달라 강한 근거 재현율이 낮습니다.
- 90일 기록으로는 연 단위 계절성을 계산할 수 없습니다 (주 단위만).
- 근거 검증은 아직 값 문자열 대조(v1)입니다. 기록 ID 단위 검증(근거 계약 v2)과 대화 모드 검증은 2단계입니다.
- 군집·지식그래프(`clusters` 필드)는 비어 있습니다. 2단계에서 BERTopic 과 개체·관계 추출로 채웁니다.
