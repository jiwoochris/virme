"""스포티파이 재생 기록과 넷플릭스 시청 기록 가져오기.

스포티파이
- 계정 → 개인정보 보호 설정 → 데이터 다운로드. 기본 "계정 데이터"에는 최근 1년 `StreamingHistory_music_0.json`
  (예전 이름 `StreamingHistory0.json`, 항목: endTime·artistName·trackName·msPlayed, 시각은 UTC)이 있고,
  "전체 재생 기록(Extended streaming history)"을 고르면 `Streaming_History_Audio_2026_0.json`
  (ts·master_metadata_album_artist_name·master_metadata_track_name·ms_played)이 온다. 둘 다 읽는다.
- 30초보다 짧게 들은 곡은 넘긴 것으로 보고 뺀다. 팟캐스트 회차(곡 이름이 없는 항목)도 뺀다.
- 기록 시각은 재생을 시작한 시각(끝난 시각 - 들은 시간)이다.

넷플릭스
- 계정 → 프로필 → 개인 정보 다운로드 → `CONTENT_INTERACTION/ViewingActivity.csv`
  (Profile Name, Start Time(UTC), Duration, Title, Supplemental Video Type …). 가족이 같은 계정을 쓰면 프로필 이름으로 거른다.
- 예고편·미리보기(Supplemental Video Type 이 있는 줄)와 1분보다 짧은 재생은 뺀다.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..privacy import Masker
from ..records import Record, iso, record_id
from .traces import Window, find, opened, parse_time, utc_naive

MIN_PLAY_MS = 30_000
SERIES = re.compile(r"^(?P<series>.+?): (?:시즌|Season|리미티드 시리즈|Limited Series|파트|Part|Volume|Book|챕터|Chapter)\b")


def _listen(end: datetime, ms: int, artist: str, track: str, masker: Masker) -> Record:
    start = end - timedelta(milliseconds=ms)
    return Record(
        id=record_id("spotify", iso(end), artist, track),
        ts=iso(start), source="spotify", kind="listen", modality="text",
        text=masker.mask(f"음악: {artist} - {track}"), author="나", is_self=True,
        meta={"artist": artist, "track": track, "ms_played": ms},
    )


def import_spotify(root: Path, masker: Masker, window: Window | None = None) -> tuple[list[Record], dict[str, Any]]:
    window = window or Window()
    recs: dict[str, Record] = {}
    short = podcast = 0
    with opened(root) as base:
        files = find(base, "StreamingHistory*.json", "Streaming_History_Audio_*.json")
        for f in files:
            for item in json.loads(f.read_text(encoding="utf-8")):
                if "ts" in item:  # 전체 재생 기록
                    end = parse_time(item.get("ts"))
                    ms = int(item.get("ms_played") or 0)
                    artist = item.get("master_metadata_album_artist_name")
                    track = item.get("master_metadata_track_name")
                else:
                    end = utc_naive(str(item.get("endTime") or ""), "%Y-%m-%d %H:%M")
                    ms = int(item.get("msPlayed") or 0)
                    artist, track = item.get("artistName"), item.get("trackName")
                if not track or not artist or end is None:
                    podcast += 1
                    continue
                if ms < MIN_PLAY_MS:
                    short += 1
                    continue
                if not window.ok(end - timedelta(milliseconds=ms)):
                    continue
                r = _listen(end, ms, str(artist).strip(), str(track).strip(), masker)
                recs[r.id] = r
    out = sorted(recs.values(), key=lambda r: r.ts)
    return out, {"files": len(files), "plays": len(out), "short": short, "not_music": podcast,
                 "out_of_range": window.skipped}


def _duration_min(value: str) -> float:
    parts = [int(x) for x in re.findall(r"\d+", value or "")]
    if len(parts) == 3:
        h, m, s = parts
        return h * 60 + m + s / 60
    if len(parts) == 2:
        return parts[0] + parts[1] / 60
    return 0.0


def import_netflix(root: Path, masker: Masker, window: Window | None = None, profile: str | None = None,
                   ) -> tuple[list[Record], dict[str, Any]]:
    window = window or Window()
    recs: dict[str, Record] = {}
    profiles: set[str] = set()
    extra = short = other_profile = 0
    with opened(root) as base:
        files = find(base, "ViewingActivity.csv")
        for f in files:
            text = f.read_bytes().decode("utf-8-sig", errors="replace")
            for row in csv.DictReader(io.StringIO(text)):
                who = (row.get("Profile Name") or "").strip()
                profiles.add(who)
                if profile and who != profile:
                    other_profile += 1
                    continue
                if (row.get("Supplemental Video Type") or "").strip():
                    extra += 1
                    continue
                dur = _duration_min(row.get("Duration") or "")
                if dur < 1:
                    short += 1
                    continue
                start = utc_naive(row.get("Start Time") or "", "%Y-%m-%d %H:%M:%S")
                title = (row.get("Title") or "").strip()
                if start is None or not title or not window.ok(start):
                    continue
                m = SERIES.match(title)
                title_m = masker.mask(title) or title
                r = Record(
                    id=record_id("netflix", who, iso(start), title),
                    ts=iso(start), source="netflix", kind="watch", modality="text",
                    text=f"넷플릭스 시청: {title_m}", author="나", is_self=True,
                    meta={"title": title_m, "series": m.group("series") if m else None, "duration_min": round(dur)},
                )
                recs[r.id] = r
    out = sorted(recs.values(), key=lambda r: r.ts)
    return out, {"files": len(files), "watched": len(out), "previews": extra, "short": short,
                 "other_profiles": other_profile, "profiles": sorted(p for p in profiles if p),
                 "out_of_range": window.skipped}
