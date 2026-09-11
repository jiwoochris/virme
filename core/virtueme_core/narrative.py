"""서사 오브젝트 `virtueme.narrative/1` 과 LLM 에 넘길 근거 기록 목록을 만든다.

서사 오브젝트는 세 렌더러(이야기·시각화·아트웍)가 공통으로 읽는 입력이다. 원문 텍스트와 사진은
넣지 않고 기록 ID 와 파생 값만 담는다. 이야기 렌더러가 쓸 원문은 `evidence_records` 로 따로 주며,
이 목록의 텍스트는 privacy.Masker 를 거친 것이다.

signals (비주얼 모드가 받는 7개 값, 모두 0..1)
- stability   : 근거 기간 동안 날짜별 정서가 얼마나 고르게 유지됐는가 (1 - 하루 변화폭 표준편차 / 0.5)
- novelty     : 이 주제가 최근에 새로 늘었는가 (전체 기간의 마지막 30% 안에 든 근거 비율 / 0.6)
- conflict    : 이 주제를 둘러싼 감정이 엇갈리는가 (근거 기록 정서가의 표준편차 / 0.6)
- uncertainty : 판단이 불확실한 정도 (기록이 드문 날의 비율, 등급이 낮을수록 높음)
- possibility : 이 주제의 감정이 좋아지는 쪽인가 (근거 후반부 정서 평균 - 전반부 평균, 0.5 중심)
- activity    : 이 주제가 기록에 나온 날의 비율 (근거가 나온 날 / 기록이 있는 날 × 2)
- confidence  : 근거 등급과 기록 밀도로 정한 확신도
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date
from functools import lru_cache
from typing import Any

import numpy as np

from . import clusters as clustering
from . import patterns
from .config import SCHEMA_DIR, SETTINGS
from .privacy import Masker
from .query import QueryEngine, QueryResult, Scored
from .records import Record
from .textproc import top_nouns

SCHEMA_ID = "virtueme.narrative/1"
SOURCE_LABEL = {"kakao": "카카오톡", "sms": "문자", "instagram": "인스타그램", "photo": "사진", "synthetic": "활동 기록"}
KIND_LABEL = {"message": "메시지", "post": "게시물", "story": "스토리", "reel": "릴스", "comment": "댓글",
              "photo": "사진", "activity": "활동"}


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _time_ko(r: Record) -> str:
    h, m = r.dt.hour, r.dt.minute
    ampm = "오전" if h < 12 else "오후"
    h12 = h % 12 or 12
    return f"{ampm} {h12}:{m:02d}"


def record_view(r: Record, masker: Masker, rel: float | None = None, chars: int | None = None) -> dict[str, Any]:
    """밖으로 내보내는 기록 한 건. 좌표는 빼고 텍스트는 가린다."""
    chars = chars or SETTINGS.context_text_chars
    text = masker.mask(r.text) if r.text else None
    if text and len(text) > chars:
        text = text[: chars - 1] + "…"
    out: dict[str, Any] = {
        "id": r.id,
        "date": r.day,
        "date_ko": patterns.ko_date(r.day),
        "weekday": patterns.WEEKDAYS[r.dt.weekday()],
        "time": _time_ko(r),
        "hour": r.dt.hour,
        "source": SOURCE_LABEL.get(r.source, r.source),
        "kind": KIND_LABEL.get(r.kind, r.kind),
        "author": r.author or "나",
        "text": text,
    }
    auto = [t["tag"] for t in r.meta.get("auto_tags", [])]
    if auto:
        out["photo_tags"] = auto
    if r.source == "synthetic":
        out["day_index"] = r.meta.get("day_index")
        out["channel"] = r.meta.get("channel")
    if r.meta.get("room"):
        out["room"] = r.meta["room"]
    if rel is not None:
        out["relevance"] = round(rel, 3)
    return out


def _signals(engine: QueryEngine, res: QueryResult, ev: list[Scored], vals: list[float]) -> dict[str, float]:
    d = engine.daily
    grade = res.grade
    base_conf = {"강한 근거": 0.85, "약한 근거": 0.6, "근거 부족": 0.3}[grade]
    if not ev or d is None:
        return {"stability": 0.5, "novelty": 0.0, "conflict": 0.0, "uncertainty": 0.9,
                "possibility": 0.5, "activity": 0.0, "confidence": round(base_conf * 0.7, 3)}
    days = sorted({s.record.dt.date() for s in ev})
    lo, hi = d.index(days[0]), d.index(days[-1]) + 1
    seg = d.valence[lo:hi]
    stability = 1 - min(1.0, float(np.std(np.diff(seg))) / 0.5) if len(seg) > 2 else 0.5
    total_span = (d.dates[-1] - d.dates[0]).days or 1
    cutoff = d.dates[0].toordinal() + 0.7 * total_span
    recent = sum(1 for s in ev if s.record.dt.date().toordinal() >= cutoff) / len(ev)
    novelty = _clip01(recent / 0.6)
    conflict = _clip01(float(np.std(vals)) / 0.6) if len(vals) > 1 else 0.0
    sparse_share = float(np.mean(d.sparse[lo:hi])) if hi > lo else 1.0
    uncertainty = _clip01(0.6 * sparse_share + {"강한 근거": 0.0, "약한 근거": 0.25, "근거 부족": 0.5}[grade])
    ordered = [engine.emotions[s.record.id][0] for s in sorted(ev, key=lambda s: s.record.ts) if s.record.id in engine.emotions]
    if len(ordered) >= 4:
        half = len(ordered) // 2
        possibility = _clip01(0.5 + 1.5 * (float(np.mean(ordered[half:])) - float(np.mean(ordered[:half]))))
    else:
        possibility = 0.5
    activity = _clip01(res.basis["strong_day_share"] * 2)
    confidence = base_conf * (1 - 0.3 * sparse_share)
    return {k: round(v, 3) for k, v in {
        "stability": stability, "novelty": novelty, "conflict": conflict, "uncertainty": uncertainty,
        "possibility": possibility, "activity": activity, "confidence": confidence,
    }.items()}


def build(engine: QueryEngine, res: QueryResult, masker: Masker | None = None,
          limit: int | None = None) -> dict[str, Any]:
    """{narrative_object, evidence_records, adjacent_records} 를 돌려준다."""
    masker = masker or Masker.from_store(engine.store)
    limit = limit or SETTINGS.context_record_limit
    d = engine.daily
    profile_cps = patterns.change_points(d.valence, d.dates) if d else []
    scope_ids = {r.id for r in res.scope}
    own_vals = [engine.emotions[i][0] for i in scope_ids if i in engine.emotions]
    # 세 화면(이야기·시각화·비주얼)이 같은 결과를 보고 있는지 확인하는 번호. 같은 키워드·같은 근거면 같은 번호가 나온다
    nid = hashlib.sha1("|".join([str(engine.store.path.name), res.keyword, res.grade,
                                 *sorted(s.record.id for s in res.strong)]).encode("utf-8")).hexdigest()[:8]
    base: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "narrative_id": nid,
        "keyword": res.keyword,
        "evidence_grade": res.grade,
        "grade_basis": res.basis,
        "data_scope": {
            "records": len(engine.records),
            "self_records": sum(1 for r in engine.records if r.is_self),
            "date_range": [d.dates[0].isoformat(), d.dates[-1].isoformat()] if d else None,
            "sources": dict(Counter(SOURCE_LABEL.get(r.source, r.source) for r in engine.records)),
        },
        "expansions": res.expansions,
    }
    if res.grade == "근거 부족":
        base.update({
            "message": "기록이 충분하지 않습니다",
            "evidence": {"strong": [s.record.id for s in res.strong], "weak": [s.record.id for s in res.weak[:20]],
                         "adjacent": []},
            "signals": _signals(engine, res, res.strong, []),
        })
        views = [record_view(s.record, masker, s.rel) for s in (res.strong + res.weak)[:12]]
        return {"narrative_object": base, "evidence_records": views, "adjacent_records": [],
                "people": people_in(engine.store, views)}

    ev = res.strong
    vals = [engine.emotions[s.record.id][0] for s in ev if s.record.id in engine.emotions]
    days = sorted({s.record.dt.date() for s in ev})
    by_day = Counter(s.record.day for s in ev)
    hours = patterns.hour_profile([s.record for s in ev])
    tags = Counter(t for s in ev for t in s.record.tags + [a["tag"] for a in s.record.meta.get("auto_tags", [])])
    series = []
    if d:
        lo, hi = d.index(days[0]), d.index(days[-1]) + 1
        for i in range(lo, hi):
            series.append({
                "date": d.dates[i].isoformat(), "valence": round(float(d.valence_smooth[i]), 3),
                "arousal": round(float(d.arousal[i]), 3), "evidence": by_day.get(d.dates[i].isoformat(), 0),
                "sparse": d.sparse[i],
            })
    ev_days = set(days)
    cps = []
    for c in profile_cps:
        cd = date.fromisoformat(c["date"])
        near = min((abs((cd - x).days) for x in ev_days), default=99)
        cps.append({**c, "near_evidence": near <= 3})
    counts = np.array([by_day.get(x.isoformat(), 0) for x in (d.dates if d else [])], dtype=float)
    per = patterns.periodicity(counts, d.dates) if d and counts.sum() > 0 else {}
    global_per = patterns.periodicity(d.count, d.dates) if d else {}
    base.update({
        "evidence": {
            "strong": [s.record.id for s in ev],
            "weak": [s.record.id for s in res.weak],
            "adjacent": [r.id for r in res.adjacent],
        },
        "stats": {
            "distinct_days": len(days),
            "span_days": (days[-1] - days[0]).days if days else 0,
            "first_date": days[0].isoformat(),
            "last_date": days[-1].isoformat(),
            "coverage_pct": round(100 * len(ev) / max(1, len(res.scope)), 1),
            "time_center": hours["center"],
            "time_band": patterns.band(hours["center"]),
            "peak_date": max(by_day.items(), key=lambda kv: (kv[1], kv[0]))[0],
            "peak_count": max(by_day.values()),
            "sources": dict(Counter(SOURCE_LABEL.get(s.record.source, s.record.source) for s in ev)),
        },
        "top_terms": [{"term": t, "count": c} for t, c in top_nouns(
            (masker.mask(s.record.text) or "" for s in ev if s.record.text), k=8)],
        "top_tags": [{"tag": t, "count": c} for t, c in tags.most_common(6)],
        "emotion": {
            "series": series,
            "mean": round(float(np.mean(vals)), 3) if vals else None,
            "min": round(float(np.min(vals)), 3) if vals else None,
            "max": round(float(np.max(vals)), 3) if vals else None,
            "baseline": round(float(np.mean(own_vals)), 3) if own_vals else None,
        },
        "change_points": cps,
        "periodicity": {
            "lag_days": per.get("lag_days"), "strength": per.get("strength"),
            "weekday_peak": per.get("weekday_peak"), "weekly_strength": global_per.get("weekly_strength"),
        },
        **clustering.summarize(engine.store, [s.record.id for s in ev]),
        "signals": _signals(engine, res, ev, vals),
    })
    # LLM 에 넘길 근거: 관련도 상위 2/3 + 나머지에서 시간 순으로 고르게 1/3
    top_n = (limit * 2) // 3
    picked = ev[:top_n]
    rest = sorted(ev[top_n:], key=lambda s: s.record.ts)
    if rest:
        step = max(1, len(rest) // max(1, limit - top_n))
        picked += rest[::step][: limit - top_n]
    picked.sort(key=lambda s: s.record.ts)
    evidence = [record_view(s.record, masker, s.rel) for s in picked]
    adjacent = [record_view(r, masker) for r in res.adjacent[:12]]
    return {
        "narrative_object": base,
        "evidence_records": evidence,
        "adjacent_records": adjacent,
        "people": people_in(engine.store, evidence + adjacent),
    }


ALIAS_RE = re.compile(r"지인[A-Z]{1,2}")


def people_in(store, views: list[dict[str, Any]]) -> dict[str, str | None]:
    """근거 기록에 나온 가명과, 사용자가 붙인 관계(없으면 null). 이야기에서 가명 대신 부를 호칭의 재료다."""
    rel = store.relations()
    seen: set[str] = set()
    for v in views:
        for field_ in ("author", "room", "text"):
            seen.update(ALIAS_RE.findall(str(v.get(field_) or "")))
    return {a: rel.get(a) for a in sorted(seen)}


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / "narrative.v1.schema.json").read_text(encoding="utf-8"))


def validate(obj: dict[str, Any]) -> None:
    import jsonschema

    jsonschema.validate(obj, schema())
