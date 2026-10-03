# 책 디자인: 표지 사례에서 장르 문법을 세고, 그 문법으로 표지와 본문을 짭니다

이야기(`/story` 결과)와 서사 오브젝트를 받아 신국판(152×225mm) 책 한 권의 앞표지, 장 시작 면, 삽화가 든 본문 펼침면을
PNG 와 PDF 로 만듭니다. 디자인 값은 손으로 정하지 않고, 실제 책 표지 206장을 장르별로 재고 라벨을 붙여 센 분포에서 고릅니다.

## 여기서 "배운다"는 뜻

모델을 새로 학습하거나 미세 조정하지 않습니다. 11월 7일 시연까지 5주 안에 검증할 수 있는 방식으로, 다음 두 가지를 합니다.

1. **사례 검색**: 참고 표지마다 측정값과 라벨을 붙여 두고, 책을 만들 때 장르와 분위기가 맞는 표지 6장을 찾습니다.
2. **디자인 문법 추출**: 장르마다 레이아웃 원형·제목 위치·서체·그림 양식·분위기·명도·색의 분포를 셉니다
   (`grammar.json`). 브리프는 이 분포의 절반과 찾은 6장의 표 절반을 더해 값을 정합니다.

그래서 결과를 설명할 수 있습니다. 브리프 파일(`brief.json`)에 고른 값마다 후보 점수와 출처("참고 표지", "기본값",
"사람이 고름"), 참고한 표지의 제목·출처 URL, 분위기를 정한 이유가 그대로 남습니다. 대신 새로운 디자인을 발명하지는 않습니다.
참고 표지에 없는 문법은 나오지 않고, 그림의 품질은 이미지 생성 모델에 달려 있습니다.

## 돌려 보기

```bash
cd core
export OPENROUTER_API_KEY=sk-or-v1-...      # 없으면 라벨은 건너뛰고, 그림은 절차적 그림으로 그린다

M="uv run python -m virtueme_core.book"
$M collect --per-genre 30      # Open Library 에서 장르마다 30장 (한국어판 절반) → $VIRTUEME_DATA/book_refs/
$M annotate                    # 측정값 + 비전 라벨 → references.json (키가 없으면 측정값만, --no-llm 도 같음)
$M grammar                     # 장르별 분포 → grammar.json, 아래 표 출력

S=virtueme_core/book/samples
$M brief  --genre 미스터리 --narrative "$S/seoyeon_narratives.json#콩이" --story $S/mystery.txt
$M render --genre 미스터리 --narrative "$S/seoyeon_narratives.json#콩이" --story $S/mystery.txt \
          --author 윤서연 -p seoyeon --copy-to ../../shots/stage6-book-mystery
```

- `--narrative` 는 서사 오브젝트 JSON 이나 `/query` 응답 전체를 받습니다. 예시 묶음 파일은 `경로#키` 로 고릅니다.
- `--story` 는 `/story` 출력 형식(첫 줄 `TITLE: 제목`, 빈 줄, 단락)이나 그냥 단락 글입니다. `<em ref>` 앵커는 벗겨서 씁니다.
- `--set archetype=framed --set typeface=serif` 처럼 사람이 값을 덮어쓸 수 있습니다. 통제 어휘 밖의 값은 거부합니다.
- `--offline` 은 네트워크 없이 절차적 그림으로 그립니다. `--fresh` 는 같은 프롬프트로 그린 그림이 있어도 새로 그립니다.
- `references.json`·`grammar.json` 은 저장소에 들어 있으므로, 새로 책을 만들 때는 `collect`·`annotate` 를 다시 돌릴 필요가
  없습니다. 다만 그림 생성에 참고 표지 이미지를 넘기려면 `collect` 로 이미지를 한 번 받아 두어야 합니다(없으면 참고 이미지
  없이 그립니다).

코드에서 쓰는 함수는 두 개입니다. CLI·API 연결은 이 둘을 부르면 됩니다.

| 함수 | 하는 일 |
|---|---|
| `book.brief(genre, narrative_object=None, story_text=None, overrides=None)` | 네트워크 없이 디자인 브리프(dict) |
| `book.make_book(genre, story_text, title=None, author="나", narrative_object=None, profile="default", offline=False)` | 브리프 + 그림 + PNG·PDF, 반환값은 `brief.json` 내용과 파일 경로 |

`genre` 는 Storyseed 장르 번호(0 미스터리 ~ 5 코미디, 6 문학·에세이), 한국어 이름, 영문 키 가운데 무엇이든 됩니다.

## 참고 표지

| 항목 | 값 |
|---|---|
| 출처 | Open Library 검색 API(주제어) + 표지 API. 장르마다 한국어판(`language:kor`)을 먼저, 모자라면 1985년 이후 원서를 읽은 사람 수 순으로 |
| 모은 수 | 210장 (한국어판 105장). 비전 모델이 "앞표지가 아님"으로 본 4장을 빼고 206장을 씀 |
| 주제어 | 미스터리 detective and mystery stories·mystery, 로맨스 love stories·romance, SF science fiction, 판타지 fantasy, 스릴러 thriller·suspense, 코미디 humorous fiction·humor, 문학 korean fiction·essays·literary fiction |
| 거른 것 | 8KB 미만 파일, 폭 180px 미만, 세로/가로 비율이 1.2~1.75 밖인 이미지 (책등·상자 사진·잘린 스캔) |
| 저장 위치 | 이미지는 `$VIRTUEME_DATA/book_refs/img/` 에만 두고 저장소에 넣지 않습니다. 제목·저자·출판사·연도·출처 URL 은 `references.json` 에 |

Open Library 표지는 이용자가 올린 것이라 표지마다 저작권자가 따로 있습니다. 경향을 재는 데만 쓰고, 그림 생성에 넘기는
참고 이미지는 512px 로 줄인 사본입니다. 주제어 분류가 거칠어서 「해리 포터」가 미스터리로 잡히는 식의 잡음이 있습니다.
그래서 비전 모델에게 장르도 따로 짐작하게 하고(아래 표의 "장르 일치"), 검색할 때 둘이 같으면 가산점을 줍니다.

## 측정값과 통제 어휘

결정적으로 재는 값(`features.py`, 같은 이미지면 언제나 같은 값):

| 값 | 재는 방법 |
|---|---|
| 팔레트 | 128px 로 줄인 픽셀을 CIELAB 에서 k-means(k=5, 시드 고정), 색과 면적 비율 |
| 명도·톤 | 평균 L\*. 45 미만 dark, 65 초과 light |
| 대비 | 명도 표준편차 / 50 |
| 색채도 | Hasler–Süsstrunk colorfulness / 100 |
| 윤곽 밀도 | 소벨 기울기 40 초과 픽셀 비율 |
| 여백 | 16×16 칸 가운데 명도 표준편차 4 미만인 칸의 비율 |

비전 모델(`google/gemini-2.5-flash`, 표지 8장씩 한 요청)은 아래 어휘에서만 고릅니다. 목록 밖의 답은 `vocab.normalize_label` 이
가까운 값으로 바꾸거나 버립니다.

| 칸 | 값 |
|---|---|
| 레이아웃 원형 | `full_bleed` 그림이 전면, 제목이 위에 · `framed` 여백 안 액자 · `typographic` 글자와 색면만 · `split` 색면과 그림이 면을 나눔 · `pattern` 반복 무늬 바탕 · `centered_motif` 단색 바탕 가운데 작은 그림 |
| 제목 위치 | `top` · `middle` · `bottom` |
| 제목 크기 | `small`(폭 40% 미만) · `medium` · `large`(70% 이상) · `dominant` |
| 서체 계열 | `serif` · `sans` · `display` · `handwritten` |
| 그림 양식 | `photo` · `painterly` · `flat_vector` · `line` · `collage` · `abstract` · `none` |
| 분위기 (최대 4) | warm 따뜻한 · melancholic 쓸쓸한 · tense 긴장된 · eerie 서늘한 · playful 장난스러운 · dreamy 몽환적인 · serene 고요한 · energetic 힘찬 · nostalgic 그리운 · romantic 설레는 · mysterious 수수께끼 같은 · hopeful 희망찬 · dark 어두운 · whimsical 엉뚱한 |

라벨 품질은 사람이 전수 검사하지 않았습니다. 무작위 12장을 눈으로 본 결과 레이아웃 원형은 10장, 제목 위치는 9장이 맞았습니다.

## 장르별 문법 (2026-10-03, 206장)

| 장르 | 표지 (한국어판) | 레이아웃 원형 | 제목 위치 · 크기 | 서체 | 그림 양식 | 분위기 | 어두운 표지 | 색채도 | 장르 일치 |
|---|---|---|---|---|---|---|---|---|---|
| 미스터리 | 30 (15) | full_bleed 63% · split 27% | middle 43% · medium 40% | sans 47% · serif 23% | photo 43% · collage 17% | 수수께끼 같은, 어두운, 긴장된 | 57% | 0.56 | 60% |
| 로맨스 | 30 (15) | full_bleed 63% · split 20% | middle 50% · medium 53% | sans 33% · serif 23% | photo 43% · painterly 30% | 설레는, 희망찬, 고요한 | 27% | 0.55 | 60% |
| SF | 30 (15) | full_bleed 60% · centered_motif 17% | top 47% · medium 57% | sans 37% · display 30% | painterly 37% · flat_vector 23% | 수수께끼 같은, 어두운, 긴장된 | 57% | 0.50 | 40% |
| 판타지 | 30 (15) | full_bleed 53% · split 23% | top 53% · medium 53% | display 30% · serif 27% | painterly 40% · line 37% | 수수께끼 같은, 어두운, 엉뚱한 | 37% | 0.58 | 77% |
| 스릴러 | 28 (13) | full_bleed 68% · split 18% | middle 54% · medium 43% | sans 39% · serif 29% | photo 50% · line 11% | 수수께끼 같은, 어두운, 긴장된 | 68% | 0.58 | 46% |
| 코미디 | 30 (15) | framed 27% · full_bleed 23% | top 73% · medium 77% | handwritten 47% · display 23% | line 60% · photo 13% | 장난스러운, 엉뚱한, 힘찬 | 20% | 0.71 | 47% |
| 문학·에세이 | 28 (13) | full_bleed 50% · split 25% | top 50% · medium 54% | sans 46% · handwritten 25% | photo 32% · painterly 18% | 수수께끼 같은, 쓸쓸한, 고요한 | 32% | 0.46 | 96% |

전체 평균보다 1.5배 이상 자주 나온 값(`grammar.json` 의 `lift`)이 장르를 가르는 특징입니다.

- 코미디는 반복 무늬 바탕(5.1배)과 액자형(2.8배), 손글씨 제목(2.2배), 선화(2.8배)가 두드러지고 색채도가 가장 높습니다(0.71).
  어두운 표지는 20%로 가장 적습니다.
- 문학·에세이는 글자만으로 짠 표지가 7.1배 많고, 여백 비율이 0.48로 가장 넓으며 색채도가 가장 낮습니다(0.46).
- 스릴러는 어두운 표지가 68%로 가장 많고, 제목이 표지를 차지하는 판(2.6배)과 그림 없는 판(3.2배)이 많습니다.
- 미스터리는 콜라주(2.5배)와 제목 아래 배치(2.4배), SF 는 추상(2.3배)·평면 벡터(1.9배)와 가운데 작은 그림(1.6배),
  판타지는 회화(1.8배)·선화(1.7배)가 두드러집니다.
- SF 는 표지만 보고 SF 로 짐작한 비율이 40%로 가장 낮습니다. 주제어로 모은 표지가 장르를 가장 덜 드러냅니다.

## 브리프: 기록이 디자인 값이 되는 길

분위기는 장르 기본값에 서사 오브젝트를 더해 정합니다(`briefing.target_moods`).

| 입력 | 분위기 |
|---|---|
| 장르 (가중치 1.0) | 미스터리 mysterious, 로맨스 romantic, SF eerie, 판타지 dreamy, 스릴러 tense, 코미디 playful, 문학 serene |
| 정서 평균 `emotion.mean` (0.8) | 0.25 이상 warm, 0.1 이상 hopeful, -0.2 이하 melancholic, -0.45 이하 dark |
| 근거 기간 평균 각성도 (0.8) | 0.55 이상 energetic, 0.3 이하 serene |
| 비주얼 신호 (0.8) | conflict 0.5↑ tense, uncertainty 0.55↑ mysterious, novelty 0.6↑ whimsical, stability 0.75↑ serene, activity 0.6↑ energetic, possibility 0.62↑ hopeful · 0.38↓ nostalgic |
| 이야기 본문 (최대 0.5) | 감정 낱말(웃음·설렘·비밀·그리움·달리기 등) 수 |

참고 표지 점수는 같은 장르 1.0(비전 모델도 그 장르로 보면 +0.25, 다른 장르인데 그 장르로 보이면 0.35) + 분위기가 겹친
가중치 비율 + 톤 맞춤 0.2(정서가 밝으면 밝은 표지, 어두우면 어두운 표지)입니다. 상위 6장을 씁니다.

칸마다 값은 "장르 분포 × 0.5 + 6장의 점수 가중 표 × 0.5" 가 가장 큰 것입니다. 가운데 그림이 있는 원형(액자·가운데 그림)에서는
제목을 가운데에 두지 않습니다.

팔레트는 6장의 주요 색과 장르 팔레트를 CIELAB 에서 다시 묶은 5색을 정서 쪽으로 옮긴 것입니다. 정서가 v 만큼 명도 +8v,
색상각은 밝으면 따뜻한 쪽(60°), 어두우면 차가운 쪽(250°)으로 최대 |v|×20°, 채도는 × (0.8 + 0.5 × 각성도)입니다.
바탕·글자·강조색은 WCAG 대비로 고릅니다(글자 4.5 이상). 정서 곡선 자체는 장 시작 면의 선과 글자만 있는 표지의 선으로 그립니다.

## 렌더

| 항목 | 값 |
|---|---|
| 판형 | 신국판 152×225mm, PNG 는 2배 해상도, PDF 는 표지·장 시작·본문 모든 쪽 |
| 표지 틀 | 레이아웃 원형 6개마다 HTML/CSS 틀 하나 |
| 서체 | 이 맥에 있는 서체만 `local()` 로: 명조 나눔명조, 고딕 Pretendard, 장식 배민 도현·연성, 손글씨 나눔손글씨 펜·붓. 렌더 보고서 `fonts` 에 서체마다 loaded·error 가 남습니다 |
| 본문 | 나눔명조 10pt, 줄 간격 1.72, `word-break: keep-all`, 양끝 맞춤, 들여쓰기 1자. 쪽 머리(왼쪽 책 제목, 오른쪽 장 제목)와 쪽 번호 |
| 쪽 나누기 | 브라우저에서 단락을 넣어 보고 넘치면 어절 단위로 잘라 다음 쪽으로. 삽화는 이야기 가운데 단락 뒤에 넣고, 자리가 모자라면 다음 쪽 맨 위로 |
| 제목 맞추기 | title_scale 크기(24·32·42·56pt, 손글씨는 1.35배)에서 시작해 상자를 넘치지 않을 때까지 6%씩 줄임 |
| 그림 위 제목 | 제목 자리의 그림 명도·복잡도를 재서 글자색을 고르고, 복잡도 0.3 초과나 대비 4.5 미만이면 그 쪽에서 번지는 반투명 막을 깖. 아래쪽 출판사 표시도 따로 잼 |

그림은 OpenRouter `google/gemini-3.1-flash-image-preview`(1K)로 그립니다. 표지는 2:3(전면·무늬) 또는 1:1, 삽화는 3:2 입니다.

- 표지 그림에는 브리프의 참고 표지 가운데 그림 양식이 같은 것 3장을 스타일 참고로 넘기고, 삽화에는 방금 그린 표지 그림을
  넘겨 같은 그림체로 맞춥니다.
- 프롬프트에 글자·의성어·글자가 실리는 물건(화면·간판·번호판)을 그리지 말라고 넣고, 받은 그림은 비전 모델이 읽을 수 있는
  글자가 있는지 다시 봅니다. 있으면 두 번까지 다시 그리고, 그래도 있으면 절차적 그림으로 바꿉니다. 이번 시험에서 그린 42장
  가운데 15장이 이 검사에 걸렸습니다(검사 기준을 다듬기 전 포함. 신발 로고처럼 글자 없는 표식까지 잡던 기준을 고쳤습니다).
- 키가 없거나 실패하면 팔레트와 정서 곡선으로 그린 물결·무늬·동심원 그림을 씁니다. 시드가 브리프 번호라 같은 브리프면 같은
  그림입니다.
- 같은 프롬프트로 그린 AI 그림은 `art_cache.json` 으로 알아보고 다시 사지 않습니다.

결과 폴더 `$VIRTUEME_DATA/books/<프로필>/<브리프 번호>/` 에는 `brief.json`, `cover_art.png`, `illustration.png`,
`book.html`, `cover.png`, `opener.png`, `spread.png`, `book.pdf` 가 남습니다. 그림 요청은 다른 요청처럼 `outbound` 로그에
"책 cover_art 그림 생성", "그림 속 글자 검사" 용도로 남습니다(이미지는 로그에 넣지 않고 장 수와 참고 표지 번호만).

## 비용

| 무엇 | 비용 (OpenRouter `usage.cost`) |
|---|---|
| 참고 표지 210장 라벨 (한 번) | $0.074, 27개 요청, 36초 |
| 책 한 권: 그림 2장 + 글자 검사 2회 | $0.139 (다시 그리기 없음) ~ $0.279 (두 번 다시 그림), 25~55초 |
| 같은 브리프로 다시 렌더 | $0 (그림 캐시), 약 3초 |
| `--offline` | $0, 약 3초 |

## 예시 결과

가상 인물 윤서연(러닝, 강아지 콩이, 제주)으로 쓴 짧은 이야기 네 편이 `samples/` 에 있습니다. 서사 오브젝트
(`samples/seoyeon_narratives.json`)는 `examples/seoyeon/README.md` 의 실행 결과(등급·기간·변화점·정서 평균)를 옮겨 손으로
만든 것이고, 정서 곡선 모양은 줄거리에 맞춘 근사값입니다. 결과 PNG 는 `../shots/stage6-book-<장르>-{cover,opener,spread}.png`.

| 장르 · 키워드 | 브리프 |
|---|---|
| 미스터리 · 콩이 | 전면 사진, 가운데 제목, Pretendard Black, 분위기 mysterious·energetic·hopeful·warm |
| 로맨스 · 러닝 | 전면 사진, 가운데 제목, Pretendard Bold |
| 코미디 · 제주 | 전면 선화, 위쪽 손글씨 제목(나눔손글씨 펜) |
| 판타지 · 외로움 | 전면 회화, 가운데 명조 제목, 분위기 dreamy·mysterious·melancholic·dark |

## 테스트

```bash
uv run pytest -q tests/test_book.py    # 12개. 네트워크 없이 팔레트·어휘·검색 순위·브리프·오프라인 렌더
```

오프라인 렌더 테스트는 `node` 와 `virme/` 의 Playwright 가 있어야 돌고, 없으면 건너뜁니다.

## 알려진 한계

- "배운 것"은 206장의 분포입니다. 장르마다 28~30장이라 한두 장이 비율을 3~4%포인트 움직이고, 라벨은 비전 모델 한 번의 판단입니다.
- 원서 표지가 절반이고 Open Library 주제어가 거칠어 장르 일치가 40~96%로 들쭉날쭉합니다. 국내 출판사 표지를 더 모으려면
  알라딘·국립중앙도서관 API 키가 필요합니다.
- 장르 분포에서 전면 그림(full_bleed)이 50~68%라, 서사가 크게 다르지 않으면 대부분 이 원형이 뽑힙니다. 다른 원형은 코미디·문학이나
  `--set archetype=` 으로 나옵니다. 여섯 원형 모두 렌더는 확인했습니다.
- 이미지 모델이 제목 자리를 비우라는 지시를 위아래로 나뉜 구도로 해석하는 일이 있습니다(미스터리 예시). 마음에 들지 않으면
  `--fresh` 로 다시 그립니다. 사람이 한 번 보고 고르는 단계를 전제로 합니다.
- 한국어 본문은 어절 단위로 줄을 바꾸고 양끝을 맞추므로 어절 사이가 벌어지는 줄이 있습니다. 브라우저가 글자 사이 맞춤
  (`text-justify: inter-character`)을 지원하지 않아 자간을 1.5% 좁히는 데서 멈췄습니다.
- 장은 하나만 만듭니다(이야기 한 편 = 한 장). 여러 편을 한 권으로 묶는 목차·속표지는 아직 없습니다.
