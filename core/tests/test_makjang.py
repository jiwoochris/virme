"""막장극장: 대본 형식 검사, 조립 규격(18.5초·1280×720·24fps·오디오), 생성물 재사용, API. 생성 모델은 가짜로 바꿔 돈다."""
import base64
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from virtueme_core.api import create_app
from virtueme_core.book import net as book_net
from virtueme_core.makjang import assemble, gen, make, story as story_mod

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 없음")

STORY = {
    "title": "콩이의 비밀", "title_en": "The Secret of Kong-i", "mood": "Dramatic soap-opera orchestral",
    "protagonist": "A Korean woman in her early 30s with shoulder-length dark hair, grey running jacket",
    "antagonist": "A Korean middle-aged woman with short permed hair, beige trench coat",
    "scenes": [{"kr": f"장면 {i} 자막입니다", "en": f"Scene {i} caption", "visual": f"Night riverside, shot {i}"}
               for i in range(1, 6)],
}
NOVEL = "한강을 달리던 밤, 강아지 한 마리가 따라왔다. 이름표에는 낯익은 글씨가 적혀 있었다. " * 3


def _fake_clip(out: Path, size: str = "1280x720") -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration=4",
                    "-pix_fmt", "yuv420p", str(out)], check=True)


def _fake_bgm(out: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
                    str(out)], check=True)


def _probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate",
                        "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    return json.loads(r.stdout)


def test_check_rejects_wrong_scene_count():
    with pytest.raises(ValueError):
        story_mod.check({**STORY, "scenes": STORY["scenes"][:4]})
    got = story_mod.check({**STORY, "title": "가" * 40})
    assert len(got["title"]) <= 24 and len(got["scenes"]) == 5


def test_clip_prompt_repeats_appearance_verbatim():
    ps = [story_mod.clip_prompt(STORY, i) for i in range(5)]
    assert all(STORY["protagonist"] in p and STORY["antagonist"] in p for p in ps)
    assert all("Photorealistic live-action" in p for p in ps)


def test_stream_audio_joins_sse_chunks(monkeypatch):
    raw = b"ID3" + bytes(range(200))
    b64 = base64.b64encode(raw).decode()
    lines = [": OPENROUTER PROCESSING", 'data: {"choices":[{"delta":{"content":"<instrumental>"}}]}',
             'data: {"choices":[{"delta":{"audio":{"data":"%s"}}}]}' % b64[:100],
             'data: {"choices":[{"delta":{"audio":{"data":"%s"}}}]}' % b64[100:],
             'data: {"choices":[{"delta":{}}],"usage":{"cost":0.04}}', "data: [DONE]"]

    class Res:
        status_code = 200

        def iter_lines(self):
            return iter(lines)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(gen.httpx, "stream", lambda *a, **k: Res())
    got, fmt, usage = gen._stream_audio({"audio": {"format": "mp3"}}, "k", 10)
    assert got == raw and fmt == "mp3" and usage["cost"] == 0.04


def test_assemble_matches_team_spec(tmp_path):
    clips = []
    for i in range(5):
        p = tmp_path / f"scene{i + 1}.mp4"
        _fake_clip(p, "1112x834" if i == 2 else "1280x720")  # 대체 모델처럼 크기가 달라도 맞춘다
        clips.append(p)
    _fake_bgm(tmp_path / "bgm.mp3")
    out = tmp_path / "out.mp4"
    got = assemble.assemble(story_mod.check(STORY), clips, tmp_path / "bgm.mp3", out)
    info = _probe(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (v["width"], v["height"], v["r_frame_rate"]) == (1280, 720, "24/1")
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"]) - assemble.total_seconds()) < 0.25
    assert got["seconds"] == 18.5


@pytest.fixture()
def fake_models(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(book_net, "DATA_DIR", tmp_path / "data")
    calls = {"clip": 0, "music": 0, "story": 0, "prompts": []}

    def clip(prompt, out, outbound, purpose, seed=None, log=lambda m: None):
        calls["clip"] += 1
        outbound.send("core", "", prompt, model="fake", purpose=purpose)
        _fake_clip(out)
        return {"model": "runway/gen-4.5", "cost_usd": 0.48, "cost_estimated": False, "seconds": 4}

    def music(prompt, out_stem, outbound, log=lambda m: None):
        calls["music"] += 1
        p = out_stem.with_suffix(".mp3")
        _fake_bgm(p)
        return {"path": str(p), "model": "fake", "cost_usd": 0.04}

    def write(novel, seed, title=None, nationality=None, rng=None):
        calls["story"] += 1
        return story_mod.check(STORY) | {"nationality": "Korean", "seed": seed, "model": "fake"}

    monkeypatch.setattr(gen, "generate_clip_any", clip)
    monkeypatch.setattr(gen, "generate_music", music)
    monkeypatch.setattr(story_mod, "write_story", write)
    return calls


def test_make_reuses_generated_clips(fake_models, tmp_path):
    rep = make.make_makjang("test", "러닝", NOVEL, title="콩이")
    d = make.makjang_dir("test") / rep["video_id"]
    assert (d / "makjang.mp4").exists() and rep["cost_usd"] == pytest.approx(2.44)
    assert fake_models == {**fake_models, "clip": 5, "music": 1, "story": 1}
    # 클립 하나가 사라진 채로 다시 돌리면 그 하나만 만든다
    (d / "scene3.mp4").unlink()
    (d / "makjang.mp4").unlink()
    rep2 = make.make_makjang("test", "러닝", NOVEL, title="콩이")
    assert fake_models["clip"] == 6 and fake_models["music"] == 1 and fake_models["story"] == 1
    assert rep2["cost_usd"] == pytest.approx(2.92)
    # 다른 에피소드는 번호가 다르다
    assert make.video_id("test", "러닝", NOVEL, "콩이", 1) != rep["video_id"]
    # 영상 모델에 보낸 글도 전송 로그에 남는다
    logs = list((tmp_path / "data" / "outbound" / "test").glob("*.jsonl"))
    assert logs and "막장극장 장면" in logs[0].read_text("utf-8")


def test_makjang_api_roundtrip(svc, fake_models):
    client = TestClient(create_app(svc))
    assert client.post("/makjang", json={"keyword": "러닝", "story": "짧음"}).status_code == 422
    assert client.get("/makjang/zzzz").status_code == 422
    assert client.get("/makjang/0123456789").status_code == 404
    assert client.get("/makjang/0123456789/report.json").status_code == 422
    body = {"keyword": "러닝", "story": NOVEL, "title": "콩이"}
    st = client.post("/makjang", json=body).json()
    assert st["state"] in ("running", "done") and len(st["id"]) == 10
    deadline = time.time() + 120
    while st["state"] == "running" and time.time() < deadline:
        time.sleep(0.5)
        st = client.get(f"/makjang/{st['id']}").json()
    assert st["state"] == "done", st
    assert st["story"]["title"] == "콩이의 비밀" and len(st["story"]["scenes"]) == 5
    assert st["report"]["seconds"] == 18.5
    mp4 = client.get(st["files"]["makjang.mp4"])
    assert mp4.status_code == 200 and mp4.content[4:8] == b"ftyp"
    # 같은 입력이면 다시 만들지 않는다
    assert client.post("/makjang", json=body).json()["state"] == "done"
    assert fake_models["clip"] == 5
    other = client.post("/makjang", json={**body, "take": 1}).json()
    assert other["id"] != st["id"]
