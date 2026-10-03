"""소설 재료 컬럼: 인물 역할·장소·리듬·취향·전환점·발견 문장이 기록의 모양대로 나오는지."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from virtueme_core import columns, story
from virtueme_core.records import Record, iso, record_id
from virtueme_core.store import Store

TZ = ZoneInfo("Asia/Seoul")
START = datetime(2026, 6, 1, tzinfo=TZ)


def msg(day: int, hour: int, author: str, thread: str, others: list[str], text: str = "응") -> Record:
    ts = iso(START + timedelta(days=day, hours=hour))
    return Record(id=record_id("kakao", thread, ts, author, text), ts=ts, source="kakao", kind="message", modality="text",
                  text=text, author=author, is_self=author == "나", thread=thread, participants=["나", *others],
                  meta={"room": thread})


def trace(day: int, hour: int, source: str, kind: str, text: str, **kw) -> Record:
    ts = iso(START + timedelta(days=day, hours=hour))
    meta = kw.pop("meta", {})
    return Record(id=record_id(source, ts, text), ts=ts, source=source, kind=kind, modality="text", text=text,
                  author="나", meta=meta, **kw)


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "c.db")
    recs: list[Record] = []
    for d in range(90):
        # 지인A: 처음부터 끝까지 매일. 지인B: 50일째 처음 나타나 밤에 많이 이야기함. 지인C: 30일째 뒤로 끊김
        recs += [msg(d, 12, "지인A", "a", ["지인A"]), msg(d, 12, "나", "a", ["지인A"])]
        if d >= 50:
            recs += [msg(d, 23, "지인B", "b", ["지인B"]), msg(d, 23, "나", "b", ["지인B"])]
        if d < 30:
            recs += [msg(d, 9, "지인C", "c", ["지인C"])]
        # 처음 한 달은 밤 1시 결제, 그 뒤는 낮 결제. 60일째부터 펫 업종이 새로 생김
        hour = 1 if d < 30 else 13
        cat = "반려동물" if d >= 60 else "편의점"
        recs.append(trace(d, hour, "card", "payment", f"결제: 가게{d % 3} 5,000원",
                          place=f"가게{d % 3}", meta={"merchant": f"가게{d % 3}", "amount": 5000, "category": cat}))
        # 집에는 매일, 한강공원에는 사흘에 한 번, 70일째 제주
        recs.append(trace(d, 20, "maps", "visit", "방문: 집", lat=37.556, lon=126.901, place="집",
                          meta={"place_id": "home", "place_type": "HOME", "duration_min": 600}))
        if d % 3 == 0:
            recs.append(trace(d, 7, "maps", "visit", "방문: 망원한강공원", lat=37.554, lon=126.896, place="망원한강공원",
                              meta={"place_id": "park", "place_type": "UNKNOWN", "duration_min": 50}))
        steps = 3000 if d < 30 else 11000
        bed = "02:10" if d < 30 else "23:20"
        recs.append(trace(d, 23, "health", "health", f"건강: 걸음 {steps}", meta={"steps": steps, "sleep_min": 380,
                                                                             "sleep_start": bed, "workouts": []}))
        if 20 <= d < 30:
            recs += [trace(d, 0, "search", "search", "검색: 번아웃 증상", meta={"query": "번아웃 증상"})]
    recs.append(trace(70, 14, "maps", "visit", "방문: 성산일출봉", lat=33.458, lon=126.942, place="성산일출봉",
                      meta={"place_id": "jeju", "place_type": "UNKNOWN", "duration_min": 90}))
    s.upsert_records(recs)
    return s


def test_cast_roles(store):
    cols = columns.compute(store, emotions={})
    by = {c["alias"]: c for c in cols["cast"]}
    assert "새로 등장한 사람" in by["지인B"]["roles"]
    assert by["지인B"]["night_share"] > 0.9
    assert "연락이 끊긴 사람" in by["지인C"]["roles"]
    assert by["지인C"]["silent_days"] >= 59
    assert "늘 곁에 있는 사람" in by["지인A"]["roles"]
    assert by["지인A"]["reply_min_mine"] == 0.0


def test_places_rhythm_taste(store):
    cols = columns.compute(store, emotions={})
    pl = cols["places"]
    assert pl["anchors"][0]["name"] == "집"
    assert pl["regular"][0]["name"] == "망원한강공원" and pl["regular"][0]["visits"] == 30
    assert pl["far"][0]["name"] == "성산일출봉" and pl["far"][0]["km"] > 300
    rh = cols["rhythm"]
    assert rh["bedtime_first_third"] > rh["bedtime_last_third"]
    assert rh["steps_monthly"]["2026-08"] > rh["steps_monthly"]["2026-06"]
    sp = cols["taste"]["spend"]
    assert "반려동물" in sp["new_categories"]
    assert "번아웃" in {b["term"] for b in cols["taste"]["search"]["bursts"]}


def test_catalog_marks_missing_sources(store):
    cols = columns.compute(store, emotions={})
    avail = {c["key"]: c["available"] for c in cols["catalog"]}
    assert avail["taste.spend"] and avail["rhythm.sleep"]
    assert not avail["taste.music"] and not avail["taste.video"]


def test_discoveries_have_evidence_and_no_coordinates(store):
    cols = columns.compute(store, emotions={})
    texts = " ".join(d["text"] for d in cols["discoveries"])
    assert "망원한강공원" in texts and "성산일출봉" in texts
    assert "번아웃" in texts
    assert any("잠든 시각" in d["text"] for d in cols["discoveries"])
    ids = {r.id for r in store.records()}
    for d in cols["discoveries"]:
        assert all(i in ids for i in d["evidence"])
    note = columns.note_for_story(cols)
    dumped = str(note)
    assert "37.5" not in dumped and "126.9" not in dumped
    assert note["cast"][0]["who"]


def test_turning_point_groups_columns(store):
    cols = columns.compute(store, emotions={})
    tp = [t for t in cols["turning_points"] if t["strength"] >= 2]
    assert tp, cols["turning_points"]
    assert abs((datetime.fromisoformat(tp[0]["date"]).date() - (START + timedelta(days=30)).date()).days) <= 7


def test_genre_hint_ranks_romance_for_new_night_person(store):
    cols = columns.compute(store, emotions={})
    assert cols["genre_hint"][0]["genre"] in ("로맨스", "미스터리")
    assert {g["genre"] for g in cols["genre_hint"]} >= {"미스터리", "로맨스", "SF", "판타지", "스릴러", "코미디"}


def test_story_note_carries_columns(svc):
    res = svc.query("러닝")
    prompt = story.story_prompt(res, genre=0, seed=1, columns=columns.note_for_story(columns.compute(svc.store)))
    assert "background" in prompt["user"]


def test_columns_api_and_place_names(store):
    from fastapi.testclient import TestClient

    from virtueme_core.api import create_app
    from virtueme_core.service import Service

    svc = Service("t", fake_models=True, store=store)
    client = TestClient(create_app(svc))
    cols = client.get("/columns").json()
    park = cols["places"]["regular"][0]
    assert park["key"] == "id:park"
    r = client.post("/places/name", json={"key": "id:park", "name": "강변 공원"}).json()
    assert r["places"]["regular"][0]["name"] == "강변 공원"
    assert client.post("/places/name", json={"key": "home", "name": "x"}).status_code == 422


def test_josa():
    assert columns.josa("러닝 크루 친구", "은는") == "러닝 크루 친구는"
    assert columns.josa("회사 동기", "과와") == "회사 동기와"
    assert columns.josa("엄마", "이가") == "엄마가"
    assert columns.josa("0시 08분", "으로") == "0시 08분으로"
    assert columns.josa("서울", "으로") == "서울로"
