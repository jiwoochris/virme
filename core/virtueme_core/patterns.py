"""날짜별 정서 곡선, 변화점, 주기성, 시간대 분포.

이 모듈은 인공지능 모델을 쓰지 않는다. 변화점은 PELT(ruptures), 주기는 STL(statsmodels) 로
계산하는 통계 기법이다. 발표에서 AI 기술과 구분해서 설명한다.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from .records import Record

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
OTHERS_WEIGHT = 0.3  # 다른 사람이 보낸 메시지가 하루 정서에 기여하는 비율
# 흔적 기록(유튜브·결제·건강 …)은 정서가 없고 하루에 수십 건씩 쌓이므로 날짜 범위에만 넣고, 기록 수·정서에는 넣지 않는다


@dataclass
class Daily:
    dates: list[date]
    count: np.ndarray        # 본인 기록 수 (다른 사람 메시지는 OTHERS_WEIGHT 로 셈)
    valence_raw: list[float | None]
    arousal_raw: list[float | None]
    valence: np.ndarray      # 빈 날을 선형 보간한 값
    arousal: np.ndarray
    valence_smooth: np.ndarray
    sparse: list[bool]       # 기록이 2건 미만인 날
    filled: list[bool]       # 기록이 없어 보간한 날

    def index(self, d: date) -> int:
        return (d - self.dates[0]).days

    def series(self) -> list[dict[str, Any]]:
        return [
            {
                "date": d.isoformat(),
                "valence": round(float(self.valence_smooth[i]), 3),
                "valence_raw": None if self.valence_raw[i] is None else round(self.valence_raw[i], 3),
                "arousal": round(float(self.arousal[i]), 3),
                "records": round(float(self.count[i]), 1),
                "sparse": self.sparse[i],
                "filled": self.filled[i],
            }
            for i, d in enumerate(self.dates)
        ]


def _interp(raw: list[float | None], default: float) -> np.ndarray:
    n = len(raw)
    known = [i for i, v in enumerate(raw) if v is not None]
    if not known:
        return np.full(n, default, dtype=float)
    return np.interp(np.arange(n), known, [raw[i] for i in known])


def moving_average(x: np.ndarray, window: int = 7) -> np.ndarray:
    half = window // 2
    out = np.empty_like(x, dtype=float)
    for i in range(len(x)):
        lo, hi = max(0, i - half), min(len(x), i + half + 1)
        out[i] = x[lo:hi].mean()
    return out


def daily(records: list[Record], emotions: dict[str, tuple[float, float, str]]) -> Daily:
    if not records:
        raise ValueError("기록이 없습니다")
    days = [r.dt.date() for r in records]
    start, end = min(days), max(days)
    n = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(n)]
    cnt = np.zeros(n)
    vs = np.zeros(n)
    as_ = np.zeros(n)
    ws = np.zeros(n)
    for r, d in zip(records, days):
        if r.is_trace:
            continue
        i = (d - start).days
        w = 1.0 if r.is_self else OTHERS_WEIGHT
        cnt[i] += w
        e = emotions.get(r.id)
        if e:
            vs[i] += w * e[0]
            as_[i] += w * e[1]
            ws[i] += w
    v_raw = [float(vs[i] / ws[i]) if ws[i] > 0 else None for i in range(n)]
    a_raw = [float(as_[i] / ws[i]) if ws[i] > 0 else None for i in range(n)]
    v = _interp(v_raw, 0.0)
    a = _interp(a_raw, 0.3)
    return Daily(
        dates=dates, count=cnt, valence_raw=v_raw, arousal_raw=a_raw, valence=v, arousal=a,
        valence_smooth=moving_average(v, 7), sparse=[bool(c < 2) for c in cnt], filled=[x is None for x in v_raw],
    )


def change_points(
    series: np.ndarray,
    dates: list[date],
    min_size: int = 7,
    beta: float = 3.0,
    min_delta: float = 0.08,
    max_points: int = 4,
) -> list[dict[str, Any]]:
    """PELT (평균 이동, l2 비용). 벌점은 β·σ²·log n, σ 는 1차 차분의 MAD 로 추정한다."""
    import ruptures as rpt

    x = np.asarray(series, dtype=float)
    n = len(x)
    if n < 2 * min_size:
        return []
    d = np.diff(x)
    sigma = 1.4826 * float(np.median(np.abs(d - np.median(d)))) / math.sqrt(2)
    if sigma < 1e-6:
        sigma = float(np.std(x)) or 1e-3
    pen = beta * sigma**2 * math.log(n)
    bkps = rpt.Pelt(model="l2", min_size=min_size, jump=1).fit(x.reshape(-1, 1)).predict(pen=pen)
    edges = [0, *bkps]
    found = []
    for k in range(1, len(edges) - 1):
        lo, b, hi = edges[k - 1], edges[k], edges[k + 1]
        before, after = float(x[lo:b].mean()), float(x[b:hi].mean())
        delta = after - before
        if abs(delta) < min_delta:
            continue
        found.append({
            "date": dates[b].isoformat(), "index": b, "direction": "상승" if delta > 0 else "하강",
            "delta": round(abs(delta), 3), "before": round(before, 3), "after": round(after, 3),
        })
    found.sort(key=lambda c: -c["delta"])
    return sorted(found[:max_points], key=lambda c: c["index"])


def periodicity(counts: np.ndarray, dates: list[date]) -> dict[str, Any]:
    """주 단위 주기(STL, period=7)의 세기와 요일별 분포. 2년 이상이면 연 주기(MSTL)도 본다."""
    y = np.log1p(np.asarray(counts, dtype=float))
    n = len(y)
    out: dict[str, Any] = {"days": n}
    by_wd = [[] for _ in range(7)]
    for d, c in zip(dates, counts):
        by_wd[d.weekday()].append(float(c))
    profile = [round(float(np.mean(v)), 2) if v else 0.0 for v in by_wd]
    out["weekday_profile"] = dict(zip(WEEKDAYS, profile))
    out["weekday_peak"] = WEEKDAYS[int(np.argmax(profile))]
    if n >= 21 and np.std(y) > 0:
        from statsmodels.tsa.seasonal import STL

        res = STL(y, period=7, robust=True).fit()
        var_r = float(np.var(res.resid))
        var_sr = float(np.var(res.seasonal + res.resid))
        out["weekly_strength"] = round(max(0.0, 1 - var_r / var_sr) if var_sr > 0 else 0.0, 3)
    else:
        out["weekly_strength"] = None
    # 자기상관이 가장 큰 시차 (Storyseed 의 periodicity 와 같은 정의, 2~21일)
    mu = float(np.mean(counts))
    den = float(np.sum((counts - mu) ** 2))
    best = (0, 0.0)
    for lag in range(2, min(21, n // 2) + 1):
        num = float(np.sum((counts[:-lag] - mu) * (counts[lag:] - mu)))
        r = num / den if den else 0.0
        if r > best[1]:
            best = (lag, r)
    out["lag_days"], out["strength"] = best[0], round(best[1], 3)
    if n >= 730:
        from statsmodels.tsa.seasonal import MSTL

        res = MSTL(y, periods=(7, 365)).fit()
        seas = res.seasonal[:, 1] if res.seasonal.ndim == 2 else res.seasonal
        var_r = float(np.var(res.resid))
        out["yearly_strength"] = round(max(0.0, 1 - var_r / float(np.var(seas + res.resid))), 3)
    else:
        out["yearly_strength"] = None
        out["yearly_note"] = "연 주기는 기록이 2년 이상일 때 계산합니다"
    return out


def hour_profile(records: list[Record]) -> dict[str, Any]:
    """시간대 분포. 건강 하루 요약은 시각이 23:59 로 정해져 있어 뺀다."""
    hours = [r.dt.hour + r.dt.minute / 60 for r in records if getattr(r, "kind", None) != "health"]
    if not hours:
        return {"center": None, "histogram": [0] * 24}
    ang = np.array(hours) / 24 * 2 * math.pi
    c = math.atan2(float(np.sin(ang).mean()), float(np.cos(ang).mean())) / (2 * math.pi) * 24
    c = c + 24 if c < 0 else c
    hist = Counter(int(h) % 24 for h in hours)
    return {"center": round(c, 1), "histogram": [hist.get(h, 0) for h in range(24)]}


def band(hour: float | None) -> str:
    if hour is None:
        return ""
    return ("새벽" if hour < 5 else "이른 아침" if hour < 8 else "오전" if hour < 12 else "한낮" if hour < 15
            else "늦은 오후" if hour < 18 else "저녁" if hour < 21 else "밤")


def ko_date(d: date | datetime | str) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return f"{d.month}월 {d.day}일"
