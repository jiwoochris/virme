"""흔적 기록 가져오기: 구글 테이크아웃(유튜브·검색·지도), 카드 이용내역·승인 문자, 삼성 헬스·애플 건강, 스포티파이, 넷플릭스.
그리고 흔적 기록이 정서 채점·주제 묶기에서 빠지고 검색 근거로는 쓰이는지."""
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from virtueme_core import narrative, patterns
from virtueme_core.build import build
from virtueme_core.emotion import score_all
from virtueme_core.importer import ingest
from virtueme_core.ingest import card, google, health, sms, streaming
from virtueme_core.ingest.traces import Window
from virtueme_core.privacy import Masker, Pseudonymizer
from virtueme_core.records import TRACE_KINDS, Record
from virtueme_core.store import Store

FIX = Path(__file__).parent / "fixtures" / "traces"
KST = ZoneInfo("Asia/Seoul")
M = Masker({"윤서연": "나"})


def test_youtube_english_json_strips_wrapper_and_skips_ads_and_removed():
    recs, info = google.import_youtube(FIX / "takeout_en", M)
    assert info == {"files": 1, "watched": 2, "ads": 1, "removed": 1, "out_of_range": 0}
    r = next(r for r in recs if "running" in r.text)
    assert r.text == "유튜브 시청: How to start running: 5 tips (Run Coach)"
    assert (r.source, r.kind, r.author, r.is_self, r.modality) == ("youtube", "watch", "나", True, "text")
    assert r.meta == {"title": "How to start running: 5 tips", "channel": "Run Coach",
                      "url": "https://www.youtube.com/watch?v=aaa111"}
    assert r.ts == "2026-06-24T22:05:10+09:00"  # UTC → 서울


def test_youtube_korean_html_variant():
    recs, info = google.import_youtube(FIX / "takeout_ko", M)
    assert info["ads"] == 1 and len(recs) == 1
    assert recs[0].text == "유튜브 시청: 유기견 임시보호 첫날 & 브이로그 (보호소 이야기)"
    assert recs[0].ts == "2026-07-20T23:42:05+09:00"


def test_search_only_searches_from_search_folder_and_masked():
    recs, info = google.import_search(FIX / "takeout_en", M)
    assert info["searches"] == 2 and info["other"] == 1  # YouTube/MyActivity.json 의 검색은 읽지 않는다
    texts = sorted(r.text for r in recs)
    assert "검색: running shoes for beginners" in texts
    masked = next(r for r in recs if "call" in r.text)
    assert "010-1234" not in masked.text and "[전화번호]" in masked.meta["query"] and "(나)" in masked.text
    ko, _ = google.import_search(FIX / "takeout_ko", M)
    assert [r.text for r in ko] == ["검색: 망원동 동물병원"] and ko[0].kind == "search"


def test_takeout_zip_finds_all_and_window_filters(tmp_path):
    z = tmp_path / "takeout.zip"
    with zipfile.ZipFile(z, "w") as f:
        for p in (FIX / "takeout_en").rglob("*.json"):
            f.write(p, p.relative_to(FIX / "takeout_en"))
    got, info = google.import_takeout(z, M, Window.of("2026-06-01", "2026-06-30"))
    assert set(got) == {"youtube", "search"}
    assert info["youtube"]["watched"] == 1 and info["youtube"]["out_of_range"] == 1  # 5월 30일 영상은 기간 밖


def test_maps_device_timeline_and_legacy_names():
    recs, info = google.import_maps(FIX / "maps" / "Timeline.json", M)
    assert info["visits"] == 2  # 이동·경로 구간은 뺀다
    park, home = recs
    assert park.place == "장소" and park.meta["duration_min"] == 63 and park.text == "방문: 장소 (1시간 3분 머무름)"
    assert (park.lat, park.lon) == (37.55451, 126.89452) and park.meta["place_id"] == "ChIJhangang"
    assert home.place == "집" and home.meta["place_type"] == "HOME" and home.ts == "2026-07-13T21:10:00+09:00"
    legacy, info = google.import_maps(FIX / "maps_legacy", M)
    vet = next(r for r in legacy if r.place == "망원동물병원")
    assert vet.ts == "2026-07-21T10:10:00+09:00" and vet.lat == 37.55684 and vet.text == "방문: 망원동물병원 (55분 머무름)"
    assert any(r.place == "집" for r in legacy)


def test_statement_header_detection_csv_cp949_and_html_xls():
    recs, info = card.import_statements(FIX / "card", M)
    assert info["files"] == 2 and info["payments"] == 4 and info["cancelled"] == 1
    shoes = next(r for r in recs if r.place == "아식스 합정점")
    assert shoes.text == "결제: 아식스 합정점 139,000원" and shoes.ts == "2026-06-28T15:20:11+09:00"
    assert shoes.meta == {"merchant": "아식스 합정점", "amount": 139000, "channel": "statement", "category": "스포츠용품"}
    assert next(r for r in recs if r.place == "무신사스토어").meta["amount"] == -129000  # 승인취소
    vet = next(r for r in recs if r.place == "망원동물병원")
    assert vet.ts == "2026-07-21T11:02:00+09:00" and vet.meta["category"] == "동물병원"
    assert not any("7890" in str(r.to_dict()) for r in recs)  # 카드 번호는 남기지 않는다


def test_statement_xlsx_with_preamble(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["신한카드 이용내역"])
    ws.append(["조회기간", "2026.06.01 ~ 2026.08.29"])
    ws.append([])
    ws.append(["이용일", "이용가맹점", "이용금액", "업종", "취소여부"])
    ws.append([datetime(2026, 8, 19, 22, 5), "서울 10K 레이스 참가비", 45000, "스포츠·레저", ""])
    ws.append(["2026.08.20", "망원동물병원", "45,000", "동물병원", "취소"])
    ws.append(["합계", "", 0, "", ""])
    wb.save(tmp_path / "s.xlsx")
    recs, info = card.import_statements(tmp_path, M, Window.of(until="2026-08-19"))
    assert [r.text for r in recs] == ["결제: 서울 10K 레이스 참가비 45,000원"] and info["out_of_range"] == 1
    assert recs[0].ts == "2026-08-19T22:05:00+09:00"


SMS_RECEIVED = datetime(2026, 9, 14, 13, 21, tzinfo=KST)


@pytest.mark.parametrize("text, merchant, amount", [
    ("[Web발신]\n삼성1234승인 홍*동\n12,000원 일시불\n09/14 13:20 스타벅스강남\n누적1,234,000원", "스타벅스강남", 12000),
    ("[Web발신]\n신한카드(1234)승인 홍*동 8,900원(일시불)09/14 13:20 GS25 망원점 누적512,300원", "GS25 망원점", 8900),
    ("[Web발신]\nKB국민카드1*2*승인\n홍*동님\n45,000원 일시불\n09/14 13:20\n서울 10K 레이스\n누적90,000원", "서울 10K 레이스", 45000),
    ("[Web발신]\n현대카드 ZERO 승인\n홍*동\n139,000원 3개월\n09/14 13:20\n아식스 합정점\n누적1,000원", "아식스 합정점", 139000),
    ("[Web발신]\n롯데카드 2*3* 승인 홍*동 4,500원 일시불 09/14 13:20 메가커피", "메가커피", 4500),
    ("[Web발신]\n우리(1234)승인\n홍*동님\n85,000원 일시불\n09/14 13:20\n망원동물병원\n누적85,000원", "망원동물병원", 85000),
    ("[Web발신]\n하나(1*2*)승인 홍*동님 6,000원 일시불 09/14 13:20 다이소 망원점 누적6,000원", "다이소 망원점", 6000),
    ("[Web발신]\nBC카드(5678) 승인 홍*동 30,000원 일시불 09/14 13:20 망원러닝크루", "망원러닝크루", 30000),
    ("[Web발신]\nNH카드1*2*승인\n홍*동\n2,400원 일시불\n09/14 13:20\nGS25 망원한강점\n총누적2,400원", "GS25 망원한강점", 2400),
    ("[Web발신]\n삼성1234승인취소 홍*동\n12,000원 일시불\n09/14 13:20 스타벅스강남", "스타벅스강남", -12000),
    ("[Web발신]\n신한카드(1234)취소 홍*동 8,900원(일시불)09/14 13:20 GS25 망원점", "GS25 망원점", -8900),
])
def test_card_sms_formats(text, merchant, amount):
    r = card.parse_card_sms(text, SMS_RECEIVED)
    assert r is not None and r.meta["merchant"] == merchant and r.meta["amount"] == amount
    assert r.meta["channel"] == "sms" and r.ts == "2026-09-14T13:20:00+09:00" and r.place == merchant
    dump = str(r.to_dict())
    assert "1234" not in dump and "홍*동" not in dump and "5678" not in dump  # 카드 번호·이름은 남지 않는다


def test_card_sms_not_card_and_year_rollover():
    assert card.parse_card_sms("[Web발신] 인증번호 123456", SMS_RECEIVED) is None
    assert card.parse_card_sms("오늘 삼성 승인 났어?", SMS_RECEIVED) is None
    r = card.parse_card_sms("[Web발신]\n삼성1234승인 홍*동\n12,000원 일시불\n12/31 23:50 편의점", datetime(2027, 1, 1, 0, 1, tzinfo=KST))
    assert r.ts.startswith("2026-12-31")


def test_sms_importer_turns_card_approvals_into_payments(tmp_path):
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<smses count="3">
  <sms address="15881688" date="1789359600000" type="1" body="[Web발신]&#10;삼성1234승인 윤*연&#10;12,000원 일시불&#10;09/14 13:20 스타벅스강남&#10;누적1,234,000원" contact_name="(Unknown)" />
  <sms address="15881688" date="1789359700000" type="1" body="[Web발신]&#10;삼성1234취소 윤*연&#10;12,000원 일시불&#10;09/14 13:21 스타벅스강남" contact_name="(Unknown)" />
  <sms address="15881234" date="1789359800000" type="1" body="[Web발신] 인증번호 123456" contact_name="(Unknown)" />
  <sms address="010-2345-6789" date="1789359900000" type="2" body="커피 샀어" contact_name="박지수" />
</smses>"""
    path = tmp_path / "sms.xml"
    path.write_text(xml, encoding="utf-8")
    recs, info = sms.to_records(sms.parse_file(path), Pseudonymizer(Store(tmp_path / "t.db")))
    pays = [r for r in recs if r.source == "card"]
    assert info["payments"] == 2 and info["skipped"] == {"service_number": 1}
    assert sorted(r.meta["amount"] for r in pays) == [-12000, 12000]
    assert all(r.kind == "payment" and r.is_self and r.author == "나" for r in pays)
    assert any(r.source == "sms" and r.text == "커피 샀어" for r in recs)


def test_samsung_health_prefixed_columns_one_record_per_day():
    recs, info = health.import_health(FIX / "samsung")
    assert info["apps"] == ["삼성 헬스"] and len(recs) == 1
    r = recs[0]
    assert r.ts == "2026-07-13T23:59:00+09:00" and (r.source, r.kind) == ("health", "health")
    assert r.meta["steps"] == 7500  # 폰 7,000 · 워치 7,500 가운데 큰 값
    assert (r.meta["sleep_min"], r.meta["sleep_start"], r.meta["sleep_end"]) == (447, "23:43", "07:10")
    assert r.meta["workouts"] == [{"type": "달리기", "minutes": 33, "km": 5.0}]
    assert r.text == "건강: 걸음 7,500 · 수면 7시간 27분 (23:43~07:10) · 달리기 33분 5.0km"


def test_apple_health_streaming_dedupes_sources_and_window():
    recs, info = health.import_health(FIX / "apple", Window.of("2026-07-01", "2026-07-31"))
    assert info["apps"] == ["애플 건강"] and info["out_of_range"] == 0 and len(recs) == 1  # 9월 걸음은 읽을 때 건너뛴다
    r = recs[0]
    assert r.meta["steps"] == 7014  # 아이폰 6,200 과 워치 7,014 가 같은 걸음을 겹쳐 적었다
    assert (r.meta["sleep_min"], r.meta["sleep_start"], r.meta["sleep_end"]) == (435, "23:45", "07:00")  # 침대에 누운 시간은 뺀다
    assert r.meta["workouts"][0]["type"] == "달리기" and r.meta["workouts"][0]["km"] == 5.0
    every, _ = health.import_health(FIX / "apple")
    assert [x.day for x in every] == ["2026-07-13", "2026-09-02"]


def test_apple_health_iterparse_clears(tmp_path, monkeypatch):
    """큰 파일도 메모리에 쌓지 않는다: 읽고 난 원소는 비워진다."""
    import xml.etree.ElementTree as ET

    seen: list = []
    real = ET.iterparse

    def spy(*a, **k):
        for ev, el in real(*a, **k):
            if ev == "end" and el.tag == "Record":
                seen.append(el)
            yield ev, el

    monkeypatch.setattr(health.ET, "iterparse", spy)
    health.import_health(FIX / "apple")
    assert seen and all(len(el.attrib) == 0 for el in seen)


def test_spotify_both_formats_skip_short_and_podcast():
    recs, info = streaming.import_spotify(FIX / "spotify", M, Window.of(since="2026-01-01"))
    assert info["plays"] == 2 and info["short"] == 1 and info["not_music"] == 1 and info["out_of_range"] == 1
    hype = next(r for r in recs if r.meta["track"] == "Hype Boy")
    assert hype.text == "음악: NewJeans - Hype Boy" and hype.kind == "listen" and hype.meta["ms_played"] == 179000
    assert hype.ts == "2026-07-13T20:30:01+09:00"  # 끝난 시각 20:33 에서 179초를 뺀 재생 시작
    assert any(r.meta["artist"] == "아이유" and r.day == "2026-07-14" for r in recs)


def test_netflix_profile_filter_previews_and_series():
    recs, info = streaming.import_netflix(FIX / "netflix", M, profile="서연")
    assert info["watched"] == 2 and info["previews"] == 1 and info["short"] == 1 and info["other_profiles"] == 1
    st = next(r for r in recs if "기묘한" in r.text)
    assert st.meta["series"] == "기묘한 이야기" and st.meta["duration_min"] == 47 and st.ts == "2026-06-07T00:10:00+09:00"
    assert st.text.startswith("넷플릭스 시청: 기묘한 이야기") and (st.source, st.kind) == ("netflix", "watch")
    movie = next(r for r in recs if "헤어질" in r.text)
    assert movie.meta["series"] is None
    every, info = streaming.import_netflix(FIX / "netflix", M)
    assert len(every) == 3 and info["profiles"] == ["서연", "엄마"]


def test_importer_sources_and_since_until(tmp_path):
    store = Store(tmp_path / "t.db")
    out = ingest(store, "google", [FIX / "takeout_ko"])
    assert out["records"] == 2 and out["lines"][0].startswith("구글 테이크아웃: 유튜브 시청 1건")
    out = ingest(store, "spotify", [FIX / "spotify"], since="2026-07-14", until="2026-07-14")
    assert out["records"] == 1 and "기간 밖" in out["lines"][0]
    out = ingest(store, "card", [FIX / "card"])
    assert out["records"] == 4
    with pytest.raises(ValueError):
        ingest(store, "health", [FIX / "card"])
    for src in ("maps", "health", "netflix"):
        ingest(store, src, [FIX / {"maps": "maps", "health": "samsung", "netflix": "netflix"}[src]])
    kinds = {r.kind for r in store.records()}
    assert kinds == {"watch", "search", "listen", "payment", "visit", "health"} == set(TRACE_KINDS)
    again = ingest(store, "card", [FIX / "card"])
    assert again["total_records"] == store.count()  # 같은 파일을 다시 넣어도 늘지 않는다


def test_trace_records_skip_emotion_clusters_but_are_searchable(svc, tmp_path):
    before = {d["date"]: d["valence"] for d in svc.engine.daily.series()}
    ingest(svc.store, "card", [FIX / "card"])
    ingest(svc.store, "health", [FIX / "samsung"])
    ingest(svc.store, "google", [FIX / "takeout_ko"])
    build(svc.store, svc.text, svc.vision, emotion_backend="truth", log=lambda *_: None, cluster=False,
          extract_entities=False, pii=False)
    emo = svc.store.emotions()
    traces = [r for r in svc.store.records() if r.is_trace]
    assert len(traces) == 7 and not any(r.id in emo for r in traces)
    assert score_all(svc.store, svc.text, backend="truth")["trace_skipped"] == 7
    svc.invalidate()
    eng = svc.engine
    after = {d["date"]: d["valence"] for d in eng.daily.series()}
    assert all(after[k] == v for k, v in before.items() if k in after)  # 정서 곡선은 그대로
    shoes = next(r for r in traces if r.place == "아식스 합정점")
    assert eng.daily.count[eng.daily.index(shoes.dt.date())] == sum(
        1 for r in eng.records if r.day == shoes.day and not r.is_trace and r.is_self) + 0.3 * sum(
        1 for r in eng.records if r.day == shoes.day and not r.is_self)
    view = narrative.record_view(shoes, Masker.from_store(svc.store))
    assert view["source"] == "카드" and view["kind"] == "결제" and view["place"] == "아식스 합정점" and view["amount"] == 139000
    assert "lat" not in view and "lon" not in view
    res = eng.query("동물병원")
    assert any(s.record.source in ("card", "search") for s in res.strong)


def test_trace_kinds_excluded_from_hours_and_terms():
    rec = Record(id="rx", ts="2026-07-13T23:59:00+09:00", source="health", kind="health", modality="text",
                 text="건강: 걸음 1", author="나")
    assert rec.is_trace and patterns.hour_profile([rec])["center"] is None
    assert narrative.term_text(rec) is None  # 건강 요약에는 주요 낱말로 셀 이름이 없다
    pay = Record(id="ry", ts="2026-06-28T15:20:00+09:00", source="card", kind="payment", modality="text",
                 text="결제: 아식스 합정점 139,000원", author="나", place="아식스 합정점", meta={"merchant": "아식스 합정점"})
    assert narrative.term_text(pay) == "아식스 합정점"
    from virtueme_core.emotion import text_for_emotion

    assert text_for_emotion(rec) is None


def test_import_api_accepts_trace_upload(svc):
    from fastapi.testclient import TestClient

    from virtueme_core.api import create_app

    client = TestClient(create_app(svc))
    page = client.get("/import").text
    assert 'data-source="google"' in page and 'data-source="health"' in page
    buf = (FIX / "card" / "현대카드_이용내역.xls").read_bytes()
    r = client.post("/import/card", files=[("files", ("현대카드.xls", buf, "application/vnd.ms-excel"))],
                    data={"since": "2026-07-01", "until": "2026-07-31"})
    assert r.status_code == 200, r.text
    assert r.json()["records"] == 2 and "카드 이용내역" in r.json()["lines"][0]
    r = client.post("/import/card", files=[("files", ("x.xls", buf, "application/vnd.ms-excel"))], data={"since": "7월"})
    assert r.status_code == 422
    import io

    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        for p in (FIX / "samsung").glob("*.csv"):
            f.write(p, "samsunghealth_x/" + p.name)
    r = client.post("/import/health", files=[("files", ("health.zip", z.getvalue(), "application/zip"))])
    assert r.status_code == 200 and r.json()["records"] == 1, r.text


def test_evidence_pick_caps_and_dedupes_traces():
    from virtueme_core.query import Scored

    def rec(i, kind, text):
        src = {"message": "kakao", "watch": "youtube"}[kind]
        return Scored(Record(id=f"r{i:011d}", ts=f"2026-07-{1 + i % 28:02d}T21:00:00+09:00", source=src, kind=kind,
                             modality="text", text=text, author="나"), 0.9, 0.9, 0.0, "text")

    written = [rec(i, "message", f"오늘 러닝 {i}") for i in range(40)]
    videos = [rec(100 + i, "watch", f"유튜브 시청: 러닝 영상 {i % 5}") for i in range(90)]
    got = narrative._pick_evidence(written + videos, 48)
    tr = [s for s in got if s.record.is_trace]
    assert len(got) == 45 and len(tr) == 5  # 같은 영상은 한 번만 (5편) + 글로 쓴 기록 40건 전부
    many = [rec(200 + i, "watch", f"유튜브 시청: 러닝 영상 {i}") for i in range(90)]
    got = narrative._pick_evidence(written + many, 48)
    assert sum(1 for s in got if s.record.is_trace) == 16  # 자리의 1/3 까지
    assert len(narrative._pick_evidence(many, 48)) == 48  # 흔적만 있으면 흔적으로 채운다
