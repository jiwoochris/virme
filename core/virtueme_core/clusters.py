"""기록 묶기: 주제(BERTopic) · 기분(정서 사분면) · 함께 여부.

- topic : bge-m3 임베딩 → UMAP → HDBSCAN(scikit-learn) 으로 묶고, 묶음마다 c-TF-IDF 명사(Kiwi)와 대표 기록을
          LLM 에 보내 3~10자 이름을 받는다. 어느 묶음에도 들지 않은 기록은 임베딩 기준으로 가장 가까운 묶음에 붙인다.
          "나도 7시 괜찮아" 같은 짧은 말끼리 모인 묶음은 공통 활동이 없어 "일상적인 대화" 같은 이름이 붙는다.
          LLM 이 공통 활동을 찾지 못한 묶음은 "짧은 말·기타"(t-1)로 합친다.
- mood  : 정서가·각성도 부호로 네 칸 (들뜸 / 편안 / 긴장 / 가라앉음). 0 근처는 "보통"
- social: 정서 채점 때 받은 함께 여부 (혼자 / 함께 / 알 수 없음)
- map   : 같은 bge-m3 벡터를 UMAP 으로 2차원에 펼친 좌표. 인사이트 화면의 "의미 지도"가 쓴다 (설계 문서의 "하나의 의미 공간")

본인이 쓴 기록만 묶는다. 결과는 DB 의 clusters·cluster_info 표에 남는다.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

import numpy as np

from .config import LLM_MODEL, openrouter_key
from .llm import chat_json
from .privacy import Masker
from .query import embed_text
from .store import Store
from .textproc import nouns

LABEL_SYSTEM = """너는 개인 기록 묶음에 이름을 붙인다. 묶음마다 대표 낱말과 기록 몇 개가 주어진다.
각 묶음에 그 기록들이 공통으로 다루는 활동·대상·장소를 3~10자 한국어 명사구로 붙인다 (예: 한강 러닝, 강아지 돌보기, 회사 야근).
대표 낱말 가운데 기록에 실제로 나오는 낱말을 살린다. 감정·말투(긍정, 만족, 감정 표현)나 "일상 대화"처럼 어느 기록에나 붙는
이름, "~에 대한 기록" 같은 말은 쓰지 않는다. 공통 활동이 보이지 않으면 label 을 빈 문자열로 둔다.
묶음끼리 이름이 겹치지 않게 한다. 가명(지인A 등)은 이름에 쓰지 않는다.
JSON 하나만 출력한다: {"labels":[{"t":묶음번호,"label":"이름"}]}"""
VAGUE = re.compile(r"감정|표현|긍정|부정|만족|일상|기록|대화 나눔")
OTHER_LABEL = "짧은 말·기타"

MOOD_LABELS = {"up": "들뜸", "calm": "편안", "tense": "긴장", "down": "가라앉음", "flat": "보통"}
SOCIAL_LABELS = {"1": "함께", "0": "혼자", "?": "알 수 없음"}


def mood_of(v: float, a: float) -> str:
    if abs(v) < 0.15:
        return "flat"
    if v > 0:
        return "up" if a >= 0.5 else "calm"
    return "tense" if a >= 0.5 else "down"


def build_topics(store: Store, llm_model: str = LLM_MODEL, seed: int = 42) -> dict[str, Any]:
    from bertopic import BERTopic
    from sklearn.cluster import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer
    from umap import UMAP

    masker = Masker.from_store(store)
    ids, mat = store.vectors("text")
    pos = {rid: i for i, rid in enumerate(ids)}
    recs = [r for r in store.records() if r.is_self and r.id in pos and embed_text(r)]
    if len(recs) < 30:
        return {"topics": 0, "note": "본인 기록이 30건보다 적어 묶지 않았습니다"}
    docs = [masker.mask(embed_text(r)) or "" for r in recs]
    emb = mat[[pos[r.id] for r in recs]]
    n = len(recs)
    # language 를 english(기본값)로 두면 BERTopic 전처리가 영문·숫자 밖의 글자를 지워 한글이 모두 사라진다
    model = BERTopic(
        language="multilingual",
        embedding_model=None,
        umap_model=UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", random_state=seed),
        hdbscan_model=HDBSCAN(min_cluster_size=max(8, n // 60), min_samples=4, metric="euclidean"),
        vectorizer_model=CountVectorizer(tokenizer=nouns, token_pattern=None, lowercase=False, min_df=1),
        calculate_probabilities=False,
    )
    topics, _ = model.fit_transform(docs, embeddings=emb)
    topics = np.array(topics)
    # 어느 묶음에도 들지 않은 기록은 묶음 중심과 가장 가까운 곳에 붙인다 (유사도가 낮으면 그대로 둔다)
    valid = sorted(t for t in set(topics.tolist()) if t >= 0)
    if valid:
        cents = np.stack([emb[topics == t].mean(axis=0) for t in valid])
        cents /= np.linalg.norm(cents, axis=1, keepdims=True)
        for i in np.where(topics < 0)[0]:
            sims = cents @ emb[i]
            if sims.max() > 0.45:
                topics[i] = valid[int(np.argmax(sims))]
    keywords = {t: [w for w, _ in (model.get_topic(t) or []) if w.strip()][:8] for t in valid}
    samples = {}
    for t in valid:
        idx = np.where(topics == t)[0]
        c = emb[idx].mean(axis=0)
        near = idx[np.argsort(-(emb[idx] @ c))][:6]
        samples[t] = [docs[i][:80].replace("\n", " / ") for i in near]
    labels = {t: " · ".join(keywords[t][:2]) or f"묶음 {t}" for t in valid}
    if openrouter_key() and valid:
        body = "\n\n".join(f"묶음 {t}\n대표 낱말: {', '.join(keywords[t])}\n기록: " + " | ".join(samples[t]) for t in valid)
        try:
            data = chat_json(LABEL_SYSTEM, body, model=llm_model, max_tokens=60 * len(valid) + 200)
            vague = []
            for item in data.get("labels", []):
                t = int(item.get("t", -99))
                if t not in labels:
                    continue
                got = str(item.get("label", "")).strip()
                if got and not VAGUE.search(got):
                    labels[t] = got[:20]
                else:
                    vague.append(t)
            for t in vague:  # 공통 활동이 없는 묶음은 기타로 합친다
                topics[topics == t] = -1
                valid.remove(t)
            seen: set[str] = set()
            for t in sorted(valid, key=lambda t: -int((topics == t).sum())):  # 이름이 겹치면 이름에 없는 대표 낱말을 붙인다
                if labels[t] in seen:
                    extra = next((w for w in keywords[t] if w not in labels[t]), str(t))
                    labels[t] = f"{labels[t]} · {extra}"
                seen.add(labels[t])
        except Exception:  # 이름을 못 받으면 대표 낱말로 둔다
            pass
    assign = {r.id: (f"t{int(t)}" if t >= 0 else "t-1") for r, t in zip(recs, topics)}
    info = [(f"t{t}", labels[t], int((topics == t).sum()), keywords[t]) for t in valid]
    info.append(("t-1", OTHER_LABEL, int((topics < 0).sum()), []))
    store.put_clusters("topic", assign, info)
    return {"topics": len(valid), "unassigned": int((topics < 0).sum()), "labels": {f"t{t}": labels[t] for t in valid}}


def build_mood_social(store: Store) -> dict[str, Any]:
    emo = store.emotions()
    soc = store.social()
    own = [r for r in store.records() if r.is_self and r.id in emo]
    mood = {r.id: mood_of(emo[r.id][0], emo[r.id][1]) for r in own}
    social = {r.id: ("?" if soc.get(r.id) is None else str(soc[r.id])) for r in own}
    store.put_clusters("mood", mood, [(k, v, sum(1 for x in mood.values() if x == k), []) for k, v in MOOD_LABELS.items()])
    store.put_clusters("social", social,
                       [(k, v, sum(1 for x in social.values() if x == k), []) for k, v in SOCIAL_LABELS.items()])
    return {"mood": {MOOD_LABELS[k]: sum(1 for x in mood.values() if x == k) for k in MOOD_LABELS},
            "social": {SOCIAL_LABELS[k]: sum(1 for x in social.values() if x == k) for k in SOCIAL_LABELS}}


MAP_VERSION = "map1"


def semantic_map(store: Store, seed: int = 42) -> dict[str, Any]:
    """본인 기록의 텍스트 벡터를 UMAP 으로 2차원(0..1)에 펼친다. 기록이나 벡터가 바뀌면 다시 계산하고, 결과는 캐시에 둔다."""
    ids, mat = store.vectors("text")
    pos = {rid: i for i, rid in enumerate(ids)}
    own = [r.id for r in store.records() if r.is_self and r.id in pos]
    if len(own) < 10:
        return {"ids": [], "xy": []}
    emb = mat[[pos[i] for i in own]]
    sig = hashlib.sha1(("|".join(own)).encode() + np.round(emb[:, :4], 4).tobytes()).hexdigest()[:12]
    key = f"{MAP_VERSION}:{sig}"
    hit = store.cache_get(key)
    if hit is not None:
        return hit
    from umap import UMAP

    xy = UMAP(n_neighbors=15, n_components=2, min_dist=0.15, metric="cosine", random_state=seed).fit_transform(emb)
    xy = (xy - xy.min(axis=0)) / np.maximum(xy.max(axis=0) - xy.min(axis=0), 1e-9)
    out = {"ids": own, "xy": [[round(float(a), 4), round(float(b), 4)] for a, b in xy]}
    store.cache_put(key, out)
    return out


def build_all(store: Store, llm_model: str = LLM_MODEL) -> dict[str, Any]:
    out = {"topic": build_topics(store, llm_model), **build_mood_social(store)}
    out["map_points"] = len(semantic_map(store)["ids"])
    return out


def summarize(store: Store, record_ids: list[str]) -> dict[str, Any]:
    """근거 기록이 어느 묶음에 들어 있는지. 서사 오브젝트의 clusters·mix 에 쓴다."""
    out: dict[str, Any] = {"clusters": [], "mood_mix": {}, "social_mix": {}}
    ids = set(record_ids)
    if not ids:
        return out
    assign, info = store.clusters("topic")
    counts: dict[str, int] = {}
    for rid in ids:
        c = assign.get(rid)
        if c and c != "t-1":
            counts[c] = counts.get(c, 0) + 1
    for c, k in sorted(counts.items(), key=lambda kv: -kv[1])[:5]:
        meta = info.get(c, {})
        out["clusters"].append({"id": f"topic:{c}", "axis": "topic", "label": meta.get("label", c),
                                "share": round(k / len(ids), 3), "size": meta.get("size", 0),
                                "keywords": meta.get("keywords", [])[:5]})
    for axis, labels, key in (("mood", MOOD_LABELS, "mood_mix"), ("social", SOCIAL_LABELS, "social_mix")):
        assign_a, _ = store.clusters(axis)
        vals = [assign_a[r] for r in ids if r in assign_a]
        base = list(assign_a.values())
        if vals:
            out[key] = {labels[k]: {"evidence": round(sum(1 for v in vals if v == k) / len(vals), 3),
                                    "all": round(sum(1 for v in base if v == k) / len(base), 3)} for k in labels}
    return out


def overview(store: Store) -> list[dict[str, Any]]:
    """전체 주제 묶음 목록 (시각화용): 이름, 크기, 기간, 대표 낱말."""
    assign, info = store.clusters("topic")
    recs = {r.id: r for r in store.records() if r.id in assign}
    out = []
    for c, meta in info.items():
        if c == "t-1":
            continue
        days = sorted(recs[rid].day for rid, cc in assign.items() if cc == c and rid in recs)
        out.append({"id": f"topic:{c}", "label": meta["label"], "size": meta["size"], "keywords": meta["keywords"][:6],
                    "first": days[0] if days else None, "last": days[-1] if days else None,
                    "by_day": {d: days.count(d) for d in sorted(set(days))}})
    return sorted(out, key=lambda x: -x["size"])

