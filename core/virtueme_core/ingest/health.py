"""건강 기록 가져오기: 삼성 헬스와 애플 건강. 하루에 한 건으로 모은다 (걸음 수, 잠, 운동).

삼성 헬스
- 삼성 헬스 앱 → 설정 → 개인 데이터 다운로드. 폴더 안에 표마다 CSV 가 있고 첫 줄은 표 이름·버전이라 둘째 줄이 머리글이다.
  최근 내보내기는 열 이름 앞에 `com.samsung.health.step_count.` 같은 표 이름이 붙어 있어 마지막 마디만 본다.
- 걸음: `com.samsung.shealth.tracker.pedometer_day_summary.*.csv` (하루 합계, 기기가 여럿이면 가장 큰 값). 없으면
  `com.samsung.health.step_count.*.csv` 를 기기별로 더해 가장 큰 기기 값.
- 잠: `com.samsung.shealth.sleep.*.csv` · `com.samsung.health.sleep.*.csv` 의 start_time·end_time (UTC, time_offset 로 지역 시간).
- 운동: `com.samsung.shealth.exercise.*.csv` · `com.samsung.health.exercise.*.csv` 의 exercise_type·duration·distance.

애플 건강
- 건강 앱 → 프로필 → 모든 건강 데이터 내보내기 → `apple_health_export/export.xml` (한국어 아이폰은 `내보내기.xml`).
  몇 GB 까지 커지므로 iterparse 로 한 줄씩 읽고 바로 지운다.
- 걸음 HKQuantityTypeIdentifierStepCount 는 아이폰·워치가 같은 걸음을 함께 적으므로 기기(sourceName)별로 더해 가장 큰 값.
- 잠 HKCategoryTypeIdentifierSleepAnalysis 는 잠든 값(Asleep, AsleepCore·Deep·REM·Unspecified)만, 기기별로 구간을 합쳐 가장 긴 기기.
- 운동 HKWorkout 은 종류·시간·거리.

어느 날 밤의 잠인지는 잠든 시각에 6시간을 더한 날짜로 정한다(새벽 1시 40분에 잠들면 그날, 밤 11시 반이면 다음 날).
기록 시각은 그날 23:59 이다.
"""
from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import DEFAULT_TZ
from ..records import Record, iso, record_id
from .traces import Window, end_of_day, find, minutes_ko, night_of, opened

# 삼성 헬스 운동 종류 (exercise_type). 자주 쓰는 것만, 나머지는 "운동"
SAMSUNG_EXERCISE = {1001: "걷기", 1002: "달리기", 11007: "자전거", 13001: "등산", 14001: "수영", 15003: "요가",
                    15006: "근력 운동", 10007: "배드민턴", 0: "운동"}
APPLE_WORKOUT = {"Running": "달리기", "Walking": "걷기", "Cycling": "자전거", "Hiking": "등산", "Swimming": "수영",
                 "Yoga": "요가", "TraditionalStrengthTraining": "근력 운동", "FunctionalStrengthTraining": "근력 운동",
                 "HighIntensityIntervalTraining": "인터벌 운동", "Badminton": "배드민턴", "Dance": "춤"}
ASLEEP = ("HKCategoryValueSleepAnalysisAsleep", "HKCategoryValueSleepAnalysisAsleepUnspecified",
          "HKCategoryValueSleepAnalysisAsleepCore", "HKCategoryValueSleepAnalysisAsleepDeep",
          "HKCategoryValueSleepAnalysisAsleepREM")


class Day:
    """하루 모음: 걸음, 잠 구간, 운동."""

    def __init__(self) -> None:
        self.steps: int | None = None
        self.sleep: list[tuple[datetime, datetime]] = []
        self.workouts: list[dict[str, Any]] = []


def _merge(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _record(d: date, day: Day, source_name: str) -> Record | None:
    parts: list[str] = []
    meta: dict[str, Any] = {"steps": day.steps, "sleep_min": None, "sleep_start": None, "sleep_end": None,
                            "workouts": day.workouts, "app": source_name}
    if day.steps is not None:
        parts.append(f"걸음 {day.steps:,}")
    if day.sleep:
        spans = _merge(day.sleep)
        total = round(sum((e - s).total_seconds() for s, e in spans) / 60)
        start, end = spans[0][0], spans[-1][1]
        meta.update(sleep_min=total, sleep_start=start.strftime("%H:%M"), sleep_end=end.strftime("%H:%M"))
        parts.append(f"수면 {minutes_ko(total)} ({meta['sleep_start']}~{meta['sleep_end']})")
    for w in day.workouts:
        parts.append(f"{w['type']} {minutes_ko(w['minutes'])}" + (f" {w['km']}km" if w.get("km") else ""))
    if not parts:
        return None
    dt = end_of_day(d)
    return Record(id=record_id("health", d.isoformat()), ts=iso(dt), source="health", kind="health", modality="text",
                  text="건강: " + " · ".join(parts), author="나", is_self=True, meta=meta)


# ---------- 삼성 헬스 ----------
def _samsung_rows(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines:
        return []
    # 첫 줄이 표 이름·버전이면 건너뛴다
    body = "\n".join(lines[1:] if lines[0].startswith("com.samsung") else lines)
    rows = []
    for row in csv.DictReader(io.StringIO(body)):
        rows.append({(k or "").split(".")[-1].strip(): (v or "").strip() for k, v in row.items() if k})
    return rows


OFFSET = re.compile(r"UTC([+-])(\d{2})(\d{2})")


def _samsung_time(value: str, offset: str | None, tz: str = DEFAULT_TZ) -> datetime | None:
    """'2026-06-03 16:40:00.000' (UTC) + 'UTC+0900' → 지역 시각."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    m = OFFSET.match(offset or "")
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return dt.astimezone(timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))))
    return dt.astimezone(ZoneInfo(tz))


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_samsung(root: Path, days: dict[date, Day]) -> int:
    files = 0
    summary = find(root, "com.samsung.shealth.tracker.pedometer_day_summary*.csv")
    by_day_dev: dict[tuple[date, str], int] = defaultdict(int)
    if summary:
        for f in summary:
            files += 1
            for row in _samsung_rows(f):
                ms, steps = _num(row.get("day_time")), _num(row.get("step_count"))
                if ms is None or steps is None:
                    continue
                d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()  # 그날 0시(UTC 표기)라 날짜만 쓴다
                key = (d, row.get("deviceuuid") or row.get("source_info") or "")
                by_day_dev[key] = max(by_day_dev[key], int(steps))
    else:
        for f in find(root, "com.samsung.health.step_count*.csv", "com.samsung.shealth.step_daily_trend*.csv"):
            files += 1
            for row in _samsung_rows(f):
                dt = _samsung_time(row.get("start_time", ""), row.get("time_offset"))
                steps = _num(row.get("count"))
                if dt is None or steps is None:
                    continue
                by_day_dev[(dt.date(), row.get("deviceuuid") or "")] += int(steps)
    for (d, _), steps in by_day_dev.items():
        day = days.setdefault(d, Day())
        day.steps = max(day.steps or 0, steps)
    for f in find(root, "com.samsung.shealth.sleep.*.csv", "com.samsung.health.sleep.*.csv"):
        files += 1
        for row in _samsung_rows(f):
            s = _samsung_time(row.get("start_time", ""), row.get("time_offset"))
            e = _samsung_time(row.get("end_time", ""), row.get("time_offset"))
            if s and e and e > s:
                days.setdefault(night_of(s), Day()).sleep.append((s, e))
    for f in find(root, "com.samsung.shealth.exercise.*.csv", "com.samsung.health.exercise.*.csv"):
        files += 1
        for row in _samsung_rows(f):
            s = _samsung_time(row.get("start_time", ""), row.get("time_offset"))
            dur = _num(row.get("duration"))
            if s is None or not dur:
                continue
            kind = SAMSUNG_EXERCISE.get(int(_num(row.get("exercise_type")) or 0), "운동")
            dist = _num(row.get("distance"))
            days.setdefault(s.date(), Day()).workouts.append(
                {"type": kind, "minutes": round(dur / 60000), "km": round(dist / 1000, 1) if dist else None})
    return files


# ---------- 애플 건강 ----------
def _apple_time(value: str | None, tz: str = DEFAULT_TZ) -> datetime | None:
    """'2026-06-03 08:50:00 +0900' → 지역 시각."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z").astimezone(ZoneInfo(tz))
    except ValueError:
        return None


def _km(value: str | None, unit: str | None) -> float | None:
    v = _num(value)
    if v is None:
        return None
    return round(v * {"km": 1, "m": 0.001, "mi": 1.609344}.get(unit or "km", 1), 1)


def parse_apple(path: Path, days: dict[date, Day], window: Window) -> None:
    steps: dict[tuple[date, str], float] = defaultdict(float)
    sleep: dict[tuple[date, str], list[tuple[datetime, datetime]]] = defaultdict(list)
    lo = window.since - timedelta(days=1) if window.since else None
    hi = window.until + timedelta(days=1) if window.until else None
    near = lambda d: (lo is None or d >= lo) and (hi is None or d <= hi)  # noqa: E731
    ctx = ET.iterparse(path, events=("start", "end"))
    _, root = next(ctx)
    for event, el in ctx:
        if event != "end":
            continue
        tag = el.tag
        if tag == "Record":
            t = el.get("type")
            if t == "HKQuantityTypeIdentifierStepCount":
                s = _apple_time(el.get("startDate"))
                if s and near(s.date()):
                    steps[(s.date(), el.get("sourceName") or "")] += _num(el.get("value")) or 0
            elif t == "HKCategoryTypeIdentifierSleepAnalysis" and el.get("value") in ASLEEP:
                s, e = _apple_time(el.get("startDate")), _apple_time(el.get("endDate"))
                if s and e and e > s and near(s.date()):
                    sleep[(night_of(s), el.get("sourceName") or "")].append((s, e))
        elif tag == "Workout":
            s, e = _apple_time(el.get("startDate")), _apple_time(el.get("endDate"))
            if s and e and near(s.date()):
                kind = (el.get("workoutActivityType") or "").replace("HKWorkoutActivityType", "")
                dur = _num(el.get("duration"))
                minutes = dur * (60 if el.get("durationUnit") == "hr" else 1) if dur else (e - s).total_seconds() / 60
                km = _km(el.get("totalDistance"), el.get("totalDistanceUnit"))
                if km is None:  # 최근 내보내기는 거리가 WorkoutStatistics 안에 있다
                    for st in el.iter("WorkoutStatistics"):
                        if "Distance" in (st.get("type") or ""):
                            km = _km(st.get("sum"), st.get("unit"))
                days.setdefault(s.date(), Day()).workouts.append(
                    {"type": APPLE_WORKOUT.get(kind, "운동"), "minutes": round(minutes), "km": km})
        else:
            continue
        el.clear()
        root.clear()  # 이미 읽은 원소가 뿌리에 쌓이지 않게
    best: dict[date, float] = {}
    for (d, _), n in steps.items():
        best[d] = max(best.get(d, 0), n)
    for d, n in best.items():
        days.setdefault(d, Day()).steps = int(round(n))
    by_night: dict[date, list[tuple[datetime, datetime]]] = {}
    for (d, _), spans in sleep.items():
        merged = _merge(spans)
        if sum((e - s).total_seconds() for s, e in merged) > sum((e - s).total_seconds() for s, e in by_night.get(d, [])):
            by_night[d] = merged
    for d, spans in by_night.items():
        days.setdefault(d, Day()).sleep = spans


def import_health(root: Path, window: Window | None = None) -> tuple[list[Record], dict[str, Any]]:
    window = window or Window()
    days: dict[date, Day] = {}
    apps: list[str] = []
    with opened(root) as base:
        if parse_samsung(base, days):
            apps.append("삼성 헬스")
        apple = [f for f in find(base, "export.xml", "내보내기.xml") if "cda" not in f.name]
        for f in apple:
            parse_apple(f, days, window)
        if apple:
            apps.append("애플 건강")
    name = " + ".join(apps) or None
    recs = [r for d, day in sorted(days.items()) if window.ok(d) and (r := _record(d, day, name or ""))]
    return recs, {"apps": apps, "days": len(recs), "with_steps": sum(1 for r in recs if r.meta["steps"] is not None),
                  "with_sleep": sum(1 for r in recs if r.meta["sleep_min"]),
                  "workouts": sum(len(r.meta["workouts"]) for r in recs), "out_of_range": window.skipped}
