"""묶기·그래프. 모델 대신 해시 임베더, LLM 은 끈다."""
import pytest

from virtueme_core import clusters, entities
from virtueme_core.build import build
from virtueme_core.ingest import synthetic
from virtueme_core.service import Service
from virtueme_core.store import Store


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    store = Store(tmp_path / "t.db")
    _, recs = synthetic.synthetic_dataset()
    store.upsert_records(recs)
    s = Service("t", fake_models=True, store=store)
    build(s.store, s.text, s.vision, emotion_backend="truth", log=lambda *_: None, cluster=True, extract_entities=False, pii=False)
    return s


def test_topics_mood_social_are_stored(svc):
    assign, info = svc.store.clusters("topic")
    assert assign and len(info) >= 2  # 합성 태그 35종이면 묶음이 여럿 나온다
    assert all(v["label"] for v in info.values())
    mood, _ = svc.store.clusters("mood")
    assert set(mood.values()) <= {"up", "calm", "tense", "down", "flat"}
    over = clusters.overview(svc.store)
    assert over and over[0]["size"] >= over[-1]["size"]


def test_narrative_carries_cluster_mix(svc):
    from virtueme_core import narrative

    out = svc.query("등산")
    no = out["narrative_object"]
    narrative.validate(no)
    assert no["clusters"] and 0 < sum(c["share"] for c in no["clusters"]) <= 1.0001
    assert "mood_mix" in no and "들뜸" in no["mood_mix"]


def test_graph_from_stored_entities(svc):
    recs = svc.store.records()[:3]
    svc.store.put_entities(recs[0].id, [("person", "나"), ("place", "한강"), ("activity", "러닝")], [("나", "함", "러닝")])
    svc.store.put_entities(recs[1].id, [("person", "나"), ("activity", "러닝")], [])
    svc.store.put_entities(recs[2].id, [("animal", "콩이"), ("person", "나")], [("나", "돌봄", "콩이")])
    g = entities.graph(svc.store, svc.engine.emotions)
    names = {n["name"]: n for n in g["nodes"]}
    assert names["나"]["count"] == 3 and names["러닝"]["count"] == 2
    edge = next(e for e in g["edges"] if {e["source"], e["target"]} == {"person:나", "activity:러닝"})
    assert edge["weight"] == 2 and edge["predicates"] == {"함": 1}
    focus = entities.graph(svc.store, svc.engine.emotions, {recs[2].id})
    assert {n["name"] for n in focus["nodes"]} == {"콩이", "나"}
