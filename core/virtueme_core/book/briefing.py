"""디자인 브리프: 장르 문법 + 분위기가 맞는 참고 표지 → 표지·본문 디자인 값.

`brief(genre, narrative_object, story_text)` 는 네트워크 없이 결정적으로 돈다.

분위기 정하기 (`target_moods`)
- 장르마다 기본 분위기 하나: 미스터리 mysterious, 로맨스 romantic, SF eerie, 판타지 dreamy, 스릴러 tense,
  코미디 playful, 문학 serene (가중치 1.0)
- 서사 오브젝트가 있으면 정서와 비주얼 신호를 분위기로 옮긴다 (가중치 0.8)
  - 정서 평균(emotion.mean, -1..1): 0.25 이상 warm, 0.1 이상 hopeful, -0.2 이하 melancholic, -0.45 이하 dark
  - 근거 기간 평균 각성도(emotion.series 의 arousal): 0.55 이상 energetic, 0.3 이하 serene
  - conflict 0.5 이상 tense, uncertainty 0.55 이상 mysterious, novelty 0.6 이상 whimsical,
    stability 0.75 이상 serene, activity 0.6 이상 energetic,
    possibility 0.62 이상 hopeful, 0.38 이하 nostalgic (좋아지는 쪽이면 희망, 나빠지는 쪽이면 그리움)
- 이야기 본문이 있으면 감정 낱말(웃음·설렘·비밀·그리움 등)을 세어 가중치 0.5 까지 더한다
- 가중치 상위 4개를 쓴다

참고 표지 고르기 (`retrieve`): 점수 = 같은 장르 1.0 (+ 비전 모델의 장르 추정도 같으면 0.25, 다른 장르인데 추정이
같으면 0.35) + 분위기 겹침(목표 분위기 가중치 합 대비 겹친 가중치) + 명도 맞춤 0.2 (정서가 밝으면 밝은 표지, 어두우면
어두운 표지) + 사람이 라벨을 검수한 표지 0.1. 상위 k 장(기본 6).

값 고르기: 칸마다 점수 = 0.5 × 장르 분포 + 0.5 × 상위 k 장의 점수 가중 표. 이 점수에서 p ∝ 점수^(1/T) 로 뽑는다
(T 기본 1.2, 0 이면 가장 큰 값). 시드는 브리프 번호(입력의 해시)와 칸 이름이라 다시 렌더해도 같은 값이 나오고,
variant 를 바꾸면 같은 이야기로 다른 디자인을 뽑는다. 뽑은 확률은 brief.json 의 decision.<칸>.probabilities 에 남는다.
레이아웃 원형과 맞지 않는 조합(그림 없는 원형에 그림 양식, 가운데 그림이 있는 원형에 가운데 제목)은 후보에서 뺀다.

장 나누기(`chapters.split_chapters`)와 장마다 삽화를 넣을 단락·삽화 프롬프트도 브리프에 들어간다(삽화는 최대 4장).

팔레트: 상위 참고 표지의 주요 색(점수 × 면적)과 장르 팔레트(0.5배)를 CIELAB k-means 로 5색으로 묶은 뒤, 정서 곡선 쪽으로
옮긴다. 정서가 v 만큼 명도 +8v, 색상각은 v>0 이면 따뜻한 쪽(60°), v<0 이면 차가운 쪽(250°)으로 |v|×20°까지,
채도는 × (0.8 + 0.5 × 각성도). 바탕·글자·강조색은 WCAG 대비로 정한다 (글자 4.5 이상, 강조 2 이상).
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import numpy as np

from .annotate import load_references, usable
from .features import contrast_ratio, hex_of, kmeans, lab_to_rgb, rgb_of, rgb_to_lab
from .grammar import load_grammar
from .vocab import (
    ARCHETYPES,
    ART_STYLES,
    GENRE_KO,
    TITLE_POSITIONS,
    TITLE_SCALES,
    TYPEFACES,
    genre_key,
)

GENRE_MOOD = {"mystery": "mysterious", "romance": "romantic", "sf": "eerie", "fantasy": "dreamy",
              "thriller": "tense", "comedy": "playful", "literary": "serene"}
# 장르 문법이 비어 있을 때(참고 표지를 아직 모으지 않았을 때)의 기본값
FALLBACK = {
    "mystery": ("full_bleed", "serif", "painterly"), "romance": ("full_bleed", "handwritten", "painterly"),
    "sf": ("full_bleed", "sans", "abstract"), "fantasy": ("full_bleed", "serif", "painterly"),
    "thriller": ("full_bleed", "sans", "photo"), "comedy": ("centered_motif", "display", "flat_vector"),
    "literary": ("framed", "serif", "painterly"),
}
FALLBACK_PALETTE = {
    "mystery": ["#1f2a36", "#c9b48a", "#6b2f2a", "#e9e2d0", "#3d5566"],
    "romance": ["#f3d9d2", "#b4475a", "#f7efe6", "#6d3b47", "#e59a8c"],
    "sf": ["#0f1b2d", "#3fa7c9", "#e8f1f5", "#5b3fa0", "#9fd3e0"],
    "fantasy": ["#22304a", "#d9a441", "#f2e6c9", "#5a7d5a", "#8e3b46"],
    "thriller": ["#111111", "#b3261e", "#e6e6e6", "#4a4a4a", "#d9c7a7"],
    "comedy": ["#ffd84d", "#1d3557", "#fff7e0", "#e63946", "#2a9d8f"],
    "literary": ["#f2ede3", "#2e3b33", "#b88a5a", "#8aa39b", "#d9cbb3"],
}
TEXT_MOODS = {
    "playful": ["웃", "농담", "엉뚱", "장난", "깔깔", "어이없"], "romantic": ["설레", "사랑", "두근", "고백", "손을 잡"],
    "mysterious": ["비밀", "수수께끼", "단서", "이상한", "사라"], "tense": ["숨을 죽", "심장", "발소리", "그림자", "쫓"],
    "melancholic": ["외로", "쓸쓸", "눈물", "혼자"], "nostalgic": ["그리", "추억", "예전", "기억"],
    "warm": ["따뜻", "다정", "포근", "온기"], "dreamy": ["꿈", "별", "마법", "빛나"], "energetic": ["달리", "뛰", "숨이 차", "땀"],
    "serene": ["조용", "고요", "바다", "바람"],
}
CONFLICTS = {"centered_motif": {"middle"}, "framed": {"middle"}}
# 뽑기 온도. 1.0 이면 점수 비례, 클수록 장르에서 드문 원형이 자주 나온다 (README 「브리프」의 원형 분포 표)
DEFAULT_TEMPERATURE = 1.2

STYLE_PHRASE = {
    "photo": "cinematic photograph, natural light, shallow depth of field, film grain",
    "painterly": "painterly illustration, visible gouache brush strokes on textured paper",
    "flat_vector": "flat vector illustration, clean simple shapes, limited palette, subtle print grain",
    "line": "fine ink line drawing with minimal hatching, printed in two colours",
    "collage": "paper collage of cut-out shapes with risograph texture",
    "abstract": "abstract composition of soft geometric shapes and paper texture",
}
SCENE = {
    "mystery": "an everyday scene holding one quiet, unexplained clue",
    "romance": "a tender, light-filled everyday moment with a sense of distance between two people",
    "sf": "an everyday scene seen through a speculative, slightly uncanny lens",
    "fantasy": "an everyday scene with a thin layer of gentle magic over it",
    "thriller": "a cool, tense everyday scene with long shadows, no violence",
    "comedy": "a warm, funny everyday moment with one absurd detail",
    "literary": "a calm, reflective everyday moment",
}
NO_TEXT = ("Absolutely no text anywhere in the image: no letters, words, numbers, title, signature, watermark, logo "
           "or typography of any language. Do not draw sound effects or onomatopoeia, and avoid objects that carry "
           "writing (screens, phones, signs, keypads, labels, open books). ")
REF_NOTE = ("The attached images are reference book covers. Take only their colour handling, rendering technique "
            "and composition. Do not copy their lettering, titles or subjects. ")
PEOPLE_NOTE = "If a person appears, show them small, from behind or in silhouette, face not visible. "


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(max(lo, min(hi, x)))


def target_moods(genre: str, narrative: dict[str, Any] | None = None, story_text: str | None = None
                 ) -> tuple[dict[str, float], list[str]]:
    """목표 분위기와 가중치, 그리고 왜 그렇게 정했는지 (사람이 읽는 문장)."""
    w: dict[str, float] = {GENRE_MOOD[genre]: 1.0}
    why = [f"장르 {GENRE_KO[genre]} → {GENRE_MOOD[genre]}"]

    def add(m: str, v: float, reason: str) -> None:
        w[m] = w.get(m, 0.0) + v
        why.append(f"{reason} → {m}")

    if narrative:
        em = narrative.get("emotion") or {}
        mean = em.get("mean")
        if mean is not None:
            if mean >= 0.25:
                add("warm", 0.8, f"정서 평균 {mean:+.2f}")
            elif mean >= 0.1:
                add("hopeful", 0.8, f"정서 평균 {mean:+.2f}")
            if mean <= -0.2:
                add("melancholic", 0.8, f"정서 평균 {mean:+.2f}")
            if mean <= -0.45:
                add("dark", 0.8, f"정서 평균 {mean:+.2f}")
        ar = [p["arousal"] for p in em.get("series") or [] if p.get("arousal") is not None]
        if ar:
            a = float(np.mean(ar))
            if a >= 0.55:
                add("energetic", 0.8, f"각성도 평균 {a:.2f}")
            elif a <= 0.3:
                add("serene", 0.8, f"각성도 평균 {a:.2f}")
        s = narrative.get("signals") or {}
        rules = [("conflict", 0.5, 1, "tense"), ("uncertainty", 0.55, 1, "mysterious"), ("novelty", 0.6, 1, "whimsical"),
                 ("stability", 0.75, 1, "serene"), ("activity", 0.6, 1, "energetic"),
                 ("possibility", 0.62, 1, "hopeful"), ("possibility", 0.38, -1, "nostalgic")]
        for key, thr, sign, mood in rules:
            v = s.get(key)
            if v is not None and (v - thr) * sign >= 0:
                add(mood, 0.8, f"신호 {key} {v:.2f}")
    if story_text:
        counts = {m: sum(story_text.count(t) for t in toks) for m, toks in TEXT_MOODS.items()}
        total = sum(counts.values())
        for m, c in sorted(counts.items(), key=lambda kv: -kv[1])[:3]:
            if c >= 2:
                add(m, round(0.5 * c / total, 3), f"본문 낱말 {c}회")
    top = dict(sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))[:4])
    return {k: round(v, 3) for k, v in top.items()}, why


def _valence(narrative: dict[str, Any] | None) -> tuple[float, float]:
    if not narrative:
        return 0.0, 0.5
    em = narrative.get("emotion") or {}
    v = em.get("mean")
    ar = [p["arousal"] for p in em.get("series") or [] if p.get("arousal") is not None]
    return (float(v) if v is not None else 0.0), (float(np.mean(ar)) if ar else 0.5)


def retrieve(refs: list[dict[str, Any]], genre: str, moods: dict[str, float], valence: float = 0.0,
             k: int = 6) -> list[dict[str, Any]]:
    """참고 표지 상위 k 장과 점수·이유."""
    total = sum(moods.values()) or 1.0
    want_tone = "light" if valence >= 0.2 else "dark" if valence <= -0.2 else None
    scored = []
    for r in usable(refs):
        lab = r.get("label") or {}
        guess = lab.get("genre_guess")
        s = 0.0
        why = []
        if r["genre"] == genre:
            s += 1.0
            why.append("같은 장르")
            if guess == genre:
                s += 0.25
                why.append("표지도 그 장르로 보임")
        elif guess == genre:
            s += 0.35
            why.append(f"{GENRE_KO[r['genre']]} 표지지만 {GENRE_KO[genre]}로 보임")
        overlap = [m for m in lab.get("moods") or [] if m in moods]
        if overlap:
            s += sum(moods[m] for m in overlap) / total
            why.append("분위기 " + ", ".join(overlap))
        if want_tone and r["features"]["tone"] == want_tone:
            s += 0.2
            why.append(f"{want_tone} 표지")
        if s > 0 and r.get("label_checked"):
            s += 0.1
            why.append("사람이 라벨 검수")
        if s > 0:
            scored.append((round(s, 3), r, why))
    scored.sort(key=lambda t: (-t[0], t[1]["id"]))
    return [{"id": r["id"], "score": s, "why": why, "ref": r} for s, r, why in scored[:k]]


def _choose(field: str, allowed: dict[str, str], prior: dict[str, float], top: list[dict[str, Any]],
            exclude: set[str] = frozenset()) -> tuple[str | None, dict[str, float]]:
    votes: dict[str, float] = {}
    tot = sum(t["score"] for t in top if (t["ref"].get("label") or {}).get(field)) or 1.0
    for t in top:
        v = (t["ref"].get("label") or {}).get(field)
        if v:
            votes[v] = votes.get(v, 0.0) + t["score"] / tot
    scores = {v: round(0.5 * prior.get(v, 0.0) + 0.5 * votes.get(v, 0.0), 3) for v in allowed}
    ranked = [v for v, sc in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])) if sc > 0 and v not in exclude]
    return (ranked[0] if ranked else None), {k: v for k, v in scores.items() if v > 0}


def draw_probabilities(scores: dict[str, float], temperature: float, exclude: set[str] = frozenset()
                       ) -> dict[str, float]:
    """점수 → 뽑을 확률. p ∝ score^(1/T). T=1 이면 점수 비례, 클수록 고르게, 0 이면 가장 큰 값 하나."""
    items = [(v, sc) for v, sc in sorted(scores.items()) if sc > 0 and v not in exclude]
    if not items:
        return {}
    if temperature <= 0:
        best = sorted([v for v, sc in items if sc == max(x[1] for x in items)])[0]
        return {best: 1.0}
    w = [sc ** (1.0 / temperature) for _, sc in items]
    tot = sum(w)
    return {v: round(x / tot, 4) for (v, _), x in zip(items, w)}


def seeded_draw(probs: dict[str, float], seed_text: str) -> str | None:
    """같은 seed_text 면 같은 값 (sha1 을 0..1 균등값으로 바꿔 누적 확률에서 고른다)."""
    if not probs:
        return None
    u = int(hashlib.sha1(seed_text.encode()).hexdigest()[:12], 16) / float(16 ** 12)
    acc = 0.0
    keys = sorted(probs)
    for v in keys:
        acc += probs[v]
        if u < acc:
            return v
    return keys[-1]


def _lch(lab: np.ndarray) -> tuple[float, float, float]:
    L, a, b = lab
    return float(L), float(math.hypot(a, b)), float(math.degrees(math.atan2(b, a)) % 360)


def _from_lch(L: float, C: float, H: float) -> np.ndarray:
    return np.array([L, C * math.cos(math.radians(H)), C * math.sin(math.radians(H))])


def _toward(h: float, target: float, amount: float) -> float:
    d = ((target - h + 180) % 360) - 180
    step = math.copysign(min(abs(d), amount), d)
    return (h + step) % 360


def shift_color(hex_: str, valence: float, arousal: float) -> str:
    L, C, H = _lch(rgb_to_lab(np.array([rgb_of(hex_)], dtype=float))[0])
    L = _clip(L + 8 * valence, 3, 97)
    if C > 6:  # 무채색은 색상각을 돌리지 않는다
        H = _toward(H, 60.0 if valence > 0 else 250.0, abs(valence) * 20)
    C = C * (0.8 + 0.5 * arousal)
    return hex_of(lab_to_rgb(_from_lch(L, C, H))[0])


def build_palette(genre: str, top: list[dict[str, Any]], grammar: dict[str, Any], valence: float, arousal: float,
                  prefer: str | None = None) -> dict[str, Any]:
    cols, w = [], []
    for t in top:
        for p in t["ref"]["features"]["palette"][:4]:
            cols.append(rgb_of(p["hex"]))
            w.append(p["share"] * t["score"])
    gp = (grammar.get("genres", {}).get(genre) or {}).get("palette") or []
    base = [p["hex"] for p in gp] or FALLBACK_PALETTE[genre]
    for i, h in enumerate(base):
        cols.append(rgb_of(h))
        w.append(0.5 * (gp[i]["share"] if gp else 1 / len(base)))
    lab = rgb_to_lab(np.array(cols, dtype=float))
    centers, labels = kmeans(lab, 5, weights=np.array(w))
    share = np.bincount(labels, weights=np.array(w), minlength=len(centers))
    order = np.argsort(-share)
    raw = [hex_of(lab_to_rgb(centers[i])[0]) for i in order]
    # 참고 색이 몇 가지뿐이면 묶음이 5개가 안 된다. 장르 기본 팔레트에서 기존 색과 충분히 다른 것(ΔE > 12)으로 채운다
    for h in FALLBACK_PALETTE[genre]:
        if len(raw) >= 5:
            break
        lab_h = rgb_to_lab(np.array([rgb_of(h)], dtype=float))[0]
        if all(np.linalg.norm(lab_h - rgb_to_lab(np.array([rgb_of(x)], dtype=float))[0]) > 12 for x in raw):
            raw.append(h)
    shifted = [shift_color(h, valence, arousal) for h in raw]
    Ls = [float(rgb_to_lab(np.array([rgb_of(h)], dtype=float))[0, 0]) for h in shifted]
    if prefer == "light":
        bg_i = int(np.argmax(Ls))
    elif prefer == "dark":
        bg_i = int(np.argmin(Ls))
    else:
        bg_i = 0
    bg = shifted[bg_i]
    rest = [h for i, h in enumerate(shifted) if i != bg_i]
    ink = max(rest + ["#111111", "#fbf8f2"], key=lambda h: contrast_ratio(h, bg))
    if contrast_ratio(ink, bg) < 4.5:
        ink = "#111111" if contrast_ratio("#111111", bg) > contrast_ratio("#fbf8f2", bg) else "#fbf8f2"

    def chroma(h: str) -> float:
        return _lch(rgb_to_lab(np.array([rgb_of(h)], dtype=float))[0])[1]

    accents = sorted([h for h in rest if h != ink and contrast_ratio(h, bg) >= 2.0], key=lambda h: -chroma(h))
    accent = accents[0] if accents else ink
    return {"colors": shifted, "background": bg, "ink": ink, "accent": accent, "before_shift": raw,
            "genre_palette": base,
            "shift": {"valence": round(valence, 3), "arousal": round(arousal, 3),
                      "rule": "명도 +8v, 색상각 v>0 따뜻한 쪽·v<0 차가운 쪽 |v|×20°, 채도 ×(0.8+0.5a)"}}


def emotion_curve(narrative: dict[str, Any] | None, n: int = 32) -> list[float]:
    """정서 곡선(valence_smooth)을 n 점으로 줄여 0..1 로. 없으면 완만한 기본 곡선."""
    series = [p["valence"] for p in ((narrative or {}).get("emotion") or {}).get("series") or []
              if p.get("valence") is not None]
    if len(series) < 2:
        return [round(0.5 + 0.12 * math.sin(i / (n - 1) * math.pi * 1.5), 3) for i in range(n)]
    xs = np.linspace(0, len(series) - 1, n)
    ys = np.interp(xs, np.arange(len(series)), series)
    return [round(_clip(0.5 + 0.5 * float(y)), 3) for y in ys]


def motifs(narrative: dict[str, Any] | None, story_text: str | None, title: str | None = None, k: int = 4) -> list[str]:
    out: list[str] = []
    if narrative:
        out.append(narrative.get("keyword") or "")
        out += [t["term"] for t in narrative.get("top_terms") or []][:3]
        out += [t["tag"] for t in narrative.get("top_tags") or []][:2]
    if story_text:
        try:
            from ..textproc import top_nouns

            out += [w for w, _ in top_nouns(re.split(r"\n\s*\n", story_text), k=6)]
        except Exception:  # noqa: BLE001 - 형태소 분석기가 없으면 서사 오브젝트 값만 쓴다
            pass
    seen, res = set(), []
    for m in out:
        m = (m or "").strip()
        if len(m) >= 2 and m not in seen and not re.match(r"지인[A-Z]", m):
            seen.add(m)
            res.append(m)
    return res[:k]


def parse_story(text: str) -> tuple[str | None, list[str]]:
    """/story 출력("TITLE: 제목" + 빈 줄 + 단락) 또는 그냥 단락 글 → (제목, 단락). <em ref> 는 벗긴다."""
    text = (text or "").strip()
    title = None
    m = re.match(r"\s*TITLE\s*:\s*(.+)", text)
    if m:
        title = m.group(1).strip()
        text = text[m.end():]
    text = re.sub(r"</?em[^>]*>", "", text)
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\n", text)]
    return title, [p for p in paras if p]


def art_prompt(genre: str, b: dict[str, Any], motif_words: list[str]) -> str:
    style = STYLE_PHRASE.get(b["art_style"] or "painterly", STYLE_PHRASE["painterly"])
    pal = ", ".join(b["palette"]["colors"])
    mood = ", ".join(b["moods"])
    zone = {"top": "upper third", "middle": "middle", "bottom": "lower third"}.get(b["title_position"] or "top")
    open_area = {"top": "open sky or a plain wall high in the scene", "middle": "a wide stretch of calm water, mist, "
                 "sky or plain wall running through the middle of the scene", "bottom": "plain ground, water or "
                 "shadow low in the scene"}.get(b["title_position"] or "top")
    comp = {
        "full_bleed": f"Full-bleed artwork filling the whole frame edge to edge. The {zone} of the picture must be "
                      f"naturally quiet and low-detail, made of {open_area}, so a title can be set over it later. "
                      "Place the subject outside that area and away from the bottom edge. One continuous photograph "
                      "or painting: the scene runs unbroken from the top edge to the bottom edge with no blank band, "
                      "no panels, bars, borders, frames or split layout.",
        "framed": "A self-contained vignette with one clear subject, simple background, generous breathing room.",
        "split": "One clear subject centred in the frame, simple background, strong silhouette.",
        "pattern": "A seamless decorative repeating pattern of small motifs evenly spread across the frame, "
                   "no focal point.",
        "centered_motif": f"One single emblem-like motif in the centre, filling about two thirds of the frame, on a plain "
                          f"flat background of exactly {b['palette']['background']} that reaches every edge. No scenery, "
                          "no horizon, no vignette.",
        "typographic": "Subtle abstract paper texture with a few soft shapes.",
    }[b["archetype"] or "full_bleed"]
    subject = ", ".join(motif_words) if motif_words else "an everyday moment"
    return (f"Book cover artwork for a Korean {GENRE_KO[genre]} novel ({genre}). {SCENE[genre]}. "
            f"Motifs from the story (Korean words): {subject}. {comp} Style: {style}. Mood: {mood}. "
            f"Use this colour palette: {pal}. {PEOPLE_NOTE}{NO_TEXT}{REF_NOTE}")


def illustration_prompt(genre: str, b: dict[str, Any], scene_text: str) -> str:
    style = STYLE_PHRASE.get(b["illustration_style"] or "line", STYLE_PHRASE["line"])
    return (f"Interior illustration for a Korean {GENRE_KO[genre]} novel, printed inside the book between paragraphs. "
            f"Depict this moment from the story (Korean): «{scene_text[:360]}». Wide landscape composition, one scene, "
            f"edges fading softly into plain off-white paper. Style: {style}. Mood: {', '.join(b['moods'])}. "
            f"Palette: {', '.join(b['palette']['colors'][:4])}. {PEOPLE_NOTE}{NO_TEXT}")


FIELD_ORDER = ("archetype", "title_position", "title_scale", "typeface", "art_style")
MAX_ILLUSTRATIONS = 4


def brief(genre: int | str | None, narrative_object: dict[str, Any] | None = None, story_text: str | None = None,
          refs: list[dict[str, Any]] | None = None, grammar: dict[str, Any] | None = None, k: int = 6,
          title: str | None = None, overrides: dict[str, str] | None = None,
          temperature: float = DEFAULT_TEMPERATURE, variant: int = 0) -> dict[str, Any]:
    """overrides: 사람이 고른 값으로 덮어쓸 칸 (archetype, title_position, title_scale, typeface, art_style).
    통제 어휘 밖의 값은 ValueError.

    칸마다 값은 섞은 분포에서 뽑는다 (temperature, 0 이면 가장 큰 값). 시드는 브리프 번호(입력의 해시)와 칸 이름이라
    같은 입력이면 언제나 같은 값이 나온다. variant 를 바꾸면 같은 이야기로 다른 디자인을 뽑는다."""
    from .chapters import illustration_spot, split_chapters

    g = genre_key(genre)
    refs = load_references() if refs is None else refs
    grammar = load_grammar() if grammar is None else grammar
    gdist = (grammar.get("genres", {}).get(g) or {}).get("dist") or {}
    moods, why = target_moods(g, narrative_object, story_text)
    valence, arousal = _valence(narrative_object)
    top = retrieve(refs, g, moods, valence, k)
    o = dict(overrides or {})
    allowed_map = {"archetype": ARCHETYPES, "title_position": TITLE_POSITIONS, "title_scale": TITLE_SCALES,
                   "typeface": TYPEFACES, "art_style": ART_STYLES}
    for f, v in o.items():
        if f not in allowed_map or v not in allowed_map[f]:
            raise ValueError(f"덮어쓸 수 없는 값: {f}={v}")
    title_s, paras = parse_story(story_text) if story_text else (None, [])
    narrative_id = (narrative_object or {}).get("narrative_id")
    # 브리프 번호는 입력만으로 정한다 (고른 값은 이 번호에서 나온다)
    key = (g, sorted(moods.items()), narrative_id, sorted(o.items()), [t["id"] for t in top], (story_text or "")[:2000],
           round(float(temperature), 3), int(variant))
    brief_id = hashlib.sha1(repr(key).encode()).hexdigest()[:10]
    fb = FALLBACK[g]
    defaults = {"archetype": fb[0], "title_position": "top", "title_scale": "large", "typeface": fb[1],
                "art_style": fb[2]}
    decision: dict[str, Any] = {}
    chosen: dict[str, str | None] = {}
    for field in FIELD_ORDER:
        exclude: set[str] = set()
        if field == "title_position":
            exclude = set(CONFLICTS.get(chosen["archetype"] or "", set()))
        if field == "art_style":
            exclude = {"none"} if chosen["archetype"] != "typographic" else set()
        _, scores = _choose(field, allowed_map[field], gdist.get(field) or {}, top)
        if field in o:
            chosen[field] = o[field]
            decision[field] = {"chosen": o[field], "scores": scores, "from": "사람이 고름"}
            continue
        if field == "art_style" and chosen["archetype"] == "typographic":
            chosen[field] = "none"
            decision[field] = {"chosen": "none", "scores": scores, "from": "원형이 글자형이라 그림 없음"}
            continue
        probs = draw_probabilities(scores, temperature, exclude)
        v = seeded_draw(probs, f"{brief_id}|{field}")
        chosen[field] = v or defaults[field]
        decision[field] = {"chosen": chosen[field], "scores": scores, "probabilities": probs,
                           "from": "참고 표지 분포에서 뽑음" if v else "기본값"}
    arch, pos, scale, face, art = (chosen[f] for f in FIELD_ORDER)
    if pos in CONFLICTS.get(arch, set()):
        pos = "top"
        decision["title_position"]["chosen"] = pos
    if arch != "typographic" and art == "none":
        art = fb[2]
        decision["art_style"]["chosen"] = art
    # 삽화는 표지와 같은 그림 양식으로 (표지 그림을 스타일 참고로 넘긴다). 글자형 표지면 장르 분포에서 사진 말고 뽑는다
    if art and art != "none":
        illus = art
    else:
        _, sc = _choose("art_style", ART_STYLES, gdist.get("art_style") or {}, top)
        illus = seeded_draw(draw_probabilities(sc, temperature, {"none", "photo"}), f"{brief_id}|illustration_style") \
            or "line"
    tone = (grammar.get("genres", {}).get(g) or {}).get("tone") or {}
    prefer = "light" if valence >= 0.2 else "dark" if valence <= -0.2 else (
        "dark" if tone.get("dark", 0) > 0.5 else "light" if tone.get("light", 0) > 0.4 else None)
    pal = build_palette(g, top, grammar, valence, arousal, prefer)
    b: dict[str, Any] = {
        "schema": "virtueme.book_brief/2",
        "genre": g, "genre_name": GENRE_KO[g],
        "moods": list(moods), "mood_weights": moods, "mood_reasons": why,
        "archetype": arch, "title_position": pos, "title_scale": scale, "typeface": face,
        "art_style": art, "illustration_style": illus,
        "palette": pal, "emotion_curve": emotion_curve(narrative_object),
        "narrative_id": narrative_id,
        "references": [{"id": t["id"], "score": t["score"], "why": t["why"], "genre": t["ref"]["genre"],
                        "title": t["ref"]["provenance"].get("title"), "label": t["ref"].get("label"),
                        "label_checked": bool(t["ref"].get("label_checked")),
                        "source_url": t["ref"]["provenance"].get("source_url")} for t in top],
        "decision": decision,
        "draw": {"temperature": temperature, "variant": variant, "seed": f"{brief_id}|<칸 이름>",
                 "rule": "p ∝ (0.5×장르 분포 + 0.5×참고 표지 표)^(1/T)"},
        "grammar_used": bool(gdist),
    }
    mw = motifs(narrative_object, "\n\n".join(paras) if paras else None, title or title_s)
    b["motifs"] = mw
    b["art_prompt"] = art_prompt(g, b, mw) if arch != "typographic" else None
    chapters = []
    for i, (a, e) in enumerate(split_chapters(paras)):
        ch: dict[str, Any] = {"index": i + 1, "start": a, "end": e}
        if i < MAX_ILLUSTRATIONS and e - a >= 2:
            spot = illustration_spot(paras, a, e)
            ch["illustration_after"] = spot
            ch["illustration_prompt"] = illustration_prompt(g, b, paras[spot])
        chapters.append(ch)
    b["chapters"] = chapters
    b["brief_id"] = brief_id
    return b
