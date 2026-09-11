"""Storyseed 합성 페르소나·90일 활동 로그의 파이썬 이식.

`public/storyseed-novel.html` 의 `buildPersonas`·`synthEventsV2`·`boot` 와 같은 시드를 넣으면
같은 사람과 같은 기록이 나온다 (tests/test_synthetic.py 가 node 로 원본을 돌려 대조한다).

원본의 `[...ALLTAGS].sort(()=>r()-0.5)` 는 비교 함수가 난수를 뽑으므로, V8 정렬이 비교를
몇 번 부르는지까지 같아야 이후 난수열이 맞는다. 그래서 V8 TimSort 의 짧은 배열 경로
(자연 런 탐색 + 이진 삽입 정렬)를 그대로 옮겼다.

합성 데이터는 정답이 있는 평가용 데이터다. 기록마다 원래 활동 그룹과 Storyseed 의 정서 값을
`meta.truth` 에 남겨, 검색·정서·변화점 결과를 자동으로 채점할 수 있다.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from ..config import DEFAULT_TZ
from ..records import Record, iso, record_id, to_aware

GROUPS = [
    {"id": "정적휴식", "tags": ["낮잠", "웹툰 정주행", "드라마 시청", "트로트 감상", "라디오 청취", "반신욕", "멍하니 있기"]},
    {"id": "신체활동", "tags": ["둘레길 산책", "등산", "배드민턴", "조깅", "자전거", "스트레칭", "수영"]},
    {"id": "사회적", "tags": ["단골 주점", "동호회 모임", "사우나 담소", "가족 외식", "전화 수다", "시장 구경", "코인노래방"]},
    {"id": "탐색소비", "tags": ["맛집 검색", "배달 주문", "중고거래 탐색", "전자기기 리뷰", "쇼핑몰 둘러보기", "여행지 검색", "카페 탐방"]},
    {"id": "창작기록", "tags": ["사진 촬영", "블로그 기록", "영상 편집", "손글씨 메모", "텃밭 가꾸기", "요리 시도", "독서"]},
]
ALLTAGS = [(t, gi) for gi, g in enumerate(GROUPS) for t in g["tags"]]
CHANNELS = ["검색", "저장", "이동", "시청", "소비", "기록"]
# Storyseed emotionCurve 의 그룹별 정서 값
VAL = {0: -0.15, 1: 0.45, 2: 0.55, 3: 0.10, 4: 0.30}

SUR = ["김", "이", "박", "정", "최", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍"]
GIV_F = ["영숙", "정숙", "미경", "은지", "지영", "유진", "지현", "수진", "혜란", "명순", "연옥", "다희", "현주", "소영", "경자"]
GIV_M = ["영수", "상철", "기태", "준호", "현우", "지훈", "동현", "상제", "재성", "성호", "태훈", "승락", "동식", "찬민", "도성"]
PROV = ["서울", "경기", "부산", "인천", "대구", "대전", "광주", "울산", "세종", "강원", "충청북", "충청남", "전북", "전남", "경상북", "경상남", "제주"]
OCC = ["회계 사무원", "소프트웨어 개발자", "간호사", "온라인 쇼핑 판매원", "건물 경비원", "전기 용접원", "일반 비서", "부동산 컨설턴트",
       "한식 조리사", "화물차 운전원", "무직", "전화 상담원", "사무 보조원", "119 구조대원", "치과 일반 의사"]
EDU = ["중학교", "고등학교", "2~3년제 전문대학", "4년제 대학교", "대학원"]
FAM = ["혼자 거주", "배우자와 거주", "배우자·자녀와 거주", "부모와 동거", "자녀와 거주", "부모·형제자매와 동거"]
MAR = ["미혼", "배우자있음", "이혼", "사별"]

DEFAULT_SEED = 20260711  # Storyseed 의 SEED


# ---------- JS 32비트 정수 연산 ----------
def _u32(x: int) -> int:
    return x & 0xFFFFFFFF


def _i32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


def _imul(a: int, b: int) -> int:
    return _i32(_u32(a) * _u32(b))


def mulberry32(seed: int) -> Callable[[], float]:
    state = _i32(seed)

    def r() -> float:
        nonlocal state
        state = _i32(state + 0x6D2B79F5)
        a = state
        t = _imul(a ^ (_u32(a) >> 15), 1 | a)
        t = _i32(t + _imul(t ^ (_u32(t) >> 7), 61 | t)) ^ t
        return _u32(t ^ (_u32(t) >> 14)) / 4294967296

    return r


def hash_str(s: str) -> int:
    """FNV-1a 32비트. JS 의 charCodeAt 처럼 UTF-16 코드 단위로 센다."""
    h = _i32(2166136261)
    data = s.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = _imul(h, 16777619)
    return _u32(h)


def js_round(x: float) -> int:
    return math.floor(x + 0.5)


def v8_sort(items: list[Any], compare: Callable[[Any, Any], float]) -> list[Any]:
    """V8 Array.prototype.sort (TimSort) 를 길이 64 미만 배열에 대해 그대로 흉내 낸다."""
    a = list(items)
    n = len(a)
    if n < 2:
        return a
    if n >= 64:
        raise NotImplementedError("길이 64 이상은 병합 단계가 필요하다 (합성 데이터에는 쓰지 않음)")

    def cmp(x: Any, y: Any) -> float:
        v = compare(x, y)
        return 0.0 if v != v else v  # NaN → 0

    # CountAndMakeRun(0, n)
    run = 2
    descending = cmp(a[1], a[0]) < 0
    prev = a[1]
    for idx in range(2, n):
        order = cmp(a[idx], prev)
        if descending:
            if order >= 0:
                break
        elif order < 0:
            break
        prev = a[idx]
        run += 1
    if descending:
        a[:run] = a[:run][::-1]
    # BinaryInsertionSort(0, run, n) — minrun 이 n 이므로 한 번에 끝까지 확장
    for start in range(run, n):
        left, right = 0, start
        pivot = a[start]
        while left < right:
            mid = left + ((right - left) >> 1)
            if cmp(pivot, a[mid]) < 0:
                right = mid
            else:
                left = mid + 1
        a[left + 1:start + 1] = a[left:start]
        a[left] = pivot
    return a


def build_personas(seed: int, n: int) -> list[dict[str, Any]]:
    out = []
    for i in range(n):
        r = mulberry32(seed + i * 7919)
        sex = "여자" if r() < 0.5 else "남자"
        age = 19 + math.floor(math.pow(r(), 0.78) * 72)
        name = SUR[math.floor(r() * len(SUR))] + (
            GIV_F[math.floor(r() * len(GIV_F))] if sex == "여자" else GIV_M[math.floor(r() * len(GIV_M))]
        )
        w = [math.pow(r(), 2.1) + 0.05 for _ in GROUPS]
        ws = sum(w)
        pool = v8_sort(ALLTAGS, lambda _a, _b: r() - 0.5)
        hobbies = [pool[k][0] for k in range(4)]
        persona = {
            "uuid": format(seed + i, "x").rjust(8, "0"),
            "name": name,
            "sex": sex,
            "age": age,
        }
        persona["province"] = PROV[math.floor(r() * len(PROV))]
        persona["occupation"] = OCC[math.floor(r() * len(OCC))]
        persona["education_level"] = EDU[math.floor(r() * len(EDU))]
        persona["family_type"] = FAM[math.floor(r() * len(FAM))]
        persona["marital_status"] = MAR[math.floor(r() * len(MAR))]
        persona["hobbies"] = hobbies
        persona["_w"] = [x / ws for x in w]
        persona["_phase"] = 6 + r() * 16
        persona["_spread"] = 1.6 + r() * 4.2
        persona["_burst"] = 0.15 + math.pow(r(), 1.6) * 1.5
        persona["_shift"] = math.pow(r(), 1.7)
        persona["_cross"] = math.pow(r(), 1.5)
        out.append(persona)
    return out


def synth_events(p: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    r = mulberry32(_i32(seed) ^ _i32(hash_str(p["uuid"] + "v2")))
    week_amp = math.pow(r(), 1.3)
    week_ph = r() * 7
    ev: list[dict[str, Any]] = []
    w2 = list(p["_w"])
    ng = len(GROUPS)
    if p["_shift"] > 0.22:
        rot = 1 + math.floor(r() * 4)
        for i in range(ng):
            w2[i] = p["_w"][(i + rot) % ng]
    for d in range(90):
        t = d / 89
        mix = min(1, max(0, (t - 0.32) / 0.30)) * min(1, p["_shift"] * 1.5) if p["_shift"] > 0.22 else 0
        w = [v * (1 - mix) + w2[i] * mix for i, v in enumerate(p["_w"])]
        weekly = 1 + week_amp * 0.75 * math.sin(math.fmod(d + week_ph, 7) / 7 * math.pi * 2)
        cnt = max(0, js_round((2.6 + (r() * 2 - 1) * p["_burst"] * 3.4) * weekly))
        if r() < 0.06 * p["_burst"]:
            cnt += js_round(r() * 6)
        for e in range(cnt):
            acc = 0.0
            x = r() * sum(w)
            gi = 0
            for i in range(len(w)):
                acc += w[i]
                if x <= acc:
                    gi = i
                    break
            if e > 0 and r() < p["_cross"] * 0.5:
                gi = (gi + 2 + math.floor(r() * 2)) % ng
            tags = GROUPS[gi]["tags"]
            tag = tags[math.floor(r() * len(tags))]
            h = p["_phase"] + (r() + r() + r() - 1.5) * p["_spread"]
            h = math.fmod(math.fmod(h, 24) + 24, 24)
            ev.append({"d": d, "h": h, "tag": tag, "gi": gi, "ch": CHANNELS[math.floor(r() * len(CHANNELS))]})
    return ev


def default_persona_index(seed: int, n: int = 30) -> int:
    return math.floor(mulberry32(seed)() * n)


def synthetic_dataset(
    seed: int = DEFAULT_SEED,
    persona_index: int | None = None,
    start: date = date(2026, 6, 1),
    tz: str = DEFAULT_TZ,
) -> tuple[dict[str, Any], list[Record]]:
    """Storyseed boot() 과 같은 방식으로 페르소나를 고르고 90일 기록을 레코드로 만든다."""
    pool = build_personas(seed, 30)
    k = default_persona_index(seed) if persona_index is None else persona_index
    me = pool[k]
    events = synth_events(me, seed)
    records = []
    for i, e in enumerate(events):
        minutes = round(e["h"] * 60)
        dt = to_aware(datetime.combine(start, datetime.min.time()) + timedelta(days=e["d"], minutes=minutes), tz)
        group = GROUPS[e["gi"]]["id"]
        records.append(
            Record(
                id=record_id("synthetic", me["uuid"], seed, i),
                ts=iso(dt), source="synthetic", kind="activity", modality="text",
                text=e["tag"], author="나", is_self=True, tags=[e["tag"], group],
                meta={
                    "channel": e["ch"],
                    "day_index": e["d"] + 1,
                    "hour": round(e["h"], 3),
                    "truth": {"group": e["gi"], "group_id": group, "valence": VAL[e["gi"]]},
                },
            )
        )
    persona = {k2: v for k2, v in me.items() if not k2.startswith("_")}
    persona.update({"seed": seed, "index": k, "shift": me["_shift"], "weights": me["_w"], "start": start.isoformat()})
    return persona, records
