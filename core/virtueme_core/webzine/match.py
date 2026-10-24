"""규칙표(rules.json)로 한 호의 기록을 템플릿에 짝짓는다.

기록 한 건이 신호의 match 조건을 모두 채우면 그 신호의 weight 만큼 템플릿 점수에 더한다. 점수와 함께 신호별 건수와
근거 기록 ID 를 남겨서, 화면과 issue.json 에서 "왜 이 템플릿이 골렸는지"를 그대로 보여 준다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..records import Record

HERE = Path(__file__).parent
RULES = HERE / "rules.json"
WRITTEN_KINDS = ("message", "post", "story", "reel", "comment")


def load_rules(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or RULES).read_text("utf-8"))


def _photo_tags(r: Record, min_score: float = 0.3) -> set[str]:
    return {t["tag"] for t in r.meta.get("auto_tags", []) if t.get("score", 0) >= min_score}


def _text_of(r: Record) -> str:
    return " ".join(str(x) for x in (r.text, r.meta.get("query"), r.meta.get("title"), r.meta.get("merchant")) if x)


def matches(r: Record, m: dict[str, Any]) -> bool:
    if "kinds" in m and r.kind not in m["kinds"]:
        return False
    if m.get("self") and not r.is_self:
        return False
    if m.get("has_text") and not (r.text or "").strip():
        return False
    if m.get("written") and r.kind not in WRITTEN_KINDS:
        return False
    if "hours" in m and r.dt.hour not in m["hours"]:
        return False
    if "photo_tags" in m and not (_photo_tags(r) & set(m["photo_tags"])):
        return False
    if "categories" in m:
        cat = str(r.meta.get("category") or "")
        if not any(c in cat for c in m["categories"]):
            return False
    if "terms" in m:
        text = _text_of(r)
        if not any(t in text for t in m["terms"]):
            return False
    return True


def score(records: list[Record], rules: dict[str, Any]) -> dict[str, Any]:
    """템플릿마다 {score, signals:[{id,label,count,points,evidence}]}. 같은 기록이 여러 신호에 맞을 수 있다."""
    out: dict[str, dict[str, Any]] = {k: {"score": 0.0, "signals": []} for k in rules["templates"]}
    for sig in rules["signals"]:
        hit = [r for r in records if matches(r, sig["match"])]
        if not hit:
            continue
        pts = round(len(hit) * float(sig["weight"]), 2)
        t = out.setdefault(sig["template"], {"score": 0.0, "signals": []})
        t["score"] = round(t["score"] + pts, 2)
        t["signals"].append({"id": sig["id"], "label": sig["label"], "count": len(hit), "weight": sig["weight"],
                             "points": pts, "evidence": [r.id for r in hit[:12]]})
    return out


def pick(scores: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, Any]]:
    """실을 꼭지 목록 (rules.order 순서). 이야기 꼭지는 story_pick 규칙으로 소설·시 가운데 고른다."""
    tpl = rules["templates"]
    sp = rules.get("story_pick") or {}
    poem, novel = scores.get("poem", {}).get("score", 0.0), scores.get("novel", {}).get("score", 0.0)
    story = {"novel"}
    if poem >= sp.get("both_over", 1e9):
        story = {"novel", "poem"}
    elif poem >= sp.get("poem_over", 1e9) and poem > novel:
        story = {"poem"}
    picked = []
    for key in rules["order"]:
        t = tpl[key]
        s = scores.get(key, {"score": 0.0, "signals": []})
        if key in ("novel", "poem"):
            take = key in story
            why = ("시 점수가 기준을 넘어 시와 소설을 함께 싣습니다" if story == {"novel", "poem"}
                   else "혼잣말형 기록이 많아 시로 싣습니다" if story == {"poem"}
                   else "대화·게시물을 재료로 소설을 싣습니다") if take else "이야기 꼭지에서 다른 형식이 골렸습니다"
        elif t.get("always"):
            take, why = True, "모든 호에 싣는 꼭지입니다"
        else:
            take = s["score"] >= t.get("min_score", 0)
            why = (f"점수 {s['score']:g}점이 기준 {t.get('min_score', 0)}점 이상입니다" if take
                   else f"점수 {s['score']:g}점이 기준 {t.get('min_score', 0)}점에 못 미칩니다")
        picked.append({"template": key, "label": t["label"], "slot": t["slot"], "score": s["score"],
                       "signals": s["signals"], "included": take, "why": why})
    return picked
