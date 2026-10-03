"""소설 재료 컬럼 `virtueme.columns/1`: 기록 전체에서 등장인물·배경·리듬·취향·전환점을 뽑는다.

키워드 검색(query.py)이 "이 주제의 근거가 있는가"를 묻는다면, 이 모듈은 키워드 없이 "이 사람의 이야기에 쓸 재료가
무엇인가"를 묻는다. 결과는 노벨 화면의 취재 노트(`story.story_context`)에 배경 정보로 들어가고, 북디자인 브리프의
장르 추천에도 쓰인다.

설계 원칙
- 본인이 의식하고 쓴 글보다 무의식적으로 남은 흔적에 비중을 둔다. 시각, 반복해서 간 장소, 끊긴 연락, 결제, 시청 기록은
  본인이 "기억은 어렴풋한데 데이터로 보면 분명한" 사실을 보여 준다(`discoveries`).
- AI 모델을 쓰지 않는다. 모든 값은 개수·비율·중앙값·변화점(PELT) 같은 통계로 계산하고, 발견 문장마다 근거 기록 ID 를 붙인다.
- 좌표와 집·직장의 실제 이름은 밖으로 내보내지 않는다. 장소는 "집"·"직장"·가게 이름·공공장소 이름으로만 부른다.
- 출처가 없어서 계산할 수 없는 컬럼은 `catalog` 에 `available: false` 와 필요한 내보내기 방법을 적는다. 이 표가 곧
  "추출할 수 있는 컬럼과 없는 컬럼의 경계"다.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from . import patterns
from .privacy import SELF_ALIAS
from .records import TRACE_KINDS, Record
from .textproc import top_nouns

SCHEMA_ID = "virtueme.columns/1"

CHAT_SOURCES = ("kakao", "sms", "instagram")
FAMILY = ("엄마", "아빠", "어머니", "아버지", "언니", "오빠", "누나", "형", "동생", "할머니", "할아버지", "이모", "고모", "삼촌")
NIGHT_HOURS = (23, 0, 1, 2, 3, 4)   # 밤 11시부터 새벽 5시 전까지
REPLY_CAP = timedelta(hours=12)     # 이보다 늦은 답은 답장이 아니라 새 대화로 본다

# 컬럼 목록: (키, 소설 요소, 이름, 필요한 출처, 출처가 없을 때 받는 방법)
CATALOG: list[tuple[str, str, str, tuple[str, ...], str]] = [
    ("cast.volume", "인물", "상대별 대화량과 월별 추이", CHAT_SOURCES, "카카오톡 대화 내보내기, 문자 백업, 인스타그램 DM"),
    ("cast.reply", "인물", "답장 간격 (내가 답하기까지, 상대가 답하기까지)", CHAT_SOURCES, "카카오톡 대화 내보내기"),
    ("cast.night", "인물", "밤 11시 이후 대화 비율", CHAT_SOURCES, "카카오톡 대화 내보내기"),
    ("cast.arc", "인물", "새로 등장한 사람과 연락이 끊긴 사람", CHAT_SOURCES, "카카오톡 대화 내보내기"),
    ("place.anchor", "배경", "집·직장·자주 간 장소와 머문 시간", ("maps", "photo"), "구글 지도 타임라인 내보내기, 사진 GPS"),
    ("place.first", "배경", "처음 간 장소와 멀리 간 날", ("maps", "photo", "card"), "구글 지도 타임라인, 사진 GPS, 카드 이용내역"),
    ("place.merchant", "배경", "자주 결제한 가게", ("card",), "카드사 이용내역 엑셀, 카드 승인 문자"),
    ("rhythm.hours", "리듬", "활동 시각 분포와 밤 기록 비율의 변화", (), "모든 출처"),
    ("rhythm.sleep", "리듬", "잠든 시각과 수면 시간", ("health",), "삼성 헬스·애플 건강 내보내기"),
    ("rhythm.move", "리듬", "걸음 수와 운동", ("health",), "삼성 헬스·애플 건강 내보내기"),
    ("taste.video", "취향", "많이 본 영상 주제·채널·작품", ("youtube", "netflix"), "구글 테이크아웃(유튜브), 넷플릭스 시청 기록"),
    ("taste.music", "취향", "많이 들은 음악가", ("spotify",), "스포티파이 개인정보 다운로드"),
    ("taste.search", "취향", "검색어 주제의 시기별 변화", ("search",), "구글 테이크아웃(내 활동 > 검색)"),
    ("taste.spend", "취향", "지출 업종과 금액의 시기별 변화", ("card",), "카드사 이용내역 엑셀, 카드 승인 문자"),
    ("turn.points", "전환점", "여러 컬럼이 같은 시기에 함께 꺾인 날", (), "출처가 많을수록 정확"),
]


def _is_trace(r: Record) -> bool:
    return r.kind in TRACE_KINDS


def _day(r: Record) -> date:
    return r.dt.date()


def _month(d: date | str) -> str:
    return (d if isinstance(d, str) else d.isoformat())[:7]


def _thirds(start: date, end: date) -> tuple[date, date]:
    """기간을 셋으로 나눈 경계. 첫 1/3 과 마지막 1/3 을 비교할 때 쓴다."""
    span = (end - start).days
    return start + timedelta(days=span // 3), start + timedelta(days=(2 * span) // 3)


def _share(n: int, d: int) -> float:
    return round(n / d, 3) if d else 0.0


def _ids(recs: list[Record], k: int = 8) -> list[str]:
    """근거 기록 ID: 시간 순으로 고르게 k 건."""
    recs = sorted(recs, key=lambda r: r.ts)
    if len(recs) <= k:
        return [r.id for r in recs]
    step = len(recs) / k
    return [recs[int(i * step)].id for i in range(k)]


def josa(word: str, pair: str) -> str:
    """받침에 맞는 조사. pair 는 "은는"·"이가"·"과와"·"을를"·"으로". 받침 없는 말과 ㄹ 받침에는 "로"를 쓴다."""
    ch = word.strip()[-1:] if word.strip() else ""
    code = ord(ch) - 0xAC00 if ch else -1
    if not 0 <= code <= 11171:
        # 한글이 아니면(숫자·영문) 읽는 소리를 모르니 받침이 있는 쪽으로 본다. 숫자는 흔한 끝소리만 따진다
        final = 0 if ch in "2459" else 1
    else:
        final = code % 28
    if pair == "으로":
        return word + ("로" if final in (0, 8) else "으로")
    with_final, without = pair[0], pair[1]
    return word + (with_final if final else without)


PHOTO_SUFFIX = " 사진을 찍은 곳"
STOP_TERMS = {"로그", "브이", "브이로그", "영상", "모음", "추천", "정리", "리뷰", "하이라이트", "클립", "시청", "검색", "결제", "방법",
              "이유", "후기", "km"}


def _ko_period(a: date, b: date) -> str:
    return f"{patterns.ko_date(a)}~{patterns.ko_date(b)}"


# ---------------------------------------------------------------- 인물
@dataclass
class _Talk:
    alias: str
    theirs: list[Record]
    mine: list[Record]          # 1:1 대화방에서 내가 이 사람에게 보낸 것
    rooms: set[str]


def _cast(records: list[Record], relations: dict[str, str | None], start: date, end: date) -> list[dict[str, Any]]:
    chats = [r for r in records if r.source in CHAT_SOURCES and r.kind == "message"]
    by_thread: dict[str, list[Record]] = defaultdict(list)
    for r in chats:
        by_thread[r.thread or r.meta.get("room") or "?"].append(r)
    talks: dict[str, _Talk] = {}

    def talk(a: str) -> _Talk:
        return talks.setdefault(a, _Talk(a, [], [], set()))

    reply_mine: dict[str, list[float]] = defaultdict(list)    # 상대 말 → 내 답 (분)
    reply_theirs: dict[str, list[float]] = defaultdict(list)  # 내 말 → 상대 답 (분)
    for thread, rs in by_thread.items():
        rs.sort(key=lambda r: r.ts)
        others = sorted({p for r in rs for p in (r.participants or [r.author]) if p and p != SELF_ALIAS})
        one_to_one = len(others) == 1
        room = rs[0].meta.get("room") or thread
        for a in others:
            talk(a).rooms.add(room)
        for r in rs:
            if r.is_self:
                if one_to_one:
                    talk(others[0]).mine.append(r)
            elif r.author and r.author != SELF_ALIAS:
                talk(r.author).theirs.append(r)
        if one_to_one:
            for prev, cur in zip(rs, rs[1:]):
                gap = cur.dt - datetime.fromisoformat(prev.meta.get("end_ts") or prev.ts)
                if prev.is_self == cur.is_self or gap > REPLY_CAP or gap.total_seconds() < 0:
                    continue
                (reply_mine if cur.is_self else reply_theirs)[others[0]].append(gap.total_seconds() / 60)

    first_cut, last_cut = _thirds(start, end)
    span = max(1, (end - start).days)
    out = []
    for a, t in talks.items():
        allr = t.theirs + t.mine
        if not allr:
            continue
        days = sorted({_day(r) for r in allr})
        months = Counter(_month(_day(r)) for r in allr)
        night = sum(1 for r in allr if r.dt.hour in NIGHT_HOURS)
        early = sum(1 for r in allr if _day(r) < first_cut)
        late = sum(1 for r in allr if _day(r) >= last_cut)
        first_seen, last_seen = days[0], days[-1]
        silent = (end - last_seen).days
        rel = relations.get(a)
        roles = []
        if (first_seen - start).days > span * 0.3:
            roles.append("새로 등장한 사람")
        if silent >= max(14, span * 0.2) and len(allr) >= 10:
            roles.append("연락이 끊긴 사람")
        if early and late >= 2 * early:
            roles.append("가까워진 사람")
        elif late and early >= 2 * late:
            roles.append("멀어지는 사람")
        if len(days) >= span * 0.4:
            roles.append("늘 곁에 있는 사람")
        out.append({
            "alias": a,
            "relation": rel,
            "family": bool(rel and any(f in rel for f in FAMILY)) or a in FAMILY,
            "rooms": sorted(t.rooms),
            "turns": len(allr),
            "their_turns": len(t.theirs),
            "active_days": len(days),
            "first": first_seen.isoformat(),
            "last": last_seen.isoformat(),
            "silent_days": silent,
            "monthly": dict(sorted(months.items())),
            "night_share": _share(night, len(allr)),
            "reply_min_mine": round(statistics.median(reply_mine[a]), 1) if reply_mine[a] else None,
            "reply_min_theirs": round(statistics.median(reply_theirs[a]), 1) if reply_theirs[a] else None,
            "roles": roles,
            "evidence": _ids(t.theirs or allr),
        })
    out.sort(key=lambda c: -c["turns"])
    return out


# ---------------------------------------------------------------- 배경
def _place_key(r: Record) -> str | None:
    pid = r.meta.get("place_id")
    if pid:
        return f"id:{pid}"
    if r.lat is not None and r.lon is not None:
        return f"ll:{round(r.lat, 3)},{round(r.lon, 3)}"   # 약 100m 격자
    return None


def _place_name(rs: list[Record], given: str | None, near_tags: Counter[str]) -> tuple[str, bool]:
    """(이름, 이름을 확실히 아는가). 휴대폰 타임라인 내보내기에는 장소 이름이 없으므로, 사용자가 붙인 이름 → 기록에 있는 이름 →
    근처(300m)에서 찍은 사진의 자동 태그 순으로 부른다. 좌표로 바깥 지도 서비스에 이름을 묻지 않는다."""
    types = Counter((r.meta.get("place_type") or "").upper() for r in rs)
    if types.get("HOME"):
        return "집", True
    if types.get("WORK"):
        return "직장", True
    if given:
        return given, True
    names = Counter(r.place for r in rs if r.place and r.place not in ("장소", "이름 없는 장소"))
    if names:
        return names.most_common(1)[0][0], True
    tags = near_tags or Counter(t["tag"] for r in rs for t in r.meta.get("auto_tags", [])[:1])
    if tags:
        return f"{tags.most_common(1)[0][0]}{PHOTO_SUFFIX}", False
    return "이름 없는 장소", False


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _places(records: list[Record], start: date, names: dict[str, str] | None = None) -> dict[str, Any]:
    names = names or {}
    located = [r for r in records if (r.kind == "visit" or r.source == "photo") and _place_key(r)]
    groups: dict[str, list[Record]] = defaultdict(list)
    for r in located:
        groups[_place_key(r)].append(r)
    photos = [r for r in records if r.source == "photo" and r.lat is not None and r.meta.get("auto_tags")]
    rows = []
    for key, rs in groups.items():
        rs.sort(key=lambda r: r.ts)
        visits = [r for r in rs if r.kind == "visit"]
        minutes = sum(float(r.meta.get("duration_min") or 0) for r in visits)
        lat = float(np.mean([r.lat for r in rs if r.lat is not None])) if any(r.lat is not None for r in rs) else None
        lon = float(np.mean([r.lon for r in rs if r.lon is not None])) if any(r.lon is not None for r in rs) else None
        near: Counter[str] = Counter()
        if lat is not None:
            for ph in photos:
                if _haversine_km((lat, lon), (ph.lat, ph.lon)) <= 0.3:
                    near[ph.meta["auto_tags"][0]["tag"]] += 1
        name, named = _place_name(rs, names.get(key), near)
        rows.append({
            "key": key,
            "name": name,
            "named": named,
            "visits": len({_day(r) for r in rs}),
            "hours": round(minutes / 60, 1),
            "photos": sum(1 for r in rs if r.source == "photo"),
            "first": _day(rs[0]).isoformat(),
            "last": _day(rs[-1]).isoformat(),
            "time_band": patterns.band(patterns.hour_profile(rs)["center"]),
            "_ll": (lat, lon) if lat is not None else None,
            "evidence": _ids(rs),
        })
    rows.sort(key=lambda p: (-p["visits"], -p["hours"]))
    # 멀리 간 날: 집(없으면 가장 많이 간 곳)에서 100km 넘게 떨어진 곳
    home = next((p for p in rows if p["name"] == "집" and p["_ll"]), next((p for p in rows if p["_ll"]), None))
    far = []
    if home:
        for p in rows:
            if p["_ll"] and _haversine_km(home["_ll"], p["_ll"]) > 100:
                far.append({**p, "km": round(_haversine_km(home["_ll"], p["_ll"]))})
    # 멀리 간 곳들을 날짜가 이어지는 여행 단위로 묶는다
    trips: list[dict[str, Any]] = []
    for p in sorted(far, key=lambda p: p["first"]):
        d0, d1 = date.fromisoformat(p["first"]), date.fromisoformat(p["last"])
        if trips and d0 <= date.fromisoformat(trips[-1]["last"]) + timedelta(days=2):
            t = trips[-1]
            t["last"] = max(t["last"], d1.isoformat())
            t["places"].append(p["name"])
            t["km"] = max(t["km"], p["km"])
            t["evidence"] = (t["evidence"] + p["evidence"])[:8]
        else:
            trips.append({"first": d0.isoformat(), "last": d1.isoformat(), "places": [p["name"]], "km": p["km"],
                          "evidence": list(p["evidence"])})
    for p in rows + far:
        p.pop("_ll", None)
    regular = [p for p in rows if p["name"] not in ("집", "직장") and p["visits"] >= 3]
    first_visits = sorted((p for p in rows if p["visits"] == 1 and date.fromisoformat(p["first"]) > start + timedelta(days=7)),
                          key=lambda p: p["first"])
    merchants: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        if r.kind == "payment" and r.meta.get("merchant") and float(r.meta.get("amount") or 0) > 0:
            merchants[r.meta["merchant"]].append(r)
    top_merchants = sorted(
        ({"name": m, "visits": len({_day(r) for r in rs}), "amount": int(sum(float(r.meta["amount"]) for r in rs)),
          "first": _day(min(rs, key=lambda r: r.ts)).isoformat(), "evidence": _ids(rs)}
         for m, rs in merchants.items()),
        key=lambda m: -m["visits"])[:10]
    return {
        "anchors": [p for p in rows if p["name"] in ("집", "직장")],
        "regular": regular[:10],
        "first_visits": first_visits[:12],
        "far": sorted(far, key=lambda p: p["first"])[:8],
        "trips": trips[:6],
        "merchants": top_merchants,
    }


# ---------------------------------------------------------------- 리듬
def _rhythm(records: list[Record], start: date, end: date) -> dict[str, Any]:
    own = [r for r in records if r.is_self and r.kind not in ("health", "visit")]
    first_cut, last_cut = _thirds(start, end)
    monthly_night: dict[str, float] = {}
    by_month: dict[str, list[Record]] = defaultdict(list)
    for r in own:
        by_month[_month(_day(r))].append(r)
    for m, rs in sorted(by_month.items()):
        monthly_night[m] = _share(sum(1 for r in rs if r.dt.hour in NIGHT_HOURS), len(rs))
    early = [r for r in own if _day(r) < first_cut]
    late = [r for r in own if _day(r) >= last_cut]
    hp = patterns.hour_profile(own)
    out: dict[str, Any] = {
        "time_center": hp["center"],
        "time_band": patterns.band(hp["center"]),
        "hour_histogram": hp["histogram"],
        "night_share": _share(sum(1 for r in own if r.dt.hour in NIGHT_HOURS), len(own)),
        "night_share_monthly": monthly_night,
        "night_share_first_third": _share(sum(1 for r in early if r.dt.hour in NIGHT_HOURS), len(early)),
        "night_share_last_third": _share(sum(1 for r in late if r.dt.hour in NIGHT_HOURS), len(late)),
        "night_evidence": {"first": _ids([r for r in early if r.dt.hour in NIGHT_HOURS]),
                           "last": _ids([r for r in late if r.dt.hour in NIGHT_HOURS])},
    }
    health = sorted((r for r in records if r.kind == "health"), key=lambda r: r.ts)
    if health:
        def month_stat(key: str) -> dict[str, float]:
            acc: dict[str, list[float]] = defaultdict(list)
            for r in health:
                v = r.meta.get(key)
                if v:
                    acc[_month(_day(r))].append(float(v))
            return {m: round(float(np.mean(v))) for m, v in sorted(acc.items())}

        def bedtime(r: Record) -> float | None:
            s = r.meta.get("sleep_start")
            if not s:
                return None
            hh, mm = (int(x) for x in str(s)[-8:-3].split(":")) if "T" in str(s) else (int(x) for x in str(s)[:5].split(":"))
            h = hh + mm / 60
            return h + 24 if h < 12 else h          # 새벽 1시 → 25시로 이어 셈

        def mean_bed(rs: list[Record]) -> float | None:
            v = [b for r in rs if (b := bedtime(r)) is not None]
            return round(float(np.mean(v)), 2) if v else None

        workouts = Counter(w.get("type") or "운동" for r in health for w in r.meta.get("workouts", []))
        out.update({
            "steps_monthly": month_stat("steps"),
            "sleep_min_monthly": month_stat("sleep_min"),
            "bedtime_first_third": mean_bed([r for r in health if _day(r) < first_cut]),
            "bedtime_last_third": mean_bed([r for r in health if _day(r) >= last_cut]),
            "workouts": dict(workouts.most_common(6)),
            "health_evidence": {"first": _ids([r for r in health if _day(r) < first_cut]),
                                "last": _ids([r for r in health if _day(r) >= last_cut])},
        })
    return out


# ---------------------------------------------------------------- 취향
def _burst(recs: list[Record], start: date, end: date, k: int = 8) -> list[dict[str, Any]]:
    """한 시기에 몰린 낱말: 전체 등장의 60% 이상이 30일 안에 몰리고 5번 이상 나온 명사."""
    by_term: dict[str, list[Record]] = defaultdict(list)
    for r in recs:
        if r.kind == "payment" and r.meta.get("merchant"):
            # 가게 이름은 형태소로 쪼개면 "배달의민족" 이 "민족" 이 되므로 지점명을 뗀 상호를 낱말로 쓴다
            by_term[str(r.meta["merchant"]).split()[0]].append(r)
            continue
        src = r.meta.get("title") or r.meta.get("query") or r.text or ""
        for t, _ in top_nouns([src], k=6):
            if t not in STOP_TERMS and len(t) >= 2:
                by_term[t].append(r)
    out = []
    for t, rs in by_term.items():
        if len(rs) < 5:
            continue
        ds = sorted(_day(r) for r in rs)
        best = (0, ds[0])
        j = 0
        for i, d in enumerate(ds):
            while ds[j] < d - timedelta(days=29):
                j += 1
            if i - j + 1 > best[0]:
                best = (i - j + 1, ds[j])
        share = best[0] / len(ds)
        if share >= 0.6 and (end - start).days > 45:
            w0 = best[1]
            inside = [r for r in rs if w0 <= _day(r) <= w0 + timedelta(days=29)]
            out.append({"term": t, "count": len(rs), "window": [w0.isoformat(), (w0 + timedelta(days=29)).isoformat()],
                        "share": round(share, 2), "evidence": _ids(inside)})
    out.sort(key=lambda b: (-b["count"] * b["share"]))
    return out[:k]


def _taste(records: list[Record], start: date, end: date) -> dict[str, Any]:
    watch = [r for r in records if r.kind == "watch"]
    listen = [r for r in records if r.kind == "listen"]
    search = [r for r in records if r.kind == "search"]
    pays = [r for r in records if r.kind == "payment" and float(r.meta.get("amount") or 0) > 0]
    out: dict[str, Any] = {}
    if watch:
        chans = Counter(r.meta.get("channel") or r.meta.get("series") for r in watch if r.meta.get("channel") or r.meta.get("series"))
        out["video"] = {
            "count": len(watch),
            "top_channels": [{"name": n, "count": c} for n, c in chans.most_common(8)],
            "top_terms": [{"term": t, "count": c} for t, c in top_nouns((r.meta.get("title") or r.text or "" for r in watch), k=12)],
            "night_share": _share(sum(1 for r in watch if r.dt.hour in NIGHT_HOURS), len(watch)),
            "bursts": _burst(watch, start, end),
        }
    if listen:
        arts = Counter(r.meta.get("artist") for r in listen if r.meta.get("artist"))
        minutes = defaultdict(float)
        for r in listen:
            minutes[r.meta.get("artist")] += float(r.meta.get("ms_played") or 0) / 60000
        out["music"] = {
            "count": len(listen),
            "top_artists": [{"name": n, "plays": c, "minutes": round(minutes[n])} for n, c in arts.most_common(8)],
            "night_share": _share(sum(1 for r in listen if r.dt.hour in NIGHT_HOURS), len(listen)),
        }
    if search:
        out["search"] = {
            "count": len(search),
            "top_terms": [{"term": t, "count": c} for t, c in top_nouns((r.meta.get("query") or r.text or "" for r in search), k=12)],
            "bursts": _burst(search, start, end),
        }
    if pays:
        cat = Counter()
        for r in pays:
            cat[r.meta.get("category") or "기타"] += int(float(r.meta["amount"]))
        monthly = Counter()
        for r in pays:
            monthly[_month(_day(r))] += int(float(r.meta["amount"]))
        first_cut, last_cut = _thirds(start, end)
        night = [r for r in pays if r.dt.hour in NIGHT_HOURS]
        early_cats = Counter(r.meta.get("category") for r in pays if _day(r) < first_cut)
        later_cats = Counter(r.meta.get("category") for r in pays if _day(r) >= first_cut and r.meta.get("category"))
        new_cats = [c for c, _ in later_cats.most_common() if c not in early_cats]
        out["spend"] = {
            "count": len(pays),
            "total": int(sum(float(r.meta["amount"]) for r in pays)),
            "by_category": dict(cat.most_common(10)),
            "monthly": dict(sorted(monthly.items())),
            "night_count": len(night),
            "bursts": _burst(pays, start, end),
            "new_categories": new_cats,
            "new_category_evidence": _ids([r for r in pays if r.meta.get("category") in new_cats]),
        }
    return out


# ---------------------------------------------------------------- 전환점
def _daily_series(records: list[Record], dates: list[date], emotions: dict[str, tuple[float, float, str]]) -> dict[str, np.ndarray]:
    idx = {d: i for i, d in enumerate(dates)}
    n = len(dates)
    s: dict[str, np.ndarray] = {}

    def acc(name: str, pick, value=lambda r: 1.0) -> None:
        arr = np.zeros(n)
        hit = False
        for r in records:
            if pick(r) and (i := idx.get(_day(r))) is not None:
                arr[i] += value(r)
                hit = True
        if hit:
            s[name] = arr

    acc("대화량", lambda r: r.source in CHAT_SOURCES and r.is_self)
    acc("밤 기록", lambda r: r.is_self and r.dt.hour in NIGHT_HOURS and r.kind not in ("health", "visit"))
    acc("장소 수", lambda r: r.kind == "visit")
    acc("지출", lambda r: r.kind == "payment", lambda r: max(0.0, float(r.meta.get("amount") or 0)) / 10000)
    acc("영상·검색", lambda r: r.kind in ("watch", "search"))
    acc("걸음 수", lambda r: r.kind == "health", lambda r: float(r.meta.get("steps") or 0) / 1000)
    val = np.zeros(n)
    w = np.zeros(n)
    for r in records:
        e = emotions.get(r.id)
        if e and r.is_self and (i := idx.get(_day(r))) is not None:
            val[i] += e[0]
            w[i] += 1
    if w.sum():
        raw = [float(val[i] / w[i]) if w[i] else None for i in range(n)]
        s["정서"] = patterns._interp(raw, 0.0)
    return s


def _turning_points(series: dict[str, np.ndarray], dates: list[date]) -> list[dict[str, Any]]:
    """컬럼마다 7일 평활 뒤 표준화해 변화점을 찾고, 7일 안에 함께 꺾인 것끼리 묶는다."""
    found = []
    for name, x in series.items():
        if len(x) < 21 or float(np.std(x)) == 0:
            continue
        sm = patterns.moving_average(x, 7)
        z = (sm - sm.mean()) / (sm.std() or 1)
        for c in patterns.change_points(z, dates, min_size=10, min_delta=0.6, max_points=3):
            found.append({"series": name, "date": date.fromisoformat(c["date"]), "direction": c["direction"],
                          "delta_sd": c["delta"]})
    found.sort(key=lambda c: c["date"])
    groups: list[list[dict[str, Any]]] = []
    for c in found:
        if groups and (c["date"] - groups[-1][0]["date"]).days <= 7:
            groups[-1].append(c)
        else:
            groups.append([c])
    out = []
    for g in groups:
        names = {c["series"] for c in g}
        mid = sorted(c["date"] for c in g)[len(g) // 2]
        out.append({
            "date": mid.isoformat(),
            "columns": [{"series": c["series"], "direction": c["direction"], "delta_sd": c["delta_sd"]} for c in g],
            "strength": len(names),
        })
    out.sort(key=lambda t: (-t["strength"], t["date"]))
    return sorted(out[:5], key=lambda t: t["date"])


# ---------------------------------------------------------------- 발견
def _discoveries(cols: dict[str, Any], by_id: dict[str, Record]) -> list[dict[str, Any]]:
    """기억은 어렴풋하지만 데이터로는 분명한 사실. 숫자와 근거 기록을 붙인 문장으로 만든다."""
    out: list[dict[str, Any]] = []
    rh = cols["rhythm"]
    a, b = rh.get("night_share_first_third"), rh.get("night_share_last_third")
    if a is not None and b is not None and abs(a - b) >= 0.08:
        more, less = ("처음", "끝") if a > b else ("끝", "처음")
        out.append({"column": "rhythm.hours",
                    "text": f"밤 11시 이후에 남긴 기록의 비율이 기간 {more} 무렵 {max(a, b):.0%}, {less} 무렵 {min(a, b):.0%}였습니다.",
                    "evidence": rh["night_evidence"]["first" if a > b else "last"]})
    if rh.get("bedtime_first_third") and rh.get("bedtime_last_third"):
        d = rh["bedtime_last_third"] - rh["bedtime_first_third"]
        if abs(d) >= 0.75:
            def hhmm(h: float) -> str:
                h %= 24
                return f"{int(h)}시 {int(round((h % 1) * 60)):02d}분"
            out.append({"column": "rhythm.sleep",
                        "text": f"잠든 시각의 평균이 {hhmm(rh['bedtime_first_third'])}에서 {josa(hhmm(rh['bedtime_last_third']), '으로')} "
                                f"{abs(d):.1f}시간 {'늦어졌습니다' if d > 0 else '앞당겨졌습니다'}.",
                        "evidence": rh["health_evidence"]["first"][:4] + rh["health_evidence"]["last"][:4]})
    steps = rh.get("steps_monthly") or {}
    if len(steps) >= 2:
        ms = list(steps.items())
        lo, hi = min(ms, key=lambda kv: kv[1]), max(ms, key=lambda kv: kv[1])
        if lo[1] and hi[1] / lo[1] >= 1.4:
            out.append({"column": "rhythm.move",
                        "text": f"하루 평균 걸음 수가 가장 적은 달은 {int(lo[0][5:])}월({lo[1]:,}보), 가장 많은 달은 "
                                f"{int(hi[0][5:])}월({hi[1]:,}보)입니다.",
                        "evidence": rh["health_evidence"]["last"][:6]})
    for c in cols["cast"][:12]:
        who = c["relation"] or "이름을 가린 한 사람"
        if "새로 등장한 사람" in c["roles"] and c["turns"] >= 15:
            rank = cols["cast"].index(c) + 1
            out.append({"column": "cast.arc", "alias": c["alias"],
                        "text": f"{josa(who, '은는')} {patterns.ko_date(c['first'])}에 처음 대화에 나타나 대화 상대 가운데 "
                                f"대화량 {rank}위가 됐습니다.",
                        "evidence": c["evidence"]})
        if "연락이 끊긴 사람" in c["roles"]:
            out.append({"column": "cast.arc", "alias": c["alias"],
                        "text": f"{josa(who, '과와')}의 대화는 {patterns.ko_date(c['last'])} 뒤로 {c['silent_days']}일 동안 없습니다.",
                        "evidence": c["evidence"]})
        if c["night_share"] >= 0.3 and c["turns"] >= 20 and not c["family"]:
            out.append({"column": "cast.night", "alias": c["alias"],
                        "text": f"{josa(who, '과와')}의 대화는 {c['night_share']:.0%}가 밤 11시 이후였습니다.",
                        "evidence": c["evidence"]})
    pl = cols["places"]
    if pl["regular"]:
        p = pl["regular"][0]
        out.append({"column": "place.anchor", "place_key": p["key"],
                    "text": f"집과 직장을 빼면 가장 자주 간 곳은 {p['name']}입니다. {p['visits']}일을 갔고 주로 {p['time_band']}에 갔습니다.",
                    "evidence": p["evidence"]})
    for t in pl["trips"][:2]:
        uniq = list(dict.fromkeys(t["places"]))
        named = [n for n in uniq if "이름 없는" not in n and not n.endswith(PHOTO_SUFFIX)][:3]
        shot = [n[: -len(PHOTO_SUFFIX)] for n in uniq if n.endswith(PHOTO_SUFFIX)][:3]
        days = (date.fromisoformat(t["last"]) - date.fromisoformat(t["first"])).days + 1
        tail = (f" 간 곳은 {', '.join(named)}입니다." if named else "") + \
            (f" 그곳에서 {'·'.join(shot)} 사진을 남겼습니다." if shot else "")
        out.append({"column": "place.first",
                    "text": f"{patterns.ko_date(t['first'])}부터 {days}일 동안 집에서 {t['km']}km 떨어진 곳에 있었습니다.{tail}",
                    "evidence": t["evidence"]})
    ta = cols["taste"]
    told: set[str] = set()
    for key, label in (("search", "검색어"), ("video", "본 영상 제목"), ("spend", "결제한 가게 이름")):
        for bu in [b for b in (ta.get(key) or {}).get("bursts", []) if b["term"] not in told][:2]:
            told.add(bu["term"])
            out.append({"column": f"taste.{key}",
                        "text": f"{label}에 나온 '{bu['term']}' {bu['count']}번 가운데 {bu['share']:.0%}가 "
                                f"{_ko_period(date.fromisoformat(bu['window'][0]), date.fromisoformat(bu['window'][1]))}에 몰려 있습니다.",
                        "evidence": bu["evidence"]})
    if (ta.get("spend") or {}).get("new_categories"):
        cats = ta["spend"]["new_categories"][:3]
        out.append({"column": "taste.spend", "text": f"기간 초반에는 없던 지출 업종이 생겼습니다: {', '.join(cats)}.",
                    "evidence": ta["spend"].get("new_category_evidence", [])})
    for t in cols["turning_points"]:
        if t["strength"] >= 2:
            moved = ", ".join(f"{c['series']} {c['direction']}" for c in t["columns"])
            out.append({"column": "turn.points", "text": f"{patterns.ko_date(t['date'])} 무렵 여러 흐름이 함께 꺾였습니다: {moved}.",
                        "evidence": []})
    for d in out:
        d["evidence"] = [i for i in d["evidence"] if i in by_id]
    return out


# ---------------------------------------------------------------- 장르 추천
def _genre_hint(cols: dict[str, Any]) -> list[dict[str, Any]]:
    """데이터의 모양으로 장르를 추천한다. 점수는 0..1, 이유를 함께 준다. 사용자가 고르는 기본값으로만 쓴다."""
    from .prompts_storyseed import GENRES

    rh, cast, ta = cols["rhythm"], cols["cast"], cols["taste"]
    tps = cols["turning_points"]
    multi = sum(1 for t in tps if t["strength"] >= 2)
    night = rh.get("night_share") or 0.0
    close = next((c for c in cast if not c["family"] and ("가까워진 사람" in c["roles"] or "새로 등장한 사람" in c["roles"])), None)
    faded = [c for c in cast if "연락이 끊긴 사람" in c["roles"]]
    screen_share = cols["data_scope"]["screen_share"]
    regular = len(cols["places"]["regular"])
    far = sum((date.fromisoformat(t["last"]) - date.fromisoformat(t["first"])).days + 1 for t in cols["places"]["trips"])
    valence = cols["data_scope"].get("valence_mean") or 0.0
    s: dict[str, tuple[float, str]] = {
        "미스터리": (min(0.9, 0.2 * multi + 0.2 * len(faded)), f"여러 흐름이 함께 꺾인 시기 {multi}번, 연락이 끊긴 사람 {len(faded)}명"),
        "로맨스": (0.8 if close and close["night_share"] >= 0.2 else 0.5 if close else 0.1,
                 f"가까워진 사람 {'있음' if close else '없음'}" + (f", 그 대화의 밤 비율 {close['night_share']:.0%}" if close else "")),
        "SF": (min(0.9, screen_share * 1.5), f"남긴 기록 가운데 영상 시청·검색 비율 {screen_share:.0%}"),
        "판타지": (min(0.9, 0.15 * far + 0.05 * regular), f"멀리 떠나 있던 날 {far}일, 반복해서 간 곳 {regular}곳"),
        "스릴러": (min(1.0, night * 2 + max(0.0, -valence)), f"밤 11시 이후 기록 {night:.0%}, 정서 평균 {valence:+.2f}"),
        "코미디": (min(1.0, max(0.0, valence) + 0.05 * regular), f"정서 평균 {valence:+.2f}, 반복해서 간 곳 {regular}곳"),
    }
    names = [g[0] for g in GENRES]
    rows = [{"genre": n, "index": names.index(n), "score": round(v, 2), "reason": why} for n, (v, why) in s.items() if n in names]
    rows.sort(key=lambda r: -r["score"])
    return rows


# ---------------------------------------------------------------- 진입점
def place_names(store) -> dict[str, str]:
    """사용자가 붙인 장소 이름 (`virtueme places --name 키=이름`). 프로필 DB 의 캐시 표에 둔다."""
    return {k.split(":", 1)[1]: v for k, v in store.cache_items("place_name:")}


def set_place_name(store, key: str, name: str | None) -> None:
    if name:
        store.cache_put(f"place_name:{key}", name)
        return
    with store.conn:
        store.conn.execute("DELETE FROM cache WHERE key=?", (f"place_name:{key}",))



def compute(store, emotions: dict[str, tuple[float, float, str]] | None = None) -> dict[str, Any]:
    records = sorted(store.records(), key=lambda r: r.ts)
    if not records:
        return {"schema": SCHEMA_ID, "records": 0}
    emotions = emotions if emotions is not None else store.emotions()
    relations = store.relations()
    start, end = _day(records[0]), _day(records[-1])
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    sources = Counter(r.source for r in records)
    own_vals = [emotions[r.id][0] for r in records if r.is_self and r.id in emotions]
    have = set(sources)
    catalog = [{"key": k, "element": el, "name": nm, "sources": list(src),
                "available": (not src) or bool(have & set(src)),
                "how_to_get": how} for k, el, nm, src, how in CATALOG]
    cols: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "data_scope": {
            "records": len(records),
            "date_range": [start.isoformat(), end.isoformat()],
            "sources": dict(sources),
            "trace_share": _share(sum(1 for r in records if _is_trace(r)), len(records)),
            "screen_share": _share(sum(1 for r in records if r.kind in ("watch", "search")), len(records)),
            "valence_mean": round(float(np.mean(own_vals)), 3) if own_vals else None,
        },
        "catalog": catalog,
        "cast": _cast(records, relations, start, end),
        "places": _places(records, start, place_names(store)),
        "rhythm": _rhythm(records, start, end),
        "taste": _taste(records, start, end),
        "turning_points": _turning_points(_daily_series(records, dates, emotions), dates),
    }
    by_id = {r.id: r for r in records}
    cols["discoveries"] = _discoveries(cols, by_id)
    cols["genre_hint"] = _genre_hint(cols)
    return cols


def note_for_story(cols: dict[str, Any], people: dict[str, str | None] | None = None, k_cast: int = 5) -> dict[str, Any]:
    """취재 노트에 덧붙일 배경 정보. 가명은 관계로 바꿔 부르도록 people 에 맞추고, 좌표·집 주소는 넣지 않는다."""
    if not cols or not cols.get("cast") and not cols.get("places"):
        return {}
    cast = []
    for c in cols["cast"][:k_cast]:
        cast.append({"who": c["relation"] or c["alias"], "roles": c["roles"], "night_share": c["night_share"],
                     "first": patterns.ko_date(c["first"]), "last": patterns.ko_date(c["last"])})
    places = [p["name"] for p in cols["places"]["regular"][:5]] + \
        [n for t in cols["places"]["trips"][:2] for n in t["places"] if "이름 없는" not in n][:3]
    return {
        "cast": cast,
        "places": places,
        "rhythm": {"time_band": cols["rhythm"].get("time_band"), "night_share": cols["rhythm"].get("night_share")},
        "discoveries": [d["text"] for d in cols["discoveries"][:6]],
    }
