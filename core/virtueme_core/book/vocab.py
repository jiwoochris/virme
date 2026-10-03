"""책 디자인 통제 어휘.

표지 사례에 붙이는 라벨과 디자인 브리프의 값은 모두 이 목록 안에서만 고른다. 비전 모델이 목록 밖의 값을 돌려주면
`normalize_label` 이 가장 가까운 값으로 바꾸거나 버린다. 렌더러는 레이아웃 원형마다 HTML 틀을 하나씩 갖고 있으므로,
여기 없는 원형은 그릴 수 없다.
"""
from __future__ import annotations

from typing import Any

from ..prompts_storyseed import GENRES

# 이야기 장르 6개(Storyseed GENRES) + 문학·에세이. 키는 파일·JSON 에 쓰는 영문 짧은 이름
GENRE_KEYS: list[str] = ["mystery", "romance", "sf", "fantasy", "thriller", "comedy", "literary"]
GENRE_KO: dict[str, str] = {
    "mystery": GENRES[0][0], "romance": GENRES[1][0], "sf": GENRES[2][0], "fantasy": GENRES[3][0],
    "thriller": GENRES[4][0], "comedy": GENRES[5][0], "literary": "문학·에세이",
}

# 레이아웃 원형: 그림과 글자가 표지 면을 나누는 방식
ARCHETYPES: dict[str, str] = {
    "full_bleed": "그림이 표지 전체를 덮고 제목이 그 위에 얹힘",
    "framed": "여백 안에 그림을 액자처럼 두고 제목은 그림 밖에",
    "typographic": "그림 없이 글자와 색면만으로 구성",
    "split": "색면 블록과 그림이 표지를 위아래나 좌우로 나눔",
    "pattern": "반복 무늬가 바탕을 채우고 제목은 띠나 상자에",
    "centered_motif": "단색 바탕 가운데 작은 그림 하나와 넓은 여백",
}
TITLE_POSITIONS: dict[str, str] = {"top": "위쪽 1/3", "middle": "가운데", "bottom": "아래쪽 1/3"}
TITLE_SCALES: dict[str, str] = {
    "small": "표지 폭의 40% 미만", "medium": "40~70%", "large": "70% 이상", "dominant": "제목이 표지의 주인공",
}
TYPEFACES: dict[str, str] = {
    "serif": "명조·세리프", "sans": "고딕·산세리프", "display": "장식·굵은 제목용 서체", "handwritten": "손글씨·붓글씨",
}
ART_STYLES: dict[str, str] = {
    "photo": "사진", "painterly": "회화·수채·유화", "flat_vector": "평면 벡터 일러스트", "line": "선화·펜화",
    "collage": "콜라주·합성", "abstract": "추상 도형·질감", "none": "그림 없음",
}
MOODS: dict[str, str] = {
    "warm": "따뜻한", "melancholic": "쓸쓸한", "tense": "긴장된", "eerie": "서늘한", "playful": "장난스러운",
    "dreamy": "몽환적인", "serene": "고요한", "energetic": "힘찬", "nostalgic": "그리운", "romantic": "설레는",
    "mysterious": "수수께끼 같은", "hopeful": "희망찬", "dark": "어두운", "whimsical": "엉뚱한",
}
LABEL_FIELDS: dict[str, dict[str, str]] = {
    "archetype": ARCHETYPES, "title_position": TITLE_POSITIONS, "title_scale": TITLE_SCALES,
    "typeface": TYPEFACES, "art_style": ART_STYLES,
}
# 비전 모델이 흔히 쓰는 다른 이름 → 통제 어휘
ALIASES: dict[str, str] = {
    "fullbleed": "full_bleed", "full-bleed": "full_bleed", "full bleed": "full_bleed", "image_overlay": "full_bleed",
    "type_only": "typographic", "typography": "typographic", "text_only": "typographic",
    "centered": "centered_motif", "motif": "centered_motif", "small_motif": "centered_motif",
    "center": "middle", "centre": "middle",
    "sans-serif": "sans", "sans_serif": "sans", "script": "handwritten", "hand": "handwritten",
    "decorative": "display",
    "photograph": "photo", "photographic": "photo", "painting": "painterly", "watercolor": "painterly",
    "illustration": "painterly", "vector": "flat_vector", "flat": "flat_vector", "line_art": "line",
    "geometric": "abstract", "texture": "abstract",
    "huge": "dominant", "xl": "large",
}


def genre_key(genre: int | str | None) -> str:
    """장르 번호(GENRES 순서)·한국어 이름·영문 키를 영문 키로. 모르면 literary."""
    if genre is None:
        return "literary"
    if isinstance(genre, int) or (isinstance(genre, str) and genre.isdigit()):
        i = int(genre)
        return GENRE_KEYS[i] if 0 <= i < len(GENRE_KEYS) else "literary"
    g = str(genre).strip().lower()
    if g in GENRE_KEYS:
        return g
    for k, ko in GENRE_KO.items():
        if g == ko.lower() or g in ko.lower().split("·"):
            return k
    return "literary"


def _norm(v: Any) -> str:
    return str(v or "").strip().lower().replace(" ", "_")


def normalize_value(field: str, value: Any) -> str | None:
    """한 칸의 값을 통제 어휘로. 바꿀 수 없으면 None."""
    allowed = LABEL_FIELDS[field]
    v = _norm(value)
    if v in allowed:
        return v
    v2 = ALIASES.get(v) or ALIASES.get(v.replace("_", " ")) or ALIASES.get(v.replace("_", "-"))
    return v2 if v2 in allowed else None


def normalize_label(raw: dict[str, Any]) -> dict[str, Any]:
    """비전 모델 라벨 한 건을 통제 어휘로 정리한다. 목록 밖의 값은 버린다."""
    out: dict[str, Any] = {f: normalize_value(f, raw.get(f)) for f in LABEL_FIELDS}
    moods = raw.get("moods") or raw.get("mood") or []
    if isinstance(moods, str):
        moods = [moods]
    out["moods"] = sorted({_norm(m) for m in moods if _norm(m) in MOODS})[:4]
    out["is_cover"] = bool(raw.get("is_cover", True))
    return out


def validate_label(label: dict[str, Any]) -> list[str]:
    """라벨이 통제 어휘를 지키는지 본다. 반환: 어긋난 곳 목록 (비면 통과)."""
    errs = []
    for f, allowed in LABEL_FIELDS.items():
        v = label.get(f)
        if v is not None and v not in allowed:
            errs.append(f"{f}={v!r}")
    for m in label.get("moods") or []:
        if m not in MOODS:
            errs.append(f"moods={m!r}")
    return errs


def vocabulary() -> dict[str, Any]:
    """비전 모델 지시문과 README 에 그대로 싣는 어휘 전체."""
    return {"genres": GENRE_KO, **{f: v for f, v in LABEL_FIELDS.items()}, "moods": MOODS}
