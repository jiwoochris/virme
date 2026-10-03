"""윤서연의 흔적 기록 샘플을 만든다: 유튜브·구글 검색·지도 타임라인·카드 이용내역·삼성 헬스·스포티파이.

README 의 줄거리표와 카카오톡 대화에 맞춘다. 6월에는 이사 뒤 혼자 늦게까지 일하고 새벽에 번아웃 영상을 보다가,
6월 24일 회사 동기가 러닝을 권한 뒤 러닝화를 사고(6/28), 6월 29일 주부터 한강 러닝 크루에 나간다(주 3회, 7/13 5km).
7월 20일 떠돌던 강아지 콩이를 임시보호하며 동물병원을 다니고, 7월 31일 엄마와 저녁을 먹고, 8월 11~14일 대학 친구들과
제주에 가고, 8월 18일 콩이를 입양하고, 8월 29일 10km 대회를 완주한다.

LLM 도 네트워크도 쓰지 않고 시드를 고정해 늘 같은 파일이 나온다. 결과는 이 폴더 아래에 쓴다.

- google/Takeout/YouTube 및 YouTube Music/시청 기록/시청 기록.json   (한국어 테이크아웃)
- google/Takeout/내 활동/검색/내활동.json
- maps/Timeline.json                                                (휴대폰 타임라인 내보내기, semanticSegments)
- card/신한카드_이용내역.xlsx                                         (머리말 줄 + 표)
- health/samsunghealth_seoyeon_20260830090000/*.csv                 (삼성 헬스 개인 데이터 다운로드)
- spotify/StreamingHistory_music_0.json

    uv run python examples/seoyeon/build_traces.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
START, END = date(2026, 6, 1), date(2026, 8, 29)
SEED = 20260601

RUNS = {  # 날짜: (시작 시각, 거리 km, 분). 카카오톡에 나온 날을 먼저 넣고 주 3회로 채운다
    date(2026, 7, 1): ("07:05", 3.0, 21), date(2026, 7, 4): ("19:40", 3.2, 22), date(2026, 7, 6): ("20:00", 3.5, 24),
    date(2026, 7, 7): ("20:05", 4.0, 27), date(2026, 7, 9): ("20:00", 4.0, 26), date(2026, 7, 11): ("08:10", 4.2, 27),
    date(2026, 7, 13): ("20:00", 5.0, 33), date(2026, 7, 15): ("20:05", 4.5, 29), date(2026, 7, 17): ("20:10", 5.0, 32),
    date(2026, 7, 18): ("19:30", 5.0, 31), date(2026, 7, 25): ("08:30", 4.0, 26), date(2026, 8, 1): ("08:20", 5.0, 31),
    date(2026, 8, 4): ("20:00", 5.5, 34), date(2026, 8, 6): ("20:05", 6.0, 37), date(2026, 8, 8): ("08:00", 6.0, 36),
    date(2026, 8, 17): ("20:00", 6.0, 36), date(2026, 8, 19): ("20:00", 7.0, 42), date(2026, 8, 21): ("19:50", 7.0, 41),
    date(2026, 8, 23): ("18:30", 8.0, 48), date(2026, 8, 26): ("20:00", 5.0, 30), date(2026, 8, 29): ("08:00", 10.0, 61),
}
DOG_FROM, ADOPT = date(2026, 7, 20), date(2026, 8, 18)
JEJU = (date(2026, 8, 11), date(2026, 8, 14))
RUN_FROM = date(2026, 6, 24)  # 회사 동기가 러닝을 권한 날


def at(d: date, hm: str | time, plus_min: float = 0) -> datetime:
    t = hm if isinstance(hm, time) else time.fromisoformat(hm)
    return datetime.combine(d, t, tzinfo=KST) + timedelta(minutes=plus_min)


def days():
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


def in_jeju(d: date) -> bool:
    return JEJU[0] <= d <= JEJU[1]


def workday(d: date) -> bool:
    return d.weekday() < 5 and not in_jeju(d) and d != date(2026, 8, 15)


def phase(d: date) -> str:
    if d < RUN_FROM:
        return "lonely"
    if d < DOG_FROM:
        return "running"
    if d < date(2026, 8, 3):
        return "dog"
    if d <= JEJU[1]:
        return "jeju"
    return "late"


def utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def vid(seed: str) -> str:
    return hashlib.sha1(seed.encode()).hexdigest()[:11]


# ---------- 유튜브 ----------
YT = {
    "burnout": ([
        "번아웃 왔을 때 나타나는 증상 7가지", "퇴사 고민될 때 스스로에게 물어볼 질문", "3년차 마케터 번아웃 브이로그",
        "혼자 사는 직장인의 평범한 밤", "자취 첫 달 혼밥 브이로그", "새벽 2시, 잠이 안 올 때 듣는 피아노",
        "아무것도 하기 싫은 날 틀어 두는 영상", "야근 끝나고 집에 오면 하는 일", "회사 그만두고 싶을 때 보는 영상",
        "직장인 무기력증 자가진단", "원룸 자취방 정리 브이로그", "편의점 신상 도시락 솔직 리뷰", "혼자 있는 시간이 길어질 때",
        "잠 못 드는 밤 빗소리 3시간", "캠페인 마감 주간 마케터 브이로그", "이사하고 첫 달 생활비 정산",
    ], ["마음연구소", "퇴사한 언니", "자취왕 제이", "밤의 피아노", "소소한 직장인", "편털TV", "빗소리 수면실"]),
    "general": ([
        "주간 경제 뉴스 3분 요약", "예능 레전드 장면 모음", "출근길 플레이리스트", "2026 하반기 마케팅 트렌드",
        "광고 카피 잘 쓰는 법", "고양이 웃긴 영상 모음", "10분 홈트 따라하기", "직장인 점심 메뉴 월드컵",
    ], ["뉴스3분", "예능클립", "마케터 노트", "냥냥TV", "10분 홈트"]),
    "running": ([
        "러닝 처음 시작할 때 꼭 알아야 할 5가지", "초보 러너 3km 달리기 루틴", "러닝 자세 교정: 착지와 케이던스",
        "입문용 러닝화 추천 TOP5 (2026)", "아식스 젤 카야노 31 실착 리뷰", "페가수스 vs 노바블라스트 러닝화 비교",
        "러닝 크루 처음 나가기 전에 보는 영상", "5km 30분 안에 달리는 훈련법", "러닝 후 스트레칭 10분",
        "한강 야간 러닝 코스 추천", "무릎 안 아프게 달리는 법", "페이스 조절이 어려운 초보 러너에게",
    ], ["러닝하는 피디", "달리는 물리치료사", "슈즈리뷰랩", "한강러너"]),
    "race": ([
        "10km 대회 첫 출전 준비 가이드", "대회 전날 먹으면 좋은 음식", "10km 페이스 전략 짜는 법", "첫 10K 완주 브이로그",
    ], ["러닝하는 피디", "한강러너"]),
    "dog": ([
        "길 잃은 강아지를 발견했을 때 해야 할 일", "유기견 임시보호 첫날 브이로그", "강아지 배변 훈련 3일 완성",
        "강아지 첫 산책 주의할 점", "강아지가 슬리퍼를 물어뜯는 이유", "보호소 강아지 입양 절차 총정리",
        "강아지 예방접종 스케줄 정리", "분리불안 강아지 훈련법", "믹스견 사료 고르는 법", "임시보호에서 입양까지 브이로그",
    ], ["댕댕훈련소", "보호소 이야기", "멍멍수의사"]),
    "jeju": ([
        "제주 3박 4일 여행 브이로그", "애월 카페 투어", "협재 해수욕장 물놀이", "성산일출봉 일출 보러 가기",
        "제주 흑돼지 맛집 모음", "초보도 오르는 제주 오름 추천",
    ], ["여행가 밍", "제주살이 일기"]),
}


def youtube(rng: random.Random) -> list[dict]:
    out = []
    for d in days():
        ph = phase(d)
        pools = {"lonely": [("burnout", 5), ("general", 2)],
                 "running": [("running", 4), ("general", 2), ("burnout", 1)] if d < date(2026, 7, 6) else [("running", 3), ("general", 2)],
                 "dog": [("dog", 4), ("running", 1), ("general", 1)],
                 "jeju": [("jeju", 3), ("dog", 1), ("general", 1)],
                 "late": [("race", 2), ("dog", 2), ("running", 1), ("general", 1)]}[ph]
        if in_jeju(d):
            pools = [("jeju", 1), ("general", 1)]
        n = {"lonely": rng.randint(5, 8)}.get(ph, rng.randint(3, 5)) if not in_jeju(d) else rng.randint(1, 2)
        for k in range(n):
            theme = rng.choices([p for p, _ in pools], [w for _, w in pools])[0]
            titles, channels = YT[theme]
            title = rng.choice(titles)
            channel = channels[titles.index(title) % len(channels)]
            # 6월에는 새벽에 몰아서 본다. 그 뒤로는 저녁·출퇴근길
            if ph == "lonely" and theme == "burnout":
                t = at(d, "23:20", rng.randint(0, 200))
            elif theme == "general":
                t = at(d, "08:15", rng.randint(0, 40)) if workday(d) else at(d, "13:00", rng.randint(0, 240))
            else:
                t = at(d, "21:30", rng.randint(0, 100))
            t += timedelta(seconds=rng.randint(0, 59), minutes=k * 9)
            out.append({"header": "YouTube", "title": f"{title}을(를) 시청했습니다.",
                        "titleUrl": f"https://www.youtube.com/watch?v={vid(title)}",
                        "subtitles": [{"name": channel, "url": f"https://www.youtube.com/channel/UC{vid(channel)}"}],
                        "time": utc_z(t), "products": ["YouTube"], "activityControls": ["YouTube 시청 기록"]})
    for i, d in enumerate([date(2026, 6, 9), date(2026, 6, 30), date(2026, 7, 21), date(2026, 8, 5), date(2026, 8, 20)]):
        out.append({"header": "YouTube", "title": "러닝 앱 프리미엄 3개월 무료을(를) 시청했습니다.",
                    "titleUrl": f"https://www.youtube.com/watch?v={vid('ad' + str(i))}", "time": utc_z(at(d, "21:00")),
                    "products": ["YouTube"], "details": [{"name": "Google 광고에서"}], "activityControls": ["YouTube 시청 기록"]})
    for d in (date(2026, 6, 12), date(2026, 7, 27)):  # 지워진 영상은 제목 대신 주소만 남는다
        url = f"https://www.youtube.com/watch?v={vid(str(d))}"
        out.append({"header": "YouTube", "title": f"{url}을(를) 시청했습니다.", "titleUrl": url, "time": utc_z(at(d, "22:00")),
                    "products": ["YouTube"], "activityControls": ["YouTube 시청 기록"]})
    return sorted(out, key=lambda x: x["time"], reverse=True)  # 테이크아웃은 최근 것부터


# ---------- 검색 ----------
SEARCH = {
    "lonely": ["번아웃 증상", "퇴사 고민 3년차", "혼자 사는 원룸 외로움", "망원동 야식 배달", "새벽에 잠 안 올 때", "불면증 원인",
               "주말에 할 일 없을 때", "직장인 무기력", "망원동 원룸 관리비", "캠페인 성과 리포트 양식"],
    "running": ["러닝 크루 서울", "망원 한강 러닝 코스", "러닝화 추천 입문", "아식스 젤 카야노 사이즈", "러닝 페이스 계산",
                "5km 기록 평균", "러닝 후 무릎 통증", "러닝 양말 추천"],
    "dog": ["길 잃은 강아지 신고", "유기견 임시보호 방법", "강아지 예방접종 시기", "망원동 동물병원", "강아지 사료 추천",
            "강아지 이름 추천", "강아지 배변패드", "강아지 혼자 두기"],
    "jeju": ["제주 애월 숙소", "협재 해수욕장 날씨", "성산일출봉 일출 시간", "제주 렌트카 가격", "제주 흑돼지 맛집",
             "김포 제주 항공권"],
    "late": ["유기견 입양 절차", "10km 대회 준비", "서울 10K 레이스 8월", "대회 전날 식단", "강아지 등록 방법", "10km 완주 시간 평균"],
}


def search(rng: random.Random) -> list[dict]:
    out = []
    for d in days():
        ph = phase(d)
        pools = [ph] + (["running"] if ph in ("dog", "late") and rng.random() < 0.3 else [])
        n = rng.choice([1, 1, 2, 2, 3]) if ph == "lonely" else rng.choice([1, 1, 2])
        for k in range(n):
            q = rng.choice(SEARCH[rng.choice(pools)])
            hm = "00:40" if ph == "lonely" and rng.random() < 0.6 else "12:30" if rng.random() < 0.5 else "21:10"
            t = at(d, hm, rng.randint(0, 80) + k * 4)
            out.append({"header": "Search", "title": f"{q} 검색함",
                        "titleUrl": f"https://www.google.com/search?q={q.replace(' ', '+')}", "time": utc_z(t),
                        "products": ["Search"], "activityControls": ["웹 및 앱 활동"]})
    for d in (date(2026, 6, 25), date(2026, 7, 21), date(2026, 8, 6)):  # 검색이 아닌 활동 (방문한 페이지)
        out.append({"header": "Search", "title": "namu.wiki 방문함", "titleUrl": "https://namu.wiki/", "time": utc_z(at(d, "13:00")),
                    "products": ["Search"], "activityControls": ["웹 및 앱 활동"]})
    return sorted(out, key=lambda x: x["time"], reverse=True)


# ---------- 지도 타임라인 ----------
PLACES = {  # 이름: (placeId 씨앗, 종류, 위도, 경도). 휴대폰 타임라인에는 이름이 없고 placeId·종류·좌표만 남는다
    "집": ("home", "HOME", 37.55602, 126.90503), "회사": ("work", "WORK", 37.50061, 127.03641),
    "망원한강공원": ("hangang", "UNKNOWN", 37.55451, 126.89452), "망원동물병원": ("vet", "UNKNOWN", 37.55684, 126.90702),
    "망원 한정식집": ("hansik", "UNKNOWN", 37.55522, 126.90311), "여의도공원": ("yeouido", "UNKNOWN", 37.52590, 126.92270),
    "김포공항": ("gmp", "UNKNOWN", 37.55860, 126.79440), "제주공항": ("cju", "UNKNOWN", 33.51040, 126.49140),
    "애월 숙소": ("aewol-stay", "UNKNOWN", 33.46300, 126.31000), "협재해수욕장": ("hyeopjae", "UNKNOWN", 33.39400, 126.23960),
    "성산일출봉": ("seongsan", "UNKNOWN", 33.45800, 126.94250), "용눈이오름": ("oreum", "UNKNOWN", 33.45900, 126.83200),
    "애월 카페": ("aewol-cafe", "UNKNOWN", 33.46500, 126.31500), "합정 러닝용품점": ("shoe", "UNKNOWN", 37.54960, 126.91380),
}


def visits(rng: random.Random) -> list[tuple[datetime, datetime, str]]:
    out: list[tuple[datetime, datetime, str]] = []
    for d in days():
        if workday(d):
            leave = at(d, "20:40", rng.randint(0, 130)) if phase(d) == "lonely" else at(d, "18:20", rng.randint(0, 50))
            out.append((at(d, "08:55", rng.randint(0, 15)), leave, "회사"))
        if d in RUNS:
            hm, km, mins = RUNS[d]
            place = "여의도공원" if d == END else "망원한강공원"
            out.append((at(d, hm, -10), at(d, hm, mins + 20), place))
    out += [
        (at(date(2026, 6, 28), "14:40"), at(date(2026, 6, 28), "15:35"), "합정 러닝용품점"),
        (at(date(2026, 7, 21), "10:10"), at(date(2026, 7, 21), "11:05"), "망원동물병원"),
        (at(date(2026, 7, 27), "19:00"), at(date(2026, 7, 27), "19:40"), "망원동물병원"),
        (at(date(2026, 8, 20), "19:10"), at(date(2026, 8, 20), "19:50"), "망원동물병원"),
        (at(date(2026, 7, 31), "19:05"), at(date(2026, 7, 31), "20:55"), "망원 한정식집"),
        (at(date(2026, 8, 11), "07:20"), at(date(2026, 8, 11), "08:30"), "김포공항"),
        (at(date(2026, 8, 11), "09:40"), at(date(2026, 8, 11), "10:30"), "제주공항"),
        (at(date(2026, 8, 11), "11:30"), at(date(2026, 8, 11), "15:40"), "협재해수욕장"),
        (at(date(2026, 8, 11), "17:00"), at(date(2026, 8, 12), "10:20"), "애월 숙소"),
        (at(date(2026, 8, 12), "10:40"), at(date(2026, 8, 12), "12:30"), "애월 카페"),
        (at(date(2026, 8, 12), "15:00"), at(date(2026, 8, 12), "16:30"), "용눈이오름"),
        (at(date(2026, 8, 12), "19:30"), at(date(2026, 8, 13), "04:50"), "애월 숙소"),
        (at(date(2026, 8, 13), "05:40"), at(date(2026, 8, 13), "07:30"), "성산일출봉"),
        (at(date(2026, 8, 13), "18:00"), at(date(2026, 8, 14), "11:00"), "애월 숙소"),
        (at(date(2026, 8, 14), "15:10"), at(date(2026, 8, 14), "16:20"), "제주공항"),
        (at(date(2026, 8, 14), "17:30"), at(date(2026, 8, 14), "18:10"), "김포공항"),
    ]
    out.sort()
    # 서울에서 다음 외출까지 비는 시간은 집에 있던 것으로 채운다 (이동 30분씩 빼고 1시간 이상일 때만)
    filled = []
    first = (at(START, "00:00"), at(START, "08:10"), "집")
    prev_end, prev_place = first[1], "집"
    filled.append(first)
    for s, e, p in out:
        if PLACES[prev_place][2] > 36 and PLACES[p][2] > 36:
            hs, he = prev_end + timedelta(minutes=30), s - timedelta(minutes=30)
            if he - hs >= timedelta(hours=1):
                filled.append((hs, he, "집"))
        filled.append((s, e, p))
        prev_end, prev_place = e, p
    filled.append((prev_end + timedelta(minutes=30), at(END, "23:50"), "집"))
    return filled


def timeline(rng: random.Random) -> dict:
    segs = []
    vs = visits(rng)
    for k, (s, e, name) in enumerate(vs):
        seed, kind, lat, lon = PLACES[name]
        segs.append({"startTime": s.isoformat(timespec="milliseconds"), "endTime": e.isoformat(timespec="milliseconds"),
                     "startTimeTimezoneUtcOffsetMinutes": 540, "endTimeTimezoneUtcOffsetMinutes": 540,
                     "visit": {"hierarchyLevel": 0, "probability": round(rng.uniform(0.6, 0.95), 2),
                               "topCandidate": {"placeId": "ChIJ" + vid("place-" + seed) + "Qx", "semanticType": kind,
                                                "probability": round(rng.uniform(0.5, 0.9), 2),
                                                "placeLocation": {"latLng": f"{lat:.7f}°, {lon:.7f}°"}}}})
        if k + 1 < len(vs):  # 다음 방문까지의 이동 구간 (가져올 때는 쓰지 않는다)
            ns = vs[k + 1][0]
            if ns > e:
                segs.append({"startTime": e.isoformat(timespec="milliseconds"), "endTime": ns.isoformat(timespec="milliseconds"),
                             "activity": {"start": {"latLng": f"{lat:.7f}°, {lon:.7f}°"},
                                          "topCandidate": {"type": "IN_SUBWAY" if name == "회사" else "WALKING",
                                                           "probability": 0.7}, "distanceMeters": 1200.0}})
    return {"semanticSegments": segs, "rawSignals": [], "userLocationProfile": {"frequentPlaces": [
        {"placeId": "ChIJ" + vid("place-home") + "Qx", "placeLocation": "37.5560200°, 126.9050300°", "label": "HOME"},
        {"placeId": "ChIJ" + vid("place-work") + "Qx", "placeLocation": "37.5006100°, 127.0364100°", "label": "WORK"}]}}


# ---------- 카드 이용내역 ----------
def card_rows(rng: random.Random) -> list[tuple[datetime, str, int, str, bool]]:
    rows: list[tuple[datetime, str, int, str, bool]] = []  # (시각, 가맹점, 금액, 업종, 취소)
    lunch = ["역삼 김밥천국", "한솥도시락 역삼점", "본죽 역삼역점", "역삼 쌀국수집", "서브웨이 역삼점", "역삼 순대국"]
    for d in days():
        ph = phase(d)
        if workday(d):
            if rng.random() < 0.6:
                rows.append((at(d, "08:40", rng.randint(0, 15)), "스타벅스 역삼역점", rng.choice([4700, 5200, 6100]), "커피전문점", False))
            rows.append((at(d, "12:10", rng.randint(0, 25)), rng.choice(lunch), rng.choice([8500, 9000, 9500, 11000, 12000]), "일반음식점", False))
        if ph == "lonely" and rng.random() < 0.8:  # 늦은 퇴근 뒤 편의점 혼밥
            rows.append((at(d, "22:40", rng.randint(0, 120)), "GS25 망원점", rng.choice([6800, 8900, 11200, 13400]), "편의점", False))
        if ph == "lonely" and d.weekday() >= 5:
            rows.append((at(d, "21:30", rng.randint(0, 120)), "배달의민족", rng.choice([19800, 23500, 27000]), "배달앱", False))
        if d in RUNS and d >= date(2026, 7, 4):
            hm = RUNS[d][0]
            rows.append((at(d, hm, RUNS[d][2] + 12), "GS25 망원한강점", rng.choice([1800, 2400, 3500]), "편의점", False))
        if ph in ("dog", "late") and rng.random() < 0.15:
            rows.append((at(d, "20:30", rng.randint(0, 60)), "쿠팡", rng.choice([12900, 18700, 24300]), "온라인쇼핑", False))
        if ph != "lonely" and not workday(d) and not in_jeju(d) and rng.random() < 0.4:
            rows.append((at(d, "13:00", rng.randint(0, 120)), rng.choice(["망원시장 칼국수", "망원동 브런치카페", "올리브영 망원점"]),
                         rng.choice([9000, 14500, 17800]), "일반음식점", False))
    rows += [
        (at(date(2026, 6, 26), "23:05"), "무신사스토어", 129000, "온라인쇼핑", False),
        (at(date(2026, 6, 27), "10:12"), "무신사스토어", 129000, "온라인쇼핑", True),     # 매장에서 신어 보고 사려고 취소
        (at(date(2026, 6, 28), "15:20"), "아식스 합정점", 139000, "스포츠용품", False),     # 러닝화
        (at(date(2026, 7, 1), "09:00"), "망원러닝크루 월회비", 20000, "스포츠·레저", False),
        (at(date(2026, 7, 13), "22:10"), "BHC 망원점", 18000, "일반음식점", False),
        (at(date(2026, 7, 14), "12:40"), "무신사스토어", 32000, "온라인쇼핑", False),       # 러닝 양말·벨트
        (at(date(2026, 7, 20), "21:20"), "GS25 망원점", 4500, "편의점", False),           # 강아지 물·간식
        (at(date(2026, 7, 21), "11:02"), "망원동물병원", 85000, "동물병원", False),
        (at(date(2026, 7, 21), "13:15"), "쿠팡", 67800, "반려동물용품", False),            # 사료·배변패드·하네스
        (at(date(2026, 7, 27), "19:35"), "망원동물병원", 35000, "동물병원", False),
        (at(date(2026, 7, 27), "22:40"), "제주항공", 128400, "항공사", False),
        (at(date(2026, 7, 31), "20:50"), "망원 한정식집", 46000, "일반음식점", False),
        (at(date(2026, 8, 1), "09:00"), "망원러닝크루 월회비", 20000, "스포츠·레저", False),
        (at(date(2026, 8, 2), "15:10"), "펫프렌즈", 15000, "반려동물용품", False),
        (at(date(2026, 8, 3), "21:30"), "에어비앤비", 186000, "숙박", False),
        (at(date(2026, 8, 11), "10:40"), "제주 렌터카", 98000, "렌터카", False),
        (at(date(2026, 8, 11), "19:30"), "제주 흑돼지 애월점", 52000, "일반음식점", False),
        (at(date(2026, 8, 12), "11:20"), "애월 바다카페", 13500, "커피전문점", False),
        (at(date(2026, 8, 13), "05:45"), "성산일출봉 입장료", 5000, "관광", False),
        (at(date(2026, 8, 13), "13:00"), "CU 협재해변점", 7600, "편의점", False),
        (at(date(2026, 8, 14), "13:40"), "오설록 티뮤지엄", 23000, "기념품", False),
        (at(date(2026, 8, 18), "18:30"), "마포구 동물보호센터 입양 등록", 10000, "공공요금", False),
        (at(date(2026, 8, 19), "22:05"), "서울 10K 레이스 참가비", 45000, "스포츠·레저", False),
        (at(date(2026, 8, 20), "19:45"), "망원동물병원", 45000, "동물병원", False),
        (at(date(2026, 8, 22), "16:20"), "펫프렌즈", 39000, "반려동물용품", False),
        (at(date(2026, 8, 29), "11:40"), "여의도 브런치카페", 21000, "일반음식점", False),
    ]
    return sorted(rows)


def write_card(rng: random.Random, path: Path) -> int:
    from openpyxl import Workbook

    wb = Workbook()
    wb.properties.created = wb.properties.modified = datetime(2026, 8, 30, 9, 0)
    ws = wb.active
    ws.title = "이용내역"
    rows = card_rows(rng)
    ws.append(["신한카드 국내이용내역"])
    ws.append(["조회기간", "2026.06.01 ~ 2026.08.29"])
    ws.append(["카드", "신한카드 Deep Dream (본인 9411-****-****-2731)"])
    ws.append([])
    ws.append(["이용일자", "이용시간", "이용카드", "이용가맹점", "이용금액", "이용구분", "업종", "승인번호", "취소여부"])
    for dt, merchant, amount, cat, cancel in rows:
        ws.append([dt.strftime("%Y.%m.%d"), dt.strftime("%H:%M"), "본인 2731", merchant, amount, "일시불", cat,
                   f"{rng.randint(10000000, 99999999)}", "취소" if cancel else ""])
    ws.append(["합계", "", "", "", sum(a * (-1 if c else 1) for _, _, a, _, c in rows), "", "", "", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return len(rows)


# ---------- 삼성 헬스 ----------
def sleep_of(rng: random.Random, d: date) -> tuple[datetime, datetime]:
    """d 날 아침에 깬 잠. 6월에는 새벽 1시 반~3시에 잠들고, 러닝을 시작한 뒤로는 자정 무렵."""
    if phase(d) == "lonely":
        start = at(d, "01:20", rng.randint(0, 100))
        end = at(d, "07:25", rng.randint(0, 25)) if workday(d) else at(d, "09:30", rng.randint(0, 90))
    else:
        start = at(d - timedelta(days=1), "23:35", rng.randint(0, 55))
        end = at(d, "07:00", rng.randint(0, 25)) if workday(d) else at(d, "08:10", rng.randint(0, 60))
    return start, end


def write_health(rng: random.Random, folder: Path) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = "20260830090000"
    dev_phone, dev_watch = "5b3c1a9e-phone", "a71e44d2-watch"
    steps_rows, sleep_rows, ex_rows = [], [], []
    for d in days():
        base = rng.randint(3200, 5800) if workday(d) else rng.randint(1500, 4200)
        if phase(d) == "lonely" and not workday(d):
            base = rng.randint(800, 2600)  # 주말에 집 밖으로 거의 나가지 않는다
        if d >= DOG_FROM + timedelta(days=2):
            base += rng.randint(2500, 4200)  # 콩이 산책
        if in_jeju(d):
            base = rng.randint(14000, 21000)
        run = RUNS.get(d)
        steps = base + (int(run[1] * 1250) if run else 0)
        mid = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        for dev, n in ((dev_phone, int(steps * rng.uniform(0.82, 0.95))), (dev_watch, steps)):
            steps_rows.append([n, int(n * 0.7), round(n * 0.72, 1), round(n * 0.04, 2), dev,
                               int(mid.timestamp() * 1000), (mid + timedelta(hours=15)).strftime("%Y-%m-%d %H:%M:%S.000"),
                               vid(f"steps-{d}-{dev}")])
        s, e = sleep_of(rng, d)
        sleep_rows.append([s.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000"),
                           e.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000"), "UTC+0900", dev_watch,
                           vid(f"sleep-{d}")])
        if run:
            hm, km, mins = run
            st = at(d, hm)
            ex_rows.append([st.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000"),
                            (st + timedelta(minutes=mins)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000"),
                            "UTC+0900", 1002, mins * 60000, km * 1000, round(km * 62), dev_watch, vid(f"run-{d}")])
        if d >= DOG_FROM + timedelta(days=2) and not in_jeju(d) and d.toordinal() % 2 == 0:
            st = at(d, "07:20", rng.randint(0, 20))
            mins = rng.randint(25, 40)
            ex_rows.append([st.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000"),
                            (st + timedelta(minutes=mins)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.000"),
                            "UTC+0900", 1001, mins * 60000, round(mins * 70), round(mins * 3.5), dev_watch, vid(f"walk-{d}")])

    def write(name: str, meta: str, header: list[str], rows: list[list]) -> None:
        with (folder / f"{name}.{stamp}.csv").open("w", encoding="utf-8", newline="") as f:
            f.write(meta + "\n")
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    write("com.samsung.shealth.tracker.pedometer_day_summary", "com.samsung.shealth.tracker.pedometer_day_summary,6320003,6",
          ["step_count", "walk_step_count", "distance", "calorie", "deviceuuid", "day_time", "create_time", "datauuid"], steps_rows)
    p = "com.samsung.health.sleep."
    write("com.samsung.shealth.sleep", "com.samsung.shealth.sleep,6315006,3",
          [p + "start_time", p + "end_time", p + "time_offset", p + "deviceuuid", p + "datauuid"], sleep_rows)
    p = "com.samsung.health.exercise."
    write("com.samsung.shealth.exercise", "com.samsung.shealth.exercise,6315004,8",
          [p + "start_time", p + "end_time", p + "time_offset", p + "exercise_type", p + "duration", p + "distance",
           p + "calorie", p + "deviceuuid", p + "datauuid"], ex_rows)
    return len(sleep_rows)


# ---------- 스포티파이 ----------
SONGS = {
    "night": [("아이유", "밤편지"), ("아이유", "Love poem"), ("성시경", "거리에서"), ("이소라", "바람이 분다"),
              ("넬", "기억을 걷는 시간"), ("백예린", "Square (2017)"), ("잔나비", "주저하는 연인들을 위해")],
    "run": [("DAY6 (데이식스)", "한 페이지가 될 수 있게"), ("NewJeans", "Hype Boy"), ("IVE (아이브)", "LOVE DIVE"),
            ("LE SSERAFIM (르세라핌)", "ANTIFRAGILE"), ("BTS", "Dynamite"), ("SEVENTEEN (세븐틴)", "아주 NICE")],
    "day": [("폴킴", "모든 날, 모든 순간"), ("검정치마", "EVERYTHING"), ("AKMU (악뮤)", "어떻게 이별까지 사랑하겠어, 널 사랑하는 거지"),
            ("볼빨간사춘기", "여행"), ("쿨", "해변의 여인")],
}


def spotify(rng: random.Random) -> list[dict]:
    out = []

    def play(start: datetime, theme: str, n: int) -> None:
        t = start
        for _ in range(n):
            artist, track = rng.choice(SONGS[theme])
            ms = rng.randint(185000, 245000) if rng.random() > 0.12 else rng.randint(4000, 25000)  # 가끔 넘긴다
            t += timedelta(milliseconds=ms)
            out.append({"endTime": t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M"), "artistName": artist,
                        "trackName": track, "msPlayed": ms})

    for d in days():
        if workday(d):
            play(at(d, "08:05", rng.randint(0, 10)), "day", rng.randint(2, 3))  # 출근길
        if phase(d) == "lonely":
            play(at(d, "00:30", rng.randint(0, 60)), "night", rng.randint(2, 4))
        if d in RUNS:
            hm, _, mins = RUNS[d]
            play(at(d, hm), "run", max(3, mins // 4))
        if in_jeju(d):
            play(at(d, "11:00", rng.randint(0, 60)), "day", 3)
    return sorted(out, key=lambda x: x["endTime"])


def main() -> None:
    takeout = HERE / "google" / "Takeout"
    yt_dir = takeout / "YouTube 및 YouTube Music" / "시청 기록"
    se_dir = takeout / "내 활동" / "검색"
    for d in (yt_dir, se_dir, HERE / "maps", HERE / "spotify"):
        d.mkdir(parents=True, exist_ok=True)
    yt = youtube(random.Random(SEED + 1))
    (yt_dir / "시청 기록.json").write_text(json.dumps(yt, ensure_ascii=False, indent=1), encoding="utf-8")
    se = search(random.Random(SEED + 2))
    (se_dir / "내활동.json").write_text(json.dumps(se, ensure_ascii=False, indent=1), encoding="utf-8")
    tl = timeline(random.Random(SEED + 3))
    (HERE / "maps" / "Timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    n_card = write_card(random.Random(SEED + 4), HERE / "card" / "신한카드_이용내역.xlsx")
    n_days = write_health(random.Random(SEED + 5), HERE / "health" / "samsunghealth_seoyeon_20260830090000")
    sp = spotify(random.Random(SEED + 6))
    (HERE / "spotify" / "StreamingHistory_music_0.json").write_text(json.dumps(sp, ensure_ascii=False, indent=1),
                                                                    encoding="utf-8")
    visits_n = sum(1 for s in tl["semanticSegments"] if "visit" in s)
    print(f"유튜브 {len(yt)}줄, 검색 {len(se)}줄, 지도 구간 {len(tl['semanticSegments'])}개(방문 {visits_n}), 카드 {n_card}건,"
          f" 건강 {n_days}일, 스포티파이 {len(sp)}곡")


if __name__ == "__main__":
    main()
