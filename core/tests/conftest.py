"""테스트 공용 준비물: 합성 데이터와 카카오톡 샘플로 만든 프로필. 모델 대신 해시 임베더를 쓰고 LLM 은 끈다."""
from pathlib import Path

import pytest

from virtueme_core.build import build
from virtueme_core.ingest import kakao, synthetic
from virtueme_core.privacy import Pseudonymizer
from virtueme_core.service import Service
from virtueme_core.store import Store

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    store = Store(tmp_path / "t.db")
    _, recs = synthetic.synthetic_dataset()
    store.upsert_records(recs)
    parsed = kakao.parse_kakao_file(FIX / "kakao_pc.txt")
    krecs, _ = kakao.to_records(parsed, "윤서연", Pseudonymizer(store))
    store.upsert_records(krecs)
    s = Service("test", fake_models=True, store=store)
    s.music_root = tmp_path / "music"
    s.upload_root = tmp_path / "uploads"
    s.outbound.root = tmp_path / "outbound"
    build(s.store, s.text, s.vision, emotion_backend="truth", log=lambda *_: None, cluster=False, extract_entities=False, pii=False)
    return s
