"""이야기·대화 프롬프트 만들기 (근거 계약 v1·v2).

노벨 화면(내 기록 모드)과 평가 스크립트가 같은 함수를 쓴다. 브라우저는 여기서 받은 프롬프트로
OpenRouter 를 스트리밍 호출하고, 끝나면 verify 모듈로 앵커를 검사받는다.
"""
from __future__ import annotations

import json
import random
from typing import Any

from . import patterns
from .prompts_storyseed import AVATAR_BASE, AVATAR_RULE2_V1, GENRES, MOODS, RULE7_V1, SPARKS, WRITER_BASE

RULE7_V2 = (
    "7. 취재 노트의 records 는 번호(n)가 붙은 실제 기록이다. 기록에서 가져온 사실(날짜, 요일, 시각, 횟수, 기록에 나온 "
    "활동·장소·물건)을 쓸 때만 <em ref=\"번호\">…</em> 으로 감싸라. ref 에는 그 사실이 나온 기록 번호를 쓰고, 여러 기록을 "
    "합친 사실(예: 세 번)이면 번호를 쉼표로 이어라. <em> 안에는 그 기록에 실제로 있는 값만 넣어라. 날짜·요일·시각을 쓸 때는 "
    "그때 한 일(활동·장소·물건)까지 한 <em> 안에 함께 넣어라. 예: <em ref=\"3\">7월 13일 5km 완주</em>. 한 <em> 안의 값은 "
    "모두 같은 기록에 있어야 한다. 기록의 날짜에 기록에 없는 사건을 붙이지 마라. 이야기 전체에 세 번에서 여섯 번이면 충분하다. "
    "지어낸 부분에는 절대 쓰지 마라. 그 외 HTML·마크다운 금지."
)
RULE_PEOPLE = (
    "\n9. 기록 속 \"지인A\" 같은 표기는 가명이다. 이야기에서는 가명 표기를 절대 쓰지 말고, people 에 관계가 적힌 사람은 "
    "그 관계(예: 회사 동기)로, 관계가 없는 사람은 \"친구\"나 \"그 사람\"처럼 불러라. 엄마처럼 관계로 적힌 사람은 그대로 불러도 된다. "
    "\"(나)\"는 기록 주인의 이름을 가린 표기이고 \"(누군가)\"는 다른 사람의 이름을 가린 표기다. 둘 다 그대로 쓰지 말고 "
    "문맥에 맞게 나·너·그 사람으로 바꿔라."
)
INSUFFICIENT = "\n\n근거가 부족하다. 기록의 흔적을 사실처럼 단정하지 말고 <em>은 한 번 이하로만 써라."
AVATAR_RULE2_V2 = (
    "2. 밋밋한 요약 금지. 기록을 말할 땐 장면으로 말해라. 장면의 세부는 지어내도 되지만 기록과 모순되면 안 된다. "
    "기록에서 가져온 값(날짜, 요일, 시각, 횟수, 기록에 나온 활동·장소·물건)은 <em ref=\"번호\">…</em> 으로 감싸고, "
    "ref 에는 아래 기록 목록의 번호를 써라. 날짜·시각을 말할 때는 그때 한 일까지 한 <em> 안에 넣고, 그 값은 모두 같은 기록에 "
    "있어야 한다. "
    "숫자를 들이밀며 지적하는 팩트폭행은 금지."
)


def writer_system(genre: int, version: str = "v2", core: bool = True) -> str:
    text = WRITER_BASE.format(genre=GENRES[genre][1])
    if version == "v2":
        text = text.replace(RULE7_V1, RULE7_V2)
    return text + (RULE_PEOPLE if core else "")


def pick_genre(genre: int | None, rng: random.Random) -> int:
    return rng.randrange(len(GENRES)) if genre is None or genre < 0 else genre


def _record_line(n: int | None, v: dict[str, Any], synthetic: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if n is not None:
        out["n"] = n
    out.update({"date": v["date_ko"], "weekday": v["weekday"] + "요일", "time": v["time"], "source": v["source"]})
    if synthetic and v.get("day_index"):
        out["day"] = f"{v['day_index']}일째"
    if v.get("room"):
        out["with"] = v["room"]
    out["author"] = v.get("author") or "나"
    if v.get("text"):
        out["text"] = v["text"]
    if v.get("photo_tags"):
        out["photo"] = ", ".join(v["photo_tags"])
    return out


def story_context(result: dict[str, Any], version: str = "v2") -> tuple[dict[str, Any], list[str]]:
    """서사 오브젝트와 근거 기록으로 취재 노트를 만든다. 반환: (노트, 번호 순 기록 ID)."""
    no = result["narrative_object"]
    st = no.get("stats") or {}
    em = no.get("emotion") or {}
    recs = result.get("evidence_records", []) + result.get("adjacent_records", [])[:8]
    synthetic = any(r.get("source") == "활동 기록" for r in recs)
    numbered = version == "v2"
    note: dict[str, Any] = {
        "keyword": no["keyword"],
        "evidence_grade": no["evidence_grade"],
        "summary": {
            "distinct_days": st.get("distinct_days"),
            "first_date": patterns.ko_date(st["first_date"]) if st.get("first_date") else None,
            "last_date": patterns.ko_date(st["last_date"]) if st.get("last_date") else None,
            "time_band": st.get("time_band"),
            "top_terms": [t["term"] for t in no.get("top_terms", [])],
            "photo_tags": [t["tag"] for t in no.get("top_tags", [])],
            "mood": {"mean": em.get("mean"), "baseline": em.get("baseline")},
            "turning_points": [{"date": patterns.ko_date(c["date"]), "direction": c["direction"]}
                               for c in no.get("change_points", [])],
        },
        "records": [_record_line(i + 1 if numbered else None, v, synthetic) for i, v in enumerate(recs)],
        "people": {a: (r or "관계 모름: 친구나 그 사람으로 부를 것") for a, r in (result.get("people") or {}).items()},
    }
    return note, [v["id"] for v in recs]


def story_prompt(result: dict[str, Any], genre: int | None = None, version: str = "v2", variation: int = 0,
                 seed: int | None = None) -> dict[str, Any]:
    rng = random.Random(seed)
    g = pick_genre(genre, rng)
    spark = rng.choice(SPARKS)
    note, ids = story_context(result, version)
    grade = result["narrative_object"]["evidence_grade"]
    user = (f"씨앗 단어: {note['keyword']}\n장르: {GENRES[g][0]} — 장르적 재미가 최우선이다. 사건과 긴장을 만들고, 끝은 반드시 갚아라."
            + (f"\n오늘의 비틀기(반드시 반영): {spark}" if spark else "")
            + f"\n근거 등급: {grade}\n\n취재 노트(JSON):\n{json.dumps(note, ensure_ascii=False)}")
    if grade == "근거 부족":
        user += INSUFFICIENT
    if variation > 0:
        user += f"\n\n변주 #{variation + 1}: 이전과 완전히 다른 설정과 도입으로 다시 써라. 골라 쓰는 흔적도 바꿔 보라."
    return {"system": writer_system(g, version), "user": user, "genre": g, "genre_name": GENRES[g][0],
            "version": version, "evidence_ids": ids}


def avatar_prompt(profile: dict[str, Any], records: list[dict[str, Any]], people: dict[str, str | None],
                  version: str = "v2", mood: int | None = None, seed: int | None = None) -> dict[str, Any]:
    """Talk 모드 시스템 프롬프트. records 는 이번 질문과 가까운 기록 + 최근 기록 (record_view 형식)."""
    rng = random.Random(seed)
    m = MOODS[mood] if mood is not None else rng.choice(MOODS)
    synthetic = any(r.get("source") == "활동 기록" for r in records)
    ctx = {
        "period": profile.get("date_range"),
        "records_total": profile.get("records"),
        "time_center": profile.get("time_center"),
        "top_terms": profile.get("top_terms"),
        "turning_points": profile.get("turning_points"),
        "records": [_record_line(i + 1 if version == "v2" else None, r, synthetic) for i, r in enumerate(records)],
        "people": {a: (r or "관계 모름: 친구나 그 사람으로 부를 것") for a, r in people.items()},
    }
    base = AVATAR_BASE.replace("90일 활동 기록", "기록").replace("나의 90일 기록", "나의 기록")
    text = base.format(mood=m, context=json.dumps(ctx, ensure_ascii=False))
    if version == "v2":
        text = text.replace(AVATAR_RULE2_V1, AVATAR_RULE2_V2)
        text = text.replace("<em>반신욕</em>", "반신욕").replace("<em>11시</em>", "11시").replace("<em>77일째</em>", "77일째")
    text += RULE_PEOPLE.replace("\n9.", "\n7.").replace("이야기에서는", "대화에서는")
    return {"system": text, "evidence_ids": [r["id"] for r in records], "mood": m}


def greeting(profile: dict[str, Any]) -> str:
    c = profile.get("time_center")
    b = patterns.band(c)
    hour = f"{round(c)}시" if c is not None else ""
    return (f"왔네. ...뭐, 기다린 건 아니고. 나야, 기록 속의 너. {b} {hour}쯤이면 휴대폰 붙들고 있던 그 사람. "
            "그래서, 오늘은 무슨 일 있었는데?")
