"""관련도 계산 방식 비교·임계치 조정.

평가셋 두 개를 쓴다.
- ko_mini.jsonl: 일기·메신저 말투 한국어 66문장, 12개 주제 라벨 (멘토가 직접 붙임)
- 합성 태그: Storyseed 활동 태그 35개와 키워드별 관련 태그 (아래 SYNTH_LABELS)

    uv run python eval/retrieval_eval.py            # 방식별 MAP·F1 표
    uv run python eval/retrieval_eval.py --grid     # z 곡선 중심·폭 격자 탐색

LLM 확장 결과는 data/eval-cache.db 에 캐시한다.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from virtueme_core.config import DATA_DIR  # noqa: E402
from virtueme_core.embed import TextEmbedder, sigmoid  # noqa: E402
from virtueme_core.ingest.synthetic import GROUPS  # noqa: E402
from virtueme_core.query import BACKGROUND, GENERIC_LEMMAS, expand_keyword  # noqa: E402
from virtueme_core.store import Store  # noqa: E402
from virtueme_core.textproc import lemmas, lemmas_many  # noqa: E402

SYNTH_LABELS = {
    "등산": {"등산", "둘레길 산책"},
    "운동": set(GROUPS[1]["tags"]),
    "휴식": set(GROUPS[0]["tags"]),
    "사람들": set(GROUPS[2]["tags"]),
    "쇼핑": {"쇼핑몰 둘러보기", "중고거래 탐색", "전자기기 리뷰", "시장 구경"},
    "음식": {"맛집 검색", "배달 주문", "가족 외식", "요리 시도", "단골 주점", "카페 탐방"},
    "기록": {"사진 촬영", "블로그 기록", "영상 편집", "손글씨 메모"},
    "음악": {"트로트 감상", "라디오 청취", "코인노래방"},
    "가족": {"가족 외식"},
    "여행": {"여행지 검색"},
}


def ap(scores: np.ndarray, labels: list[bool]) -> float:
    order = np.argsort(-scores)
    hits, s = 0, 0.0
    for i, idx in enumerate(order, 1):
        if labels[idx]:
            hits += 1
            s += hits / i
    return s / max(1, sum(labels))


def prf(pred: np.ndarray, labels: list[bool]) -> tuple[float, float, float]:
    lab = np.array(labels)
    tp = int((pred & lab).sum())
    p = tp / max(1, int(pred.sum()))
    r = tp / max(1, int(lab.sum()))
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def components(texts: list[str], queries: list[str], emb, store: Store):
    recs = emb.encode(texts)
    bg = emb.encode(BACKGROUND) @ recs.T
    sd = bg.std(0)
    sd = np.maximum(sd, np.median(sd) * 0.5) + 1e-6
    mu = bg.mean(0)
    rec_lem = [lem for lem in lemmas_many(texts)]
    out = {}
    for q in queries:
        ex = expand_keyword(store, q)
        z_kw = (emb.encode([q]) @ recs.T - mu) / sd
        extra = ex["situations"] + ex["forms"]
        z_ex = (emb.encode(extra) @ recs.T - mu) / sd if extra else np.zeros((0, len(texts)))
        kw_l = {x for x in lemmas(q) if x not in GENERIC_LEMMAS} or {q}
        form_l = set()
        for f in ex["forms"]:
            form_l |= {x for x in lemmas(f) if x not in GENERIC_LEMMAS and (len(x) > 1 or len(q) == 1)}
        form_l -= kw_l
        direct = np.array([0.9 if kw_l & lem else 0.75 if form_l & lem else 0.0 for lem in rec_lem])
        out[q] = (z_kw[0], z_ex, direct)
    return out


STRATEGIES = {
    "키워드만": lambda zk, ze: zk,
    "반반+최대": lambda zk, ze: 0.5 * zk + 0.5 * ze.max(0) if len(ze) else zk,
    "반반+상위3평균": lambda zk, ze: 0.5 * zk + 0.5 * np.sort(ze, 0)[-3:].mean(0) if len(ze) else zk,
    "6:4+최대": lambda zk, ze: 0.6 * zk + 0.4 * ze.max(0) if len(ze) else zk,
    "반반+상위2평균": lambda zk, ze: 0.5 * zk + 0.5 * np.sort(ze, 0)[-2:].mean(0) if len(ze) else zk,
}


def evaluate(sets, strategy: str, zc: float, zw: float, strong: float = 0.6, weak: float = 0.35, use_direct=True):
    rows = {}
    for name, (labels_by_q, comps) in sets.items():
        maps, f_s, f_w, p_s, r_s = [], [], [], [], []
        for q, labels in labels_by_q.items():
            zk, ze, direct = comps[q]
            sem = sigmoid((STRATEGIES[strategy](zk, ze) - zc) / zw)
            rel = 1 - (1 - (direct if use_direct else 0)) * (1 - sem)
            maps.append(ap(rel, labels))
            p, r, f = prf(rel >= strong, labels)
            f_s.append(f), p_s.append(p), r_s.append(r)
            f_w.append(prf(rel >= weak, labels)[2])
        rows[name] = {"MAP": np.mean(maps), "F1강": np.mean(f_s), "P강": np.mean(p_s), "R강": np.mean(r_s),
                      "F1약이상": np.mean(f_w)}
    return rows


def main() -> None:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--grid", action="store_true")
    args = ap_.parse_args()
    store = Store(DATA_DIR / "eval-cache.db")
    emb = TextEmbedder()
    mini = [json.loads(line) for line in (ROOT / "eval/ko_mini.jsonl").read_text(encoding="utf-8").splitlines()]
    qs = json.loads((ROOT / "eval/ko_mini.meta.json").read_text(encoding="utf-8"))["queries"]
    texts = [m["text"] for m in mini]
    mini_labels = {q: [q in m["labels"] for m in mini] for q in qs}
    tags = [t for g in GROUPS for t in g["tags"]]
    synth_labels = {q: [t in rel for t in tags] for q, rel in SYNTH_LABELS.items()}
    sets = {
        "한국어 문장": (mini_labels, components(texts, qs, emb, store)),
        "합성 태그": (synth_labels, components(tags, list(SYNTH_LABELS), emb, store)),
    }
    if args.grid:
        best = []
        for strat, zc, zw in itertools.product(STRATEGIES, [1.5, 1.75, 2.0, 2.25, 2.5, 2.75], [0.4, 0.55, 0.7]):
            rows = evaluate(sets, strat, zc, zw)
            score = np.mean([r["F1강"] for r in rows.values()]) + 0.5 * np.mean([r["F1약이상"] for r in rows.values()])
            best.append((score, strat, zc, zw, rows))
        best.sort(key=lambda b: -b[0])
        for score, strat, zc, zw, rows in best[:8]:
            print(f"{score:.3f}  {strat:10s} z중심 {zc} 폭 {zw}  " + "  ".join(
                f"{n}: MAP {r['MAP']:.3f} F1강 {r['F1강']:.3f}(P{r['P강']:.2f}/R{r['R강']:.2f}) F1약이상 {r['F1약이상']:.3f}"
                for n, r in rows.items()))
        return
    for strat in STRATEGIES:
        rows = evaluate(sets, strat, 2.0, 0.55)
        print(f"{strat:10s} " + "  ".join(
            f"{n}: MAP {r['MAP']:.3f} F1강 {r['F1강']:.3f}(P{r['P강']:.2f}/R{r['R강']:.2f}) F1약이상 {r['F1약이상']:.3f}"
            for n, r in rows.items()))
    rows = evaluate(sets, "반반+최대", 2.0, 0.55, use_direct=False)
    print("직접 일치 끔(반반+최대) " + "  ".join(f"{n}: MAP {r['MAP']:.3f} F1강 {r['F1강']:.3f}" for n, r in rows.items()))


if __name__ == "__main__":
    main()
