"""책 만들기 API: 백그라운드 작업, 상태 조회, 파일 내려받기, 번호·이름 검사. 키 없이(절차적 그림) 돈다."""
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from virtueme_core.api import create_app
from virtueme_core.book import make as book_make

SAMPLES = Path(__file__).resolve().parents[1] / "virtueme_core" / "book" / "samples"


@pytest.fixture()
def client(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(book_make, "DATA_DIR", tmp_path / "data")  # 책 폴더를 테스트 폴더 안에
    return TestClient(create_app(svc))


def test_book_api_validates_input(client):
    assert client.post("/book", json={"keyword": "등산", "story": "짧음"}).status_code == 422
    assert client.post("/book", json={"keyword": "등산", "story": "가" * 40, "genre": 9}).status_code == 422
    assert client.get("/book/zzzz").status_code == 422
    assert client.get("/book/0123456789").status_code == 404
    assert client.get("/book/0123456789ab").status_code == 404
    assert client.get("/book/0123456789/cover.png").status_code == 404
    assert client.get("/book/0123456789/brief.json").status_code == 422
    assert client.get("/book/0123456789/..%2Fbrief.json").status_code in (404, 422)
    assert client.get("/book/0123456789ab/cover.png").status_code == 422


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_book_api_offline_roundtrip(client):
    story = (SAMPLES / "fantasy.txt").read_text("utf-8")
    body = {"keyword": "등산", "story": story, "genre": 3, "author": "윤서연"}
    st = client.post("/book", json=body).json()
    assert st["state"] in ("running", "done") and len(st["id"]) == 12
    assert client.post("/book", json=body).json()["id"] == st["id"]  # 같은 입력이면 같은 작업
    deadline = time.time() + 120
    while st["state"] == "running" and time.time() < deadline:
        time.sleep(0.5)
        st = client.get(f"/book/{st['id']}").json()
    if st["state"] == "error" and ("playwright" in st["error"].lower() or "node" in st["error"].lower()):
        pytest.skip(st["error"])
    assert st["state"] == "done", st
    assert st["book"]["genre"] == "판타지" and st["book"]["author"] == "윤서연"
    assert st["book"]["art"]["cover"] == "procedural" and st["book"]["art"]["cost_usd"] == 0
    assert set(st["files"]) == {"cover.png", "opener.png", "spread.png", "book.pdf"}
    assert all(not v.startswith("/Users") and "/tmp" not in v for v in st["files"].values())  # 경로 대신 주소
    png = client.get(st["files"]["cover.png"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    pdf = client.get(st["files"]["book.pdf"])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    # 서버를 다시 켠 뒤에도 책 번호로 찾는다
    again = client.get(f"/book/{st['book_id']}").json()
    assert again["state"] == "done" and again["files"] == st["files"]
