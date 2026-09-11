"""기록 한 건마다 정서가(valence)와 각성도(arousal)를 매긴다.

- 텍스트, `llm` (기본, 키가 있을 때): 가려진 텍스트를 40건씩 묶어 LLM 이 채점한다. 결과는 DB 캐시에 남아
  같은 문장을 다시 보내지 않는다.
- 텍스트, `anchor` (키가 없을 때): bge-m3 공간에서 긍정·부정, 들뜸·차분 기준 문장과의 거리 차이로 계산한다.
- 사진, `siglip`: SigLIP 2 로 "밝고 즐거운 장면" 과 "어둡고 쓸쓸한 장면" 설명문과 비교한다. 사진은 밖으로 나가지 않는다.
- `truth`: 합성 데이터의 정답 값(Storyseed 그룹 정서)을 그대로 쓴다. 평가 비교용.

인공지능이 추정한 값이므로 기록 한 건의 점수보다 여러 날에 걸친 흐름으로 읽어야 한다.
"""
from __future__ import annotations

import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from .config import LLM_MODEL, openrouter_key
from .llm import LLMUnavailable, chat_json
from .privacy import Masker
from .records import Record
from .store import Store

EMO_SYSTEM = """너는 개인 기록의 정서를 채점한다. 기록마다 두 값을 준다.
- v (정서가): -1 매우 부정 ~ 0 중립 ~ 1 매우 긍정
- a (각성도): 0 매우 차분하거나 무기력함 ~ 1 매우 들뜨거나 격앙됨
감정 단서가 없는 사실 서술(예: "택배 도착")은 v 0, a 0.3 근처로 둔다.
활동 이름만 있는 기록(예: "등산", "낮잠")은 그 활동이 보통 주는 느낌으로 약하게 매긴다(|v| 0.3 이하).
반어·농담(ㅋㅋ, ㅠㅠ)은 문맥으로 판단한다.
JSON 하나만 출력한다: {"scores":[{"i":번호,"v":실수,"a":실수}]}"""

POS = ["정말 행복하다", "기분이 너무 좋다", "신나고 즐겁다", "뿌듯하고 고맙다", "설레고 기대된다", "마음이 편안하고 따뜻하다"]
NEG = ["너무 우울하고 슬프다", "힘들고 지친다", "짜증 나고 화가 난다", "외롭고 쓸쓸하다", "불안하고 걱정된다", "속상하고 서운하다"]
HIGH = ["너무 신나서 흥분된다", "화가 치밀어 오른다", "긴장되고 초조하다", "심장이 두근거린다"]
LOW = ["차분하고 조용하다", "나른하고 졸리다", "아무 생각 없이 멍하다", "평온하고 느긋하다"]
NEUTRAL = ["택배가 도착했다", "은행에 들렀다", "휴대폰 요금제를 바꿨다", "버스를 탔다", "회의 자료를 보냈다"]

IMG_POS = ["a joyful happy photo", "a cheerful bright scene", "people smiling and having fun"]
IMG_NEG = ["a sad gloomy photo", "a lonely depressing scene", "a dark melancholic image"]
IMG_HIGH = ["an energetic exciting scene", "a crowded lively event", "people cheering loudly"]
IMG_LOW = ["a calm peaceful quiet scene", "a still relaxing moment", "an empty silent room"]

BATCH = 40


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _key(model: str, text: str) -> str:
    return f"emo:{model}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:20]}"


def llm_scores(store: Store, texts: list[str], model: str = LLM_MODEL) -> list[tuple[float, float]]:
    """텍스트 목록을 채점한다. 캐시에 있는 문장은 다시 보내지 않는다."""
    results: dict[int, tuple[float, float]] = {}
    todo: list[int] = []
    for i, t in enumerate(texts):
        hit = store.cache_get(_key(model, t))
        if hit:
            results[i] = (hit[0], hit[1])
        else:
            todo.append(i)

    def run(batch: list[int]) -> dict[int, tuple[float, float]]:
        lines = "\n".join(f"{j}. {texts[i][:200].replace(chr(10), ' / ')}" for j, i in enumerate(batch))
        data = chat_json(EMO_SYSTEM, f"기록 {len(batch)}건:\n{lines}", model=model, max_tokens=60 * len(batch) + 200)
        got: dict[int, tuple[float, float]] = {}
        for item in data.get("scores", []) if isinstance(data, dict) else data:
            j = int(item.get("i", -1))
            if 0 <= j < len(batch):
                got[batch[j]] = (_clip(item.get("v", 0), -1, 1), _clip(item.get("a", 0.3), 0, 1))
        return got

    batches = [todo[k : k + BATCH] for k in range(0, len(todo), BATCH)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for got in pool.map(run, batches):
            for i, (v, a) in got.items():
                results[i] = (v, a)
                store.cache_put(_key(model, texts[i]), [v, a])
    missing = [i for i in range(len(texts)) if i not in results]
    if missing:
        # 응답에서 빠진 문장은 한 번 더 작은 묶음으로 요청한다
        for got in map(run, [missing[k : k + 10] for k in range(0, len(missing), 10)]):
            for i, (v, a) in got.items():
                results[i] = (v, a)
                store.cache_put(_key(model, texts[i]), [v, a])
    return [results.get(i, (0.0, 0.3)) for i in range(len(texts))]


class AnchorScorer:
    """임베딩 공간의 기준 문장 비교. 기준 문장 자체가 ±0.8 근처가 되도록 배율을 맞춘다."""

    def __init__(self, text_embedder):
        self.emb = text_embedder
        vec = lambda xs: text_embedder.encode(xs).mean(axis=0)  # noqa: E731
        self.pos, self.neg, self.high, self.low = vec(POS), vec(NEG), vec(HIGH), vec(LOW)
        neutral = text_embedder.encode(NEUTRAL)
        self.v0 = float(np.mean(neutral @ self.pos - neutral @ self.neg))
        self.a0 = float(np.mean(neutral @ self.high - neutral @ self.low))
        pos_raw = float(np.mean(text_embedder.encode(POS) @ self.pos - text_embedder.encode(POS) @ self.neg)) - self.v0
        high_raw = float(np.mean(text_embedder.encode(HIGH) @ self.high - text_embedder.encode(HIGH) @ self.low)) - self.a0
        self.sv = max(pos_raw, 1e-3) / math.atanh(0.8)
        self.sa = max(high_raw, 1e-3) / 1.4

    def score_vectors(self, m: np.ndarray) -> list[tuple[float, float]]:
        v = np.tanh((m @ self.pos - m @ self.neg - self.v0) / self.sv)
        a = 1 / (1 + np.exp(-((m @ self.high - m @ self.low - self.a0) / self.sa - 0.85)))
        return [(float(x), float(y)) for x, y in zip(v, a)]


def image_scores(vision, img_vecs: np.ndarray) -> list[tuple[float, float]]:
    if len(img_vecs) == 0:
        return []
    scale, bias = vision.logit_params()
    t = lambda xs: vision.encode_texts(xs)  # noqa: E731
    lp = (img_vecs @ t(IMG_POS).T).mean(1) * scale
    ln = (img_vecs @ t(IMG_NEG).T).mean(1) * scale
    lh = (img_vecs @ t(IMG_HIGH).T).mean(1) * scale
    ll = (img_vecs @ t(IMG_LOW).T).mean(1) * scale
    v = np.tanh((lp - ln) / 4.0)
    a = 1 / (1 + np.exp(-(lh - ll) / 4.0)) * 0.8 + 0.1
    return [(float(x), float(y)) for x, y in zip(v, a)]


def text_for_emotion(r: Record) -> str | None:
    return r.text.strip() if r.text and r.text.strip() else None


def score_all(
    store: Store,
    text_embedder=None,
    vision=None,
    backend: str = "auto",
    model: str = LLM_MODEL,
    force: bool = False,
) -> dict[str, Any]:
    """정서가 없는 기록을 채점해 저장한다. backend: auto | llm | anchor | truth."""
    done = store.emotions()
    records = [r for r in store.records() if force or r.id not in done]
    masker = Masker.from_store(store)
    rows: list[tuple[str, float, float, str, str | None]] = []
    stats = {"llm": 0, "anchor": 0, "siglip": 0, "truth": 0, "skipped": 0}

    if backend == "truth":
        for r in records:
            truth = (r.meta.get("truth") or {}).get("valence")
            if truth is None:
                stats["skipped"] += 1
                continue
            rows.append((r.id, float(truth), 0.4, "truth", None))
            stats["truth"] += 1
        store.put_emotions(rows)
        return stats

    text_recs = [r for r in records if text_for_emotion(r)]
    text_scores: dict[str, tuple[float, float]] = {}
    use_llm = backend in ("auto", "llm") and openrouter_key()
    if backend == "llm" and not use_llm:
        raise LLMUnavailable("--emotion llm 에는 OPENROUTER_API_KEY 가 필요합니다")
    if text_recs and use_llm:
        texts = [masker.mask(text_for_emotion(r)) or "" for r in text_recs]
        for r, (v, a) in zip(text_recs, llm_scores(store, texts, model=model)):
            text_scores[r.id] = (v, a)
        stats["llm"] = len(text_recs)
        tag = ("llm", model)
    elif text_recs:
        if text_embedder is None:
            raise RuntimeError("anchor 채점에는 텍스트 임베더가 필요합니다")
        ids, mat = store.vectors("text")
        pos = {rid: i for i, rid in enumerate(ids)}
        scorer = AnchorScorer(text_embedder)
        have = [r for r in text_recs if r.id in pos]
        if have:
            for r, s in zip(have, scorer.score_vectors(mat[[pos[r.id] for r in have]])):
                text_scores[r.id] = s
        stats["anchor"] = len(have)
        tag = ("anchor", text_embedder.name)
    else:
        tag = ("llm", model)

    img_scores: dict[str, tuple[float, float]] = {}
    img_recs = [r for r in records if r.modality in ("image", "text+image")]
    if img_recs and vision is not None:
        ids, mat = store.vectors("vision")
        pos = {rid: i for i, rid in enumerate(ids)}
        have = [r for r in img_recs if r.id in pos]
        for r, s in zip(have, image_scores(vision, mat[[pos[r.id] for r in have]] if have else np.zeros((0, 1)))):
            img_scores[r.id] = s
        stats["siglip"] = len(have)

    for r in records:
        t, i = text_scores.get(r.id), img_scores.get(r.id)
        if t and i:
            rows.append((r.id, 0.7 * t[0] + 0.3 * i[0], 0.7 * t[1] + 0.3 * i[1], tag[0] + "+siglip", tag[1]))
        elif t:
            rows.append((r.id, t[0], t[1], tag[0], tag[1]))
        elif i:
            rows.append((r.id, i[0], i[1], "siglip", vision.name if vision else None))
        else:
            stats["skipped"] += 1
    store.put_emotions(rows)
    return stats
