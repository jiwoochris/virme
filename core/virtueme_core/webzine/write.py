"""웹진 꼭지의 글. LLM 이 쓰고, 근거 계약 v2 검사기가 앵커를 기록·사실과 대조한다.

- 기사·다큐·패션·디지털 프리뷰: 한 번의 요청으로 꼭지별 제목·리드·본문을 JSON 으로 받는다.
- 시: 이번 주 혼잣말형 기록(게시물·스토리·밤에 쓴 글)을 재료로 쓴다.
- 소설: 노벨 화면과 같은 프롬프트(Service.story_prompt)로 이번 주 대표 낱말을 씨앗 삼아 쓴다.
키가 없거나 호출이 실패하면 사실 문장만으로 꼭지를 짜는 로컬 경로(offline)로 간다. 이때는 지어낸 문장이 없다.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .. import patterns
from ..config import openrouter_key
from ..narrative import record_view
from ..privacy import Masker
from ..story import RULE_PEOPLE, _record_line
from ..textproc import top_nouns
from ..verify import verify_story

STYLE_MODEL = "anthropic/claude-sonnet-4.5"
EM_RE = re.compile(r"</?em[^>]*>")

EDITOR_SYSTEM = (
    "너는 한 사람의 일주일 기록으로 개인 웹진 한 호를 만드는 편집장이다. 독자는 기록의 주인 자신이다. "
    "취재 노트의 records 는 번호(n)가 붙은 실제 기록이고, facts 는 기록에서 계산한 사실이며 역시 번호(n)가 있다.\n"
    "규칙:\n"
    "1. 기록이나 사실에서 가져온 값(날짜, 요일, 시각, 횟수, 숫자, 장소, 활동, 물건)을 쓸 때만 <em ref=\"번호\">…</em> 으로 감싸라. "
    "ref 에는 그 값이 나온 기록이나 사실의 번호를 쓰고, 날짜·시각을 쓸 때는 그때 한 일까지 한 <em> 안에 넣어라. "
    "숫자는 사실에 적힌 값을 그대로 쓰거나 반올림만 해라.\n"
    "2. records 와 facts 어디에도 없는 숫자·날짜·시각·비교는 지어내지 마라. 해석과 분위기는 써도 되지만 사실처럼 단정하지 마라.\n"
    "3. 제목은 잡지 표지처럼 짧고 선명하게, 본문은 합쇼체가 아닌 잡지 기사체(-다)로 써라. 이모지·해시태그·마크다운 금지.\n"
    "4. facts 의 「이름을 붙이지 않은 한 장소」·「이름 없는 장소」는 이름이 아니다. 제목이나 장소 이름처럼 쓰지 말고, 그 장소는 머문 시간과 그곳 사진으로만 말해라.\n"
    "5. 기록의 주인을 \"그녀\"·\"그\"로 부르지 말고 \"나\" 또는 \"당신\"으로 불러라. 꼭지마다 하나를 정해 끝까지 지켜라."
    + RULE_PEOPLE.replace("\n9.", "\n6.")
)

SECTION_SPEC = {
    "article": "이번 주의 나 (셀프 포트레이트 기사): kicker(8자 안 영문 대문자 아님, 한국어 소제목), headline(15자 안), "
               "deck(한 문장), body(문단 3개, 각 2~3문장, 사람과 대화 중심)",
    "documentary": "다녀온 곳 (다큐멘터리 피처): headline(15자 안), deck(한 문장), body(문단 2개, 장소와 머문 시간, 사진 중심)",
    "fashion": "이번 주의 룩 (패션 리포트): headline(12자 안), deck(한 문장), body(문단 1개), "
               "looks(2~4개, 각 {title: 8자 안, line: 한 문장})",
    "digital_preview": "보고 들은 것 (디지털 프리뷰): headline(12자 안), deck(한 문장), body(문단 1개), "
                       "items(3~4개, 각 {title: 12자 안, line: 한 문장}, 영상·음악·검색에서 하나씩)",
}


def _note(service, fx: dict[str, Any], section_keys: list[str], k_records: int = 40) -> tuple[dict[str, Any], list]:
    """번호 붙은 취재 노트와 번호 순 근거 목록 (기록 Record 또는 사실 dict)."""
    masker = Masker.from_store(service.store)
    by_id = {r.id: r for r in fx["records"]}
    want: list[str] = []
    for f in fx["facts"]:
        if f["section"] in section_keys or f["headline"]:
            want.extend(f["evidence"][:4])
    for r in fx["monologue"][:8]:
        want.append(r.id)
    seen, recs = set(), []
    for i in want:
        if i in by_id and i not in seen:
            seen.add(i)
            recs.append(by_id[i])
        if len(recs) >= k_records:
            break
    recs.sort(key=lambda r: r.ts)
    evidence: list = []
    lines = []
    for r in recs:
        evidence.append(r)
        lines.append(_record_line(len(evidence), record_view(r, masker, chars=280), False))
    facts = []
    for f in fx["facts"]:
        evidence.append(f)
        values = {k: ([patterns.ko_date(d) for d in v] if k == "dates" else v) for k, v in f["values"].items()}
        facts.append({"n": len(evidence), "about": f["about"], "section": f["section"], "text": f["text"], "values": values})
    people = {c["alias"]: c["who"] for c in fx["cast"]}
    for a, rel in (fx.get("relations") or {}).items():
        people.setdefault(a, rel or "관계 모름: 친구나 그 사람으로 부를 것")
    note = {"period": f"{patterns.ko_date(fx['range'][0])}~{patterns.ko_date(fx['range'][1])}", "records": lines,
            "facts": facts, "people": people}
    return note, evidence


def _paras_check(paras: list[str], evidence: list, service) -> tuple[list[str], dict[str, int]]:
    if not paras:
        return [], {"anchors": 0, "ok": 0, "fixed": 0}
    res = verify_story(paras, evidence, mode="blur", start=None, people=service.store.relations())
    return list(res["paragraphs_html"]), _stats(res)


def _stats(res: dict[str, Any]) -> dict[str, int]:
    st = res.get("stats") or {}
    ok = st.get("ok", 0) + st.get("ref_fixed", 0) + st.get("unreferenced_ok", 0)
    return {"anchors": st.get("anchors", 0), "ok": ok, "blurred": st.get("failed", 0) + st.get("context_mismatch", 0),
            "loose_unsupported": st.get("loose_unsupported", 0)}


def editorial(service, fx: dict[str, Any], sections: list[str], log=print) -> dict[str, Any]:
    keys = [k for k in sections if k in SECTION_SPEC]
    note, evidence = _note(service, fx, keys)
    spec = "\n".join(f"- {k}: {SECTION_SPEC[k]}" for k in keys)
    user = (f"이번 호의 기간: {note['period']}\n\n만들 꼭지와 형식:\n{spec}\n"
            "- cover: 표지. headline(12자 안, 이번 호 전체를 꿰는 말), lines(표지에 얹을 짧은 문장 3개, 각 25자 안, "
            "facts 가운데 놀라운 것에서)\n\n"
            "JSON 하나로만 답하라. 키는 꼭지 이름(cover 포함)이고 값은 위 형식의 객체다. body 는 문자열 배열이다.\n\n"
            f"취재 노트(JSON):\n{json.dumps(note, ensure_ascii=False)}")
    if not openrouter_key():
        return {"offline": True, "sections": {}, "evidence_n": len(evidence)}
    from ..llm import chat_json

    try:
        raw = chat_json(EDITOR_SYSTEM, user, model=STYLE_MODEL, temperature=0.7, max_tokens=4000, timeout=180)
    except Exception as err:  # noqa: BLE001 — 실패하면 로컬 경로
        log(f"편집 글 실패, 사실 문장으로 대신합니다: {err}")
        return {"offline": True, "sections": {}, "error": str(err)}
    out: dict[str, Any] = {"offline": False, "sections": {}, "checks": {}}
    for k, v in (raw or {}).items():
        if not isinstance(v, dict):
            continue
        v = dict(v)
        if isinstance(v.get("body"), list):
            v["body"], out["checks"][k] = _paras_check([str(p) for p in v["body"]], evidence, service)
        for key in ("headline", "deck", "kicker"):
            if isinstance(v.get(key), str):
                v[key] = EM_RE.sub("", v[key])
        for lst in ("lines", "looks", "items"):
            if isinstance(v.get(lst), list):
                v[lst] = [({kk: EM_RE.sub("", str(vv)) for kk, vv in x.items()} if isinstance(x, dict) else EM_RE.sub("", str(x)))
                          for x in v[lst]]
        out["sections"][k] = v
    return out


POEM_SYSTEM = (
    "너는 한 사람이 일주일 동안 혼잣말처럼 남긴 글로 짧은 시 한 편을 쓰는 시인이다. 재료는 records 의 글뿐이다. "
    "기록에 있는 낱말과 장면을 쓰고, 기록에 없는 사건·숫자·날짜는 넣지 마라. 날짜·시각·숫자는 아예 쓰지 않는 편이 낫다. "
    "행은 8~14행, 연은 2~3연. 제목은 10자 안. 이모지·해시태그 금지. 가명(지인A)과 (나)·(누군가) 표기는 쓰지 마라. "
    "JSON 으로만 답하라: {\"title\": 제목, \"stanzas\": [[행, 행, ...], ...], \"epigraph\": 기록에서 그대로 옮긴 한 줄}"
)


def poem(service, fx: dict[str, Any], log=print) -> dict[str, Any]:
    mats = (fx["monologue"] + fx["night_written"] + [r for r in fx["records"] if r.is_self and r.kind == "message"
                                                    and len(r.text or "") >= 12])[:30]
    masker = Masker.from_store(service.store)
    lines = [masker.mask(r.text) for r in mats if r.text]
    if not lines:
        return {}
    found = _found_poem(lines)
    if not openrouter_key():
        return {**found, "offline": True}
    from ..llm import chat_json

    try:
        got = chat_json(POEM_SYSTEM, json.dumps({"records": lines}, ensure_ascii=False), model=STYLE_MODEL,
                        temperature=0.9, max_tokens=1200)
    except Exception as err:  # noqa: BLE001
        log(f"시 쓰기 실패, 기록 문장으로 대신합니다: {err}")
        return {**found, "offline": True}
    st = [[EM_RE.sub("", str(x)) for x in s] for s in got.get("stanzas") or [] if isinstance(s, list)]
    # 숫자·날짜가 섞이면 기록과 대조할 수 없으므로 그 행을 뺀다
    st = [[ln for ln in s if not re.search(r"\d", ln)] for s in st]
    st = [s for s in st if s]
    if not st:
        return {**found, "offline": True}
    epi = str(got.get("epigraph") or "")
    if epi and not any(epi.strip() in l for l in lines):
        epi = found["epigraph"]
    return {"title": EM_RE.sub("", str(got.get("title") or found["title"])), "stanzas": st, "epigraph": epi,
            "offline": False, "sources": len(lines)}


def _found_poem(lines: list[str]) -> dict[str, Any]:
    """키가 없을 때: 기록 속 문장을 그대로 행으로 놓는 발견 시. 모든 행이 기록 원문이다."""
    clean = []
    for t in lines:
        for part in re.split(r"[\n.!?]+", re.sub(r"#\S+", "", t)):
            p = part.strip(" ~ㅠㅋ")
            if 4 <= len(p) <= 28 and not re.search(r"\d|지인|\(나\)|\(누군가\)", p) and p not in clean:
                clean.append(p)
    clean = clean[:12]
    half = max(1, len(clean) // 2)
    title = (top_nouns(clean, k=1) or [("기록", 0)])[0][0]
    return {"title": title, "stanzas": [clean[:half], clean[half:]] if len(clean) > 3 else [clean],
            "epigraph": clean[0] if clean else "", "sources": len(lines)}


def novel_keyword(service, fx: dict[str, Any]) -> str | None:
    own = [r.text for r in fx["records"] if r.is_self and r.text and not r.is_trace]
    cands = [t for t, _ in top_nouns(own, k=12) if len(t) >= 2]
    for t in cands[:5]:
        try:
            g = service.query(t)["narrative_object"]["evidence_grade"]
        except Exception:  # noqa: BLE001
            continue
        if g != "근거 부족":
            return t
    return cands[0] if cands else None


def novel(service, fx: dict[str, Any], log=print) -> dict[str, Any]:
    kw = novel_keyword(service, fx)
    if not kw:
        return {}
    if not openrouter_key():
        return {"keyword": kw, "offline": True}
    from ..llm import chat_text

    try:
        cols = service.columns()
        genre = cols["genre_hint"][0]["index"] if cols.get("genre_hint") else 6
        p = service.story_prompt(kw, genre)
        prompt = p["prompt"]
        raw = chat_text(prompt["system"], prompt["user"], model=STYLE_MODEL, temperature=0.9, max_tokens=3000, timeout=240)
    except Exception as err:  # noqa: BLE001
        log(f"소설 쓰기 실패: {err}")
        return {"keyword": kw, "offline": True, "error": str(err)}
    title, paras = _split_story(raw)
    checked = service.verify(paras, prompt["evidence_ids"], mode="blur", title=title)
    return {"keyword": kw, "genre": prompt.get("genre_name"), "title": title,
            "paragraphs": checked["paragraphs_html"], "offline": False, "check": _stats(checked)}


def _split_story(raw: str) -> tuple[str, list[str]]:
    lines = [l.strip() for l in raw.strip().splitlines()]
    title = "무제"
    if lines and lines[0].upper().startswith("TITLE:"):
        title = EM_RE.sub("", lines[0].split(":", 1)[1].strip())
        lines = lines[1:]
    paras = [l for l in lines if l]
    return title, paras


def safe_lines(lines: list[str], evidence: list, service) -> list[str]:
    """앵커 없는 짧은 문장(표지 문구·항목 설명)에서 기록·사실로 확인되지 않는 수치·날짜가 있는 문장을 뺀다."""
    out = []
    for ln in lines:
        ln = EM_RE.sub("", str(ln)).strip()
        if not ln:
            continue
        res = verify_story([ln], evidence, mode="plain", start=None, people=service.store.relations())
        if all(x["supported"] for x in res.get("loose") or []):
            out.append(ln)
    return out


KIND_LINE = {"payment": "결제", "search": "검색", "photo": "사진", "post": "게시물", "story": "스토리", "message": "대화",
             "watch": "시청"}


def offline_section(key: str, fx: dict[str, Any]) -> dict[str, Any]:
    """키가 없을 때 꼭지 글: 그 꼭지의 사실 문장을 그대로 싣는다. 지어낸 문장이 없다."""
    mine = [f["text"] for f in fx["facts"] if f["section"] == key]
    heads = {"article": "이번 주의 나", "documentary": "다녀온 곳", "fashion": "이번 주의 룩", "digital_preview": "보고 들은 것"}
    out: dict[str, Any] = {"headline": heads.get(key, ""), "deck": mine[0] if mine else "", "body": mine[1:4] or mine[:1]}
    t = fx["taste"]
    if key == "digital_preview":
        out["items"] = ([{"title": v, "line": "이번 주에 본 영상"} for v in (t.get("videos") or [])[:2]]
                        + [{"title": v, "line": "이번 주에 들은 곡"} for v in (t.get("tracks") or [])[:2]])
    if key == "fashion":
        looks = []
        for r in fx.get("fashion") or []:
            title = r.meta.get("merchant") or r.meta.get("query") or r.meta.get("title") or ("사진" if r.kind == "photo" else KIND_LINE.get(r.kind, "기록"))
            amt = f" · {int(float(r.meta['amount'])):,}원" if r.kind == "payment" and r.meta.get("amount") else ""
            looks.append({"title": str(title)[:14], "line": f"{patterns.ko_date(r.day)} {KIND_LINE.get(r.kind, '기록')}{amt}"})
        out["looks"] = looks[:4]
    return out
