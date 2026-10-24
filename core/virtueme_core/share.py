"""공유 카드와 공유 페이지.

사용자가 직접 고른 것 하나(발견 문장, 책 표지, 쓴 이야기의 한 대목)를 인스타그램 피드(1080×1350)와 스토리(1080×1920)
크기의 이미지 카드로 만들고, 링크로 열리는 읽기 전용 페이지(`/s/<번호>`)를 붙인다. 실제 SNS 에 자동으로 올리지 않는다.
사용자가 PNG 를 내려받아 직접 올리거나 링크를 붙여 넣는다.

카드에 들어가는 글은 모두 `public_text` 를 거친다.
- 가명표의 실명은 가명으로 가린 뒤 다시 관계("대학 친구")나 "그 사람"으로, 본인 이름은 "나"로 바꾼다. 가명(지인A)은 남기지 않는다.
- 집·직장으로 기록된 장소의 원래 이름은 "집"·"직장"으로 바꾼다.
- 좌표, 장소 키, 주소, 전화번호·이메일 같은 값이 있으면 카드를 만들지 않는다 (지우고 만들면 문장 뜻이 바뀌므로).
- 마지막으로 `Outbound.guard` 와 같은 이름 검사를 한 번 더 하고, 렌더한 카드에 실제로 찍힌 글자도 다시 검사한다.

카드는 책과 같은 방식(HTML → virme/ 의 Playwright)으로 그린다. 책이 있으면 그 책 브리프의 팔레트와 제목 서체를, 없으면
기본 종이색을 쓴다. 공유 페이지에서 방문자는 정해진 반응 네 가지 가운데 하나와 100자 이내 한마디를 남길 수 있다.
한마디는 주인만 앱에서 보고, 페이지에는 반응 수만 보인다.

저장 위치: `$VIRTUEME_DATA/shares/<프로필>/<번호>/` (share.json, card.html, feed.png, story.png, 책이면 cover.png,
reactions.jsonl). 프로필을 지우면 같이 지워진다.
"""
from __future__ import annotations

import html
import json
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .privacy import _PII_PATTERNS, SELF_ALIAS, Masker

HERE = Path(__file__).resolve().parent
SHARE_ID = re.compile(r"^[0-9a-f]{12}$")
KINDS = {"discovery": "발견", "book": "책 표지", "passage": "이야기 한 대목", "webzine": "웹진 한 호"}
FILES = ("feed.png", "story.png", "cover.png")
SIZES = {"feed": (1080, 1350), "story": (1080, 1920)}

# 방문자 반응: 정해진 것만 받는다 (자유 입력은 한마디 100자뿐)
REACTIONS = {"like": "❤️ 좋아요", "same": "🙌 나도 그래요", "cheer": "👏 응원해요", "wow": "😮 몰랐어요"}
COMMENT_MAX = 100
MAX_REACTIONS_PER_SHARE = 500
RATE_GAP_SEC = 10          # 같은 곳(IP)에서 다음 반응까지
RATE_PER_HOUR = 12         # 같은 곳에서 한 시간에

ELEMENT = {"cast": "인물", "place": "배경", "rhythm": "리듬", "taste": "취향", "turn": "전환점"}
DEFAULT_STYLE = {"bg": "#f3eee5", "ink": "#1f1b16", "accent": "#b8471f", "tf": "BkSerif", "tw": 800, "tls": "-0.01em",
                 "art": None, "genre": None, "from_book": None}


class ShareError(ValueError):
    """카드를 만들지 않은 이유 (화면에 그대로 보여 준다)."""


def shares_dir(profile: str) -> Path:
    safe = "".join(ch for ch in profile if ch.isalnum() or ch in "-_") or "default"
    return DATA_DIR / "shares" / safe


# ---------------------------------------------------------------- 공개용 글 다듬기
ALIAS_RE = re.compile(r"지인([A-Z]{1,2})(이가|이랑|에게|한테|이야|가|이|은|는|을|를|와|과|아|야|랑|의|도|만)?")
MASK_RE = re.compile(r"\((나|누군가)\)(이가|이랑|이는|이도|이를|이야|이한테|가|이|은|는|을|를|랑|아|야|씨|님|의|도|만|한테)?")
SELF_FORM = {"이가": "내가", "가": "내가", "이": "나", "이는": "나는", "는": "나는", "은": "나는", "이를": "나를", "를": "나를",
             "을": "나를", "이랑": "나랑", "랑": "나랑", "이도": "나도", "도": "나도", "이한테": "나한테", "한테": "나한테",
             "의": "나의", "만": "나만", "이야": "나야", "아": "", "야": "", "씨": "", "님": ""}
COORD_RE = re.compile(r"(?<![\d.])-?\d{1,3}\.\d{4,}(?!\d)")
PLACE_KEY_RE = re.compile(r"(?<![A-Za-z])(?:id|pt):[A-Za-z0-9_-]{4,}")
# 도로명 주소("망원로 12-3")는 길 이름 바로 뒤 건물 번호로 본다. 조사 "으로"·소수("1.7시간")·단위("3번")는 주소가 아니다
ADDRESS_RE = re.compile(r"\d+\s*번지|[가-힣]{1,12}(?<!으)(?:로|길)\s?\d+(?:-\d+)?(?![\d.,가-힣A-Za-z])|\d+동\s?\d+호")
PII_TOKEN_RE = re.compile(r"\[(?:링크|이메일|주민번호|카드번호|전화번호|계좌번호|계정)[^\]]*\]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


def _jong(w: str) -> bool:
    c = ord(w[-1]) - 0xAC00 if w else -1
    return 0 <= c <= 11171 and c % 28 != 0


def with_particle(who: str, pt: str | None) -> str:
    """바꾼 이름의 받침에 맞춰 조사를 고친다. 부르는 말(아·야)은 뗀다."""
    if not pt:
        return who
    j = _jong(who)
    fix = {"가": "이" if j else "가", "이": "이" if j else "가", "이가": "이" if j else "가", "은": "은" if j else "는",
           "는": "은" if j else "는", "을": "을" if j else "를", "를": "을" if j else "를", "와": "과" if j else "와",
           "과": "과" if j else "와", "랑": "이랑" if j else "랑", "이랑": "이랑" if j else "랑", "아": "", "야": "",
           "이야": "이야" if j else "야"}
    return who + fix.get(pt, pt)


def unalias(text: str, relations: dict[str, str | None]) -> tuple[str, int]:
    """가명은 관계로(관계가 없으면 "그 사람"), 가린 표기 "(나)"·"(누군가)" 는 "나"·"그 사람"으로. 반환: (글, 바꾼 수)."""
    n = 0

    def alias(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return with_particle(relations.get(f"지인{m.group(1)}") or "그 사람", m.group(2))

    def masked(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        who, pt = m.group(1), m.group(2)
        if who == SELF_ALIAS:
            return SELF_FORM.get(pt, "나" + pt) if pt else "나"
        if pt in ("님", "씨"):
            return "그 사람"
        return with_particle("그 사람", pt[1:] if pt and pt.startswith("이") and len(pt) > 1 else pt)

    out = ALIAS_RE.sub(alias, text)
    out = MASK_RE.sub(masked, out)
    return out, n


def private_place_names(store) -> dict[str, str]:
    """집·직장으로 기록된 장소에 붙어 있던 원래 이름 → "집"/"직장". 사용자가 그 장소 키에 붙인 이름도 같이."""
    from .columns import _place_key, place_names

    out: dict[str, str] = {}
    given = place_names(store)
    for r in store.records():
        kind = (r.meta.get("place_type") or "").upper()
        if kind not in ("HOME", "WORK"):
            continue
        label = "집" if kind == "HOME" else "직장"
        if r.place and r.place not in ("집", "직장", "장소", "이름 없는 장소") and len(r.place) >= 2:
            out[r.place] = label
        key = _place_key(r)
        if key and given.get(key) and given[key] not in ("집", "직장"):
            out[given[key]] = label
    return out


def leaks(text: str, store) -> list[str]:
    """공개 글에 남으면 안 되는 것. 비어 있어야 카드를 만든다."""
    found = []
    m = Masker(store.people())
    if any(p.search(text) for p, _ in m._names):
        found.append("실명")
    if re.search(r"지인[A-Z]{1,2}", text):
        found.append("가명")
    if MASK_RE.search(text):
        found.append("가린 표기")
    if COORD_RE.search(text) or PLACE_KEY_RE.search(text):
        found.append("좌표")
    if ADDRESS_RE.search(text):
        found.append("주소")
    if PII_TOKEN_RE.search(text) or any(p.search(text) for p, _ in _PII_PATTERNS):
        found.append("연락처·링크")
    if any(name in text for name in private_place_names(store)):
        found.append("집·직장 이름")
    return found


def public_text(text: str, store, outbound=None) -> tuple[str, list[str]]:
    """카드·공유 페이지에 싣는 글. 반환: (다듬은 글, 바꾼 것 목록). 좌표·주소·연락처가 있으면 ShareError."""
    raw = CONTROL_RE.sub("", (text or "").replace("\r", "")).strip()
    if COORD_RE.search(raw) or PLACE_KEY_RE.search(raw):
        raise ShareError("좌표나 장소 번호처럼 보이는 값이 있어 카드를 만들지 않았습니다. 그 부분을 빼고 다시 골라 주세요.")
    if ADDRESS_RE.search(raw):
        raise ShareError("주소처럼 보이는 글이 있어 카드를 만들지 않았습니다. 그 부분을 빼고 다시 골라 주세요.")
    changes: list[str] = []
    if outbound is not None:
        _, found = outbound.guard(raw)  # 무엇이 들어 있었는지 종류만 센다 (실명은 남기지 않는다)
        changes += ["본인 이름 → '나'" if k == "본인 이름" else "다른 사람 이름 → 관계" if "이름" in k else k for k in found]
    out = Masker.from_store(store).mask(raw) or ""
    if PII_TOKEN_RE.search(out):
        raise ShareError("전화번호·이메일·링크 같은 값이 있어 카드를 만들지 않았습니다. 그 부분을 빼고 다시 골라 주세요.")
    out, n = unalias(out, store.relations())
    if n and not changes:
        changes.append("가명 → 관계")
    for name, label in sorted(private_place_names(store).items(), key=lambda kv: -len(kv[0])):
        if name in out:
            out = out.replace(name, label)
            changes.append(f"{label} 이름 → '{label}'")
    left = leaks(out, store)
    if left:
        raise ShareError(f"공개하면 안 되는 값({', '.join(left)})이 남아 카드를 만들지 않았습니다.")
    return out, sorted(set(changes))


# ---------------------------------------------------------------- 스타일
def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ra, rb))


def style_from_book(book_dir: Path | None) -> dict[str, Any]:
    """책 브리프의 팔레트·제목 서체. 책이 없거나 읽지 못하면 기본값."""
    if not book_dir or not (book_dir / "brief.json").exists():
        return dict(DEFAULT_STYLE)
    from .book.features import contrast_ratio
    from .book.render import title_font

    try:
        b = json.loads((book_dir / "brief.json").read_text("utf-8"))
        pal = b["palette"]
        bg, ink = pal["background"], pal["ink"]
        # 강조색은 바탕 위에서 글자로 읽혀야 한다 (대비 3 이상). 없으면 글자색
        cands = [pal["accent"], *pal.get("colors", [])]
        accent = next((c for c in cands if contrast_ratio(c, bg) >= 3), ink)
        tf, tw, tls = title_font(b.get("typeface") or "serif", b.get("genre") or "literary")
    except (OSError, ValueError, KeyError):
        return dict(DEFAULT_STYLE)
    art = book_dir / "cover_art.png"
    return {"bg": bg, "ink": ink, "accent": accent, "tf": tf, "tw": tw, "tls": tls,
            "art": art if art.exists() else None, "genre": b.get("genre_name"), "from_book": book_dir.name}


# ---------------------------------------------------------------- 카드 HTML
def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


NUM_RE = re.compile(r"(\d[\d,.]*\s?(?:%|보|km|시간|번|위))")


def _words(text: str, numbers: bool = False) -> str:
    """어절마다 줄바꿈 없는 상자로 싼다. 줄은 어절 사이에서만 바뀌고(마침표가 줄 첫머리로 가지 않음), 가장 긴 어절이
    폭을 넘으면 글자 크기를 줄인다 (CARD_JS)."""
    out = []
    for w in (text or "").split():
        e = _esc(w)
        if numbers:
            e = NUM_RE.sub(r"<span class='num'>\1</span>", e)
        out.append(f"<span class='w'>{e}</span>")
    return " ".join(out)


CARD_CSS = r"""
* { box-sizing: border-box; margin: 0; padding: 0 }
body { background: #888; padding: 40px; display: flex; flex-direction: column; gap: 40px; align-items: flex-start;
       font-family: 'BkSans', 'Apple SD Gothic Neo', sans-serif; -webkit-font-smoothing: antialiased }
.card { flex: none; position: relative; overflow: hidden; background: var(--bg); color: var(--ink); display: flex; flex-direction: column;
        word-break: keep-all; overflow-wrap: break-word; line-break: strict }
.card.feed { width: 1080px; height: 1350px; padding: 92px 96px 84px }
.card.story { width: 1080px; height: 1920px; padding: 250px 96px 300px }
.w { white-space: nowrap }
.top { display: flex; align-items: center; gap: 18px; font-size: 30px; font-weight: 600; color: var(--sub); letter-spacing: -0.01em;
       position: relative; z-index: 2 }
.tag { font-size: 26px; font-weight: 600; padding: 6px 18px 7px; border-radius: 999px; border: 2px solid var(--line); color: var(--sub) }
.mid { flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: center; position: relative; z-index: 2 }
.inner { width: 100% }
.bar { width: 84px; height: 8px; background: var(--accent); border-radius: 4px; margin-bottom: 52px }
.main { font-family: 'BkSerif', serif; font-weight: 800; line-height: 1.48; letter-spacing: -0.02em }
.main .num { color: var(--accent) }
.foot { display: flex; align-items: flex-end; justify-content: space-between; gap: 32px; font-size: 27px; color: var(--sub);
        position: relative; z-index: 2; line-height: 1.5; margin-top: 28px }
.foot .src { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis }
.mark { display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 28px; color: var(--ink); white-space: nowrap;
        letter-spacing: 0.02em }
.mark i { display: block; width: 18px; height: 18px; border-radius: 50%; background: var(--accent) }
.ttl { font-family: var(--tf); font-weight: var(--tw); letter-spacing: var(--tls); color: var(--ink) }
.passage { font-family: 'BkSerif', serif; font-weight: 400; line-height: 1.78; letter-spacing: -0.015em;
           border-left: 8px solid var(--accent); padding-left: 52px }
.passage p + p { margin-top: 0.62em }
.k-passage .ttl, .k-webzine .ttl { font-size: 46px; line-height: 1.3; margin: 0 0 56px }
.story.k-passage .ttl, .story.k-webzine .ttl { font-size: 54px }
.hand.k-passage .ttl { font-size: 62px }
.hand.story.k-passage .ttl { font-size: 72px }
.head { display: flex; align-items: center; gap: 36px; margin: 0 0 56px }
.head .ttl { margin: 0 }
.thumb { flex: none; width: 132px; height: 132px; border-radius: 16px; background-size: cover; background-position: center;
         box-shadow: 0 6px 18px rgba(0,0,0,.18) }
.story .thumb { width: 168px; height: 168px }
.k-book { background: var(--bg2) }
.k-book .inner { display: flex; flex-direction: column; align-items: center; text-align: center }
.cover { display: block; border-radius: 6px; box-shadow: 0 28px 60px rgba(0,0,0,.28), 0 4px 12px rgba(0,0,0,.18) }
.feed .cover { height: 760px }
.story .cover { height: 980px }
.blurb { font-family: 'BkSerif', serif; font-weight: 400; line-height: 1.62; margin-top: 56px; max-width: 860px; color: var(--ink) }
.story .blurb { margin-top: 80px }
"""

CARD_JS = r"""
(async () => {
  await document.fonts.ready
  const report = { fit: [] }
  for (const el of document.querySelectorAll('.fit')) {
    const box = el.closest('.mid'), inner = el.closest('.inner')
    let size = parseFloat(el.dataset.max)
    const min = parseFloat(el.dataset.min)
    el.style.fontSize = size + 'px'
    const words = [...el.querySelectorAll('.w')]
    const cs = getComputedStyle(box)
    const room = box.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom)
    const over = () => {
      if (inner.offsetHeight > room + 1) return true
      const right = el.getBoundingClientRect().right + 1
      return words.some((w) => w.getBoundingClientRect().right > right)
    }
    while (over() && size > min) { size = Math.max(min, size * 0.95); el.style.fontSize = size + 'px' }
    report.fit.push({ card: el.closest('.card').id, size: Math.round(size * 10) / 10, overflow: over() })
  }
  window.__cardReport = report
  window.__cardDone = true
})()
"""


def card_html(spec: dict[str, Any], style: dict[str, Any], cover_uri: str | None = None) -> str:
    """피드(#feed)와 스토리(#story) 카드 두 장이 든 HTML. spec: kind, text/lines, title, tag, scope, source."""
    from .book.render import FALLBACK_STACK, _font_css

    bg, ink, accent = style["bg"], style["ink"], style["accent"]
    # 책 표지 카드는 표지가 바탕에 묻히지 않게 바탕을 강조색 쪽으로 조금 옮긴다
    vars_ = (f"--bg:{bg};--bg2:{_mix(bg, accent, 0.16)};--ink:{ink};--accent:{accent};--sub:{_mix(ink, bg, 0.38)};--line:{_mix(ink, bg, 0.72)};"
             f"--tf:'{style['tf']}';--tw:{style['tw']};--tls:{style['tls']}")
    kind = spec["kind"]
    art = style.get("art")
    thumb = (f"<div class='thumb' style=\"background-image:url('{art.as_uri()}')\"></div>"
             if kind == "passage" and art is not None else "")
    mark = "<div class='mark'><i></i>버추얼미</div>"

    def one(fmt: str) -> str:
        big = fmt == "story"
        top = (f"<div class='top'><span class='label'>{_esc(spec['label'])}</span>"
               + (f"<span class='tag'>{_esc(spec['tag'])}</span>" if spec.get("tag") else "") + "</div>")
        foot = f"<div class='foot'><span class='src'>{_esc(spec.get('source', ''))}</span>{mark}</div>"
        if kind == "discovery":
            mx, mn = (88, 44) if big else (76, 40)
            body = (f"<div class='inner'><div class='bar'></div>"
                    f"<p class='main fit' data-max='{mx}' data-min='{mn}'>{_words(spec['text'], numbers=True)}</p></div>")
        elif kind in ("passage", "webzine"):
            mx, mn = (64, 34) if big else (56, 30)
            title = f"<div class='ttl'>{_words(spec['title'])}</div>" if spec.get("title") else ""
            if title or thumb:
                title = f"<div class='head'>{thumb}{title}</div>"
            lines = "".join(f"<p>{_words(x)}</p>" for x in spec["lines"])
            body = f"<div class='inner'>{title}<div class='passage fit' data-max='{mx}' data-min='{mn}'>{lines}</div></div>"
        else:
            mx, mn = (40, 26) if big else (34, 24)
            img = f"<img class='cover' src='{cover_uri}' alt=''>" if cover_uri else ""
            blurb = f"<p class='blurb fit' data-max='{mx}' data-min='{mn}'>{_words(spec.get('text', ''))}</p>" if spec.get("text") else ""
            body = f"<div class='inner'>{img}{blurb}</div>"
        hand = " hand" if style["tf"] in ("BkPen", "BkBrush") else ""
        return f"<div class='card {fmt} k-{kind}{hand}' id='{fmt}'>{top}<div class='mid'>{body}</div>{foot}</div>"

    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>card</title>"
            f"<style>{_font_css()}\n{CARD_CSS.replace('sans-serif', FALLBACK_STACK)}</style></head>"
            f"<body style=\"{vars_}\">{one('feed')}{one('story')}<script>{CARD_JS}</script></body></html>")


def run_render(html_path: Path, out_dir: Path, shots: list[tuple[str, str]], kind: str = "card", scale: int = 1,
               timeout: int = 180) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node 가 없습니다 (virme/ 의 playwright 로 카드를 그립니다)")
    res = subprocess.run([node, str(HERE / "share_render.mjs"), str(html_path), str(out_dir), json.dumps(shots), kind,
                          str(scale)], capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"카드 렌더 실패: {res.stderr[-1500:]}")
    return json.loads(res.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------- 공유 페이지
PAGE_CSS = r"""
:root { color-scheme: dark }
* { box-sizing: border-box }
body { margin: 0; background: #0e0f16; color: #eceae4; font: 16px/1.75 -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo',
       'Pretendard', system-ui, sans-serif; word-break: keep-all; overflow-wrap: break-word }
main { max-width: 560px; margin: 0 auto; padding: 28px 18px 64px }
.brand { font-size: 13px; font-weight: 700; letter-spacing: .04em; color: #c3c2b7; margin: 0 0 16px }
.cardimg { display: block; width: 100%; height: auto; border-radius: 14px; box-shadow: 0 16px 40px rgba(0,0,0,.45) }
.alt { margin: 10px 0 0; font-size: 13px; color: #898781 }
.alt a { color: #c3c2b7 }
h1 { font-size: 21px; line-height: 1.45; margin: 26px 0 8px }
blockquote { margin: 18px 0 0; padding: 4px 0 4px 18px; border-left: 3px solid #d95926; font-size: 17px; line-height: 1.85; color: #fff }
blockquote p { margin: 0 0 8px }
.cover { display: block; width: 62%; margin: 24px auto 0; border-radius: 6px; box-shadow: 0 14px 34px rgba(0,0,0,.5) }
.note { margin: 28px 0 0; padding: 16px 18px; border: 1px solid rgba(255,255,255,.1); border-radius: 12px; background: #15161d;
        font-size: 14px; color: #c3c2b7 }
.note b { color: #fff }
.react { margin: 28px 0 0 }
.react h2 { font-size: 15px; margin: 0 0 10px }
.chips { display: flex; flex-wrap: wrap; gap: 8px }
.chips button { font: inherit; font-size: 15px; padding: 9px 14px; border-radius: 999px; border: 1px solid rgba(255,255,255,.16);
                background: #1a1b23; color: #fff; cursor: pointer }
.chips button[aria-pressed=true] { border-color: #d95926; background: rgba(217,89,38,.18) }
.chips button b { font-weight: 600; color: #c3c2b7; margin-left: 4px }
textarea { width: 100%; margin-top: 12px; min-height: 76px; resize: vertical; padding: 10px 12px; border-radius: 10px;
           border: 1px solid rgba(255,255,255,.14); background: #0b0c11; color: #fff; font: inherit; font-size: 15px }
.row { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 8px; font-size: 13px; color: #898781 }
.send { font: inherit; font-size: 15px; font-weight: 700; padding: 10px 20px; border-radius: 10px; border: 0; background: #d95926;
        color: #fff; cursor: pointer }
.send:disabled { opacity: .45; cursor: default }
.msg { min-height: 1.6em; margin: 8px 0 0; font-size: 14px; color: #c3c2b7 }
footer { margin-top: 40px; font-size: 12px; color: #6f6e69 }
"""

PAGE_JS = r"""
(() => {
  const root = document.getElementById('react')
  if (!root) return
  const api = root.dataset.api
  let pick = null
  const chips = [...root.querySelectorAll('.chips button')]
  const send = root.querySelector('.send'), msg = root.querySelector('.msg'), ta = root.querySelector('textarea'), left = root.querySelector('.left')
  const paint = (counts) => chips.forEach((b) => { b.querySelector('b').textContent = counts[b.dataset.r] || '' })
  chips.forEach((b) => b.onclick = () => {
    pick = b.dataset.r
    chips.forEach((c) => c.setAttribute('aria-pressed', String(c === b)))
    send.disabled = false
  })
  ta.oninput = () => { left.textContent = `${ta.value.length}/100` }
  send.onclick = async () => {
    if (!pick) return
    send.disabled = true; msg.textContent = '보내는 중'
    try {
      // text/plain 으로 보내면 다른 주소에 올린 페이지에서도 사전 요청 없이 간다
      const r = await fetch(api, { method: 'POST', headers: { 'Content-Type': 'text/plain' },
        body: JSON.stringify({ reaction: pick, comment: ta.value.slice(0, 100) }) })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(d.detail || '보내지 못했습니다')
      paint(d.counts || {}); ta.value = ''; left.textContent = '0/100'
      msg.textContent = '반응을 남겼습니다. 한마디는 이 카드를 만든 사람만 봅니다.'
    } catch (e) { msg.textContent = String(e.message || e); send.disabled = false }
  }
})()
"""


def page_html(share: dict[str, Any], img_base: str, og_base: str | None, react_api: str | None,
              counts: dict[str, int]) -> str:
    """공유 페이지. img_base: 이미지 주소 앞부분('' 이면 같은 폴더). og_base: 미리보기용 절대 주소 앞부분(없으면 상대 주소).
    react_api: 반응을 보낼 주소. 없으면 반응 수만 보여 준다."""
    title = share.get("title") or KINDS[share["kind"]]
    desc = share.get("description") or ""
    og_img = f"{og_base.rstrip('/')}/feed.png" if og_base else "feed.png"
    og_url = f"<meta property='og:url' content='{_esc(og_base.rstrip('/') + '/')}'>" if og_base else ""
    files = share.get("files", {})
    body: list[str] = [f"<img class='cardimg' src='{img_base}feed.png' width='1080' height='1350' alt='{_esc(share.get('alt', ''))}'>"]
    if "story.png" in files:
        body.append(f"<p class='alt'><a href='{img_base}story.png'>세로 카드(스토리 크기) 보기</a></p>")
    if share["kind"] == "passage":
        body.append(f"<h1>{_esc(share.get('title') or '기록에서 지은 이야기')}</h1>")
        body.append("<blockquote>" + "".join(f"<p>{_esc(x)}</p>" for x in share.get("lines", [])) + "</blockquote>")
    elif share["kind"] == "webzine":
        body.append(f"<h1>{_esc(share.get('title', ''))}</h1>")
        if share.get("period"):
            body.append(f"<p class='alt'>{_esc(share['period'])}의 기록으로 만든 한 호</p>")
        body.append("<blockquote>" + "".join(f"<p>{_esc(x)}</p>" for x in share.get("lines", [])) + "</blockquote>")
        if "cover.png" in files:
            body.append(f"<img class='cover' style='width:100%' src='{img_base}cover.png' alt='웹진 표지'>")
    elif share["kind"] == "book":
        body.append(f"<h1>{_esc(share.get('title', ''))}</h1>")
        if "cover.png" in files:
            body.append(f"<img class='cover' src='{img_base}cover.png' alt='책 표지'>")
        if share.get("text"):
            body.append(f"<p>{_esc(share['text'])}</p>")
    else:
        body.append(f"<h1>{_esc(share.get('text', ''))}</h1>")
    note = ("이 카드는 <b>버추얼미</b>가 한 사람의 휴대폰 기록(대화·사진·걸음 수 같은 흔적)을 그 사람의 컴퓨터 안에서 세어 만든 것입니다. "
            + ("문장의 숫자는 기록에서 센 값입니다." if share["kind"] in ("discovery", "webzine")
               else "이야기는 기록 속 날짜·장소·활동에서 출발했지만 사건과 대사는 지어낸 것입니다."))
    body.append(f"<div class='note'><b>기록에서 지은 이야기</b><br>{note} 이름과 장소는 관계와 '집'·'직장' 같은 말로 바꿨습니다.</div>")
    chips = "".join(f"<button type='button' data-r='{k}' aria-pressed='false'>{_esc(v)}<b>{counts.get(k) or ''}</b></button>"
                    for k, v in REACTIONS.items())
    if react_api:
        body.append(f"<section class='react' id='react' data-api='{_esc(react_api)}'><h2>반응 남기기</h2>"
                    f"<div class='chips'>{chips}</div>"
                    f"<textarea maxlength='{COMMENT_MAX}' placeholder='한마디 (선택, 100자까지)'></textarea>"
                    "<div class='row'><span class='left'>0/100</span><button type='button' class='send' disabled>보내기</button></div>"
                    "<p class='msg' aria-live='polite'></p></section>")
    else:
        body.append(f"<section class='react'><h2>반응</h2><div class='chips'>{chips}</div>"
                    "<p class='msg'>이 사본에서는 반응을 받지 않습니다.</p></section>")
    made = share.get("created", "")[:10]
    body.append(f"<footer>버추얼미 · {_esc(made)} 공유</footer>")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{_esc(title)} · 버추얼미</title>
<meta name="description" content="{_esc(desc)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="버추얼미">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(desc)}">
<meta property="og:image" content="{_esc(og_img)}">
<meta property="og:image:width" content="1080">
<meta property="og:image:height" content="1350">
<meta property="og:image:alt" content="{_esc(share.get('alt', ''))}">
{og_url}
<meta name="twitter:card" content="summary_large_image">
<style>{PAGE_CSS}</style></head>
<body><main><p class="brand">버추얼미</p>{''.join(body)}</main>
<script>{PAGE_JS}</script></body></html>"""


# ---------------------------------------------------------------- 저장소
class Shares:
    def __init__(self, profile: str, store, outbound, root: Path | None = None):
        self.profile = profile
        self.store = store
        self.outbound = outbound
        self.root = root or shares_dir(profile)
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    # ----- 만들기
    def create(self, kind: str, *, columns: dict[str, Any] | None = None, summary_id: str | None = None,
               book_dir: Path | None = None, lines: list[str] | None = None, title: str | None = None,
               style_book_dir: Path | None = None, date_range: tuple[str, str] | None = None,
               webzine_dir: Path | None = None) -> dict[str, Any]:
        if kind not in KINDS:
            raise ShareError("모르는 카드 종류입니다")
        changes: list[str] = []
        spec: dict[str, Any] = {"kind": kind}
        share: dict[str, Any] = {"kind": kind, "kind_ko": KINDS[kind]}
        cover_src: Path | None = None
        webzine_cover: Path | None = None
        if kind == "webzine":
            if not webzine_dir or not (webzine_dir / "issue.json").exists():
                raise ShareError("그런 웹진이 없습니다")
            iss = json.loads((webzine_dir / "issue.json").read_text("utf-8"))
            cv = iss.get("cover") or {}
            title_, ch = public_text(cv.get("headline") or iss.get("title") or "이번 주 웹진", self.store, self.outbound)
            changes += ch
            clean = []
            for x in (cv.get("lines") or [])[:3]:
                t, ch = public_text(str(x), self.store, self.outbound)
                clean.append(t)
                changes += ch
            style = style_from_book(style_book_dir)
            spec.update(lines=clean, title=title_, label="이번 주 웹진", tag=f"VirtualME · {iss.get('title', '')}",
                        source=iss.get("period_ko", ""))
            share.update(title=title_, lines=clean, text=" ".join(clean), webzine_id=webzine_dir.name,
                         period=iss.get("period_ko", ""), description=" ".join(clean)[:120] or "기록으로 만든 이번 주 웹진",
                         alt=f"웹진 표지: {title_}")
            if (webzine_dir / "pg-cover.png").exists():
                webzine_cover = webzine_dir / "pg-cover.png"
        elif kind == "discovery":
            items = {s["id"]: s for s in (columns or {}).get("summaries", [])}
            item = items.get(summary_id or "")
            if not item:
                raise ShareError("그 발견 문장을 찾지 못했습니다. 기록이 바뀌었으면 화면을 새로 고쳐 주세요.")
            text, ch = public_text(item["text"], self.store, self.outbound)
            changes += ch
            element = ELEMENT.get(item["column"].split(".")[0], "기타")
            style = style_from_book(style_book_dir)
            scope = ""
            if date_range and all(date_range):
                a, b = (date.fromisoformat(d[:10]) for d in date_range)
                scope = f"{a.month}월 {a.day}일 ~ {b.month}월 {b.day}일 기록에서 센 값"
            spec.update(text=text, label="기록에서 찾은 발견", tag=element, source=scope)
            share.update(text=text, title=f"기록에서 찾은 발견 · {element}", summary_id=summary_id, column=item["column"],
                         description=text, alt=text)
        elif kind == "book":
            if not book_dir or not (book_dir / "brief.json").exists():
                raise ShareError("그런 책이 없습니다")
            b = json.loads((book_dir / "brief.json").read_text("utf-8"))
            title_, ch = public_text(b.get("title") or "제목 없음", self.store, self.outbound)
            changes += ch
            raw_blurb = b.get("blurb") or ""
            if isinstance(raw_blurb, dict):  # 브리프에는 blurb.json 내용이 통째로 들어 있다
                raw_blurb = raw_blurb.get("blurb") or ""
            blurb = _first_sentences(str(raw_blurb), 90)
            blurb, ch = public_text(blurb, self.store, self.outbound) if blurb else ("", [])
            changes += ch
            style = style_from_book(book_dir)
            genre = b.get("genre_name") or ""
            label = f"{genre} 소설" if genre and genre != "문학·에세이" else "소설"
            spec.update(text=blurb, label="기록에서 지은 이야기", tag=label, source=f"「{title_}」")
            share.update(title=title_, text=blurb, book_id=book_dir.name, genre=genre,
                         description=blurb or f"기록에서 지은 {label}", alt=f"책 표지: {title_}")
            cover_src = book_dir
        else:
            got = [x.strip() for x in (lines or []) if x and x.strip()]
            if not 1 <= len(got) <= 3:
                raise ShareError("한 줄에서 세 줄까지 골라 주세요")
            if sum(len(x) for x in got) > 300:
                raise ShareError("고른 글이 너무 깁니다. 300자 안으로 골라 주세요")
            clean = []
            for x in got:
                t, ch = public_text(x, self.store, self.outbound)
                clean.append(t)
                changes += ch
            title_ = ""
            if title and title.strip():
                title_, ch = public_text(title.strip()[:80], self.store, self.outbound)
                changes += ch
            style = style_from_book(style_book_dir)
            spec.update(lines=clean, title=title_, label="기록에서 지은 이야기", tag=style.get("genre") or "",
                        source=f"「{title_}」에서" if title_ else "")
            share.update(title=title_ or "기록에서 지은 이야기", lines=clean, text=" ".join(clean),
                         description=" ".join(clean)[:120], alt=" ".join(clean))
            if style.get("from_book"):
                share["book_id"] = style["from_book"]

        share_id = secrets.token_hex(6)
        d = self.root / share_id
        d.mkdir(parents=True, exist_ok=True)
        try:
            report: dict[str, Any] = {}
            cover_uri = None
            if cover_src is not None:
                report["cover"] = self._render_cover(cover_src, share["title"], d)
                cover_uri = (d / "cover.png").as_uri()
            if webzine_cover is not None:
                # 웹진 표지 쪽 그대로. 표지에 찍힌 글(제목·표지 문구)은 위에서 공개용으로 검사했다
                from PIL import Image

                with Image.open(webzine_cover) as im:
                    im.convert("RGB").save(d / "cover.png", optimize=True)
            html_path = d / "card.html"
            html_path.write_text(card_html(spec, style, cover_uri), "utf-8")
            rep = run_render(html_path, d, [("#feed", "feed.png"), ("#story", "story.png")])
            report["cards"] = {"fit": rep.get("fit", []), "fonts": rep.get("fonts", [])}
            # 카드에 실제로 찍힌 글자를 한 번 더 검사한다 (책 표지 그림 속 글자는 책을 만들 때 검사했다)
            seen = " ".join(rep.get("text", {}).values()) + " " + " ".join(report.get("cover", {}).get("text", {}).values())
            left = leaks(seen, self.store)
            if left:
                raise ShareError(f"렌더한 카드에 공개하면 안 되는 값({', '.join(left)})이 보여 지웠습니다.")
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        share.update(id=share_id, profile=self.profile, created=datetime.now().isoformat(timespec="seconds"),
                     files={n: f"/s/{share_id}/{n}" for n in FILES if (d / n).exists()},
                     style={"from_book": style.get("from_book"), "bg": style["bg"], "accent": style["accent"]},
                     privacy={"replaced": sorted(set(changes)),
                              "checked": ["실명", "가명", "좌표·장소 번호", "주소", "연락처·링크", "집·직장 이름", "렌더한 글자"]},
                     render={"fit": report["cards"]["fit"]})
        (d / "share.json").write_text(json.dumps(share, ensure_ascii=False, indent=1), "utf-8")
        # 밖으로 나갈 수 있는 글이므로 전송 로그에도 남긴다 (/import 의 "밖으로 나간 글")
        if self.outbound is not None:
            self.outbound.send("share", "", self._log_text(share), purpose=f"공유 카드 ({share['kind_ko']})")
        return self.view(share_id) or {}

    @staticmethod
    def _log_text(share: dict[str, Any]) -> str:
        parts = [share.get("title") or "", share.get("text") or ""]
        return "\n".join(p for p in parts if p) + f"\n(공유 번호 {share['id']})"

    def _render_cover(self, book_dir: Path, title: str, out: Path) -> dict[str, Any]:
        """책의 앞표지를 지은이 자리만 "나"로 바꿔 다시 그린다. 책 PNG 에는 사용자가 적은 지은이(실명일 수 있음)가 찍혀 있다."""
        src = book_dir / "book.html"
        if not src.exists():
            raise ShareError("책 파일(book.html)이 없어 표지 카드를 만들 수 없습니다. 책을 다시 만들어 주세요.")
        b = json.loads((book_dir / "brief.json").read_text("utf-8"))
        doc = src.read_text("utf-8")
        author = html.escape(b.get("author") or "나", quote=True)
        for tail in (" 지음<", " 님의 "):
            doc = doc.replace(f">{author}{tail}", f">나{tail}")
        old_title = html.escape(b.get("title") or "", quote=True)
        if old_title and old_title != html.escape(title, quote=True):
            doc = doc.replace(old_title, html.escape(title, quote=True))
        tmp = out / "cover.html"
        tmp.write_text(doc, "utf-8")
        try:
            rep = run_render(tmp, out, [("#pg-cover", "cover.png")], kind="book", scale=2)
        finally:
            tmp.unlink(missing_ok=True)
        return {"text": rep.get("text", {})}

    # ----- 읽기
    def _dir(self, share_id: str) -> Path | None:
        if not SHARE_ID.match(share_id or ""):
            return None
        d = self.root / share_id
        return d if (d / "share.json").exists() else None

    def get(self, share_id: str) -> dict[str, Any] | None:
        d = self._dir(share_id)
        if not d:
            return None
        try:
            return json.loads((d / "share.json").read_text("utf-8"))
        except (OSError, ValueError):
            return None

    def reactions(self, share_id: str) -> list[dict[str, Any]]:
        d = self._dir(share_id)
        if not d or not (d / "reactions.jsonl").exists():
            return []
        out = []
        for line in (d / "reactions.jsonl").read_text("utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def counts(self, share_id: str) -> dict[str, int]:
        c = {k: 0 for k in REACTIONS}
        for r in self.reactions(share_id):
            if r.get("reaction") in c:
                c[r["reaction"]] += 1
        return c

    def view(self, share_id: str, recent: int = 20) -> dict[str, Any] | None:
        """주인이 앱에서 보는 모양: 공유 정보 + 반응 수 + 한마디(최근 순)."""
        s = self.get(share_id)
        if not s:
            return None
        rs = self.reactions(share_id)
        counts = self.counts(share_id)
        return {**s, "url": f"/s/{share_id}",
                "reactions": {"counts": counts, "total": sum(counts.values()), "labels": REACTIONS,
                              "recent": [{"ts": r["ts"], "reaction": r["reaction"], "label": REACTIONS.get(r["reaction"], ""),
                                          "comment": r.get("comment", "")} for r in reversed(rs[-recent:])]}}

    def list(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        out = [v for d in self.root.iterdir() if d.is_dir() and (v := self.view(d.name, recent=5))]
        return sorted(out, key=lambda s: s["created"], reverse=True)

    def file(self, share_id: str, name: str) -> Path | None:
        d = self._dir(share_id)
        if not d or name not in FILES:
            return None
        p = d / name
        return p if p.exists() else None

    def delete(self, share_id: str) -> bool:
        d = self._dir(share_id)
        if not d:
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True

    # ----- 방문자 반응
    def react(self, share_id: str, reaction: str, comment: str = "", client: str = "") -> dict[str, int]:
        d = self._dir(share_id)
        if not d:
            raise LookupError("그런 공유가 없습니다")
        if reaction not in REACTIONS:
            raise ShareError("고를 수 있는 반응이 아닙니다")
        comment = re.sub(r"\s+", " ", CONTROL_RE.sub("", comment or "")).strip()
        if len(comment) > COMMENT_MAX:
            raise ShareError(f"한마디는 {COMMENT_MAX}자까지입니다")
        # 링크·연락처는 받지 않는다 (광고·스팸 방지). 꺾쇠는 글자로만 저장하고 화면에서는 언제나 이스케이프한다
        for pat, repl in _PII_PATTERNS:
            comment = pat.sub(repl, comment)
        now = time.time()
        with self._lock:
            hits = self._hits.setdefault(client or "?", deque())
            while hits and now - hits[0] > 3600:
                hits.popleft()
            if hits and now - hits[-1] < RATE_GAP_SEC:
                raise PermissionError("조금 뒤에 다시 남겨 주세요")
            if len(hits) >= RATE_PER_HOUR:
                raise PermissionError("한 시간에 남길 수 있는 반응을 다 썼습니다")
            if len(self.reactions(share_id)) >= MAX_REACTIONS_PER_SHARE:
                raise PermissionError("이 카드는 반응을 더 받지 않습니다")
            hits.append(now)
            # 누가 남겼는지(IP)는 저장하지 않는다
            with (d / "reactions.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "reaction": reaction,
                                    "comment": comment}, ensure_ascii=False) + "\n")
        return self.counts(share_id)

    # ----- 페이지·내보내기
    def page(self, share_id: str, base_url: str) -> str | None:
        s = self.get(share_id)
        if not s:
            return None
        base = base_url.rstrip("/") + f"/s/{share_id}"
        # 페이지 주소는 /s/<id> 이고, 앱의 프록시(/api/core/s/<id>)로 열어도 맞도록 그림·반응 주소는 상대 경로로 둔다
        return page_html(s, f"{share_id}/", base, f"{share_id}/react", self.counts(share_id))

    def export(self, share_id: str, out_dir: Path, base_url: str | None = None, react_url: str | None = None) -> list[Path]:
        """정적 사본: index.html + 이미지. 아무 정적 호스팅(깃허브 페이지 등)에 폴더째 올리면 열린다.
        base_url: 올릴 주소(미리보기 이미지는 절대 주소여야 해서). react_url: 반응을 받을 core 서버 주소(터널 등)."""
        s = self.get(share_id)
        d = self._dir(share_id)
        if not s or not d:
            raise LookupError("그런 공유가 없습니다")
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for n in FILES:
            if (d / n).exists():
                shutil.copy2(d / n, out_dir / n)
                written.append(out_dir / n)
        api = f"{react_url.rstrip('/')}/s/{share_id}/react" if react_url else None
        (out_dir / "index.html").write_text(page_html(s, "", base_url, api, self.counts(share_id)), "utf-8")
        written.append(out_dir / "index.html")
        if self.outbound is not None:
            self.outbound.send("share", "", self._log_text(s), purpose="공유 페이지 내보내기")
        return written


def _first_sentences(text: str, limit: int) -> str:
    """소개글 앞 문장들을 limit 자 안에서. 한 문장도 안 들어가면 어절 단위로 자르고 말줄임."""
    text = re.sub(r"\s+", " ", text or "").strip()
    parts = re.findall(r"[^.!?。]+[.!?。]?", text)
    out = ""
    for p in parts:
        p = p.strip()
        if len(out) + len(p) + 1 > limit:
            break
        out = f"{out} {p}".strip()
    if out:
        return out
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…" if cut else ""
