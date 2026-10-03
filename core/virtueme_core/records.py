"""공통 레코드 스키마 `virtueme.record/1`.

카카오톡 메시지, 인스타그램 게시물, 사진, 합성 활동 로그가 모두 같은 모양으로
저장된다. 이후 단계(임베딩·정서·검색·서사 오브젝트)는 출처를 몰라도 된다.

흔적 기록(TRACE_KINDS): 유튜브 시청·구글 검색·지도 방문·카드 결제·건강 하루 요약·음악 재생처럼 사람이 글로 쓰지 않고
기기와 서비스에 남은 것. 모두 본인 기록(is_self, author "나")이고 text 는 검색·임베딩용 한국어 설명문이다
("유튜브 시청: 제목 (채널)"). 정서 채점·개체 추출·주제 묶기에서는 빼고, 키워드 검색의 근거로만 쓴다.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .config import DEFAULT_TZ

RECORD_SCHEMA = "virtueme.record/1"

SOURCES = ("kakao", "sms", "instagram", "photo", "synthetic",
           "youtube", "search", "maps", "card", "health", "spotify", "netflix")
KINDS = ("message", "post", "story", "reel", "comment", "photo", "activity",
         "watch", "listen", "search", "visit", "payment", "health")
TRACE_KINDS = ("watch", "listen", "search", "visit", "payment", "health")
MODALITIES = ("text", "image", "text+image")


def record_id(source: str, *parts: object) -> str:
    """출처와 원본 식별 정보로 만든 안정적인 ID. 같은 파일을 다시 가져와도 같은 ID 가 나온다."""
    h = hashlib.sha1("\x1f".join([source, *map(str, parts)]).encode("utf-8")).hexdigest()
    return f"r{h[:11]}"


def to_aware(dt: datetime, tz: str = DEFAULT_TZ) -> datetime:
    """시간대가 없는 시각은 기록이 만들어진 지역 시간(기본 서울)으로 본다."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(tz))
    return dt


def from_epoch(seconds: float, tz: str = DEFAULT_TZ) -> datetime:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(ZoneInfo(tz))


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@dataclass
class Record:
    id: str
    ts: str                       # ISO 8601, 시간대 포함
    source: str                   # SOURCES 가운데 하나
    kind: str                     # KINDS 가운데 하나 (흔적 기록은 TRACE_KINDS)
    modality: str                 # text | image | text+image
    text: str | None = None       # 본문. 가져올 때 이미 참여자 이름·연락처를 가린 상태로 저장한다
    media: list[str] = field(default_factory=list)   # 로컬 파일 경로
    author: str | None = None     # 가명 ("나", "지인A" …). 실명은 people 표에만 둔다
    is_self: bool = True
    thread: str | None = None     # 대화방·스레드 식별자 (가명 처리된 제목)
    participants: list[str] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    place: str | None = None
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"알 수 없는 출처: {self.source}")
        if self.kind not in KINDS:
            raise ValueError(f"알 수 없는 기록 종류: {self.kind}")
        if self.modality not in MODALITIES:
            raise ValueError(f"알 수 없는 형식: {self.modality}")
        datetime.fromisoformat(self.ts)  # 형식 검사

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.ts)

    @property
    def day(self) -> str:
        return self.dt.date().isoformat()

    @property
    def is_trace(self) -> bool:
        """글로 쓴 기록이 아니라 기기·서비스에 남은 흔적인가."""
        return self.kind in TRACE_KINDS

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def shift_minutes(ts: str, minutes: float) -> str:
    return iso(datetime.fromisoformat(ts) + timedelta(minutes=minutes))
