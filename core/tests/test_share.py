"""공유 카드: 공개용 글 다듬기(실명·가명·좌표·주소·연락처), 카드·페이지 API, 반응 제한, 지우기·내보내기. 네트워크 없음.
카드 렌더는 node 와 virme/ 의 Playwright 가 있어야 돌고, 없으면 렌더가 필요한 테스트만 건너뛴다."""
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from virtueme_core import share as share_mod
from virtueme_core.api import create_app
from virtueme_core.book import make as book_make
from virtueme_core.share import ShareError, public_text, unalias

SAMPLES = Path(__file__).resolve().parents[1] / "virtueme_core" / "book" / "samples"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")


@pytest.fixture()
def client(svc):
    svc.store.set_relation("지인A", "대학 친구")
    return TestClient(create_app(svc))


def test_unalias_uses_relations_and_fixes_particles():
    rel = {"지인A": "대학 친구", "지인B": None}
    assert unalias("지인A가 웃었다", rel)[0] == "대학 친구가 웃었다"
    assert unalias("지인A랑 지인B를 만났다", rel)[0] == "대학 친구랑 그 사람을 만났다"
    assert unalias("(나)는 달렸다. (누군가)님도 왔다", rel)[0] == "나는 달렸다. 그 사람도 왔다"


def test_public_text_removes_names_and_refuses_coordinates(svc):
    svc.store.set_relation("지인A", "대학 친구")
    names = [n for n, a in svc.store.people().items() if a not in ("나", n)]
    friend = next(n for n, a in svc.store.people().items() if a == "지인A")
    text, changes = public_text(f"윤서연은 {friend}랑 한강을 달렸다", svc.store, svc.outbound)
    assert text == "나는 대학 친구랑 한강을 달렸다"
    assert "본인 이름 → '나'" in changes and "다른 사람 이름 → 관계" in changes
    assert not any(n in text for n in names) and "지인" not in text
    for bad in ("집 37.55631, 126.91234 에서", "id:ChIJf9ce69a326fQx 근처", "망원로 12-3 앞에서", "010-1234-5678 로 전화",
                "a@b.com 으로 보냈다", "https://x.com/a 를 봤다"):
        with pytest.raises(ShareError):
            public_text(bad, svc.store, svc.outbound)
    for fine in ("1.7시간 앞당겨졌고 4,102보를 걸었다", "1시 47분에서 0시 08분으로 1.7시간 앞당겨졌습니다.",
                 "한강으로 3번 갔다", "카페로 20분 걸었다"):
        assert public_text(fine, svc.store)[0] == fine
    # 발견 문장은 모두 그대로 통과한다 (숫자·시각이 주소로 잘못 걸리지 않음)
    for d in svc.columns()["discoveries"]:
        public_text(d["text"], svc.store)


def test_home_and_work_place_names_become_generic(svc):
    from virtueme_core.records import Record

    r = next(iter(svc.store.records()))
    home = Record(**{**r.__dict__, "id": "r0000000000a", "place": "망원동 우리집", "meta": {"place_type": "HOME"}})
    svc.store.upsert_records([home])
    text, changes = public_text("망원동 우리집에서 잠들었다", svc.store, svc.outbound)
    assert text == "집에서 잠들었다" and any("집" in c for c in changes)


def test_share_api_validates_input(client):
    assert client.post("/share", json={"kind": "tweet"}).status_code == 422
    assert client.post("/share", json={"kind": "discovery"}).status_code == 422
    assert client.post("/share", json={"kind": "discovery", "summary_id": "s0000000000z"}).status_code == 422
    assert client.post("/share", json={"kind": "discovery", "summary_id": "s00000000000"}).status_code == 400
    assert client.post("/share", json={"kind": "book", "book_id": "0123456789"}).status_code == 400
    assert client.post("/share", json={"kind": "passage", "lines": ["a", "b", "c", "d"]}).status_code == 422
    assert client.post("/share", json={"kind": "passage", "lines": ["가" * 161]}).status_code == 422
    r = client.post("/share", json={"kind": "passage", "lines": ["010-1234-5678 로 전화했다"]})
    assert r.status_code == 400 and "전화번호" in r.json()["detail"]
    assert client.get("/s/zzzz").status_code == 422
    assert client.get("/s/0123456789ab").status_code == 404
    assert client.get("/s/0123456789ab/brief.json").status_code == 422
    assert client.post("/s/0123456789ab/react", content='{"reaction":"like"}').status_code == 404
    assert client.post("/share/0123456789ab/delete").status_code == 404
    assert client.get("/share").json()["shares"] == []


def _skip_if_no_browser(r):
    if r.status_code == 500 and any(w in r.json().get("detail", "").lower() for w in ("playwright", "node", "chromium")):
        pytest.skip(r.json()["detail"][:200])


@needs_node
def test_share_roundtrip_page_reactions_delete(client, svc, tmp_path):
    disc = svc.columns()["discoveries"][0]
    r = client.post("/share", json={"kind": "discovery", "summary_id": disc["id"]})
    _skip_if_no_browser(r)
    assert r.status_code == 200, r.text
    s = r.json()
    sid = s["id"]
    assert len(sid) == 12 and s["url"] == f"/s/{sid}" and set(s["files"]) == {"feed.png", "story.png"}
    assert not any(f.startswith("/Users") or "/tmp" in f for f in s["files"].values())
    assert all(not f["overflow"] for f in s["render"]["fit"])
    # 앱 프록시(/api/core/s/<id>)로 열어도 그림·반응 주소가 맞도록 페이지 안 주소는 상대 경로다
    page = client.get(f"/s/{sid}").text
    assert f"src='{sid}/feed.png'" in page and f"data-api='{sid}/react'" in page and '"/s/' not in page.split("og:")[0]
    png = client.get(s["files"]["feed.png"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    from PIL import Image
    import io

    assert Image.open(io.BytesIO(png.content)).size == (1080, 1350)
    assert Image.open(io.BytesIO(client.get(s["files"]["story.png"]).content)).size == (1080, 1920)

    page = client.get(f"/s/{sid}")
    assert page.status_code == 200
    html = page.text
    assert 'property="og:image" content="http://testserver/s/' in html and "feed.png" in html
    assert 'name="twitter:card" content="summary_large_image"' in html and "noindex" in html
    assert "기록에서 지은 이야기" in html and "Content-Security-Policy" in page.headers

    # 반응: 정해진 것만, 한마디 100자, HTML 은 글자로, 같은 곳에서 연달아 못 남김
    bad = client.post(f"/s/{sid}/react", content='{"reaction":"hate"}')
    assert bad.status_code == 400
    long = client.post(f"/s/{sid}/react", content='{"reaction":"like","comment":"' + "가" * 101 + '"}')
    assert long.status_code == 400
    ok = client.post(f"/s/{sid}/react", content='{"reaction":"same","comment":"<b>나도</b> 010-9999-8888"}',
                     headers={"Content-Type": "text/plain", "Origin": "https://example.github.io"})
    assert ok.status_code == 200 and ok.json()["counts"]["same"] == 1
    assert ok.headers.get("access-control-allow-origin") == "*"
    again = client.post(f"/s/{sid}/react", content='{"reaction":"like"}')
    assert again.status_code == 429
    pre = client.options(f"/s/{sid}/react", headers={"Origin": "https://example.github.io",
                                                      "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 200 and pre.headers.get("access-control-allow-origin") == "*"

    mine = client.get(f"/share/{sid}").json()
    c = mine["reactions"]["recent"][0]
    assert c["label"].endswith("나도 그래요") and "[전화번호]" in c["comment"] and "<b>" in c["comment"]
    assert "나도</b>" not in client.get(f"/s/{sid}").text  # 한마디는 공개 페이지에 싣지 않는다
    assert client.get("/share").json()["shares"][0]["reactions"]["total"] == 1
    assert client.get(f"/s/{sid}/counts").json()["counts"]["same"] == 1

    # 전송 로그에 "공유 카드" 로 남는다
    ob = client.get("/outbound").json()
    assert any(g["channel"] == "share" for g in ob["by_purpose"])

    # 정적 사본
    out = svc.shares.export(sid, tmp_path / "export", base_url="https://me.example/s1", react_url="https://tunnel.example")
    names = sorted(p.name for p in out)
    assert names == ["feed.png", "index.html", "story.png"]
    idx = (tmp_path / "export" / "index.html").read_text("utf-8")
    assert 'content="https://me.example/s1/feed.png"' in idx and "https://tunnel.example/s/" in idx
    assert "src='feed.png'" in idx
    plain = svc.shares.export(sid, tmp_path / "export2")
    assert "이 사본에서는 반응을 받지 않습니다" in (tmp_path / "export2" / "index.html").read_text("utf-8") and plain

    assert client.post(f"/share/{sid}/delete").json()["shares"] == []
    assert client.get(f"/s/{sid}").status_code == 404


@needs_node
def test_passage_card_masks_names_in_rendered_text(client, svc):
    friend = next(n for n, a in svc.store.people().items() if a == "지인A")
    r = client.post("/share", json={"kind": "passage", "title": "윤서연의 여름",
                                    "lines": [f"{friend}가 먼저 손을 흔들었다.", "나는 웃었다."]})
    _skip_if_no_browser(r)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["title"] == "나의 여름" and s["lines"][0] == "대학 친구가 먼저 손을 흔들었다."
    assert "윤서연" not in r.text and friend not in r.text and "지인A" not in r.text
    page = client.get(f"/s/{s['id']}").text
    assert "윤서연" not in page and friend not in page and "대학 친구가 먼저" in page


@needs_node
def test_book_cover_card_hides_author_name(client, svc, tmp_path, monkeypatch):
    monkeypatch.setattr(book_make, "DATA_DIR", tmp_path / "data")
    story = (SAMPLES / "fantasy.txt").read_text("utf-8")
    st = client.post("/book", json={"keyword": "등산", "story": story, "genre": 3, "author": "윤서연", "offline": True}).json()
    deadline = time.time() + 120
    while st["state"] == "running" and time.time() < deadline:
        time.sleep(0.5)
        st = client.get(f"/book/{st['id']}").json()
    if st["state"] == "error":
        pytest.skip(st["error"][:200])
    r = client.post("/share", json={"kind": "book", "book_id": st["book_id"]})
    _skip_if_no_browser(r)
    assert r.status_code == 200, r.text
    s = r.json()
    assert set(s["files"]) == {"feed.png", "story.png", "cover.png"}
    assert "윤서연" not in r.text
    assert s["style"]["from_book"] == st["book_id"]


def test_profile_wipe_removes_shares(svc):
    d = svc.shares.root / "0123456789ab"
    d.mkdir(parents=True)
    (d / "share.json").write_text("{}", "utf-8")
    out = svc.delete_all()
    assert "공유 카드" in out["folders"] and not svc.shares.root.exists()


def test_reaction_rate_limit_and_cap(svc, monkeypatch):
    d = svc.shares.root / "0123456789ab"
    d.mkdir(parents=True)
    (d / "share.json").write_text('{"kind":"discovery","id":"0123456789ab","created":"2026-10-03T10:00:00"}', "utf-8")
    t = [1000.0]
    monkeypatch.setattr(share_mod.time, "time", lambda: t[0])
    svc.shares.react("0123456789ab", "like", "", "1.2.3.4")
    with pytest.raises(PermissionError):
        svc.shares.react("0123456789ab", "like", "", "1.2.3.4")
    svc.shares.react("0123456789ab", "wow", "", "5.6.7.8")  # 다른 곳은 따로 센다
    for _ in range(share_mod.RATE_PER_HOUR - 1):
        t[0] += share_mod.RATE_GAP_SEC + 1
        svc.shares.react("0123456789ab", "cheer", "", "1.2.3.4")
    t[0] += share_mod.RATE_GAP_SEC + 1
    with pytest.raises(PermissionError):
        svc.shares.react("0123456789ab", "cheer", "", "1.2.3.4")
    t[0] += 3601
    svc.shares.react("0123456789ab", "cheer", "", "1.2.3.4")
    assert svc.shares.counts("0123456789ab")["cheer"] == share_mod.RATE_PER_HOUR
