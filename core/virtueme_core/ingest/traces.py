"""흔적 기록 가져오기의 공통 도구: 폴더·zip 열기, 파일 찾기, 기간 거르기, 시각 읽기.

흔적 기록은 각 서비스의 공식 "내 데이터 다운로드" 파일에서 읽는다(앱을 따로 설치하지 않는다). 유튜브·스포티파이처럼
몇 년 치가 수만 줄인 곳이 있어서 모든 가져오기가 기간(since·until, 그 지역 날짜 기준)을 받는다.
"""
from __future__ import annotations

import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import DEFAULT_TZ


@dataclass
class Window:
    """가져올 기간. 둘 다 None 이면 전부. 날짜는 기록 지역 시간 기준이고 until 도 그날을 포함한다."""

    since: date | None = None
    until: date | None = None
    skipped: int = field(default=0)

    @classmethod
    def of(cls, since: date | str | None = None, until: date | str | None = None) -> "Window":
        conv = lambda d: date.fromisoformat(d) if isinstance(d, str) and d else (d or None)  # noqa: E731
        return cls(conv(since), conv(until))

    def ok(self, dt: datetime | date) -> bool:
        d = dt.date() if isinstance(dt, datetime) else dt
        good = (self.since is None or d >= self.since) and (self.until is None or d <= self.until)
        if not good:
            self.skipped += 1
        return good


@contextmanager
def opened(path: Path) -> Iterator[Path]:
    """zip 이면 임시 폴더에 풀어서, 폴더·파일이면 그대로 돌려준다. 흔적 기록은 원본 파일을 가리키지 않으므로 다 읽고 나면 지운다."""
    path = Path(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        from ..importer import safe_extract

        with tempfile.TemporaryDirectory(prefix="virtueme-") as tmp:
            yield safe_extract(path, Path(tmp))
    else:
        yield path


def find(root: Path, *patterns: str) -> list[Path]:
    """root 아래에서 glob 패턴(대소문자 무시 안 함)에 맞는 파일. root 가 파일이면 이름이 맞을 때 그 파일만."""
    if root.is_file():
        return [root] if any(root.match(p.split("/")[-1]) for p in patterns) else []
    out: set[Path] = set()
    for p in patterns:
        out.update(x for x in root.rglob(p) if x.is_file() and not x.name.startswith((".", "._")) and "__MACOSX" not in x.parts)
    return sorted(out)


def parse_time(value: str | None, tz: str = DEFAULT_TZ) -> datetime | None:
    """ISO 8601 시각("2026-06-03T05:12:00.123Z", "+09:00" 포함)을 기록 지역 시간으로. 시간대가 없으면 tz 로 본다."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    # 소수 초가 6자리를 넘으면 fromisoformat 이 못 읽는다
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(ZoneInfo(tz))


def utc_naive(value: str, fmt: str, tz: str = DEFAULT_TZ) -> datetime | None:
    """시간대 표시 없이 UTC 로 적힌 시각(스포티파이·넷플릭스)."""
    try:
        return datetime.strptime(value.strip(), fmt).replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz))
    except (ValueError, AttributeError):
        return None


def end_of_day(d: date, tz: str = DEFAULT_TZ) -> datetime:
    """하루 요약 기록의 시각: 그날 23:59."""
    return datetime(d.year, d.month, d.day, 23, 59, tzinfo=ZoneInfo(tz))


def minutes_ko(minutes: float) -> str:
    """190 → '3시간 10분', 45 → '45분'."""
    m = int(round(minutes))
    h, m = divmod(m, 60)
    if h and m:
        return f"{h}시간 {m}분"
    return f"{h}시간" if h else f"{m}분"


def night_of(start: datetime) -> date:
    """잠든 시각으로 어느 날 밤의 잠인지 정한다: 6시간을 더한 날짜 (23:30 → 다음 날, 01:40 → 그날, 낮잠 14:00 → 그날)."""
    return (start + timedelta(hours=6)).date()
