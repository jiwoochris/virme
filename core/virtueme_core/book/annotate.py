"""참고 표지에 측정값과 디자인 라벨 붙이기 → `references.json`.

1. 결정적 측정 (`features.measure`): 팔레트·명도·대비·색채도·윤곽 밀도·여백·비율. 키가 없어도 돈다.
2. 비전 모델 라벨: 레이아웃 원형, 제목 위치·크기, 서체 계열, 그림 양식, 분위기(최대 4개), 장르 추정.
   값은 `vocab` 의 통제 어휘에서만 고르게 하고, 돌아온 값도 `vocab.normalize_label` 로 한 번 더 거른다.
   표지 8장을 한 요청에 묶고(장당 512px), 결과는 `$VIRTUEME_DATA/book_refs/labels.json` 에 모델·지시문 판본별로
   남겨 같은 표지를 다시 사지 않는다.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from ..config import LLM_MODEL, openrouter_key
from . import features, net
from .collect import refs_dir
from .vocab import GENRE_KEYS, LABEL_FIELDS, MOODS, normalize_label

PKG_DIR = Path(__file__).resolve().parent
REFERENCES_PATH = PKG_DIR / "references.json"
VISION_MODEL = os.environ.get("VIRTUEME_BOOK_VISION_MODEL", LLM_MODEL)
PROMPT_VERSION = "cover-label-v1"
BATCH = 8

SYSTEM = (
    "너는 책 표지 디자인을 분류한다. 이미지마다 아래 칸을 정해진 값 가운데서만 골라 JSON 배열로 답하라. "
    "설명 문장은 쓰지 말고 JSON 만 출력하라.\n\n"
    + "\n".join(f"- {f}: " + ", ".join(f"{k}({v})" for k, v in allowed.items()) for f, allowed in LABEL_FIELDS.items())
    + "\n- moods: 1~4개. " + ", ".join(MOODS)
    + "\n- genre_guess: 표지만 보고 짐작한 장르. " + ", ".join(GENRE_KEYS)
    + "\n- is_cover: 책 앞표지 디자인이면 true. 무지 양장 천 표지·책등·낱장 사진·빈 이미지면 false"
    + "\n\n판단 기준: archetype 은 그림과 글자가 면을 나누는 방식이다. 사진·그림이 가장자리까지 채우고 제목이 그 위에 "
    "얹히면 full_bleed, 그림이 여백 안 사각형·원 안에 있으면 framed, 그림 없이 글자·색면뿐이면 typographic, 단색 블록과 "
    "그림이 면을 나누면 split, 반복 무늬가 바탕이면 pattern, 단색 바탕 가운데 작은 그림 하나면 centered_motif. "
    "title_position 은 제목(저자 이름이 아니라)이 놓인 높이, title_scale 은 제목 글자 줄의 폭이 표지 폭에서 차지하는 비율이다. "
    "typeface 는 제목 서체 기준이다. art_style 은 그림이 없으면 none.\n\n"
    '형식: [{"i": 0, "is_cover": true, "archetype": "...", "title_position": "...", "title_scale": "...", '
    '"typeface": "...", "art_style": "...", "moods": ["..."], "genre_guess": "..."}, ...]'
)


def _labels_path() -> Path:
    return refs_dir() / "labels.json"


def _cache_key(item_id: str) -> str:
    return f"{VISION_MODEL}|{PROMPT_VERSION}|{item_id}"


def label_batch(items: list[dict[str, Any]], root: Path, outbound: Any) -> tuple[dict[str, dict[str, Any]], dict]:
    urls = [net.image_data_url(root / it["file"], max_side=512, quality=80) for it in items]
    text = (f"표지 {len(items)}장. 순서대로 i=0..{len(items) - 1}. 각 표지의 출처 장르 표기(참고만): "
            + ", ".join(f"{i}:{it['genre']}" for i, it in enumerate(items)))
    data, usage = net.vision_json(SYSTEM, text, urls, VISION_MODEL, "참고 표지 디자인 라벨", outbound,
                                  image_note=", ".join(it["id"] for it in items))
    rows = data if isinstance(data, list) else data.get("items") or data.get("covers") or []
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            i = int(row.get("i"))
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(items):
            lab = normalize_label(row)
            g = str(row.get("genre_guess") or "").lower()
            lab["genre_guess"] = g if g in GENRE_KEYS else None
            out[items[i]["id"]] = lab
    return out, usage


def annotate(use_llm: bool = True, limit: int | None = None, workers: int = 4,
             log: Callable[[str], None] = print) -> dict[str, Any]:
    root = refs_dir()
    corpus = json.loads((root / "corpus.json").read_text("utf-8"))
    items = corpus["items"][:limit] if limit else corpus["items"]
    log(f"측정: 표지 {len(items)}장")
    feats = {}
    for it in items:
        try:
            feats[it["id"]] = features.measure(root / it["file"])
        except OSError:
            continue
    cache_path = _labels_path()
    cache: dict[str, Any] = json.loads(cache_path.read_text("utf-8")) if cache_path.exists() else {}
    cost = 0.0
    if use_llm and openrouter_key():
        todo = [it for it in items if it["id"] in feats and _cache_key(it["id"]) not in cache]
        batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        log(f"라벨: {len(todo)}장, {len(batches)}개 요청 ({VISION_MODEL})")
        outbound = net.outbound_for(None)

        def run(b: list[dict[str, Any]]) -> tuple[dict, dict]:
            try:
                return label_batch(b, root, outbound)
            except Exception as err:  # noqa: BLE001 - 한 묶음 실패는 다음 실행에서 다시 한다
                log(f"  실패 {b[0]['id']}…: {err}")
                return {}, {}

        with ThreadPoolExecutor(workers) as ex:
            for got, usage in ex.map(run, batches):
                for k, v in got.items():
                    cache[_cache_key(k)] = v
                cost += float(usage.get("cost") or 0)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), "utf-8")
        log(f"  라벨 비용 ${cost:.4f}")
    elif use_llm:
        log("OPENROUTER_API_KEY 가 없어 측정값만 남깁니다 (라벨 없음)")
    refs = []
    for it in items:
        if it["id"] not in feats:
            continue
        lab = cache.get(_cache_key(it["id"]))
        refs.append({
            "id": it["id"], "genre": it["genre"], "lang": it["lang"],
            "provenance": {k: it.get(k) for k in ("title", "original_title", "author", "publisher", "year",
                                                  "source", "source_url", "cover_url", "subject")},
            "features": feats[it["id"]],
            "label": lab,
        })
    out = {
        "schema": "virtueme.book_references/1",
        "source": corpus.get("source"), "collected": corpus.get("collected"),
        "label_model": VISION_MODEL if any(r["label"] for r in refs) else None,
        "label_prompt": PROMPT_VERSION,
        "image_dir": "$VIRTUEME_DATA/book_refs/img (저장소에 없음, collect 로 다시 받음)",
        "items": refs,
    }
    REFERENCES_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    log(f"→ {REFERENCES_PATH} ({len(refs)}건, 라벨 {sum(1 for r in refs if r['label'])}건)")
    return out


def load_references(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or REFERENCES_PATH
    if not p.exists():
        return []
    return json.loads(p.read_text("utf-8"))["items"]


def usable(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """표지가 아니라고 라벨된 것을 뺀다 (라벨이 없으면 남긴다)."""
    return [r for r in refs if not (r.get("label") and r["label"].get("is_cover") is False)]
