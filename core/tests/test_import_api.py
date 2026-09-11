"""웹 화면에서 기록 넣기: 파일 올리기 → 가져오기 → 처리(build) → 관계 붙이기."""
import io
import time
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_sms import XML
from virtueme_core.api import create_app
from virtueme_core.importer import safe_extract

FIX = Path(__file__).parent / "fixtures"


def test_import_pages_upload_build_and_relation(svc):
    client = TestClient(create_app(svc))
    assert "내 기록 넣기" in client.get("/import").text
    before = client.get("/health").json()["records"]

    # 카카오톡: 본인 이름이 없으면 보낸 사람 목록과 함께 거절
    kakao = (FIX / "kakao_pc.txt").read_bytes()
    r = client.post("/import/kakao", files=[("files", ("대화.txt", kakao, "text/plain"))])
    assert r.status_code == 400 and "보낸 사람" in r.json()["detail"]
    # 문자 XML (zip 으로 묶어서)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("backup/sms-20260911.xml", XML)
    r = client.post("/import/sms", files=[("files", ("sms.zip", buf.getvalue(), "application/zip"))])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["records"] > 0 and out["total_records"] > before and "문자" in out["lines"][0]
    assert list(svc.upload_root.rglob("*.xml"))  # 올린 파일은 이 컴퓨터의 프로필 폴더에 남는다

    people = client.get("/people/local").json()["people"]
    friend = next(p for p in people if "박지수" in p["names"])
    r = client.post("/people/relation", json={"alias": friend["alias"], "relation": "러닝 크루 친구"})
    assert r.status_code == 200
    assert client.post("/people/relation", json={"alias": "지인ZZ", "relation": "x"}).status_code == 404

    st = client.post("/build", json={"pii": False, "cluster": False, "entities": False}).json()
    assert st["state"] in ("running", "done")
    for _ in range(100):
        st = client.get("/build/status").json()
        if st["state"] != "running":
            break
        time.sleep(0.1)
    assert st["state"] == "done", st
    assert client.get("/health").json()["built"]
    hits = client.post("/query", json={"keyword": "등산"}).json()
    assert hits["people"] is not None


def test_safe_extract_blocks_path_traversal(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.txt", "x")
    try:
        safe_extract(bad, tmp_path / "out")
    except ValueError as err:
        assert "폴더 밖" in str(err)
    else:
        raise AssertionError("경로 탈출을 막지 못했습니다")
