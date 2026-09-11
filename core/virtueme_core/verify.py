"""근거 계약 v2: `<em ref="번호">…</em>` 앵커를 그 번호의 기록과 대조한다.

앵커 안의 글자를 주장 단위로 나눈다.
- 날짜: "7월 5일", "5일째"(합성 기록의 일차), 요일: "금요일"
- 시각: "오후 9시", "밤 10시 20분", "9:42". 분이 없으면 그 시각부터 한 시간 반 사이를 같은 시각으로 본다
- 횟수: "5km", "세 번". 기록 본문에 같은 숫자·단위가 있거나, 번·회·일은 참조한 기록 수·날 수와 같으면 통과
- 낱말: 나머지 명사. 글자나 형태소 원형이 참조 기록의 본문·태그·출처·대화 상대에 있어야 통과.
  "생겼다", "좋았다" 같은 용언은 해석이 섞이므로 명사가 하나도 없을 때만 검사한다.
- 날짜·요일·시각 값이 기록 본문에 글자 그대로 있으면(인용문 속 "목요일까지") 그것도 통과로 본다.

날짜가 하나인 앵커는 날짜·시각·요일·낱말이 **한 기록 안에서 함께** 맞아야 한다. "7월 5일"과 "등산"이 각각
다른 기록에 있으면 v1 화이트리스트는 통과시키지만 v2 는 짝 오류로 잡는다. 날짜가 여럿인 앵커("8월 4일과 5일")는
값마다 참조 기록 가운데 하나에 있으면 된다.

통과하지 못한 앵커는 세 가지 방식 가운데 하나로 강등한다.
- plain  : 강조만 벗긴다 (v1 과 같음)
- blur   : 날짜·시각·횟수를 "어느 날", "그 시각", "몇 번"처럼 흐리게 바꾼다
- rewrite: 그 문단을 LLM 에 다시 쓰게 하고 다시 검사한다. 그래도 틀리면 blur
앵커 밖에 쓴 날짜·시각도 사실 주장이므로 따로 검사해 `loose` 로 보고하고, blur·rewrite 에서는 흐리게 바꾼다.
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .records import Record
from .textproc import lemmas

ANCHOR_RE = re.compile(r"<em(?:\s+ref\s*=\s*[\"']?([^\"'>]*)[\"']?)?\s*>(.*?)</em>", re.S)
PERIOD = r"(새벽|아침|오전|낮|오후|저녁|밤)"
TIME_COLON = re.compile(rf"(?:{PERIOD}\s*)?(\d{{1,2}}):(\d{{2}})")
TIME_KO = re.compile(rf"(?:{PERIOD}\s*)?(\d{{1,2}})\s*시(?!간|작|절|청|장|험|계|선|내|외|민)(?:\s*(\d{{1,2}})\s*분)?")
DATE_MD = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일(?!째)")
DAY_OF_MONTH = re.compile(r"(?<![\d월])(\d{1,2})\s*일(?=\s*(?:부터|까지|에|엔|날|은|의|과|와|,|$))")
MONTH_ONLY = re.compile(r"(?<!\d)(\d{1,2})\s*월(?!\s*\d)")
DAY_INDEX = re.compile(r"(\d{1,3})\s*일째")
WEEKDAY = re.compile(r"([월화수목금토일])요일")
UNITS = r"(km|킬로미터|킬로|키로|번째|번|회|개|잔|명|시간|분|kg|층|주|바퀴|권|곡|장|일|달)"
COUNT_NUM = re.compile(rf"(\d+(?:\.\d+)?)\s*{UNITS}")
KO_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
COUNT_KO = re.compile(rf"(?<![가-힣])(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*{UNITS}")
UNIT_NORM = {"킬로미터": "km", "킬로": "km", "키로": "km"}
TERM_STOP = {
    "하다", "되다", "있다", "없다", "같다", "보다", "가다", "오다", "이다", "아니다", "날", "때", "것", "수", "나", "너", "우리",
    "그날", "오늘", "어제", "내일", "쯤", "번", "새벽", "아침", "오전", "낮", "오후", "저녁", "밤", "시", "분", "월", "일",
    "요일", "그", "이", "저", "좀", "더", "다", "처음", "마지막", "또", "다시", "그때", "지금", "매일", "매주", "거의", "모두",
    "동안", "부터", "까지", "주", "첫째", "둘째", "셋째", "넷째", "다섯째", "마지막", "하루", "이틀", "사흘", "무렵", "즈음",
    # 이야기를 전하는 말(서술·대화 틀)은 기록 속 사실이 아니다
    "주인", "생명체", "대화", "얘기", "이야기", "말", "메시지", "글", "휴대폰", "핸드폰", "폰", "제안", "고백", "사과", "연락",
    "답장", "답", "대답", "질문", "다음", "연속", "추천", "후기", "소식", "기억", "순간", "장면", "누군가", "자신", "스스로",
    "문장", "단어", "초", "중순", "말일",
}
NOUN_TAGS = {"NNG", "NNP", "SL", "XR", "NR"}
WEEKDAYS = "월화수목금토일"


@dataclass
class Claim:
    kind: str            # date | day_index | weekday | time | count | term
    value: Any
    span: tuple[int, int] | None = None
    text: str = ""


@dataclass
class Facts:
    """기록 한 건에서 대조에 쓰는 값."""
    id: str
    date: date
    weekday: str
    minutes: int
    day_index: int | None
    text: str            # 본문 + 태그 + 사진 태그 + 출처·종류 + 대화 상대
    lemmas: set[str]
    numbers: set[tuple[float, str]]
    raw: str = ""        # 본문만 (날짜·요일 글자 대조용)


@dataclass
class AnchorResult:
    index: int
    text: str
    refs: list[int]
    status: str          # ok | ref_fixed | unreferenced_ok | failed | empty
    record_id: str | None = None
    failed_claims: list[str] = field(default_factory=list)
    claims: list[str] = field(default_factory=list)


def _hour24(period: str | None, h: int) -> list[int]:
    if period in ("오후", "저녁", "밤"):
        return [0 if (h == 12 and period == "밤") else (h if h >= 12 else h + 12)]
    if period in ("새벽", "아침", "오전"):
        return [0 if h == 12 else h]
    if period == "낮":
        return [h if h >= 11 else h + 12]
    return [h % 24, (h + 12) % 24] if h <= 12 else [h % 24]


def _numbers_in(text: str) -> set[tuple[float, str]]:
    out = set()
    for m in COUNT_NUM.finditer(text or ""):
        out.add((float(m.group(1)), UNIT_NORM.get(m.group(2), m.group(2))))
    for m in COUNT_KO.finditer(text or ""):
        out.add((float(KO_NUM[m.group(1)]), UNIT_NORM.get(m.group(2), m.group(2))))
    return out


SOURCE_WORDS = {"kakao": "카카오톡 카톡 메시지 대화 톡 채팅", "sms": "문자 문자메시지 메시지 대화", "instagram": "인스타그램 인스타 게시물 피드", "photo": "사진",
                "synthetic": "활동"}
GROUP_ROOM_WORDS = "단톡방 단톡 톡방 단체방"
KIND_WORDS = {"post": "게시물", "story": "스토리", "reel": "릴스", "comment": "댓글", "photo": "사진", "message": "메시지"}


def facts_of(r: Record, start: date | None = None) -> Facts:
    body = " ".join(filter(None, [r.text, " ".join(r.tags), " ".join(t["tag"] for t in r.meta.get("auto_tags", [])),
                                  SOURCE_WORDS.get(r.source, ""), KIND_WORDS.get(r.kind, ""),
                                  " ".join(p for p in r.participants if p != "나"), r.meta.get("room") or "",
                                  GROUP_ROOM_WORDS if len(r.participants) >= 3 else ""]))
    lem = set(r.meta.get("lemmas") or []) | lemmas(body) | set(r.tags)
    d = r.dt.date()
    di = r.meta.get("day_index") or ((d - start).days + 1 if start else None)
    return Facts(r.id, d, WEEKDAYS[d.weekday()], r.dt.hour * 60 + r.dt.minute, di, body, lem,
                 _numbers_in(r.text or ""), r.text or "")


def parse_claims(text: str) -> list[Claim]:
    claims: list[Claim] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    for m in DATE_MD.finditer(text):
        claims.append(Claim("date", (int(m.group(1)), int(m.group(2))), m.span(), m.group(0)))
        taken.append(m.span())
    for m in DAY_INDEX.finditer(text):
        if free(*m.span()):
            claims.append(Claim("day_index", int(m.group(1)), m.span(), m.group(0)))
            taken.append(m.span())
    for m in DAY_OF_MONTH.finditer(text):
        if free(*m.span()) and 1 <= int(m.group(1)) <= 31:
            claims.append(Claim("dom", int(m.group(1)), m.span(), m.group(0).strip()))
            taken.append(m.span())
    for m in MONTH_ONLY.finditer(text):
        if free(*m.span()) and 1 <= int(m.group(1)) <= 12:
            claims.append(Claim("month", int(m.group(1)), m.span(), m.group(0)))
            taken.append(m.span())
    for m in WEEKDAY.finditer(text):
        claims.append(Claim("weekday", m.group(1), m.span(), m.group(0)))
        taken.append(m.span())
    for rx in (TIME_COLON, TIME_KO):
        for m in rx.finditer(text):
            if not free(*m.span()):
                continue
            period, h = m.group(1), int(m.group(2))
            mins = int(m.group(3)) if m.group(3) else None
            if h > 24 or (mins is not None and mins > 59):
                continue
            claims.append(Claim("time", (_hour24(period, h), mins), m.span(), m.group(0)))
            taken.append(m.span())
    for rx, conv in ((COUNT_NUM, float), (COUNT_KO, lambda s: float(KO_NUM[s]))):
        for m in rx.finditer(text):
            if not free(*m.span()):
                continue
            unit = UNIT_NORM.get(m.group(2), m.group(2))
            taken.append(m.span())
            if unit == "번째":
                continue  # 서수는 검사하지 않는다
            claims.append(Claim("count", (conv(m.group(1)), unit), m.span(), m.group(0)))
    rest = "".join(" " if not free(i, i + 1) else ch for i, ch in enumerate(text))
    from .textproc import _kiwi

    toks = [t for t in _kiwi().tokenize(rest) if t.form not in TERM_STOP and not t.form.isdigit()]
    noun_forms = sorted({t.form for t in toks if t.tag in NOUN_TAGS and len(t.form) >= 2})
    if noun_forms:
        for f in noun_forms:
            claims.append(Claim("term", f, None, f))
    else:  # 명사가 없을 때만 용언을 검사한다 ("뛰었다" → 뛰다). 원래 낱말(어절)도 함께 대조한다 ("오름")
        words = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[가-힣A-Za-z0-9]+", rest)]
        seen: set[str] = set()
        for t in toks:
            if t.tag not in ("VV", "VA") or t.form + "다" in TERM_STOP or t.form + "다" in seen:
                continue
            seen.add(t.form + "다")
            word = next((w for a, b, w in words if a <= t.start < b), "")
            claims.append(Claim("term", t.form + "다", None, word))
    return claims


def _term_ok(term: str, f: Facts) -> bool:
    if term in f.lemmas or term in f.text:
        return True
    stem = term[:-1] if term.endswith("다") and len(term) > 2 else term
    return stem in f.text


def _time_ok(value: tuple[list[int], int | None], f: Facts) -> bool:
    hours, mins = value
    for h in hours:
        target = h * 60 + (mins or 0)
        diff = (f.minutes - target) % 1440
        if mins is not None and min(diff, 1440 - diff) <= 15:
            return True
        if mins is None and (diff <= 90 or 1440 - diff <= 30):
            return True
    return False


def _single_ok(c: Claim, f: Facts) -> bool:
    literal = bool(c.text) and c.kind in ("date", "dom", "weekday", "month", "time") and c.text in f.raw
    if c.kind == "date":
        return (f.date.month, f.date.day) == c.value or literal
    if c.kind == "dom":
        return f.date.day == c.value or literal
    if c.kind == "month":
        return f.date.month == c.value or literal
    if c.kind == "day_index":
        return f.day_index == c.value
    if c.kind == "weekday":
        return f.weekday == c.value or literal
    if c.kind == "time":
        return _time_ok(c.value, f) or literal
    if c.kind == "term":
        return _term_ok(c.value, f) or (bool(c.text) and c.text != c.value and c.text in f.text)
    return True


def _count_ok(c: Claim, refs: list[Facts], extra: set[int]) -> bool:
    n, unit = c.value
    if any((n, unit) in f.numbers for f in refs):
        return True
    if unit in ("번", "회") and n == len(refs) and len(refs) > 1:
        return True
    if unit == "일" and n == len({f.date for f in refs}) and len(refs) > 1:
        return True
    return unit in ("번", "회", "일", "개", "건") and int(n) in extra


def check_anchor(text: str, refs: list[Facts], extra_counts: set[int] | None = None) -> tuple[bool, str | None, list[str], list[Claim]]:
    """참조 기록들로 앵커 하나를 검사한다. 반환: (통과, 맞은 기록 ID, 실패한 주장, 전체 주장)."""
    claims = parse_claims(text)
    if not claims:
        return False, None, ["검사할 값 없음"], claims
    joint = [c for c in claims if c.kind in ("date", "dom", "month", "day_index", "weekday", "time")]
    terms = [c for c in claims if c.kind == "term"]
    counts = [c for c in claims if c.kind == "count"]
    failed = [c.text for c in counts if not _count_ok(c, refs, extra_counts or set())]
    n_dates = sum(1 for c in joint if c.kind in ("date", "dom", "day_index", "month"))
    n_times = sum(1 for c in joint if c.kind == "time")
    if (n_dates >= 2 or n_times >= 2) and len(refs) >= 2:
        # 날짜·시각이 여럿인 앵커 ("6월부터 8월까지", "10시 47분과 12시 1분"): 값마다 참조 기록 가운데 하나에 있으면 된다
        failed += [c.text for c in joint + terms if not any(_single_ok(c, f) for f in refs)]
        return not failed, (refs[0].id if not failed else None), failed, claims
    if joint or len(refs) <= 1:
        need = joint + terms
        best, best_miss = None, None
        for f in refs:
            miss = [c.text for c in need if not _single_ok(c, f)]
            if best_miss is None or len(miss) < len(best_miss):
                best, best_miss = f, miss
            if not miss:
                break
        failed += best_miss if best_miss is not None else [c.text for c in need]
        return not failed, (best.id if best and not best_miss else None), failed, claims
    # 여러 기록을 합친 주장 (날짜·시각 없음): 낱말마다 어느 한 기록에 있으면 된다
    failed += [c.text for c in terms if not any(_term_ok(c.value, f) for f in refs)]
    return not failed, (refs[0].id if not failed else None), failed, claims


def _refs(raw: str | None) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", raw or "")]


BLUR_RUN = re.compile(r"(?:어느 날|어느 요일|그달)(?:(?:\s*,\s*|\s+|\s*(?:과|와|이나|나|부터|에서)\s+)(?:어느 날|어느 요일|그달))+")


def _blur(text: str) -> str:
    """앵커 글자에서 확인되지 않은 구체 값을 흐리게 바꾼다."""
    out = DATE_MD.sub("어느 날", text)
    out = DAY_INDEX.sub("며칠째", out)
    out = DAY_OF_MONTH.sub("어느 날", out)
    out = MONTH_ONLY.sub("그달", out)
    out = WEEKDAY.sub("어느 요일", out)
    out = TIME_COLON.sub(lambda m: f"{m.group(1)} 무렵" if m.group(1) else "그 시각", out)
    out = TIME_KO.sub(lambda m: f"{m.group(1)} 무렵" if m.group(1) else "그 시각", out)
    out = COUNT_NUM.sub(lambda m: "몇 " + UNIT_NORM.get(m.group(2), m.group(2)), out)
    out = COUNT_KO.sub(lambda m: "몇 " + m.group(2), out)
    out = out.replace("몇 일", "며칠")
    # "어느 날, 어느 날" "어느 날 어느 요일" 처럼 흐린 값이 이어지면 하나로 줄인다
    return BLUR_RUN.sub("어느 날", out)


def loose_facts(text: str, evidence: list[Facts]) -> list[dict[str, Any]]:
    """앵커 밖의 날짜·시각 언급. 근거 기록 가운데 맞는 것이 하나라도 있으면 supported."""
    plain = ANCHOR_RE.sub(lambda m: " " * len(m.group(0)), text)
    out = []
    for c in parse_claims(plain):
        if c.kind not in ("date", "dom", "day_index", "time"):
            continue
        ok = any(_single_ok(c, f) for f in evidence)
        out.append({"text": c.text, "kind": c.kind, "supported": ok, "span": c.span})
    return out


class Verifier:
    def __init__(self, evidence: list[Record], start: date | None = None, extra_counts: set[int] | None = None):
        self.records = evidence
        self.facts = [facts_of(r, start) for r in evidence]
        self.extra = extra_counts or set()

    def verify_text(self, text: str, offset: int = 0) -> list[AnchorResult]:
        results = []
        for i, m in enumerate(ANCHOR_RE.finditer(text)):
            inner = re.sub(r"<[^>]+>", "", m.group(2))
            raw_refs = _refs(m.group(1))
            refs = [self.facts[n - 1] for n in raw_refs if 1 <= n <= len(self.facts)]
            if refs:
                ok, rid, failed, claims = check_anchor(inner, refs, self.extra)
                status = "ok" if ok else "failed"
                if not ok and claims and failed != ["검사할 값 없음"]:
                    # 참조 번호만 틀리고 다른 근거 기록에 맞는 값이 있으면 번호를 고쳐 준다
                    for f in self.facts:
                        ok2, rid2, _, _ = check_anchor(inner, [f], self.extra)
                        if ok2:
                            status, rid, failed = "ref_fixed", rid2, []
                            break
            else:
                ok, rid, failed, claims = False, None, [], parse_claims(inner)
                for f in self.facts:
                    ok, rid, failed, _ = check_anchor(inner, [f], self.extra)
                    if ok:
                        break
                status = "unreferenced_ok" if ok else "failed"
            if not claims:
                status = "empty"
            results.append(AnchorResult(offset + i, inner, raw_refs, status, rid, failed, [c.text for c in claims]))
        return results

    def render(self, text: str, results: list[AnchorResult], mode: str, blur_loose: bool) -> str:
        """검사 결과를 안전한 HTML 로. 모델이 쓴 다른 태그는 전부 글자로 바꾼다."""
        pieces: list[str] = []
        pos = 0
        by_index = iter(results)
        loose = {tuple(x["span"]) for x in loose_facts(text, self.facts) if not x["supported"]} if blur_loose else set()

        def plain(seg: str, base: int) -> str:
            if not loose:
                return html.escape(seg)
            out, p = [], 0
            for a, b in sorted(loose):
                if a >= base and b <= base + len(seg):
                    out.append(html.escape(seg[p : a - base]))
                    out.append(f'<span class="vx-blur" title="기록과 맞지 않아 흐리게 바꾼 값">{html.escape(_blur(seg[a - base:b - base]))}</span>')
                    p = b - base
            out.append(html.escape(seg[p:]))
            return "".join(out)

        for m in ANCHOR_RE.finditer(text):
            pieces.append(plain(text[pos : m.start()], pos))
            r = next(by_index)
            inner = html.escape(r.text)
            if r.status in ("ok", "ref_fixed", "unreferenced_ok"):
                pieces.append(f'<em class="vx vx-{r.status}" data-rid="{r.record_id or ""}">{inner}</em>')
            elif r.status == "empty" or mode == "plain":
                pieces.append(inner)
            elif r.status == "context_mismatch":
                pieces.append(f'<span class="vx-blur" title="문장 속 사건이 인용한 기록과 달라 흐리게 바꾼 값">'
                              f'{html.escape(_blur(r.text))}</span>')
            else:  # blur (rewrite 가 실패한 경우 포함)
                blurred = html.escape(_blur(r.text))
                cls = "vx-blur" if blurred != inner else "vx-drop"
                pieces.append(f'<span class="{cls}" title="기록과 맞지 않아 강조를 뺀 값">{blurred}</span>')
            pos = m.end()
        pieces.append(plain(text[pos:], pos))
        return "".join(pieces)


CONTEXT_SYSTEM = """너는 소설·대화 문장이 인용한 기록과 맞는지 확인한다. 항목마다 문장(인용한 값은 << >> 로 표시)과,
그 값이 가리키는 실제 기록이 주어진다. 기록에는 쓴 사람이 적혀 있고, "나"는 기록 주인이다. 문장에서 기록 주인은 소설이면
"나"(주인공), 대화이면 "너" 또는 "나"로 불린다.
문장이 인용 값과 한 사건으로 묶어 말하는 사실(그날·그때 한 일, 장소, 함께 한 사람, 횟수, "처음"·"마지막" 같은 순서,
그 일을 한 사람)이 그 기록과 어긋나면 ok=false 다. 다른 사람이 자기 일을 말한 기록을 기록 주인이 한 일처럼 쓰면 어긋난 것이다.
문장의 나머지가 지어낸 이야기(대사, 감정, 비유, 새 인물)인 것은 괜찮다. 인용 값에 직접 묶인 사실만 본다.
JSON 하나만 출력한다: {"checks":[{"c":번호,"ok":true}]}"""


def _sentence_of(text: str, start: int, end: int) -> str:
    a = max(text.rfind(ch, 0, start) for ch in ".!?…\n") + 1
    ends = [i for i in (text.find(ch, end) for ch in ".!?…\n") if i != -1]
    b = min(ends) + 1 if ends else len(text)
    return text[a:b].strip()


def _record_line(r: Record) -> str:
    who = [p for p in r.participants if p != "나"]
    body = r.text or " ".join(t["tag"] for t in r.meta.get("auto_tags", []))
    return (f"{r.dt.month}월 {r.dt.day}일 {WEEKDAYS[r.dt.weekday()]}요일 {r.dt.strftime('%H:%M')} "
            f"[{SOURCE_WORDS.get(r.source, r.source).split()[0]}{' · ' + ', '.join(who) if who else ''}] "
            f"쓴 사람: {r.author or '나'} | {body.replace(chr(10), ' / ')[:160]}")


def context_check(items: list[dict[str, Any]], fn: Callable[[str, str], Any]) -> list[bool]:
    """규칙 검사를 통과한 앵커가 문장 속에서 기록과 다른 사건에 묶이지 않았는지 LLM 에 한 번에 묻는다."""
    if not items:
        return []
    body = "\n\n".join(f"[{i + 1}] 문장: {it['sentence']}\n기록: {it['record']}" for i, it in enumerate(items))
    try:
        data = fn(CONTEXT_SYSTEM, body)
        got = {int(c.get("c", 0)): bool(c.get("ok", True)) for c in data.get("checks", [])}
    except Exception:  # 판정이 실패하면 규칙 검사 결과만 쓴다
        return [True] * len(items)
    return [got.get(i + 1, True) for i in range(len(items))]


REWRITE_SYSTEM = (
    "너는 소설 교정자다. 문단 안에서 [[ ]] 로 표시한 부분은 실제 기록과 맞지 않는 사실 주장이다. 이야기의 흐름·문체·다른 문장은 "
    "그대로 두고, 표시한 부분만 구체적인 날짜·시각·횟수·기록 속 활동을 단정하지 않는 표현으로 바꿔 문단 전체를 다시 써라. "
    "다른 <em ref=\"…\">…</em> 표시는 글자 그대로 남겨라. [[ ]] 표시는 결과에 남기지 마라. 문단 하나만 출력한다."
)


def verify_story(
    paragraphs: list[str],
    evidence: list[Record],
    mode: str = "blur",
    title: str | None = None,
    start: date | None = None,
    extra_counts: set[int] | None = None,
    rewrite_fn: Callable[[str, str], str] | None = None,
    context_fn: Callable[[str, str], Any] | None = None,
) -> dict[str, Any]:
    """문단 목록을 검사하고 강등 방식에 맞춰 HTML 로 돌려준다.

    context_fn 을 주면 규칙 검사를 통과한 앵커를 문장째로 한 번 더 판정한다 (문맥 불일치 → context_mismatch).
    """
    v = Verifier(evidence, start, extra_counts)
    by_id = {r.id: r for r in evidence}
    out_paras, anchors, loose_all = [], [], []
    rewritten = 0
    offset = 0
    first_pass: list[list[AnchorResult]] = []
    for para in paragraphs:
        first_pass.append(v.verify_text(para, offset))
        offset += len(first_pass[-1])
    if context_fn:
        items, where = [], []
        for pi, (para, res) in enumerate(zip(paragraphs, first_pass)):
            for m, r in zip(list(ANCHOR_RE.finditer(para)), res):
                if r.status in ("ok", "ref_fixed", "unreferenced_ok") and r.record_id in by_id:
                    plain = ANCHOR_RE.sub(lambda mm: re.sub(r"<[^>]+>", "", mm.group(2)), para[: m.start()])
                    marked = plain + f"<<{r.text}>>" + ANCHOR_RE.sub(lambda mm: re.sub(r"<[^>]+>", "", mm.group(2)), para[m.end():])
                    sent = _sentence_of(marked, len(plain), len(plain) + len(r.text) + 4)
                    items.append({"sentence": sent, "record": _record_line(by_id[r.record_id])})
                    where.append(r)
        for r, ok in zip(where, context_check(items, context_fn)):
            if not ok:
                r.status, r.failed_claims = "context_mismatch", ["문장 속 사건이 인용 기록과 다름"]
    offset = 0
    for para, res in zip(paragraphs, first_pass):
        if mode == "rewrite" and rewrite_fn and any(r.status in ("failed", "context_mismatch") for r in res):
            marked, k = para, 0
            for m, r in zip(list(ANCHOR_RE.finditer(para)), res):
                if r.status in ("failed", "context_mismatch"):
                    marked = marked.replace(m.group(0), f"[[{r.text}]]", 1)
            try:
                new_para = rewrite_fn(REWRITE_SYSTEM, marked)
                res2 = v.verify_text(new_para, offset)
                if not any(r.status == "failed" for r in res2) and "[[" not in new_para:
                    para, res, k = new_para, res2, 1
            except Exception:  # 다시 쓰기에 실패하면 blur 로 강등한다
                k = 0
            rewritten += k
        loose_all += [{**x, "paragraph": len(out_paras)} for x in loose_facts(para, v.facts)]
        out_paras.append(v.render(para, res, "plain" if mode == "plain" else "blur", blur_loose=mode != "plain"))
        anchors += res
        offset += len(res)
    stats = {s: sum(1 for a in anchors if a.status == s)
             for s in ("ok", "ref_fixed", "unreferenced_ok", "failed", "context_mismatch", "empty")}
    stats.update({"anchors": len(anchors), "loose": len(loose_all),
                  "loose_unsupported": sum(1 for x in loose_all if not x["supported"]), "rewritten_paragraphs": rewritten})
    return {
        "title_html": html.escape(title) if title else None,
        "paragraphs_html": out_paras,
        "anchors": [a.__dict__ for a in anchors],
        "loose": [{k: x[k] for k in ("text", "kind", "supported", "paragraph")} for x in loose_all],
        "stats": stats,
        "mode": mode,
    }


def whitelist_v1(text: str, allow: set[str]) -> list[bool]:
    """Storyseed v1 방식: <em> 값 문자열이 화이트리스트에 있으면 통과 (비교용)."""
    return [re.sub(r"<[^>]+>", "", m.group(2)).strip() in allow for m in ANCHOR_RE.finditer(text)]
