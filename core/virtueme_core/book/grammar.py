"""장르별 디자인 문법: 참고 표지의 라벨·측정값을 장르마다 세어 분포로 만든다 → `grammar.json`.

여기서 "배운다"는 것은 이 분포를 세는 일이다. 모델 가중치를 고치지 않는다. 브리프는 이 분포를 장르의 기본값(사전 분포)으로
쓰고, 분위기가 맞는 참고 표지 몇 장의 표를 더해 값을 고른다.

- dist[field]     : 라벨 값별 비율 (라벨이 있는 표지만). 사람이 검수한 표지(`label_checked`)는 2배로 센다
- lift[field]     : 이 장르 비율 / 전체 비율. 1.5 이상이면 이 장르에서 두드러지는 값
- tone            : dark·mid·light 비율, 평균 명도·대비·색채도·윤곽 밀도·여백
- palette         : 장르 표지들의 주요 색을 면적 가중 k-means 로 다시 묶은 6색
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .annotate import PKG_DIR, load_references, usable
from .review import checked_weight
from .features import hex_of, kmeans, lab_to_rgb, rgb_of, rgb_to_lab
from .vocab import GENRE_KEYS, GENRE_KO, LABEL_FIELDS, MOODS

GRAMMAR_PATH = PKG_DIR / "grammar.json"
FIELDS = list(LABEL_FIELDS) + ["moods"]


def _dist(values: list[tuple[str, float]]) -> dict[str, float]:
    c: Counter[str] = Counter()
    for v, w in values:
        if v:
            c[v] += w
    n = sum(c.values())
    return {k: round(v / n, 3) for k, v in c.most_common()} if n else {}


def _values(refs: list[dict[str, Any]], field: str) -> list[tuple[str, float]]:
    """(값, 무게). 사람이 검수한 표지(label_checked)는 2배로 센다."""
    if field == "moods":
        return [(m, checked_weight(r)) for r in refs if r.get("label") for m in r["label"].get("moods") or []]
    return [(r["label"].get(field), checked_weight(r)) for r in refs if r.get("label")]


def genre_palette(refs: list[dict[str, Any]], k: int = 6) -> list[dict[str, Any]]:
    cols, w = [], []
    for r in refs:
        for p in r["features"]["palette"]:
            cols.append(rgb_of(p["hex"]))
            w.append(p["share"])
    if not cols:
        return []
    lab = rgb_to_lab(np.array(cols, dtype=float))
    centers, labels = kmeans(lab, k, weights=np.array(w))
    share = np.bincount(labels, weights=np.array(w), minlength=len(centers))
    share = share / share.sum()
    rgb = lab_to_rgb(centers)
    out = [{"hex": hex_of(rgb[i]), "share": round(float(share[i]), 3), "L": round(float(centers[i, 0]), 1)}
           for i in range(len(centers))]
    return sorted(out, key=lambda p: -p["share"])


def build_grammar(refs: list[dict[str, Any]] | None = None, path: Path | None = GRAMMAR_PATH) -> dict[str, Any]:
    refs = usable(refs if refs is not None else load_references())
    overall = {f: _dist(_values(refs, f)) for f in FIELDS}
    genres: dict[str, Any] = {}
    for g in GENRE_KEYS:
        rs = [r for r in refs if r["genre"] == g]
        if not rs:
            continue
        labeled = [r for r in rs if r.get("label")]
        dist = {f: _dist(_values(rs, f)) for f in FIELDS}
        lift = {f: {k: round(v / overall[f][k], 2) for k, v in d.items() if overall[f].get(k)} for f, d in dist.items()}
        ft = [r["features"] for r in rs]
        guesses = [r["label"].get("genre_guess") for r in labeled]
        genres[g] = {
            "name": GENRE_KO[g], "n": len(rs), "n_labeled": len(labeled),
            "n_korean": sum(1 for r in rs if r["lang"] == "ko"),
            "n_checked": sum(1 for r in rs if r.get("label_checked")),
            "genre_agreement": round(sum(1 for x in guesses if x == g) / len(guesses), 3) if guesses else None,
            "dist": dist, "lift": lift,
            "tone": {
                **{t: round(sum(1 for f in ft if f["tone"] == t) / len(ft), 3) for t in ("dark", "mid", "light")},
                **{m: round(float(np.mean([f[m] for f in ft])), 3)
                   for m in ("lightness", "contrast", "colorfulness", "edge_density", "negative_space")},
            },
            "palette": genre_palette(rs),
        }
    out = {"schema": "virtueme.book_grammar/1", "n": len(refs), "n_labeled": sum(1 for r in refs if r.get("label")),
           "n_checked": sum(1 for r in refs if r.get("label_checked")),
           "overall": overall, "genres": genres}
    if path:
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    return out


def load_grammar(path: Path | None = None) -> dict[str, Any]:
    p = path or GRAMMAR_PATH
    return json.loads(p.read_text("utf-8")) if p.exists() else {"genres": {}, "overall": {}}


def top(d: dict[str, float], n: int = 2) -> str:
    return " · ".join(f"{k} {round(v * 100)}%" for k, v in list(d.items())[:n])


def table_markdown(grammar: dict[str, Any]) -> str:
    """README 에 붙이는 장르별 결과 표."""
    rows = ["| 장르 | 표지 (한국어판) | 레이아웃 원형 | 제목 위치 · 크기 | 서체 | 그림 양식 | 분위기 | 어두운 표지 | 색채도 | 장르 일치 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for g, s in grammar["genres"].items():
        d = s["dist"]
        rows.append(
            f"| {s['name']} | {s['n']} ({s['n_korean']}) | {top(d.get('archetype', {}))} | "
            f"{top(d.get('title_position', {}), 1)} · {top(d.get('title_scale', {}), 1)} | {top(d.get('typeface', {}))} | "
            f"{top(d.get('art_style', {}))} | {', '.join(MOODS.get(k, k) for k in list(d.get('moods', {}))[:3])} | "
            f"{round(s['tone']['dark'] * 100)}% | {s['tone']['colorfulness']:.2f} | "
            f"{'' if s['genre_agreement'] is None else str(round(s['genre_agreement'] * 100)) + '%'} |")
    return "\n".join(rows)
