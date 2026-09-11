"""raw_weeks.json → 실제 앱이 내보내는 형식의 파일들.

- kakao/대학친구들.txt : PC 형식 (구분선 + [이름] [오후 1:00])
- kakao/엄마.txt       : 모바일 점 날짜 형식 (2026. 6. 14. 오후 7:12, 엄마 : …)
- kakao/정우진.csv     : Mac csv (Date,User,Message)
- instagram/           : "내 정보 다운로드" JSON 구조, 한글은 실제 내보내기처럼 latin-1 깨짐 형태로 적는다
- photo_manifest.json  : fetch_photos.py 가 내려받을 사진 목록 (촬영 시각·장소 포함)

같은 입력에서 늘 같은 파일이 나온다.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
WD = "월화수목금토일"
ME = "윤서연"

PLACES = [  # (장소 이름에 들어간 낱말, 위도, 경도) — 공개된 관광지·공원 좌표
    (("협재",), 33.3940, 126.2396), (("성산", "일출봉"), 33.4580, 126.9425), (("오름",), 33.4590, 126.8320),
    (("해녀",), 33.5190, 126.8540), (("애월",), 33.4630, 126.3100), (("제주",), 33.4996, 126.5312),
    (("반포",), 37.5100, 126.9960), (("여의도", "한강"), 37.5284, 126.9340), (("망원",), 37.5556, 126.9040),
    (("대전",), 36.3504, 127.3845), (("성수",), 37.5446, 127.0557),
]


def mojibake(s: str) -> str:
    return s.encode("utf-8").decode("latin-1")


def ampm(dt: datetime) -> str:
    h = dt.hour
    return f"{'오전' if h < 12 else '오후'} {h % 12 or 12}:{dt.minute:02d}"


def gps_for(place: str) -> tuple[float, float] | None:
    for keys, lat, lon in PLACES:
        if any(k in (place or "") for k in keys):
            return lat, lon
    return None


def load() -> list[dict]:
    return json.loads((HERE / "raw_weeks.json").read_text(encoding="utf-8"))


def parse_dt(d: str, t: str) -> datetime:
    return datetime.fromisoformat(f"{d}T{t}:00").replace(tzinfo=KST)


def main() -> None:
    weeks = load()
    rooms: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
    posts, stories, dms, photos = [], [], [], []
    for w in weeks:
        for m in w["kakao"]:
            rooms[m["room"]].append((parse_dt(m["date"], m["time"]), m["sender"], m["text"].strip()))
        posts += w.get("instagram_posts", [])
        stories += w.get("instagram_stories", [])
        dms += w.get("instagram_dm", [])
        photos += w.get("photos", [])
    for msgs in rooms.values():
        msgs.sort(key=lambda x: x[0])

    kdir = HERE / "kakao"
    kdir.mkdir(exist_ok=True)
    # PC 형식
    lines = ["대학친구들 님과 카카오톡 대화", "저장한 날짜 : 2026-08-30 22:41:07", ""]
    day = None
    for dt, sender, text in rooms["대학친구들"]:
        if dt.date() != day:
            day = dt.date()
            lines.append(f"--------------- {day.year}년 {day.month}월 {day.day}일 {WD[day.weekday()]}요일 ---------------")
        lines.append(f"[{sender}] [{ampm(dt)}] {text}")
    (kdir / "대학친구들.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 모바일 점 날짜 형식
    lines = ["엄마 님과 카카오톡 대화", "저장한 날짜 : 2026. 8. 30. 오후 10:42", ""]
    day = None
    for dt, sender, text in rooms["엄마"]:
        if dt.date() != day:
            if day is not None:
                lines.append("")
            day = dt.date()
            lines.append(f"{day.year}년 {day.month}월 {day.day}일 {WD[day.weekday()]}요일")
        lines.append(f"{dt.year}. {dt.month}. {dt.day}. {ampm(dt)}, {sender} : {text}")
    (kdir / "엄마.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Mac csv
    with (kdir / "정우진.csv").open("w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["Date", "User", "Message"])
        for dt, sender, text in rooms["정우진"]:
            wr.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), sender, text])

    # 인스타그램
    ig = HERE / "instagram"
    content = ig / "your_instagram_activity" / "content"
    content.mkdir(parents=True, exist_ok=True)
    manifest = []

    def media_entry(item: dict, kind: str, i: int) -> dict:
        dt = parse_dt(item["date"], item["time"])
        uid = hashlib.sha1(f"{kind}{i}{item['date']}{item['time']}".encode()).hexdigest()[:10]
        uri = f"media/{kind}/{dt.strftime('%Y%m')}/{uid}.jpg"
        manifest.append({"target": f"instagram/{uri}", "query": item.get("photo_query") or "city street",
                         "date": item["date"], "time": item["time"], "place": item.get("place", ""), "exif": False})
        entry = {"uri": uri, "creation_timestamp": int(dt.timestamp()), "title": mojibake(item.get("caption", "")),
                 "media_metadata": {"photo_metadata": {"exif_data": []}}}
        g = gps_for(item.get("place", ""))
        if g:
            entry["media_metadata"]["photo_metadata"]["exif_data"].append({"latitude": g[0], "longitude": g[1]})
        return entry

    post_json = [{"media": [media_entry(p, "posts", i)]} for i, p in enumerate(posts)]
    (content / "posts_1.json").write_text(json.dumps(post_json, indent=1), encoding="utf-8")
    story_json = {"ig_stories": [media_entry(s, "stories", i) for i, s in enumerate(stories)]}
    (content / "stories.json").write_text(json.dumps(story_json, indent=1), encoding="utf-8")
    inbox = ig / "your_instagram_activity" / "messages" / "inbox" / "haeun_1029384756"
    inbox.mkdir(parents=True, exist_ok=True)
    msgs = sorted(dms, key=lambda m: (m["date"], m["time"]), reverse=True)
    (inbox / "message_1.json").write_text(json.dumps({
        "participants": [{"name": mojibake("한하은")}, {"name": mojibake(ME)}],
        "title": mojibake("한하은"),
        "messages": [{"sender_name": mojibake(m["sender"]), "content": mojibake(m["text"]),
                      "timestamp_ms": int(parse_dt(m["date"], m["time"]).timestamp() * 1000) + k}
                     for k, m in enumerate(msgs)],
    }, indent=1), encoding="utf-8")
    pi = ig / "personal_information" / "personal_information"
    pi.mkdir(parents=True, exist_ok=True)
    (pi / "personal_information.json").write_text(json.dumps({"profile_user": [
        {"string_map_data": {"Name": {"value": mojibake(ME)}, "Username": {"value": "seoyeon.runs"}}}]}, indent=1),
        encoding="utf-8")

    # 휴대폰 사진 폴더 (EXIF 를 넣어 저장)
    for i, p in enumerate(photos):
        dt = parse_dt(p["date"], p["time"])
        manifest.append({"target": f"photos/IMG_{dt.strftime('%Y%m%d_%H%M')}_{i:02d}.jpg",
                         "query": p.get("photo_query") or "city street", "date": p["date"], "time": p["time"],
                         "place": p.get("place", ""), "exif": True, "gps": gps_for(p.get("place", ""))})
    (HERE / "photo_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print({"대학친구들": len(rooms["대학친구들"]), "엄마": len(rooms["엄마"]), "정우진": len(rooms["정우진"]),
           "posts": len(posts), "stories": len(stories), "dm": len(dms), "photos": len(photos),
           "manifest": len(manifest), "generated_at": datetime.now(timezone.utc).date().isoformat()})


if __name__ == "__main__":
    main()
