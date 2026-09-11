import io
import json
import wave
from datetime import date, timedelta

import numpy as np
import pytest

from virtueme_core import music


def narrative(valences, cps=(), grade="강한 근거", nid="a1b2c3d4", alone=0.7):
    start = date(2026, 6, 1)
    series = [{"date": (start + timedelta(days=i)).isoformat(), "valence": v, "arousal": 0.4,
               "evidence": 1 if i % 2 == 0 else 0, "sparse": False} for i, v in enumerate(valences)]
    return {"narrative_id": nid, "keyword": "러닝", "evidence_grade": grade,
            "emotion": {"series": series}, "change_points": [{"date": c} for c in cps],
            "signals": {"stability": 0.5, "novelty": 0.3, "activity": 0.5, "possibility": 0.5},
            "social_mix": {"혼자": {"evidence": alone, "all": 0.5}, "함께": {"evidence": 1 - alone, "all": 0.4}}}


def tone(sec, sr=48000, freq=440.0, tail=0.0):
    t = np.arange(int(sec * sr)) / sr
    x = 0.5 * np.sin(2 * np.pi * freq * t).astype(np.float32)
    x = np.concatenate([x, np.zeros(int(tail * sr), dtype=np.float32)])
    return np.stack([x, x])


def wav_bytes(x, sr=48000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype("<i2").T.tobytes())
    return buf.getvalue()


def test_plan_sections_keys_and_determinism():
    p = music.plan(narrative([0.2] * 30))
    assert [s["name"] for s in p["sections"]] == ["도입", "전개", "마무리"]
    assert p == music.plan(narrative([0.2] * 30))  # 같은 서사면 같은 계획, 같은 번호
    assert 60 <= p["bpm"] <= 140 and p["time_signature"] in (3, 4)
    assert all(s["keyscale"].endswith(" major") for s in p["sections"])
    assert "solo piano" in p["sections"][0]["caption"] and "no vocals" in p["sections"][0]["caption"]
    assert 55 <= p["total_sec"] <= 65

    # 변화점에서 자르고, 정서가가 음수인 구간은 나란한조(단조)
    q = music.plan(narrative([-0.3] * 15 + [0.3] * 15, cps=["2026-06-16"]))
    assert [s["name"] for s in q["sections"]] == ["앞", "뒤"]
    minor, major = q["sections"][0]["keyscale"], q["sections"][1]["keyscale"]
    assert minor.endswith(" minor") and major.endswith(" major")
    pair = next(pr for pr in music.KEY_PAIRS if f"{pr[0]} major" == major)
    assert minor == f"{pair[1]} minor"
    assert q["music_id"] != p["music_id"]


def test_plan_refuses_insufficient_evidence():
    with pytest.raises(ValueError):
        music.plan(narrative([0.1] * 20, grade="근거 부족"))


def test_stitch_trims_silence_and_reports_bounds():
    a = (tone(4, tail=2.0), 48000)
    b = (tone(4, freq=330, tail=2.0), 48000)
    out, bounds = music.stitch([a, b], crossfade=1.0)
    # 끝 무음 2초씩이 잘리고, 1초 겹치므로 약 4 + 4 - 1 = 7초
    assert 6.8 < out.shape[1] / 48000 < 7.6
    assert bounds[0][0] == 0 and abs(bounds[1][0] - (bounds[0][1] - 1.0)) < 0.05
    assert abs(float(np.abs(out).max()) - 0.891) < 1e-3


def test_features_find_beats():
    sr = 48000
    x = np.zeros(sr * 4, dtype=np.float32)
    for k in range(8):  # 0.5초마다 짧은 소리 = 120 BPM
        i = int(k * 0.5 * sr)
        x[i : i + 2000] = np.sin(np.arange(2000) * 0.3) * np.linspace(1, 0, 2000)
    f = music.features(np.stack([x, x]), sr)
    assert f["frames"] == 96 and len(f["rms"]) == 96
    gaps = np.diff([p / f["fps"] for p in f["peaks"]])
    assert len(f["peaks"]) >= 6 and np.allclose(gaps, 0.5, atol=0.1)


def test_make_uses_client_and_cache(tmp_path):
    calls = []

    class Fake:
        def generate(self, section, bpm, ts):
            calls.append(section["index"])
            return wav_bytes(tone(section["duration"] * 0.2, freq=220 + 50 * section["index"], tail=0.5))

    p = music.plan(narrative([0.2] * 30))
    st = music.make(tmp_path, p, Fake())
    assert st["state"] == "ready" and calls == [0, 1, 2]
    d = tmp_path / p["music_id"]
    feats = json.loads((d / "features.json").read_text())
    assert len(feats["sections"]) == 3 and feats["duration"] > 0
    assert (d / "music.wav").exists()
    assert music.make(tmp_path, p, Fake())["state"] == "ready" and calls == [0, 1, 2]  # 두 번째는 캐시


def test_music_api_plan_and_unavailable_server(svc, monkeypatch):
    from fastapi.testclient import TestClient

    from virtueme_core.api import create_app

    client = TestClient(create_app(svc))
    no = client.post("/query", json={"keyword": "등산"}).json()["narrative_object"]
    if no["evidence_grade"] == "근거 부족":
        assert client.post("/music/plan", json={"keyword": "등산"}).status_code == 400
        return
    p = client.post("/music/plan", json={"keyword": "등산"}).json()
    assert p["schema"] == "virtueme.music/1" and p["narrative_id"] == no["narrative_id"]
    monkeypatch.setattr(svc.acestep, "health", lambda: False)
    st = client.post("/music", json={"keyword": "등산"}).json()
    assert st["state"] == "unavailable" and "ACE-Step" in st["error"]
    assert client.get(f"/music/{p['music_id']}").json()["state"] == "missing"
    assert client.get(f"/music/{p['music_id']}/music.m4a").status_code == 404
    assert client.get("/music/zzz/music.m4a").status_code == 422
