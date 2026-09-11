"""밖으로 나가는 글: 마지막 이름 검사, 전송 로그, 전부 지우기."""
import json

import httpx
from fastapi.testclient import TestClient

from virtueme_core import llm
from virtueme_core.api import create_app


def test_guard_replaces_leftover_real_names_and_logs_only_aliases(svc):
    people = svc.store.people()
    alias = people["김민지"]
    text, found = svc.outbound.guard("어제 김민지랑 윤서연이 010-2222-3333 으로 통화함")
    assert "김민지" not in text and "윤서연" not in text and "010-2222-3333" not in text
    assert alias in text and "(나)" in text
    assert set(found) == {f"{alias} 의 이름", "본인 이름", "전화번호"}
    svc.outbound.send("core", "너는 개인 기록의 정서를 채점한다.", "김민지 만남", model="m")
    raw = "".join(p.read_text(encoding="utf-8") for p in svc.outbound.root.glob("*.jsonl"))
    assert "김민지" not in raw  # 로그에도 실명은 남지 않는다
    s = svc.outbound.summary()
    assert s["requests"] == 1 and s["guarded_requests"] == 1
    assert s["by_purpose"][0]["purpose"] == "정서 채점"
    item = svc.outbound.item(s["recent"][0]["id"])
    assert item["user"] == f"{alias} 만남"


def test_llm_requests_pass_the_guard_before_leaving(svc, monkeypatch):
    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: A002
        sent.append(json)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    llm.set_outbound(svc.outbound)
    assert llm.chat_json("너는 개인 기록 검색 결과를 검수한다.", "기록: 박지훈이 야근") == {"ok": True}
    body = sent[0]["messages"][1]["content"]
    assert "박지훈" not in body and svc.store.people()["박지훈"] in body
    assert svc.outbound.summary()["by_purpose"][0]["purpose"] == "키워드 관련도 판정"


def test_story_and_talk_prompts_are_logged_as_browser_requests(svc):
    svc.story_prompt("등산", genre=0)
    svc.talk_context("요즘 등산 자주 가?")
    groups = {(g["channel"], g["purpose"]) for g in svc.outbound.summary()["by_purpose"]}
    assert {("browser", "이야기 집필"), ("browser", "대화 답")} <= groups


def test_delete_all_wipes_records_files_and_changes_data_id(svc):
    client = TestClient(create_app(svc))
    before = client.get("/health").json()
    assert before["records"] > 0 and before["data_id"]
    (svc.upload_root / "x").mkdir(parents=True)
    (svc.music_root / "y").mkdir(parents=True)
    svc.outbound.send("core", "너는 개인 기록의 정서를 채점한다.", "글", model="m")
    assert client.post("/profile/delete", json={"confirm": "다른이름"}).status_code == 400
    r = client.post("/profile/delete", json={"confirm": svc.profile}).json()
    assert r["records"] == before["records"] and r["people"] > 0
    assert set(r["folders"]) == {"올린 파일", "음악·영상", "전송 로그"}
    assert not svc.upload_root.exists() and not svc.music_root.exists() and not svc.outbound.root.exists()
    after = client.get("/health").json()
    assert after["records"] == 0 and after["data_id"] is None
    assert svc.store.people() == {} and svc.store.cache_get("data_id") is None
    assert client.get("/outbound").json()["requests"] == 0


def test_outbound_api_lists_recent_items(svc):
    client = TestClient(create_app(svc))
    svc.outbound.send("browser", "지시문", "기록 1. 등산", purpose="이야기 집필")
    d = client.get("/outbound").json()
    assert d["requests"] == 1 and d["recent"][0]["channel"] == "browser"
    item = client.get("/outbound/item", params={"id": d["recent"][0]["id"]}).json()
    assert item["system"] == "지시문" and json.dumps(item, ensure_ascii=False).count("등산") == 1
    assert client.get("/outbound/item", params={"id": "../../etc:0"}).status_code == 404
