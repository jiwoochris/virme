"""구글 테이크아웃(takeout.google.com)과 구글 지도 타임라인 가져오기: 유튜브 시청 기록, 검색 기록, 방문한 장소.

유튜브 시청 기록
- `YouTube and YouTube Music/history/watch-history.json` (한국어 계정: `YouTube 및 YouTube Music/시청 기록/시청 기록.json`).
  테이크아웃에서 형식을 HTML 로 두면 `watch-history.html`·`시청 기록.html` 이 오는데, 이것도 읽는다.
- 제목의 "Watched …"·"…을(를) 시청했습니다." 는 떼고, 광고(details 에 "From Google Ads"·"Google 광고")와
  지워진 영상(제목이 주소뿐)은 뺀다.

검색 기록
- `My Activity/Search/MyActivity.json` (한국어: `내 활동/검색/내활동.json`). "Searched for …"·"…을(를) 검색함" 항목만
  쓰고, 방문한 페이지("Visited …")는 뺀다. 검색어는 Masker 로 전화번호·이름을 가린다.

지도 타임라인
- 2024년부터 타임라인은 휴대폰에만 남는다. 휴대폰의 설정 → 위치 → 위치 서비스 → 타임라인 → 타임라인 데이터 내보내기 로
  `Timeline.json` 을 만든다(`semanticSegments`). 이 파일에는 장소 이름이 없고 placeId·종류(HOME, WORK …)와 좌표만 있다.
- 아이폰 구글 지도 앱이 내보내는 `location-history.json`(목록, `placeLocation: "geo:37.5,127.0"`)도 읽는다.
- 예전 테이크아웃의 `Semantic Location History/2024/2024_JANUARY.json`(`timelineObjects[].placeVisit`)은 장소 이름·주소가 있다.
  같은 placeId 가 예전 파일에 이름과 함께 나오면 새 형식의 방문에도 그 이름을 붙인다.
- 이동 구간(activity·timelinePath)은 쓰지 않는다. 좌표는 로컬 DB 에만 두고 밖으로 내보내지 않는다.
"""
from __future__ import annotations

import html
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..config import DEFAULT_TZ
from ..privacy import Masker
from ..records import Record, iso, record_id
from .traces import Window, find, minutes_ko, opened, parse_time

YOUTUBE_JSON = ("watch-history.json", "시청 기록.json", "시청기록.json")
YOUTUBE_HTML = ("watch-history.html", "시청 기록.html", "시청기록.html")
SEARCH_JSON = ("MyActivity.json", "My Activity.json", "내활동.json", "내 활동.json")
TIMELINE_JSON = ("Timeline.json", "타임라인.json", "location-history.json")
SEMANTIC_DIRS = ("Semantic Location History", "시맨틱 위치 기록")

ADS = re.compile(r"From Google Ads|Google 광고")
WATCHED = re.compile(r"^\s*Watched\s+|\s*을\(를\)\s*시청했습니다\.?\s*$|\s*시청함\s*$")
SEARCHED = re.compile(r"^\s*Searched for\s+(?P<a>.+)$|^\s*(?P<b>.+?)\s*(?:을\(를\)\s*)?검색(?:함|했습니다)\.?\s*$")
PLACE_TYPES = {"HOME": "집", "INFERRED_HOME": "집", "TYPE_HOME": "집", "WORK": "직장", "INFERRED_WORK": "직장",
               "TYPE_WORK": "직장"}


def _is_search_file(path: Path) -> bool:
    """MyActivity.json 은 서비스마다 같은 이름이라 폴더(Search·검색)로 고른다."""
    return any(part in ("Search", "검색") for part in path.parts[:-1])


# ---------- 유튜브 ----------
def _watch_record(title: str, channel: str | None, url: str | None, dt: datetime, masker: Masker) -> Record:
    title = WATCHED.sub("", title).strip()
    channel = (channel or "").strip() or None
    text = f"유튜브 시청: {title}" + (f" ({channel})" if channel else "")
    return Record(
        id=record_id("youtube", iso(dt), url or title),
        ts=iso(dt), source="youtube", kind="watch", modality="text",
        text=masker.mask(text), author="나", is_self=True,
        meta={"title": masker.mask(title), "channel": channel, "url": url},
    )


def _is_removed(title: str, url: str | None) -> bool:
    bare = WATCHED.sub("", title).strip()
    return not bare or bare.startswith("http") or (url is not None and bare == url)


def parse_youtube_json(path: Path, masker: Masker, window: Window, info: Counter) -> list[Record]:
    out = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        if ADS.search(json.dumps(item.get("details") or [], ensure_ascii=False)):
            info["ads"] += 1
            continue
        title, url = str(item.get("title") or ""), item.get("titleUrl")
        dt = parse_time(item.get("time"))
        if dt is None or _is_removed(title, url):
            info["removed"] += 1
            continue
        if not window.ok(dt):
            continue
        subs = item.get("subtitles") or []
        out.append(_watch_record(title, subs[0].get("name") if subs else None, url, dt, masker))
    return out


# HTML 내보내기: 항목(outer-cell)마다 본문 칸에 "Watched <a>제목</a><br><a>채널</a><br>Jun 3, 2026, 3:20:11 PM KST",
# 옆 세부정보 칸에 "From Google Ads" 가 붙는다
OUTER = re.compile(r'<div class="outer-cell')
CELL = re.compile(r'<div class="content-cell[^"]*mdl-typography--body-1">(.*?)</div>', re.S)
LINK = re.compile(r'<a href="([^"]+)">(.*?)</a>', re.S)
EN_TIME = re.compile(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4}), (\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)\s*([A-Z]{2,5})?")
KO_TIME = re.compile(r"(\d{4})\. (\d{1,2})\. (\d{1,2})\.?\s*(오전|오후) (\d{1,2}):(\d{2}):(\d{2})\s*([A-Z]{2,5})?")
MONTHS = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
TZ_ABBR = {"KST": 9, "UTC": 0, "GMT": 0, "JST": 9}


def _html_time(text: str, tz: str = DEFAULT_TZ) -> datetime | None:
    if m := EN_TIME.search(text):
        mon, d, y, h, mi, s, ap, abbr = m.groups()
        h = int(h) % 12 + (12 if ap == "PM" else 0)
        dt = datetime(int(y), MONTHS[mon], int(d), h, int(mi), int(s))
    elif m := KO_TIME.search(text):
        y, mon, d, ap, h, mi, s, abbr = m.groups()
        h = int(h) % 12 + (12 if ap == "오후" else 0)
        dt = datetime(int(y), int(mon), int(d), h, int(mi), int(s))
    else:
        return None
    off = TZ_ABBR.get(abbr or "")
    zone = timezone(timedelta(hours=off)) if off is not None else ZoneInfo(tz)
    return dt.replace(tzinfo=zone).astimezone(ZoneInfo(tz))


def parse_youtube_html(path: Path, masker: Masker, window: Window, info: Counter) -> list[Record]:
    out = []
    for item in OUTER.split(path.read_text(encoding="utf-8"))[1:]:
        if ADS.search(item):
            info["ads"] += 1
            continue
        cells = CELL.findall(item)
        if not cells:
            continue
        cell = cells[0]
        links = LINK.findall(cell)
        dt = _html_time(html.unescape(re.sub(r"<[^>]+>", " ", cell)))
        if not links or dt is None:
            info["removed"] += 1
            continue
        url, title = links[0][0], html.unescape(links[0][1])
        if _is_removed(title, url):
            info["removed"] += 1
            continue
        if not window.ok(dt):
            continue
        channel = html.unescape(links[1][1]) if len(links) > 1 else None
        out.append(_watch_record(title, channel, url, dt, masker))
    return out


def import_youtube(root: Path, masker: Masker, window: Window | None = None) -> tuple[list[Record], dict[str, Any]]:
    window = window or Window()
    info: Counter = Counter()
    recs: list[Record] = []
    files = find(root, *YOUTUBE_JSON)
    for f in files:
        recs += parse_youtube_json(f, masker, window, info)
    if not files:  # JSON 이 없을 때만 HTML 을 읽는다 (둘 다 받았으면 같은 기록이다)
        files = find(root, *YOUTUBE_HTML)
        for f in files:
            recs += parse_youtube_html(f, masker, window, info)
    recs = list({r.id: r for r in recs}.values())
    return recs, {"files": len(files), "watched": len(recs), "ads": info["ads"], "removed": info["removed"],
                  "out_of_range": window.skipped}


# ---------- 검색 ----------
def import_search(root: Path, masker: Masker, window: Window | None = None) -> tuple[list[Record], dict[str, Any]]:
    window = window or Window()
    files = [f for f in find(root, *SEARCH_JSON) if _is_search_file(f) or root.is_file()]
    recs: dict[str, Record] = {}
    other = 0
    for f in files:
        for item in json.loads(f.read_text(encoding="utf-8")):
            m = SEARCHED.match(str(item.get("title") or ""))
            dt = parse_time(item.get("time"))
            if not m or dt is None:
                other += 1  # 방문한 페이지·이미지 보기 같은 항목
                continue
            if not window.ok(dt):
                continue
            q = masker.mask((m.group("a") or m.group("b") or "").strip()) or ""
            if not q:
                continue
            r = Record(id=record_id("search", iso(dt), q), ts=iso(dt), source="search", kind="search", modality="text",
                       text=f"검색: {q}", author="나", is_self=True, meta={"query": q})
            recs[r.id] = r
    return list(recs.values()), {"files": len(files), "searches": len(recs), "other": other,
                                 "out_of_range": window.skipped}


# ---------- 지도 ----------
LATLNG = re.compile(r"(-?\d+(?:\.\d+)?)°?,\s*(-?\d+(?:\.\d+)?)°?")


def _latlng(value: Any) -> tuple[float | None, float | None]:
    """"37.5558°, 126.9013°" · "geo:37.5558,126.9013" → (위도, 경도)."""
    m = LATLNG.search(str(value or "").replace("geo:", ""))
    return (round(float(m.group(1)), 6), round(float(m.group(2)), 6)) if m else (None, None)


def _visit(start: datetime, end: datetime | None, place_id: str | None, place_type: str | None, name: str | None,
           lat: float | None, lon: float | None, masker: Masker) -> Record:
    dur = round((end - start).total_seconds() / 60) if end else None
    place = name or PLACE_TYPES.get((place_type or "").upper(), "장소")
    place = masker.mask(place) or place
    text = f"방문: {place}" + (f" ({minutes_ko(dur)} 머무름)" if dur is not None else "")
    return Record(
        id=record_id("maps", iso(start), place_id or f"{lat},{lon}"),
        ts=iso(start), source="maps", kind="visit", modality="text", text=text, author="나", is_self=True,
        lat=lat, lon=lon, place=place,
        meta={"place_id": place_id, "place_type": place_type, "start": iso(start), "end": iso(end) if end else None,
              "duration_min": dur},
    )


def _ms_time(d: dict[str, Any], key: str) -> datetime | None:
    if d.get(key):
        return parse_time(d[key])
    ms = d.get(key + "Ms")
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).astimezone(ZoneInfo(DEFAULT_TZ)) if ms else None


def _legacy_visits(path: Path) -> list[dict[str, Any]]:
    out = []
    for obj in json.loads(path.read_text(encoding="utf-8")).get("timelineObjects", []):
        pv = obj.get("placeVisit")
        if not pv:
            continue
        loc, dur = pv.get("location") or {}, pv.get("duration") or {}
        lat = loc.get("latitudeE7")
        out.append({"start": _ms_time(dur, "startTimestamp"), "end": _ms_time(dur, "endTimestamp"),
                    "place_id": loc.get("placeId"), "place_type": loc.get("semanticType"),
                    "name": loc.get("name") or loc.get("address"),
                    "lat": round(lat / 1e7, 6) if lat is not None else None,
                    "lon": round(loc["longitudeE7"] / 1e7, 6) if loc.get("longitudeE7") is not None else None})
    return out


def _device_visits(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segs = data.get("semanticSegments", []) if isinstance(data, dict) else data
    out = []
    for seg in segs:
        v = seg.get("visit")
        if not v:
            continue  # 이동(activity)·경로(timelinePath) 구간
        top = v.get("topCandidate") or {}
        loc = top.get("placeLocation")
        lat, lon = _latlng(loc.get("latLng") if isinstance(loc, dict) else loc)
        out.append({"start": parse_time(seg.get("startTime")), "end": parse_time(seg.get("endTime")),
                    "place_id": top.get("placeId") or top.get("placeID"), "place_type": top.get("semanticType"),
                    "name": None, "lat": lat, "lon": lon})
    return out


def import_maps(root: Path, masker: Masker, window: Window | None = None) -> tuple[list[Record], dict[str, Any]]:
    window = window or Window()
    legacy = [f for f in find(root, "*.json") if any(d in f.parts for d in SEMANTIC_DIRS)] if root.is_dir() else []
    device = find(root, *TIMELINE_JSON)
    if root.is_file() and not device:  # 이름을 바꾼 파일: 내용으로 형식을 고른다
        head = root.read_text(encoding="utf-8")[:4000]
        (legacy if "timelineObjects" in head else device).append(root)
    visits = [v for f in legacy for v in _legacy_visits(f)]
    names = {v["place_id"]: v["name"] for v in visits if v["place_id"] and v["name"]}
    for f in device:
        for v in _device_visits(f):
            v["name"] = names.get(v["place_id"])
            visits.append(v)
    recs: dict[str, Record] = {}
    for v in visits:
        if v["start"] is None or not window.ok(v["start"]):
            continue
        r = _visit(v["start"], v["end"], v["place_id"], v["place_type"], v["name"], v["lat"], v["lon"], masker)
        recs[r.id] = r
    out = sorted(recs.values(), key=lambda r: r.ts)
    return out, {"files": len(legacy) + len(device), "visits": len(out), "named": sum(1 for r in out if r.place not in ("집", "직장", "장소")),
                 "out_of_range": window.skipped}


# ---------- 테이크아웃 한 폴더 ----------
def import_takeout(root: Path, masker: Masker, window: Window | None = None) -> tuple[dict[str, list[Record]], dict[str, Any]]:
    """테이크아웃 폴더(또는 zip)에서 유튜브·검색·지도를 있는 만큼 읽는다. 출처별 레코드와 요약을 돌려준다."""
    window = window or Window()
    out: dict[str, list[Record]] = {}
    info: dict[str, Any] = {}
    with opened(root) as base:
        for name, fn in (("youtube", import_youtube), ("search", import_search), ("maps", import_maps)):
            w = Window(window.since, window.until)
            recs, i = fn(base, masker, w)
            if i.get("files"):
                out[name], info[name] = recs, i
    return out, info
