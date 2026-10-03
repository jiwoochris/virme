"""본문에만 나오는 제3자 이름 가리기 (GLiNER).

가져오기 단계의 Masker 는 대화 참여자 이름만 안다. "김민수 팀장님이 칭찬해줬어"처럼 참여자가 아닌 사람의 이름은
그대로 남는다. 빌드 첫 단계에서 GLiNER 다국어 개인정보 모델(urchade/gliner_multi_pii-v1, Apache-2.0)로 사람 이름을
찾아 "(누군가)" 로 바꾼다. 임베딩·정서 계산보다 먼저 하므로 이름은 벡터에도 남지 않는다.

- 라벨을 "person"·"animal" 둘로 주면 반려동물 이름("콩이")은 animal 로 갈라진다 (2026-09-11 확인).
- 이 모델은 "나도"·"우리 딸"·"회사 동기가" 같은 대명사·일반 명사도 person 으로 자주 잡는다. 형태소 분석(Kiwi)으로
  대명사(NP)와 일반 명사·부사 한 낱말은 이름이 아닌 것으로 본다. 윤서연 샘플에서 잡힌 57건 가운데 이름은 5건이었다.
- 관계어("엄마", "옆집 할머니"), 가명("지인A"), 본인 표기("나")는 건너뛰고, 본인 이름("서연씨")은 "(나)" 로 바꾼다.
- 잘못 잡히는 이름은 `virtueme people --keep 이름` 으로 예외에 넣는다.
- 모델은 처음 쓸 때 내려받는다 (약 1.1GB). `virtueme build --no-pii` 로 끌 수 있다.
- 흔적 기록은 검색어만 검사한다. 영상 제목·가맹점·곡 이름에 나오는 이름은 연예인·상호라 가리면 검색만 나빠진다.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any

from .privacy import RELATION_NAMES, SELF_TEXT_TOKEN, relation_label
from .store import Store

PII_MODEL = os.environ.get("VIRTUEME_PII_MODEL", "urchade/gliner_multi_pii-v1")
PII_VERSION = "gliner2"
PLACEHOLDER = "(누군가)"
THRESHOLD = 0.6
PARTICLES = sorted(["이랑", "에게", "한테", "께서", "이가", "이는", "이도", "이를", "이의", "이야", "이에요", "이한테",
                    "이라고", "이라는", "이라서", "라고", "라는", "이네", "이랑은", "한테는", "에게는",
                    "아", "야", "이", "가", "은", "는", "을", "를", "랑", "와", "과", "도", "의", "만", "씨", "님"],
                   key=len, reverse=True)
HONORIFICS = ("씨", "님")
PRONOUNS = {"나", "너", "저", "제", "내", "네", "니", "우리", "저희", "너희", "당신", "자기", "자네", "여러분", "걔", "얘",
            "쟤", "그", "그녀", "그분", "이분", "저분", "본인", "누구", "아무", "다들", "모두", "혼자"}
# 한 낱말로 분석될 때 이름이 아닌 것으로 보는 품사: 일반 명사, 의존 명사, 수사, 부사, 감탄사, 관형사, 어근
COMMON_TAGS = {"NNG", "NNB", "NR", "MAG", "IC", "MM", "XR"}
KIWI_UNKNOWN = 2  # 사전에 없는 낱말의 형태소 ID
SURNAMES = set("김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민류나진지엄채원천방공현함변"
               "염여추도소석선설마길연위표명기반라왕금옥육인맹제모탁국어은편용예경봉사부") | {"남궁", "황보", "제갈", "선우", "독고"}
ROLE_WORDS = RELATION_NAMES | {"할머니", "할아버지", "아저씨", "아주머니", "이모님", "선생님", "사장님", "팀장님", "선배", "후배",
                               "동료", "친구", "사람", "사람들", "애들", "기사님", "직원", "손님", "옆집", "아랫집", "윗집"}


@lru_cache(maxsize=1)
def _model():
    from gliner import GLiNER

    return GLiNER.from_pretrained(PII_MODEL)


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi

    return Kiwi()


def _strip(word: str) -> str:
    """조사 하나, 그다음 호칭 하나를 뗀다. '하은님도' → '하은', '서연씨는' → '서연'. 두 글자보다 짧아지면 떼지 않는다."""
    for p in PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= 2:
            word = word[: -len(p)]
            break
    for p in HONORIFICS:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


def _not_a_name(raw: str, core: str) -> bool:
    """형태소 분석으로 대명사와 사전에 있는 일반 낱말을 거른다. '나도' '우리' '회사' '혼자' '러너스' → True.

    사전에 없는 세 글자 낱말은 성씨로 시작할 때만 이름으로 본다 ('김도은' 은 이름, '러너스' 는 아님).
    """
    toks = [t for t in _kiwi().tokenize(raw)
            if not t.tag.startswith(("J", "E", "S")) and t.tag not in ("VCP", "XSN") and t.form not in HONORIFICS]
    if not toks or toks[0].tag == "NP":
        return True
    if len(toks) == 1 and toks[0].tag in COMMON_TAGS:
        if toks[0].id != KIWI_UNKNOWN:
            return True
        return len(core) >= 3 and core[0] not in SURNAMES and core[:2] not in SURNAMES
    return False


def names_in(span: str) -> list[str]:
    """모델이 잡은 구간에서 이름만. '승우랑 준호가' → ['승우', '준호'], '김민수 팀장님이' → ['김민수']."""
    return [n for w in span.split() if (n := _word_name(w))]


def name_core(span: str) -> str | None:
    """'김민수 팀장님이' → '김민수', '수빈이랑' → '수빈', '옆집 할머니'·'나도'·'회사 동기가' → None (이름이 아님)."""
    got = names_in(span)
    return got[0] if got else None


def _word_name(word: str) -> str | None:
    raw = re.sub(r"[^가-힣A-Za-z]", "", word)
    first = _strip(raw)
    if (len(first) < 2 or first in ROLE_WORDS or relation_label(first) or first.startswith("지인")
            or first in PRONOUNS or raw in PARTICLES):
        return None
    if re.fullmatch(r"[가-힣]+", first) and _not_a_name(raw, first):
        return None
    return first


def keep_list(store: Store) -> set[str]:
    return set(store.cache_get("pii_keep") or [])


def mask_texts(texts: list[str], keep: set[str] | None = None, self_names: set[str] | None = None,
               ) -> list[tuple[str, list[str]]]:
    """텍스트마다 (가린 텍스트, 가린 이름 목록). self_names 에 든 이름(성을 뗀 이름 포함)은 "(나)" 로 바꾼다."""
    keep = keep or set()
    selfs = set(self_names or ())
    selfs |= {n[1:] for n in selfs if re.fullmatch(r"[가-힣]{3}", n)}
    model = _model()
    out = []
    for t in texts:
        ents = model.predict_entities(t, ["person", "animal"], threshold=THRESHOLD) if t.strip() else []
        names = []
        for e in ents:
            if e["label"] != "person":
                continue
            start = e.get("start")
            if start and t[start - 1] == ")":  # "(나)이도" 처럼 이미 가린 표기에 붙은 조각
                continue
            names += [n for n in names_in(e["text"]) if n not in keep]
        masked = t
        for n in sorted(set(names), key=len, reverse=True):
            masked = re.sub(rf"(?<![가-힣(]){re.escape(n)}", SELF_TEXT_TOKEN if n in selfs else PLACEHOLDER, masked)
        out.append((masked, sorted(n for n in set(names) if n not in selfs)))
    return out


def run(store: Store, log=print) -> dict[str, Any]:
    """아직 검사하지 않은 기록의 본문에서 제3자 이름을 가린다."""
    todo = [r for r in store.records() if r.text and r.meta.get("pii") != PII_VERSION
            and (not r.is_trace or r.kind == "search")]
    if not todo:
        return {"scanned": 0}
    log(f"제3자 이름 검사 {len(todo)}건 (GLiNER)")
    keep = keep_list(store)
    changed, names_total = [], 0
    results = mask_texts([r.text for r in todo], keep, set(store.self_names()))
    for r, (masked, names) in zip(todo, results):
        r.meta["pii"] = PII_VERSION
        if masked != r.text:
            names_total += len(names)
            r.text = masked
            r.meta.pop("lemmas", None)  # 본문이 바뀌었으므로 원형도 다시 계산
            r.meta["pii_masked"] = len(names)
        changed.append(r)
    store.upsert_records(changed)
    return {"scanned": len(todo), "records_masked": sum(1 for r in changed if r.meta.get("pii_masked")),
            "names_masked": names_total}
