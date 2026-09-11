"""한국어 형태소 처리 (Kiwi).

직접 일치를 글자 단위로 찾으면 "비" 가 "비밀"·"비행기" 에 걸린다. 그래서 기록과 키워드를 모두
형태소로 나눠 원형(명사, 동사·형용사 어간)끼리 비교한다. "외로웠다" 와 "외롭다" 는 같은 원형
"외롭" 으로 만난다.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from functools import lru_cache

CONTENT_TAGS = {"NNG", "NNP", "VV", "VA", "XR", "SL", "NR", "MAG"}
NOUN_TAGS = {"NNG", "NNP", "SL"}
STOP_NOUNS = {
    "오늘", "내일", "어제", "진짜", "정말", "그냥", "이거", "저거", "그거", "우리", "사람", "생각", "시간", "때문",
    "하나", "정도", "이번", "다음", "지금", "요즘", "거기", "여기", "저기", "뭔가", "얘기", "근데", "그래서",
    "사진", "동영상", "이모티콘", "ㅋㅋ", "ㅎㅎ", "ㅠㅠ", "지인",  # "지인" 은 가명 표기
}


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi

    return Kiwi()


def _key(form: str, tag: str) -> str | None:
    if tag not in CONTENT_TAGS:
        return None
    if tag in ("VV", "VA"):
        return form + "다"  # 어간 → 기본형 표기 (외롭 → 외롭다)
    return form


def lemmas(text: str) -> set[str]:
    if not text:
        return set()
    out = set()
    for tok in _kiwi().tokenize(text):
        k = _key(tok.form, tok.tag)
        if k:
            out.add(k)
    return out


def lemmas_many(texts: Iterable[str]) -> list[set[str]]:
    texts = list(texts)
    out: list[set[str]] = []
    for toks in _kiwi().tokenize(texts):
        out.append({k for t in toks if (k := _key(t.form, t.tag))})
    return out


def nouns(text: str) -> list[str]:
    if not text:
        return []
    return [t.form for t in _kiwi().tokenize(text)
            if t.tag in NOUN_TAGS and len(t.form) >= 2 and t.form not in STOP_NOUNS]


def top_nouns(texts: Iterable[str], k: int = 8) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for t in texts:
        c.update(set(nouns(t)))  # 기록 한 건 안에서 되풀이된 단어는 한 번만 센다
    return c.most_common(k)
