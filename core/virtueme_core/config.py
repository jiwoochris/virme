"""경로·모델·임계치 설정.

값은 전부 여기 한 곳에 두고, 환경 변수로 덮어쓸 수 있게 한다. 임계치를 바꾸면
서사 오브젝트의 `grade_basis` 에도 그대로 실리므로, 등급이 왜 그렇게 나왔는지
화면과 발표에서 설명할 수 있다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("VIRTUEME_DATA", CORE_ROOT / "data"))
SCHEMA_DIR = CORE_ROOT / "schema"

DEFAULT_TZ = os.environ.get("VIRTUEME_TZ", "Asia/Seoul")

# 텍스트끼리의 의미 비교는 bge-m3, 사진과 한국어 키워드의 비교는 SigLIP 2.
# 2026-09-11 eval/embed_bench.py (한국어 66문장·사진 15장): SigLIP 2 텍스트 탑의 문장 검색 MAP 은
# 0.20~0.22 로 bge-m3(0.514)·multilingual-e5-base(0.413)보다 크게 낮았고, 사진 검색은 top-1 0.818 로 충분했다.
TEXT_MODEL = os.environ.get("VIRTUEME_TEXT_MODEL", "BAAI/bge-m3")
VISION_MODEL = os.environ.get("VIRTUEME_VISION_MODEL", "google/siglip2-base-patch16-224")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# 키워드 확장·정서 채점처럼 양이 많은 일은 저렴한 모델
LLM_MODEL = os.environ.get("VIRTUEME_LLM_MODEL", "google/gemini-2.5-flash")
# 근거 판정은 품질이 등급을 좌우하므로 한 단계 좋은 모델. 2026-09-11 eval/profile_eval.py 에서
# 강한 근거 F1 이 gemini-2.5-flash 0.625, claude-haiku-4.5 0.675 였다 (은 라벨: claude-sonnet-4.5).
JUDGE_MODEL = os.environ.get("VIRTUEME_JUDGE_MODEL", "anthropic/claude-haiku-4.5")


def openrouter_key() -> str | None:
    """OPENROUTER_API_KEY 환경 변수. 없으면 LLM 단계는 로컬 대체 경로로 돈다."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return key or None


@dataclass(frozen=True)
class RelevanceConfig:
    # 의미 유사도 z 점수를 0..1 로 바꾸는 로지스틱 곡선의 중심과 폭.
    # eval/retrieval_eval.py --grid 결과(2026-09-11)로 정했다: 한국어 문장 66개 강한 근거 F1 0.615,
    # 합성 태그 35개 F1 0.671 로 두 평가셋 합산 점수가 가장 높았다.
    z_center: float = 2.25
    z_width: float = 0.55
    # 키워드 자체와 확장 표현 점수를 섞는 비율 (0.5 = 반반), 확장 표현은 상위 k 개 평균
    expansion_mix: float = 0.5
    expansion_top_k: int = 3
    # 직접 일치 점수: 키워드 원형, 활용형·동의어
    direct_keyword: float = 0.9
    direct_form: float = 0.75
    strong: float = 0.6
    weak: float = 0.35
    # 강한 근거 기록 주변에서 같은 대화방 기록을 인접 근거로 붙이는 시간 창(분)
    adjacent_minutes: int = 20
    # LLM 판정: 임베딩 관련도가 이 값 이상인 기록 가운데 상위 judge_limit 건만 판정한다
    judge_floor: float = 0.2
    judge_limit: int = 240


@dataclass(frozen=True)
class GradeConfig:
    strong_min_records: int = 8
    strong_min_days: int = 5
    strong_min_day_share: float = 0.05  # 기록이 있는 날 가운데 강한 근거가 나온 날의 비율
    weak_min_records: int = 3
    weak_min_days: int = 2
    # 또는: 강한·약한 근거를 합쳐 이 건수·일수 이상이면 약한 근거
    weak_min_any_records: int = 8
    weak_min_any_days: int = 4


@dataclass(frozen=True)
class Settings:
    relevance: RelevanceConfig = field(default_factory=RelevanceConfig)
    grade: GradeConfig = field(default_factory=GradeConfig)
    # 다른 사람이 보낸 메시지는 기본적으로 근거 등급 계산에서 뺀다 (인접 근거로만 쓴다)
    include_others_as_evidence: bool = False
    # 임베딩 후보를 LLM 이 한 번 더 판정한다 (키가 없으면 자동으로 끈다)
    use_judge: bool = True
    # LLM 에 넘기는 근거 기록 수 상한
    context_record_limit: int = 48
    # 기록 한 건을 LLM 에 넘길 때 자르는 길이
    context_text_chars: int = 160


SETTINGS = Settings()


def profile_db_path(profile: str) -> Path:
    safe = "".join(ch for ch in profile if ch.isalnum() or ch in "-_") or "default"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{safe}.db"
