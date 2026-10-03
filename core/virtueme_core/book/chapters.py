"""이야기를 장으로 나누고, 장 제목과 뒤표지 소개글을 만든다.

나누기 (`split_chapters`, 결정적)
- 장면 구분 줄(`***`, `* * *`, `---`, `#`, `◇` 등)이 있으면 그 자리에서 먼저 자른다.
- 장 수는 본문 글자 수로 정한다: 1,000자 미만이면 한 장, 그 이상이면 800자마다 한 장(2~5장). 장마다 단락이 둘 이상이어야 한다.
- 자를 자리는 같은 길이로 나눈 목표 지점에 가까운 단락 경계다. 다음 단락이 시간·장면 전환 말("다음 날", "그날 밤",
  "8월" 등)로 시작하면 가산점을 준다.

장 제목 (`chapter_title`, 결정적): 장 안에서 대사가 아닌 짧은 단락(4~18자) 가운데 장 끝에 가까운 것을 쓴다.
없으면 첫 문장을 어절 단위로 18자 안에서 자른다. 사람이나 언어 모델이 정한 제목이 있으면 그것을 쓴다.

소개글 (`blurb`): 언어 모델이 있으면 한 번 불러(`outbound` 로그에 "책 뒤표지 소개글") 소개글 두세 문장과 장 제목을 받는다.
결과는 책 폴더의 `blurb.json` 에 본문 해시로 남겨 다시 사지 않는다. 키가 없거나 실패하면 첫 장에서 대사가 아닌 문장
두세 개를 뽑아 쓴다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

BREAK_RE = re.compile(r"^\s*(\*\s*\*\s*\*|\*{3,}|-{3,}|#{1,3}|◇+|◆+|○+|§|~{3,}|\d+\s*\.?)\s*$")
TURN_RE = re.compile(r"^(다음 날|그다음|그날|이튿날|며칠|일주일|한 달|몇 주|몇 달|어느 날|열흘|아침|저녁|밤|새벽|그 주|"
                     r"\d+월|[일이삼사오육칠팔구십]+월|월요일|화요일|수요일|목요일|금요일|토요일|일요일|"
                     r"그리고 어느|그 뒤|그 후|이후|다시|8월|9월)")
CHARS_PER_CHAPTER = 800
SINGLE_CHAPTER_BELOW = 1000
MAX_CHAPTERS = 5
BLURB_MODEL = os.environ.get("VIRTUEME_BOOK_BLURB_MODEL", "google/gemini-2.5-flash")
BLURB_PROMPT_VERSION = "blurb-v2"


def _is_dialogue(p: str) -> bool:
    return p.lstrip()[:1] in "\"'“‘「『"


def chapter_count(paras: list[str]) -> int:
    chars = sum(len(p) for p in paras)
    if chars < SINGLE_CHAPTER_BELOW or len(paras) < 4:
        return 1
    return max(2, min(MAX_CHAPTERS, round(chars / CHARS_PER_CHAPTER), len(paras) // 2))


def _cut_by_length(paras: list[str], n: int, lo: int, hi: int) -> list[int]:
    """paras[lo:hi] 를 n 조각으로 나누는 단락 경계(시작 번호) n-1 개."""
    if n <= 1 or hi - lo < 2 * n:
        return []
    lens = [len(p) for p in paras[lo:hi]]
    total = sum(lens)
    cum = [0]
    for x in lens:
        cum.append(cum[-1] + x)
    cuts: list[int] = []
    prev = 0
    for j in range(1, n):
        target = total * j / n
        best, best_s = None, -1e9
        for b in range(prev + 2, len(lens) - 2 * (n - j) + 1):
            s = -abs(cum[b] - target) / total
            if TURN_RE.match(paras[lo + b]):
                s += 0.06
            if _is_dialogue(paras[lo + b]):
                s -= 0.04  # 대사로 장을 열지 않는다
            if s > best_s:
                best, best_s = b, s
        if best is None:
            break
        cuts.append(lo + best)
        prev = best
    return cuts


def split_chapters(paras: list[str], n: int | None = None) -> list[tuple[int, int]]:
    """단락 목록 → [(시작, 끝)] (끝은 포함하지 않음). 장면 구분 줄은 결과에 들어가지 않게 build 쪽에서 뺀다."""
    if not paras:
        return []
    breaks = [i for i, p in enumerate(paras) if BREAK_RE.match(p)]
    if breaks:
        out, start = [], 0
        for b in breaks + [len(paras)]:
            if b > start:
                out.append((start, b))
            start = b + 1
        # 장면 구분이 너무 많으면 짧은 것끼리 붙인다
        while len(out) > MAX_CHAPTERS:
            i = min(range(len(out) - 1), key=lambda k: sum(len(p) for p in paras[out[k][0]:out[k + 1][1]]))
            out[i:i + 2] = [(out[i][0], out[i + 1][1])]
        return out
    n = n or chapter_count(paras)
    cuts = _cut_by_length(paras, n, 0, len(paras))
    bounds = [0] + cuts + [len(paras)]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def _first_sentence(p: str) -> str:
    m = re.match(r"(.+?[.!?。])(\s|$)", p)
    return (m.group(1) if m else p).strip()


def _trim_words(s: str, limit: int = 18) -> str:
    out = ""
    for w in s.split():
        if len(out) + len(w) + (1 if out else 0) > limit:
            break
        out = f"{out} {w}" if out else w
    return out or s[:limit]


def chapter_title(paras: list[str]) -> str:
    """장 안의 짧고 대사가 아닌 단락 (끝에 가까울수록 우선). 없으면 첫 문장을 18자 안으로."""
    cands = [p for p in paras if 4 <= len(p) <= 18 and not _is_dialogue(p) and not BREAK_RE.match(p)
             and not re.search(r"[:：]|\.jpg|\d{4}년", p)]
    if cands:
        t = cands[-1]
    else:
        first = next((p for p in paras if not _is_dialogue(p)), paras[0])
        t = _trim_words(_first_sentence(first))
    return re.sub(r"[.,。、]+$", "", t).strip()


def illustration_spot(paras: list[str], start: int, end: int) -> int:
    """장 안에서 삽화를 넣을 단락 번호 (그 단락 뒤에 넣는다). 장 가운데 절반의 묘사 단락 가운데 가장 긴 것.
    그 자리에 그림이 안 들어가면 렌더러가 그 쪽 맨 앞으로 그림을 올려 장을 다시 앉힌다 (render.py)."""
    n = end - start
    if n <= 2:
        return start
    lo, hi = start + max(1, n // 4), start + max(2, (3 * n) // 4)
    cands = [i for i in range(lo, min(hi, end - 1) + 1) if not _is_dialogue(paras[i])] or list(range(lo, min(hi, end - 1) + 1))
    return max(cands, key=lambda i: (len(paras[i]), -abs(i - (start + n // 2))))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.findall(r"[^.!?。]+[.!?。]", text) if s.strip()]


def fallback_blurb(paras: list[str], chapters: list[tuple[int, int]], limit: int = 150) -> str:
    """첫 장에서 대사가 아닌 문장을 앞에서부터 limit 자까지. 날짜·숫자만 있는 문장은 뺀다."""
    end = chapters[0][1] if chapters else len(paras)
    out: list[str] = []
    for p in paras[:end]:
        if _is_dialogue(p):
            continue
        for s in sentences(p):
            if len(re.sub(r"[\d\s.:년월일시분km]", "", s)) < 6:
                continue
            if sum(len(x) for x in out) + len(s) > limit and out:
                return " ".join(out)
            out.append(s)
    return " ".join(out)


def _drop_rhetorical(blurb: str) -> str:
    """모델이 지시를 어기고 붙이는 「과연 ~일까?」 마지막 문장을 뗀다 (두 문장 넘게 남을 때만)."""
    ss = sentences(blurb)
    if len(ss) >= 3 and re.match(r"과연|과연,", ss[-1]):
        return " ".join(ss[:-1])
    return blurb


BLURB_SYSTEM = (
    "너는 한국어 소설의 편집자다. 주어진 이야기의 뒤표지 소개글과 장 제목을 쓴다. "
    "소개글은 두세 문장, 120자 안팎, 결말을 밝히지 않고 첫 장면의 궁금증으로 끝낸다. 줄표(—)·말줄임표·감탄사 남용 금지. "
    "'과연'으로 여는 질문, '~일까?'로 끝나는 수사 의문문은 쓰지 않는다. 마지막 문장은 장면이나 사실로 끝낸다. "
    "장 제목은 장마다 하나, 2~14자, 그 장의 장면에서 나온 명사구나 짧은 문장. 숫자 번호는 붙이지 않는다. "
    'JSON 만 답하라: {"blurb": "...", "chapter_titles": ["...", ...]}'
)


def make_blurb(title: str, genre_name: str, paras: list[str], chapters: list[tuple[int, int]], out_dir: Path | None,
               profile: str | None, use_llm: bool, log: Callable[[str], None] = print) -> dict[str, Any]:
    """반환: {blurb, chapter_titles, source(llm|fallback|cache), cost_usd}."""
    titles = [chapter_title(paras[a:b]) for a, b in chapters]
    fb = {"blurb": fallback_blurb(paras, chapters), "chapter_titles": titles, "source": "fallback", "cost_usd": 0.0}
    if not use_llm:
        return fb
    body = "\n".join(paras)
    key = hashlib.sha1(f"{BLURB_MODEL}|{BLURB_PROMPT_VERSION}|{title}|{chapters}|{body}".encode()).hexdigest()[:16]
    cache = out_dir / "blurb.json" if out_dir else None
    if cache and cache.exists():
        got = json.loads(cache.read_text("utf-8"))
        if got.get("key") == key:
            return {**got, "blurb": _drop_rhetorical(got.get("blurb") or fb["blurb"]), "source": "cache", "cost_usd": 0.0}
    from ..llm import LLMUnavailable
    from . import net

    text = f"제목: {title}\n장르: {genre_name}\n장 수: {len(chapters)}\n\n" + "\n\n".join(
        f"[{i + 1}장]\n" + "\n".join(paras[a:b]) for i, (a, b) in enumerate(chapters))
    try:
        data, usage = net.vision_json(BLURB_SYSTEM, text, [], BLURB_MODEL, "책 뒤표지 소개글", net.outbound_for(profile),
                                      image_note="없음", max_tokens=800)
    except (LLMUnavailable, RuntimeError) as err:
        log(f"  소개글 모델 실패, 본문 문장으로 대신함: {err}")
        return fb
    blurb = _drop_rhetorical(str((data or {}).get("blurb") or "").strip().replace("—", ", "))
    got_titles = [str(t).strip() for t in (data or {}).get("chapter_titles") or []]
    if len(got_titles) == len(chapters) and all(1 <= len(t) <= 16 for t in got_titles):
        titles = [re.sub(r"^\d+\s*[.장:]\s*", "", t) for t in got_titles]
    res = {"blurb": blurb if 20 <= len(blurb) <= 260 else fb["blurb"], "chapter_titles": titles, "source": "llm",
           "model": BLURB_MODEL, "cost_usd": round(float(usage.get("cost") or 0), 5), "key": key}
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    return res
