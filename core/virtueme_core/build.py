"""가져온 기록에 파생 값을 붙인다: 형태소 원형 → 사진 벡터·자동 태그 → 텍스트 벡터 → 정서.

이미 계산한 값은 건너뛰므로, 새 기록을 가져온 뒤 다시 돌리면 새 기록만 계산한다.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import emotion
from .embed import zero_shot_tags
from .query import embed_text
from .records import Record
from .store import Store
from .textproc import lemmas_many


def build(
    store: Store,
    text_embedder,
    vision_embedder=None,
    emotion_backend: str = "auto",
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    records = store.records()

    # 1) 사진: SigLIP 벡터와 자동 태그
    if vision_embedder is not None:
        have = store.vector_ids("vision")
        todo = [r for r in records if r.media and r.modality in ("image", "text+image") and r.id not in have]
        if todo:
            log(f"사진 {len(todo)}장 임베딩 (SigLIP 2)")
            vecs = vision_embedder.encode_images([r.media[0] for r in todo])
            store.put_vectors("vision", vision_embedder.name, [r.id for r in todo], vecs)
            for r, tags in zip(todo, zero_shot_tags(vision_embedder, vecs)):
                r.meta["auto_tags"] = [{"tag": t, "score": round(p, 3)} for t, p in tags]
                r.meta.pop("lemmas", None)  # 태그가 바뀌었으므로 원형도 다시 계산
            store.upsert_records(todo)
        report["vision"] = len(todo)
        records = store.records()

    # 2) 형태소 원형 (직접 일치용)
    todo = [r for r in records if r.meta.get("lemmas") is None]
    if todo:
        for r, lem in zip(todo, lemmas_many(embed_text(r) or "" for r in todo)):
            r.meta["lemmas"] = sorted(lem | set(r.tags))
        store.upsert_records(todo)
    report["lemmas"] = len(todo)

    # 3) 텍스트 공간 벡터 (bge-m3)
    have = store.vector_ids("text")
    todo2: list[tuple[Record, str]] = [(r, t) for r in store.records() if r.id not in have and (t := embed_text(r))]
    if todo2:
        log(f"텍스트 {len(todo2)}건 임베딩 ({text_embedder.name})")
        vecs = text_embedder.encode([t for _, t in todo2])
        store.put_vectors("text", text_embedder.name, [r.id for r, _ in todo2], vecs)
    report["text"] = len(todo2)

    # 4) 정서
    log("정서 채점")
    report["emotion"] = emotion.score_all(store, text_embedder, vision_embedder, backend=emotion_backend)
    return report
