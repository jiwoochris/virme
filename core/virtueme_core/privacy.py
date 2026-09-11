"""개인정보 가리기.

원칙: 실명은 로컬 DB 의 people 표에만 둔다. 본문은 가져올 때 한 번 가리고, API 응답과 LLM 요청처럼
밖으로 나가는 텍스트는 `Masker.mask` 를 한 번 더 거친다 (나중에 가져온 기록에서 새로 알게 된 이름까지 잡기 위해).

- 대화 참여자 실명은 가져오기 단계에서 가명(나, 지인A, 지인B …)으로 바뀐다.
- "엄마"·"팀장님"처럼 관계로 저장된 연락처는 신원을 드러내지 않으므로 그대로 둔다.
- 본문 속 실명(참여자 이름·성을 뗀 이름)도 같은 가명으로 바꾼다.
- 전화번호·이메일·주민번호·카드번호·계좌번호·URL 은 정규식으로 가린다.
- GPS 좌표는 밖으로 내보내지 않는다.

한계: 참여자 목록에 없는 제3자의 이름(예: 본문에만 나오는 직장 동료 이름)은 잡지 못한다.
2단계에서 GLiNER 개인정보 탐지 모델을 붙여 보완한다.
"""
from __future__ import annotations

import re
import string
from collections.abc import Mapping

from .store import Store

SELF_ALIAS = "나"
SELF_TEXT_TOKEN = "(나)"

_PARTICLES = (
    "이랑", "에게", "한테", "께서", "이가", "이는", "이도", "이를", "이의", "이야", "이에요",
    "아", "야", "이", "가", "은", "는", "을", "를", "랑", "님", "씨", "도", "의", "와", "과", "만",
)

_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"https?://([^\s/]+)[^\s]*"), r"[링크:\1]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[이메일]"),
    (re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)"), "[주민번호]"),
    (re.compile(r"(?<!\d)\d{4}[-\s]\d{4}[-\s]\d{4}[-\s]\d{4}(?!\d)"), "[카드번호]"),
    (re.compile(r"(?<!\d)01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)0\d{1,2}[-\s.]\d{3,4}[-\s.]\d{4}(?!\d)"), "[전화번호]"),
    (re.compile(r"(?<!\d)\d{2,6}-\d{2,6}-\d{2,8}(?!\d)"), "[계좌번호]"),
    (re.compile(r"(?<![\w.])@[A-Za-z0-9_.]{3,30}"), "[계정]"),
]


# 관계를 나타내는 연락처 이름은 신원을 드러내지 않고, 가리면 "엄마" 같은 키워드로 대화를 찾을 수 없게 된다.
# 이런 이름은 가명 대신 그대로 쓴다 (꾸밈 기호·"님" 은 떼고 비교).
RELATION_NAMES = {
    "엄마", "아빠", "어머니", "아버지", "어머님", "아버님", "엄니", "아부지", "할머니", "할아버지", "외할머니", "외할아버지",
    "누나", "언니", "오빠", "형", "동생", "남동생", "여동생", "막내", "첫째", "둘째", "셋째",
    "이모", "고모", "삼촌", "외삼촌", "큰아빠", "큰엄마", "작은아빠", "작은엄마", "숙모", "사촌", "조카",
    "남편", "아내", "와이프", "신랑", "자기", "여보", "애기", "아들", "딸", "장모님", "장인어른", "시어머니", "시아버지",
    "팀장님", "부장님", "과장님", "대리님", "사장님", "선배", "후배", "교수님", "선생님", "담임",
}


def relation_label(name: str) -> str | None:
    core = re.sub(r"[^가-힣]", "", name)
    if core in RELATION_NAMES:
        return core
    if core.endswith("님") and core[:-1] in RELATION_NAMES:
        return core[:-1]
    return None


def _alias_letters(n: int) -> str:
    """0 → A, 25 → Z, 26 → AA …"""
    letters = string.ascii_uppercase
    out = ""
    n += 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = letters[rem] + out
    return out


class Pseudonymizer:
    """실명 → 가명 대응표. DB 의 people 표에 저장해서 가져오기를 여러 번 해도 가명이 유지된다."""

    def __init__(self, store: Store):
        self.store = store
        self.table: dict[str, str] = store.people()

    def alias(self, name: str, is_self: bool = False) -> str:
        name = name.strip()
        if name in self.table:
            return self.table[name]
        if is_self:
            # 본인 이름이 둘 이상이어도 (예: 카톡 이름과 인스타 이름) 모두 "나"
            alias = SELF_ALIAS
        elif rel := relation_label(name):
            alias = rel
        else:
            used = set(self.table.values())
            i = 0
            while f"지인{_alias_letters(i)}" in used:
                i += 1
            alias = f"지인{_alias_letters(i)}"
        self.store.add_person(name, alias, is_self)
        self.table[name] = alias
        return alias


def _given_name(name: str) -> str | None:
    """'김지민' → '지민'. 한글 세 글자 이름만 성을 떼어 부르는 경우를 잡는다."""
    if re.fullmatch(r"[가-힣]{3}", name):
        return name[1:]
    return None


class Masker:
    def __init__(self, names_to_alias: Mapping[str, str]):
        patterns: list[tuple[re.Pattern[str], str]] = []
        entries: list[tuple[str, str]] = []
        for name, alias in names_to_alias.items():
            if alias == name or relation_label(name):
                continue  # 관계어 연락처("엄마")는 본문에서도 바꾸지 않는다
            token = SELF_TEXT_TOKEN if alias == SELF_ALIAS else alias
            if len(name) >= 2:
                entries.append((name, token))
            given = _given_name(name)
            if given:
                entries.append((given, token))
        # 긴 이름부터 바꿔야 '김지민' 이 '지민' 보다 먼저 잡힌다
        seen: set[str] = set()
        particle = "|".join(sorted(map(re.escape, _PARTICLES), key=len, reverse=True))
        for text, token in sorted(entries, key=lambda e: len(e[0]), reverse=True):
            if text in seen:
                continue
            seen.add(text)
            if re.fullmatch(r"[가-힣]+", text):
                # 한글 이름: 앞이 한글이 아니고, 뒤는 조사 하나까지만 허용
                pat = re.compile(rf"(?<![가-힣]){re.escape(text)}(?=(?:{particle})?(?![가-힣]))")
            else:
                pat = re.compile(rf"(?<!\w){re.escape(text)}(?!\w)")
            patterns.append((pat, token))
        self._names = patterns

    @classmethod
    def from_store(cls, store: Store) -> "Masker":
        return cls(store.people())

    def mask(self, text: str | None) -> str | None:
        if text is None:
            return None
        out = text
        for pat, repl in _PII_PATTERNS:
            out = pat.sub(repl, out)
        for pat, token in self._names:
            out = pat.sub(token, out)
        return out
