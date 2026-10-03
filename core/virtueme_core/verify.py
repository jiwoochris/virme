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
    "문장", "단어", "초", "중순", "말일", "말씀", "자랑", "전화",
    # 때·정도를 흐리는 말은 기록 속 낱말이 아니다 (eval/grounding_eval.py 에서 맞는 앵커를 떨어뜨리던 것들)
    "시작", "정도", "요즘", "최근", "며칠", "반쯤", "밤늦게", "다음날", "가까이", "일주일", "들어서다",
    # 형태소 분석이 조사·관형형을 명사로 잘못 자르는 경우 ("25일에도" → 에도, "쓴 날" → 쓴)
    "에도", "쓴",
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
    context: str | None = None  # 문맥 검사 판정 (ok | wrong | missing). 검사하지 않았으면 None


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
                "synthetic": "활동", "youtube": "유튜브 영상 동영상", "search": "검색", "maps": "지도 위치",
                "card": "카드 결제 계산", "health": "건강 걸음 수면", "spotify": "스포티파이 음악 노래", "netflix": "넷플릭스 드라마 영화"}
GROUP_ROOM_WORDS = "단톡방 단톡 톡방 단체방"
KIND_WORDS = {"post": "게시물", "story": "스토리", "reel": "릴스", "comment": "댓글", "photo": "사진", "message": "메시지",
              "watch": "시청 봤다", "listen": "들었다", "search": "검색", "visit": "방문 들렀다", "payment": "결제 샀다 구매",
              "health": "건강"}


def facts_of(r: Record, start: date | None = None, people: dict[str, str | None] | None = None) -> Facts:
    """people(가명 → 관계)을 주면 대화 상대의 관계("회사 동기")도 대조할 낱말에 넣는다. 이야기·대화는 가명 대신 관계로 부른다."""
    rel = " ".join(v for p in r.participants if (v := (people or {}).get(p)))
    body = " ".join(filter(None, [r.text, " ".join(r.tags), " ".join(t["tag"] for t in r.meta.get("auto_tags", [])),
                                  SOURCE_WORDS.get(r.source, ""), KIND_WORDS.get(r.kind, ""),
                                  " ".join(p for p in r.participants if p != "나"), rel, r.meta.get("room") or "",
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
    if len(term) >= 3 and term[-1] in "도시" and term[:-1] in f.text:  # 제주도 ↔ 제주, 서울시 ↔ 서울
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
        if best is not None and best_miss and all(_single_ok(c, best) for c in joint):
            # 한 대화를 여러 메시지로 나눠 보낸 경우: 날짜·시각이 맞은 기록과 같은 날 90분 안에 인용한 다른 기록에
            # 나머지 낱말이 있으면 한 사건으로 본다 ("고양이 키우기로 했어" / "콩이 정식으로 입양하기로 했어")
            near = [f for f in refs if f is not best and f.date == best.date and abs(f.minutes - best.minutes) <= 90]
            if all(any(_single_ok(c, f) for f in near) for c in terms if not _single_ok(c, best)):
                best_miss = []
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
    def __init__(self, evidence: list[Record], start: date | None = None, extra_counts: set[int] | None = None,
                 people: dict[str, str | None] | None = None):
        self.records = evidence
        self.facts = [facts_of(r, start, people) for r in evidence]
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
                why = r.failed_claims[0].split(": ", 1)[1] if r.failed_claims and ": " in r.failed_claims[0] else ""
                title = "문장 속 사건이 인용한 기록과 달라 흐리게 바꾼 값" + (f" ({why})" if why else "")
                pieces.append(f'<span class="vx-blur" title="{html.escape(title, quote=True)}">'
                              f'{html.escape(_blur(r.text))}</span>')
            else:  # blur (rewrite 가 실패한 경우 포함)
                blurred = html.escape(_blur(r.text))
                cls = "vx-blur" if blurred != inner else "vx-drop"
                pieces.append(f'<span class="{cls}" title="기록과 맞지 않아 강조를 뺀 값">{blurred}</span>')
            pos = m.end()
        pieces.append(plain(text[pos:], pos))
        return "".join(pieces)


CONTEXT_VERSION = "c4"  # 문맥 검사 지시문이나 넘기는 기록 모양을 바꾸면 올린다 (평가 캐시 구분)
CONTEXT_SYSTEM = """너는 소설·대화 속 문장이 인용한 기록과 맞는지 확인한다. 항목마다 문장(인용한 값은 << >> 로 표시)과
그 값이 가리키는 기록(한 건 이상)이 주어진다. 기록마다 쓴 사람이 적혀 있고, "나"는 기록 주인이다. 가명 옆 괄호는 기록 주인과의
관계이고(예: 지인D(회사 동기)), 글에서는 가명 대신 관계로 부를 수 있다.

인용 값의 날짜·요일·시각·숫자는 규칙 검사가 이미 기록과 대조했다. 너는 그 값에 묶인 사건이 기록과 맞는지만 본다.

항목마다 판정 v 를 하나 고른다.
- "wrong": 인용 값에 묶인 사실이 기록과 어긋난다.
  · 기록과 다른 활동·장소·물건·함께 한 사람을 말한다
  · 다른 사람(지인A 등)이 쓴 기록 속 그 사람의 일을 기록 주인이 한 일처럼 쓴다. 기록 주인이 한 말을 다른 사람이 한 말처럼 쓴다
  · 기록 내용을 뒤집는다 (기록은 "갔다"인데 "안 갔다")
  · 주어진 기록만으로도 틀린 것이 드러나는 순서를 말한다 ("처음", "이틀 연속")
- "missing": 인용 값에 묶인 기록 주인의 구체적인 행동(무엇을 했는지, 어디서, 누구와)이 기록에 없어 확인할 수 없지만
  어긋나지는 않는다.
- "ok": 기록이 뒷받침하거나, 묶인 나머지가 창작이다.

아래는 wrong 이 아니다.
- 기록 주인을 "주인"·"집사"·"그"·"그녀"·이름처럼 3인칭으로 부르는 것. 반려동물이나 사물이 화자인 소설이 많다.
  대화 답에서는 기록 주인이 "너" 또는 "나"다
- 감정·기분·해석이 기록의 말투와 다른 것 (기록은 뿌듯한데 소설에서 화가 났다고 써도 창작이다)
- 기록에 없는 장면·대사·사물·초자연적인 일, 이야기 속 설정을 그날에 덧붙인 것
- 횟수가 인용한 기록 수보다 많은 것 (인용하지 않은 기록에 있을 수 있다). 인용한 기록이 다른 활동일 때만 wrong 이다
- 기록 문장을 줄이거나 바꿔 말한 것, "쯤"·"무렵"·"거의"로 흐린 것

v 가 ok 가 아니면 why 에 기록의 어느 부분과 어긋나는지 한 줄로 쓴다 (예: "러닝 크루에 다녀온 사람은 지인D").
why 에서는 기록 주인을 "기록 주인"이라고만 쓴다.
JSON 하나만 출력한다: {"checks":[{"c":번호,"v":"ok"},{"c":번호,"v":"wrong","why":"…"}]}"""
CONTEXT_MAX_RECORDS = 8


def _sentence_of(text: str, start: int, end: int) -> str:
    a = max(text.rfind(ch, 0, start) for ch in ".!?…\n") + 1
    ends = [i for i in (text.find(ch, end) for ch in ".!?…\n") if i != -1]
    b = min(ends) + 1 if ends else len(text)
    return text[a:b].strip()


def _record_line(r: Record, people: dict[str, str | None] | None = None) -> str:
    """문맥 검사에 넘기는 기록 한 줄. people(가명 → 관계)을 주면 "지인D(회사 동기)"처럼 적는다:
    대화 답은 가명 대신 관계로 부르므로, 이게 없으면 "회사 동기한테 말했잖아"를 다른 사람으로 오해한다."""
    people = people or {}
    name = lambda a: f"{a}({people[a]})" if people.get(a) else a  # noqa: E731
    who = [name(p) for p in r.participants if p != "나"]
    body = r.text or " ".join(t["tag"] for t in r.meta.get("auto_tags", []))
    return (f"{r.dt.month}월 {r.dt.day}일 {WEEKDAYS[r.dt.weekday()]}요일 {r.dt.strftime('%H:%M')} "
            f"[{SOURCE_WORDS.get(r.source, r.source).split()[0]}{' · ' + ', '.join(who) if who else ''}] "
            f"쓴 사람: {name(r.author) if r.author else '나'} | {body.replace(chr(10), ' / ')[:160]}")


def context_items(paragraphs: list[str], results: list[list[AnchorResult]], evidence: list[Record],
                  people: dict[str, str | None] | None = None) -> tuple[list[dict[str, Any]], list[AnchorResult]]:
    """규칙 검사를 통과한 앵커마다 문맥 검사에 넘길 문장과 기록 줄. 인용 번호가 가리키는 기록을 모두 넘긴다
    ("7월 21일과 22일, 이틀 연속 등산"은 두 기록을 함께 봐야 맞는지 안다). 번호를 고친 앵커는 맞은 기록을 앞에 둔다."""
    by_id = {r.id: r for r in evidence}
    strip = lambda s: ANCHOR_RE.sub(lambda mm: re.sub(r"<[^>]+>", "", mm.group(2)), s)  # noqa: E731
    items, where = [], []
    for para, res in zip(paragraphs, results):
        for m, r in zip(list(ANCHOR_RE.finditer(para)), res):
            if r.status not in ("ok", "ref_fixed", "unreferenced_ok") or r.record_id not in by_id:
                continue
            plain = strip(para[: m.start()])
            marked = plain + f"<<{r.text}>>" + strip(para[m.end():])
            cited = [evidence[n - 1] for n in dict.fromkeys(r.refs) if 1 <= n <= len(evidence)]
            if by_id[r.record_id] not in cited:
                cited = [by_id[r.record_id]] + ([] if r.status == "ref_fixed" else cited)
            items.append({"sentence": _sentence_of(marked, len(plain), len(plain) + len(r.text) + 4),
                          "records": [_record_line(x, people) for x in cited]})
            where.append(r)
    return items, where


def context_verdicts(items: list[dict[str, Any]], fn: Callable[[str, str], Any], kind: str = "story") -> list[dict[str, str]]:
    """규칙 검사를 통과한 앵커가 문장 속에서 기록과 다른 사건에 묶이지 않았는지 LLM 에 한 번에 묻는다.

    item: {"sentence": 인용 값을 << >> 로 표시한 문장, "records": 기록 줄 목록}. 반환: 항목마다 {"v": ok|wrong|missing, "why"}.
    판정이 실패하면 모두 ok 로 보고 규칙 검사 결과만 쓴다.
    """
    if not items:
        return []
    head = "글 종류: " + ("대화 답 (\"너\"는 기록 주인)" if kind == "talk" else "소설")
    blocks = []
    for i, it in enumerate(items):
        recs = it["records"]
        lines = "\n".join(f"  - {x}" for x in recs[:CONTEXT_MAX_RECORDS])
        more = f"\n  (인용한 기록 {len(recs) - CONTEXT_MAX_RECORDS}건 더 있음)" if len(recs) > CONTEXT_MAX_RECORDS else ""
        blocks.append(f"[{i + 1}] 문장: {it['sentence']}\n기록:\n{lines}{more}")
    try:
        data = fn(CONTEXT_SYSTEM, head + "\n\n" + "\n\n".join(blocks))
        got = {}
        for c in data.get("checks", []):
            v = str(c.get("v") or ("ok" if c.get("ok", True) else "wrong")).lower()
            # 화면은 "(나)"를 "나"로 바꿔 보여 주므로 "기록 주인(나)가" 가 "기록 주인나가" 로 깨지지 않게 미리 줄인다
            why = re.sub(r"기록\s*주인\s*\(나\)|\(나\)", "기록 주인", str(c.get("why") or ""))[:160]
            got[int(c.get("c", 0))] = {"v": v if v in ("ok", "wrong", "missing") else "ok", "why": why}
    except Exception:  # 판정이 실패하면 규칙 검사 결과만 쓴다
        return [{"v": "ok", "why": ""} for _ in items]
    return [got.get(i + 1, {"v": "ok", "why": ""}) for i in range(len(items))]


def context_check(items: list[dict[str, Any]], fn: Callable[[str, str], Any], kind: str = "story") -> list[bool]:
    """어긋난 것("wrong")만 탈락시킨다. 기록에 없는 사실("missing")은 규칙 검사가 이미 값을 확인했으므로 통과시킨다."""
    return [x["v"] != "wrong" for x in context_verdicts(items, fn, kind)]


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
    kind: str = "story",
    people: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """문단 목록을 검사하고 강등 방식에 맞춰 HTML 로 돌려준다.

    context_fn 을 주면 규칙 검사를 통과한 앵커를 문장째로 한 번 더 판정한다 (문맥 불일치 → context_mismatch).
    kind 는 "story"(소설) 또는 "talk"(대화 답), people 은 가명 → 관계 (이야기·대화가 "회사 동기"처럼 관계로 부른
    사람을 기록의 가명과 맞춰 보게 한다).
    """
    v = Verifier(evidence, start, extra_counts, people)
    out_paras, anchors, loose_all = [], [], []
    rewritten = 0
    offset = 0
    first_pass: list[list[AnchorResult]] = []
    for para in paragraphs:
        first_pass.append(v.verify_text(para, offset))
        offset += len(first_pass[-1])
    if context_fn:
        items, where = context_items(paragraphs, first_pass, evidence, people)
        for r, verdict in zip(where, context_verdicts(items, context_fn, kind)):
            r.context = verdict["v"]
            if verdict["v"] == "wrong":
                r.status = "context_mismatch"
                r.failed_claims = ["문장 속 사건이 인용 기록과 다름" + (f": {verdict['why']}" if verdict["why"] else "")]
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
