"""막장극장: 노벨 화면의 이야기를 약 18.5초 막장 쇼츠로 만든다 (팀 막장극장 파이프라인 v3 규격, OpenRouter 로 자동화).

팀 원본은 대본 생성 → Runway 웹에서 클립·BGM 수동 생성 → 조립의 세 단계였다. 가운데 단계를 OpenRouter 영상 API 로
바꿔 버튼 하나로 끝까지 돈다.

1. 대본 (story.py): 노벨 이야기 + 씨앗 단어 → 5장면 JSON. 규칙·출력 형식은 팀 1_gen_story.py 그대로이고, 재료만
   「데모 페르소나 90일 기록」에서 「이 노벨」로 바꿨다. 국적 12개국 추첨, 모델 anthropic/claude-sonnet-4.5.
2. 클립·BGM (gen.py): 장면 5개를 병렬로 runway/gen-4.5(16:9, 720p, 4초)에 보낸다. 프롬프트는 팀 README 공식
   (visual + 주인공 외모 + 상대역 외모 + Photorealistic live-action …)이고 외모 묘사는 장면마다 글자 하나 바꾸지 않는다.
   Runway 가 거절하면 kwaivgi/kling-v3.0-std 로 한 번 더 시도한다. BGM 은 google/lyria-3-clip-preview(30초)에 mood 를 보낸다.
3. 조립 (assemble.py): 타이틀 1.3초 + 장면 3.2/3.0/3.0/3.0/3.6초 + 엔딩 1.4초, 1280×720·24fps, BGM 페이드.
   글꼴·크기·색·위치는 팀 2_assemble.py 값 그대로, drawtext 대신 PIL PNG 를 얹는다.

결과: $VIRTUEME_DATA/makjang/<프로필>/<영상 번호>/ 에 story.json, scene1~5.mp4, bgm.*, makjang.mp4, report.json.
실패한 뒤 다시 누르면 이미 만든 클립은 건너뛴다.

비용(2026-10-24 단가): 클립 4초 × 5개 × $0.12/초 = $2.40, BGM $0.04, 대본 몇 센트 → 한 편 약 $2.5, 2~5분.

쓰는 법 (OPENROUTER_API_KEY = 6조 팀 키):
  앱: NOVEL → 단어 심기 → 이야기 아래 「막장극장 만들기」, 만든 뒤에는 「다른 에피소드 만들기」(take+1)
  CLI: uv run virtueme makjang 러닝 --story 이야기.txt [--take 1] [--story-json 팀대본.json]
  API: POST /makjang {keyword, story, title?, take?} → GET /makjang/{id} → GET /makjang/{id}/makjang.mp4

환경 변수: VIRTUEME_MAKJANG_STORY_MODEL, VIRTUEME_MAKJANG_VIDEO_MODEL, VIRTUEME_MAKJANG_VIDEO_FALLBACK,
VIRTUEME_MAKJANG_MUSIC_MODEL.

개인정보: 영상·음악 모델에는 대본의 영어 장면 묘사와 분위기 문장만 가고, 대본 모델에는 화면에 보이는 노벨 본문
(가명은 관계로 바뀐 글)이 간다. 세 요청 모두 outbound 실명 검사를 거쳐 프로필 전송 로그에 남는다.
"""
from .make import FINAL, make_makjang, makjang_dir, video_id

__all__ = ["FINAL", "make_makjang", "makjang_dir", "video_id"]
