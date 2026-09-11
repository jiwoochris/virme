"""검색·서사 오브젝트·API. 모델 대신 해시 임베더를 쓰고 LLM 은 끈다."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from virtueme_core import narrative
from virtueme_core.api import create_app

FIX = Path(__file__).parent / "fixtures"


def test_strong_keyword_builds_valid_object(svc):
    out = svc.query("등산")
    no = out["narrative_object"]
    narrative.validate(no)
    assert no["evidence_grade"] == "강한 근거"
    assert no["evidence"]["strong"]
    assert set(no["signals"]) == {"stability", "novelty", "conflict", "uncertainty", "possibility", "activity", "confidence"}
    assert all(0 <= v <= 1 for v in no["signals"].values())
    # 근거 기록은 서사 오브젝트의 강한 근거 안에서 고른다
    assert {r["id"] for r in out["evidence_records"]} <= set(no["evidence"]["strong"])
    assert len(out["evidence_records"]) <= 48


def test_insufficient_keyword_is_honest(svc):
    out = svc.query("퀀텀컴퓨팅")  # 해시 임베더에서 어떤 기록과도 글자 2-gram 이 겹치지 않는 낱말
    no = out["narrative_object"]
    narrative.validate(no)
    assert no["evidence_grade"] == "근거 부족"
    assert no["message"] == "기록이 충분하지 않습니다"
    assert "stats" not in no


def test_output_never_leaks_names_gps_or_paths(svc):
    blob = json.dumps(svc.query("우산"), ensure_ascii=False)
    for leaked in ("김민지", "윤서연", "010-1234", "lat", "lon", "media"):
        assert leaked not in blob, leaked


def test_api_endpoints(svc):
    client = TestClient(create_app(svc))
    h = client.get("/health").json()
    assert h["status"] == "ready" and h["built"] is True
    q = client.post("/query", json={"keyword": "등산"}).json()
    rid = q["evidence_records"][0]["id"]
    r = client.get(f"/records/{rid}").json()
    assert r["id"] == rid and "lat" not in r
    assert client.get("/records/r00000000000").status_code == 404
    assert client.post("/query", json={"keyword": ""}).status_code == 422
    series = client.get("/emotion").json()["series"]
    assert series and {"date", "valence", "sparse"} <= set(series[0])
    prof = client.get("/profile").json()
    assert prof["periodicity"]["weekday_peak"]


def test_others_messages_are_not_evidence_by_default(svc):
    out = svc.query("우산")
    ids = set(out["narrative_object"]["evidence"]["strong"])
    authors = {svc.store.record(i).author for i in ids}
    assert authors <= {"나"}


def test_llm_judge_reassigns_evidence(svc, monkeypatch):
    """판정 결과가 등급을 바꾼다: 2 는 강한 근거, 1 은 약한 근거, 0 과 미판정은 근거에서 빠진다."""
    from virtueme_core import query as q

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(q, "expand_keyword", lambda *a, **k: {"forms": [], "situations": []})
    eng = svc.engine
    base = eng.query("등산", use_judge=False)
    target = base.strong[0].record.id

    def fake_chat(system, user, model=None, **kw):
        lines = [ln for ln in user.splitlines() if ln[:1].isdigit()]
        return {"labels": [{"i": i, "r": 2 if i == 0 else 0} for i in range(len(lines))]}

    monkeypatch.setattr(q, "chat_json", fake_chat)
    res = eng.query("등산", use_judge=True)
    assert res.basis["method"] == "임베딩 후보 + LLM 판정"
    assert res.basis["judged_records"] > 0
    # 배치마다 첫 기록만 2 로 판정했으므로 강한 근거는 배치 수만큼만 남는다
    assert 1 <= len(res.strong) <= -(-res.basis["judged_records"] // q.JUDGE_BATCH)
    assert all(s.judge == 2 for s in res.strong)
    assert not res.weak
    # 판정은 캐시에 남아 두 번째 질의는 LLM 을 부르지 않는다
    monkeypatch.setattr(q, "chat_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("캐시 미사용")))
    again = eng.query("등산", use_judge=True)
    assert [s.record.id for s in again.strong] == [s.record.id for s in res.strong]
    assert target  # 기준 질의가 근거를 찾았는지 확인


def test_label_tool_roundtrip(svc):
    client = TestClient(create_app(svc))
    assert "정답 라벨" in client.get("/label").text
    cands = client.get("/label/candidates", params={"keyword": "등산"}).json()
    assert len(cands["items"]) == 80 and all("lat" not in x for x in cands["items"])
    rid = cands["items"][0]["id"]
    assert client.post("/label", json={"keyword": "등산", "record_id": rid, "label": 2}).json()["labeled"] == 1
    assert client.post("/label", json={"keyword": "등산", "record_id": rid, "label": 3}).status_code == 422
    again = client.get("/label/candidates", params={"keyword": "등산"}).json()
    assert next(x for x in again["items"] if x["id"] == rid)["label"] == 2
    assert client.get("/label/export").json()["labels"] == {"등산": {rid: 2}}
