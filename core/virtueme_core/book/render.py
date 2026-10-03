"""브리프 + 그림 + 이야기 → 책 한 권의 HTML → PNG·PDF (신국판 152×225mm, 재단 여백 선택).

쪽 차례: 앞표지 / 속표지(1) / 빈 쪽(2) / 차례(3) / 빈 쪽(4) / 장마다 장 시작 면과 본문 / 빈 쪽으로 채워 판권이 4의 배수
쪽(왼쪽)에 오게 / 뒤표지. PNG 는 cover, title, toc, opener(1장), opener-2, spread(삽화가 든 펼침면), colophon, back.

- 레이아웃 원형마다 표지 틀이 하나씩 있다 (`_cover_html`). 제목 크기는 브리프의 title_scale 에서 시작해 정해진 상자를
  넘치지 않을 때까지 줄인다 (브라우저 안에서 잰다).
- 그림 위에 제목이 얹히는 원형(full_bleed)은 제목 자리의 그림 명도와 복잡도를 재서 글자색과 바탕막을 정한다.
- 본문은 나눔명조 10pt, 줄 간격 1.72, `word-break: keep-all`, 양끝 맞춤. 쪽 나누기는 브라우저에서 단락을 넣어 보며
  넘치면 어절 단위로 잘라 다음 쪽으로 넘긴다. 장마다 삽화 한 장을 정한 단락 뒤에 넣고, 자리가 모자라면 다음 쪽 맨 위로.
- 쪽 머리: 왼쪽(짝수) 쪽은 책 제목, 오른쪽(홀수) 쪽은 장 제목. 쪽 번호는 속표지부터 센다(앞붙이에는 찍지 않음).
- 재단 여백(bleed_mm)을 주면 쪽 크기가 사방으로 그만큼 커지고, 그림과 바탕색은 끝까지, 글자는 재단선 안에 둔다.
- 서체는 이 맥에 있는 것만 쓴다: 명조 나눔명조, 고딕 Pretendard, 장식 배민 도현·연성, 손글씨 나눔손글씨 펜·붓.
"""
from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .features import contrast_ratio, region_stats

HERE = Path(__file__).resolve().parent
PRETENDARD = Path.home() / "Library/Fonts"

FONT_FACES = {
    # 이름: (local 이름들, 굵기, 파일 대안)
    "BkSerif": [(["NanumMyeongjo", "Nanum Myeongjo"], 400), (["NanumMyeongjoBold"], 700),
                (["NanumMyeongjoExtraBold"], 800)],
    "BkSans": [(["Pretendard-Regular", "Pretendard Regular"], 400), (["Pretendard-SemiBold"], 600),
               (["Pretendard-Bold", "Pretendard Bold"], 700), (["Pretendard-Black", "Pretendard Black"], 900)],
    "BkDohyeon": [(["BMDoHyeon-OTF", "BM DoHyeon OTF", "BM Dohyeon"], 400)],
    "BkYeonsung": [(["BMYEONSUNG-OTF", "BM Yeonsung"], 400)],
    "BkPen": [(["NanumPen", "Nanum Pen Script"], 400)],
    "BkBrush": [(["NanumBrush", "Nanum Brush Script"], 400)],
}
FALLBACK_STACK = "'AppleMyungjo', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif"


def title_font(typeface: str, genre: str) -> tuple[str, int, str]:
    """(font-family, 굵기, 자간): 서체 계열과 장르로 실제 서체를 고른다."""
    if typeface == "serif":
        return "BkSerif", 800, "-0.01em"
    if typeface == "sans":
        return "BkSans", 900 if genre in ("thriller", "sf", "mystery") else 700, "-0.03em"
    if typeface == "display":
        if genre in ("mystery", "thriller"):
            return "BkSans", 900, "-0.05em"
        return ("BkDohyeon", 400, "-0.02em") if genre in ("comedy", "sf") else ("BkYeonsung", 400, "0")
    if typeface == "handwritten":
        return ("BkBrush", 400, "0") if genre in ("romance", "fantasy", "mystery", "thriller") else ("BkPen", 400, "0")
    return "BkSerif", 800, "0"


SCALE_PT = {"small": 24, "medium": 32, "large": 42, "dominant": 56}
HANDWRITTEN_BOOST = 1.35  # 손글씨 서체는 같은 pt 에서 글자가 작아 보인다


def _font_css() -> str:
    out = []
    for fam, faces in FONT_FACES.items():
        for names, weight in faces:
            src = ", ".join(f"local('{n}')" for n in names)
            if fam == "BkSans":
                fname = {400: "Regular", 600: "SemiBold", 700: "Bold", 900: "Black"}[weight]
                f = PRETENDARD / f"Pretendard-{fname}.otf"
                if f.exists():
                    src += f", url('{f.as_uri()}')"
            out.append(f"@font-face{{font-family:'{fam}';src:{src};font-weight:{weight};font-display:block}}")
    return "\n".join(out)


def title_treatment(art: Path | None, brief: dict[str, Any]) -> dict[str, Any]:
    """그림 위 제목의 글자색과 바탕막. 그림 밖 제목이면 팔레트 글자색."""
    pal = brief["palette"]
    if brief["archetype"] not in ("full_bleed",) or art is None or not art.exists():
        return {"color": pal["ink"], "scrim": None, "mark_color": pal["ink"], "reason": "바탕색 위 제목: 팔레트 글자색"}
    box = {"top": (0.08, 0.04, 0.92, 0.36), "middle": (0.08, 0.34, 0.92, 0.66),
           "bottom": (0.08, 0.62, 0.92, 0.94)}[brief["title_position"] or "top"]
    st = region_stats(art, box)
    # 아래쪽 출판사 표시 자리도 따로 잰다 (제목 자리와 명도가 다를 수 있다)
    mark_l = region_stats(art, (0.3, 0.9, 0.7, 0.97))["lightness"]
    mark_color = "#16130f" if mark_l > 58 else "#fbf8f2"
    light = st["lightness"] > 58
    color = "#16130f" if light else "#fbf8f2"
    # 영역 평균색을 명도만으로 근사해 대비를 잰다
    g = int(round(st["lightness"] / 100 * 255))
    zone = f"#{g:02x}{g:02x}{g:02x}"
    cr = contrast_ratio(color, zone)
    scrim = None
    if st["busy"] > 0.3 or cr < 4.5:
        scrim = "rgba(251,248,242,0.78)" if light else "rgba(10,10,14,0.62)"
    return {"color": color, "scrim": scrim, "mark_color": mark_color, "zone_lightness": round(st["lightness"], 1),
            "zone_busy": round(st["busy"], 3), "contrast_vs_zone": round(cr, 2),
            "reason": f"제목 자리 명도 {st['lightness']:.0f}, 복잡도 {st['busy']:.2f}"
                      + (", 바탕막 추가" if scrim else "")}


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _curve_svg(curve: list[float], color: str, w: int = 300, h: int = 60, stroke: float = 1.6) -> str:
    if not curve:
        return ""
    n = len(curve)
    # 화면용으로만 곡선 자체 범위에 맞춰 늘린다 (폭이 작으면 0.25 를 최소 폭으로)
    lo, hi = min(curve), max(curve)
    span = max(hi - lo, 0.25)
    mid = (hi + lo) / 2
    curve = [0.5 + (v - mid) / span * 0.9 for v in curve]
    pts = " ".join(f"{i / (n - 1) * w:.1f},{(1 - v) * (h - 6) + 3:.1f}" for i, v in enumerate(curve))
    return (f"<svg class='curve' viewBox='0 0 {w} {h}' preserveAspectRatio='none'>"
            f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='{stroke}' "
            f"vector-effect='non-scaling-stroke' stroke-linecap='round' stroke-linejoin='round'/></svg>")


def _curve_segment_svg(curve: list[float], color: str, lo: float, hi: float, w: int = 300, h: int = 40) -> str:
    """정서 곡선 전체를 옅게, 이 장에 해당하는 구간(lo..hi, 0~1)을 진하게."""
    if not curve:
        return ""
    n = len(curve)
    a, b = min(curve), max(curve)
    span = max(b - a, 0.25)
    mid = (a + b) / 2
    ys = [0.5 + (v - mid) / span * 0.9 for v in curve]
    pts = [(i / (n - 1) * w, (1 - y) * (h - 6) + 3) for i, y in enumerate(ys)]
    seg = [p for p in pts if lo * w - 1e-6 <= p[0] <= hi * w + 1e-6]
    if len(seg) < 2:
        j = min(n - 2, int(lo * (n - 1)))
        seg = pts[j:j + 2]
    fmt = lambda ps: " ".join(f"{x:.1f},{y:.1f}" for x, y in ps)  # noqa: E731
    return (f"<svg class='curve' viewBox='0 0 {w} {h}' preserveAspectRatio='none'>"
            f"<polyline points='{fmt(pts)}' fill='none' stroke='{color}' stroke-opacity='0.28' stroke-width='1' "
            f"vector-effect='non-scaling-stroke' stroke-linecap='round' stroke-linejoin='round'/>"
            f"<polyline points='{fmt(seg)}' fill='none' stroke='{color}' stroke-width='2' "
            f"vector-effect='non-scaling-stroke' stroke-linecap='round' stroke-linejoin='round'/></svg>")


def _cover_html(b: dict[str, Any], title: str, author: str, art_uri: str | None, tt: dict[str, Any]) -> str:
    arch = b["archetype"]
    pos = b["title_position"] or "top"
    pal = b["palette"]
    label = f"{b['genre_name']} 소설" if b["genre"] != "literary" else "소설"
    tblock = (f"<div class='tblock'><div class='label'>{_esc(label)}</div>"
              f"<h1 class='title fit'>{_esc(title)}</h1><div class='author'>{_esc(author)} 지음</div></div>")
    mark = "<div class='mark'>버추얼미</div>"
    art = f"<img class='art' src='{art_uri}' alt=''>" if art_uri else ""
    if arch == "full_bleed":
        scrim = ""
        if tt.get("scrim"):
            if pos == "middle":
                scrim = (f"<div class='scrim' style='background:linear-gradient(to bottom, transparent 20%, {tt['scrim']} 36%, "
                         f"{tt['scrim']} 64%, transparent 80%)'></div>")
            else:
                side = "to bottom" if pos == "top" else "to top"
                scrim = (f"<div class='scrim' style='background:linear-gradient({side}, {tt['scrim']} 0%, "
                         f"{tt['scrim']} 34%, transparent 58%)'></div>")
        return f"<div class='cv full pos-{pos}'>{art}{scrim}<div class='trim'>{tblock}{mark}</div></div>"
    if arch == "framed":
        frame = f"<div class='frame'>{art}</div>"
        body = tblock + frame if pos != "bottom" else frame + tblock
        return f"<div class='cv framed pos-{pos}'><div class='trim'>{body}{mark}</div></div>"
    if arch == "split":
        txt = f"<div class='half txt-half'><div class='rule'></div>{tblock}{mark}</div>"
        pic = f"<div class='half art-half'>{art}</div>"
        return f"<div class='cv split pos-{pos}'>{pic + txt if pos == 'bottom' else txt + pic}</div>"
    if arch == "pattern":
        return f"<div class='cv pattern pos-{pos}'>{art}<div class='trim'><div class='box'>{tblock}</div>{mark}</div></div>"
    if arch == "centered_motif":
        motif = f"<div class='motif-wrap'>{art}</div>"
        body = tblock + motif if pos != "bottom" else motif + tblock
        return f"<div class='cv motif pos-{pos}'><div class='trim'>{body}{mark}</div></div>"
    # typographic: 제목과 강조색 면, 그 면에 정서 곡선
    field = f"<div class='typo-field'>{_curve_svg(b['emotion_curve'], pal['background'], stroke=2.4)}</div>"
    return f"<div class='cv typo pos-{pos}'>{field}<div class='trim'>{tblock}{mark}</div></div>"


def _back_html(b: dict[str, Any], title: str, blurb: str, art_uri: str | None) -> str:
    pal = b["palette"]
    art = ""
    if art_uri and b["archetype"] in ("full_bleed", "split", "pattern"):
        art = f"<div class='back-art'><img src='{art_uri}' alt=''></div>"
    curve = _curve_svg(b["emotion_curve"], pal["accent"] if contrast_ratio(pal["accent"], pal["background"]) >= 2
                       else pal["ink"], stroke=1.6)
    return (f"<div class='cv back'>{art}<div class='trim'><div class='blurb'>{_esc(blurb)}</div>"
            f"<div class='back-curve'>{curve}<div class='back-cap'>기록에서 잰 정서의 흐름</div></div>"
            f"<div class='back-mark'><b>버추얼미</b> · 기록에서 지은 이야기</div></div></div>")


CSS = r"""
@page { size: var(--pw) var(--ph); margin: 0 }
* { box-sizing: border-box; margin: 0; padding: 0 }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact }
body { background: #d8d4cc; font-family: 'BkSerif', __FALLBACK__; color: #1b1814; padding: 10mm;
       display: flex; flex-direction: column; gap: 10mm; align-items: flex-start }
.page { width: var(--pw); height: var(--ph); position: relative; overflow: hidden; background: #fbf8f2;
        word-break: keep-all; overflow-wrap: break-word; line-break: strict; flex: none }
.trim { position: absolute; inset: var(--bl) }
/* ---------- 앞표지 ---------- */
.cover { background: var(--bg); color: var(--tc) }
.cv { position: absolute; inset: 0 }
.cv > .trim { display: flex; flex-direction: column }
.tblock { position: relative; z-index: 3; padding: 0 14mm; text-align: var(--talign) }
.label { font-family: 'BkSans'; font-weight: 600; font-size: 8pt; letter-spacing: 0.08em; opacity: 0.85; margin-bottom: 4mm }
.title { font-family: var(--tf); font-weight: var(--tw); letter-spacing: var(--tls); font-size: var(--tsize);
         line-height: 1.16; max-height: var(--tmaxh); overflow: hidden; text-wrap: balance; word-break: keep-all }
.author { font-family: 'BkSans'; font-weight: 600; font-size: 10pt; margin-top: 5mm; letter-spacing: 0.02em }
.mark { position: absolute; z-index: 3; left: 0; right: 0; bottom: 9mm; text-align: center; font-family: 'BkSans';
        font-weight: 700; font-size: 7.5pt; letter-spacing: 0.3em; opacity: 0.85 }
.full .art { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover }
.full .scrim { position: absolute; inset: 0; z-index: 2 }
.full .mark { color: var(--mc); text-shadow: 0 0 2mm var(--shadow) }
.full.pos-top > .trim { justify-content: flex-start; padding-top: 22mm }
.full.pos-middle > .trim { justify-content: center }
.full.pos-bottom > .trim { justify-content: flex-end; padding-bottom: 24mm }
.full .tblock { text-shadow: 0 0 6mm var(--shadow) }
/* 액자형: 여백 안 사각 그림, 제목은 그림 밖 */
.framed > .trim { padding: 24mm 0 22mm; justify-content: flex-start; gap: 10mm }
.framed.pos-bottom > .trim { padding-top: 20mm }
.framed .frame { margin: 0 auto; width: 100mm; height: 100mm; flex: none; outline: 0.5pt solid var(--tc);
                 outline-offset: 2.4mm }
.framed .frame .art { width: 100%; height: 100%; object-fit: cover; display: block }
.framed .tblock { padding: 0 20mm }
/* 나눔형: 색면(제목) + 그림 면 */
.split .half { position: absolute; left: 0; right: 0; overflow: hidden }
.split .txt-half { height: calc(var(--bl) + 44%); background: var(--bg); display: flex; flex-direction: column;
                   justify-content: center; padding: 0 var(--bl) }
.split .art-half { height: calc(var(--bl) + 56%) }
.split.pos-top .txt-half, .split.pos-middle .txt-half { top: 0; padding-top: calc(var(--bl) + 4mm) }
.split.pos-top .art-half, .split.pos-middle .art-half { bottom: 0 }
.split.pos-bottom .art-half { top: 0 } .split.pos-bottom .txt-half { bottom: 0; padding-bottom: calc(var(--bl) + 6mm) }
.split .art-half .art { width: 100%; height: 100%; object-fit: cover; display: block }
.split .rule { position: absolute; left: calc(var(--bl) + 14mm); width: 12mm; height: 1.2mm; background: var(--accent3) }
.split.pos-top .rule, .split.pos-middle .rule { bottom: 7mm } .split.pos-bottom .rule { top: 9mm }
.split .mark { bottom: 7mm; text-align: right; right: calc(var(--bl) + 14mm); left: auto }
.split.pos-bottom .mark { bottom: calc(var(--bl) + 9mm) }
/* 무늬형: 반복 무늬 바탕 + 제목 상자 */
.pattern .art { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover }
.pattern > .trim { justify-content: center; align-items: center }
.pattern.pos-top > .trim { justify-content: flex-start; padding-top: 30mm }
.pattern.pos-bottom > .trim { justify-content: flex-end; padding-bottom: 34mm }
.pattern .box { position: relative; z-index: 3; background: var(--bg); width: 112mm; padding: 12mm 0 11mm;
                box-shadow: 0 0 0 1.2mm var(--bg), 0 0 0 1.6mm var(--tc) }
.pattern .tblock { padding: 0 9mm }
.pattern .title { font-size: max(var(--tsize), 30pt) }
.pattern .mark { background: var(--bg); width: 40mm; margin: 0 auto; padding: 1.5mm 0; bottom: 8mm }
/* 가운데 그림형 */
.motif > .trim { padding: 26mm 0 24mm; justify-content: flex-start; gap: 12mm }
.motif.pos-bottom > .trim { padding-top: 30mm }
.motif .motif-wrap { margin: 0 auto; width: 86mm; height: 86mm; flex: none }
.motif .art { width: 100%; height: 100%; object-fit: cover; display: block;
              -webkit-mask-image: radial-gradient(circle, #000 52%, transparent 70%); mask-image: radial-gradient(circle, #000 52%, transparent 70%) }
.motif .tblock { padding: 0 18mm }
/* 글자형 */
.typo { background: var(--bg) }
.typo > .trim { justify-content: flex-start; padding: 28mm 0 calc(36% + 8mm) }
.typo.pos-middle > .trim { padding-top: 40mm }
.typo-field { position: absolute; left: 0; right: 0; bottom: 0; height: calc(var(--bl) + 36%); background: var(--accent3);
              padding: 16mm calc(var(--bl) + 14mm) calc(var(--bl) + 26mm) }
.typo.pos-bottom .typo-field { top: 0; bottom: auto; padding: calc(var(--bl) + 22mm) calc(var(--bl) + 14mm) 18mm }
.typo.pos-bottom > .trim { justify-content: flex-end; padding: calc(36% + 8mm) 0 30mm }
.typo .mark { color: var(--bg) } .typo.pos-bottom .mark { color: var(--tc) }
.typo .title { font-size: calc(var(--tsize) * 1.15); max-height: 64mm }
.curve { width: 100%; height: 100%; display: block }
/* ---------- 뒤표지 ---------- */
.back { background: var(--bg); color: var(--ink) }
.back-art { position: absolute; left: 0; right: 0; bottom: 0; height: calc(var(--bl) + 34%); overflow: hidden }
.back-art img { width: 100%; height: 100%; object-fit: cover; object-position: 50% 80%; display: block }
.back > .trim { padding: 30mm 20mm 0 }
.blurb { font-family: 'BkSerif'; font-size: 11pt; line-height: 1.85; letter-spacing: -0.01em; max-width: 100mm }
.back-curve { margin-top: 12mm; width: 64mm; height: 14mm }
.back-cap { font-family: 'BkSans'; font-size: 6.5pt; opacity: 0.7; margin-top: 1.5mm; letter-spacing: 0.04em }
.back-mark { position: absolute; left: 20mm; top: 120mm; font-family: 'BkSans'; font-size: 7.5pt; letter-spacing: 0.08em;
             opacity: 0.85 }
.back-mark b { letter-spacing: 0.25em }
/* ---------- 앞붙이: 속표지·차례·판권 ---------- */
.titlepage .trim { padding: 58mm 22mm 0 }
.titlepage .tp-label { font-family: 'BkSans'; font-size: 8pt; letter-spacing: 0.12em; color: var(--accent2) }
.titlepage .tp-title { font-family: var(--tf); font-weight: var(--tw); letter-spacing: var(--tls); font-size: var(--tpsize);
                       line-height: 1.22; margin-top: 6mm; text-wrap: balance; max-height: 60mm; overflow: hidden }
.titlepage .tp-author { font-family: 'BkSans'; font-weight: 600; font-size: 10pt; margin-top: 8mm }
.titlepage .tp-mark { position: absolute; left: 22mm; bottom: 22mm; font-family: 'BkSans'; font-weight: 700;
                      font-size: 7.5pt; letter-spacing: 0.3em; color: #6f675c }
.toc .trim { padding: 40mm 22mm 0 26mm }
.toc h2 { font-family: 'BkSans'; font-weight: 700; font-size: 10pt; letter-spacing: 0.3em; color: var(--accent2); margin-bottom: 14mm }
.toc-row { display: flex; align-items: baseline; gap: 5mm; font-size: 11pt; line-height: 1.5; padding: 3.2mm 0;
           border-bottom: 0.3pt solid #d9d2c5 }
.toc-no { font-family: 'BkSans'; font-weight: 600; font-size: 8.5pt; color: var(--accent2); width: 6mm; flex: none }
.toc-t { flex: 1; font-family: 'BkSerif' }
.toc-p { font-family: 'BkSans'; font-size: 8.5pt; color: #6f675c }
.colophon .trim { padding: 0 22mm 26mm 18mm; display: flex; flex-direction: column; justify-content: flex-end }
.colophon .c-title { font-family: 'BkSerif'; font-weight: 800; font-size: 11pt }
.colophon .c-author { font-family: 'BkSans'; font-size: 8pt; margin-top: 2mm; color: #4a443c }
.colophon hr { border: 0; border-top: 0.4pt solid #b9b1a3; margin: 5mm 0 }
.colophon dl { display: grid; grid-template-columns: 18mm 1fr; row-gap: 1.6mm; font-family: 'BkSans'; font-size: 7pt;
               color: #4a443c; line-height: 1.5 }
.colophon dt { color: #8a8378 }
.colophon .c-note { font-family: 'BkSans'; font-size: 7pt; line-height: 1.65; color: #4a443c; margin-top: 5mm }
.colophon .c-mark { font-family: 'BkSans'; font-weight: 700; font-size: 8pt; margin-bottom: 4mm }
.colophon .c-mark b { letter-spacing: 0.25em }
/* ---------- 장 시작 면 ---------- */
.opener .head { position: absolute; top: calc(var(--bl) + 34mm); left: calc(var(--bl) + var(--ml));
                right: calc(var(--bl) + var(--mr)) }
.opener .chap-no { font-family: var(--cnf); font-weight: var(--cnw); font-size: 30pt; line-height: 1; color: var(--accent2) }
.opener .chap-title { font-family: 'BkSerif'; font-weight: 800; letter-spacing: -0.01em; font-size: 15pt;
                      line-height: 1.35; margin-top: 5mm; color: #1b1814; text-wrap: balance }
.opener .curve-wrap { margin-top: 7mm; height: 8mm; width: 60%; }
/* ---------- 본문 ---------- */
.textarea { position: absolute; left: calc(var(--bl) + var(--ml)); right: calc(var(--bl) + var(--mr));
            top: calc(var(--bl) + 22mm); bottom: calc(var(--bl) + 24mm); overflow: hidden }
.opener .textarea { top: calc(var(--bl) + 96mm) }
.body-p { font-family: 'BkSerif'; font-size: 10pt; line-height: 1.72; text-align: justify; text-indent: 1em;
          letter-spacing: -0.015em; hanging-punctuation: none; color: #1f1b16 }
.body-p.cont { text-indent: 0 }
.body-p.first { text-indent: 0 }
figure.illus { margin: 3mm 0 4mm; height: var(--figh); display: flex; align-items: center; justify-content: center }
figure.illus img { width: 100%; height: 100%; object-fit: cover; display: block;
                   -webkit-mask-image: radial-gradient(ellipse 72% 70% at 50% 50%, #000 62%, transparent 100%);
                   mask-image: radial-gradient(ellipse 72% 70% at 50% 50%, #000 62%, transparent 100%) }
.rh { position: absolute; top: calc(var(--bl) + 11mm); left: calc(var(--bl) + var(--ml)); right: calc(var(--bl) + var(--mr));
      font-family: 'BkSans'; font-size: 7pt; color: #8a8378; letter-spacing: 0.04em }
.folio { position: absolute; bottom: calc(var(--bl) + 12mm); left: calc(var(--bl) + var(--ml));
         right: calc(var(--bl) + var(--mr)); font-family: 'BkSans'; font-size: 7.5pt; color: #8a8378 }
.verso .rh, .verso .folio { text-align: left } .recto .rh, .recto .folio { text-align: right }
.opener .folio { text-align: center }
.verso { --ml: 15mm; --mr: 18mm } .recto { --ml: 18mm; --mr: 15mm }
/* ---------- 펼침면 미리보기 ---------- */
#spread { display: flex; box-shadow: 0 2mm 6mm rgba(0,0,0,.18) }
#spread .page:first-child { box-shadow: inset -6mm 0 6mm -6mm rgba(0,0,0,.18) }
#spread .page:last-child { box-shadow: inset 6mm 0 6mm -6mm rgba(0,0,0,.18) }
#book { display: flex; flex-direction: column; gap: 6mm }
@media print {
  body { background: none; padding: 0; display: block }
  #spread { display: none !important }
  #book { display: block }
  .page { break-after: page }
}
"""

JS = r"""
(async () => {
  await document.fonts.ready
  const D = JSON.parse(document.getElementById('bookdata').textContent)
  const report = { fits: [], pages: 0, figure_pages: [], chapter_pages: [], overflow: [] }
  for (const el of document.querySelectorAll('.fit')) {
    let size = parseFloat(getComputedStyle(el).fontSize)
    const start = size
    const tooBig = () => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1
    while (tooBig() && size > 12) { size *= 0.94; el.style.fontSize = size + 'px' }
    report.fits.push({ text: el.textContent, start_px: Math.round(start), final_px: Math.round(size) })
  }
  const book = document.getElementById('book')
  const backCover = document.getElementById('pg-back')
  let pageNo = D.front_pages
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  const addPage = (kind, inner) => {
    pageNo += 1
    const p = document.createElement('div')
    p.className = `page ${kind} ${pageNo % 2 ? 'recto' : 'verso'}`
    p.dataset.no = pageNo
    p.innerHTML = inner
    book.insertBefore(p, backCover)
    return p
  }
  const bodyPage = () => addPage('body', '<div class="rh"></div><div class="textarea"></div><div class="folio"></div>')
  const fixHead = (p, chap) => {
    const n = parseInt(p.dataset.no)
    const rh = p.querySelector('.rh'); if (rh) rh.textContent = n % 2 ? chap : D.book
    const fo = p.querySelector('.folio'); if (fo) fo.textContent = n
  }
  const fits = (a) => a.scrollHeight <= a.clientHeight + 0.5
  // 장 하나를 쪽에 앉힌다. figTopOf 가 k 면 장의 k 번째 쪽 글 맨 앞에 삽화를 먼저 둔다.
  // 정한 단락 뒤에 그림이 안 들어가 다음 쪽으로 밀리면, 그 장을 지우고 그림이 밀리기 전 쪽의 맨 앞에 그림을 두고 다시 앉힌다
  // (장이 한두 쪽뿐일 때 장 끝 쪽에 그림과 한두 줄만 남는 일을 막는다)
  const layChapter = (ch, figTopOf) => {
    const startNo = pageNo
    const pagesMade = []
    const figs = []
    const op = addPage('opener', `<div class="head"><div class="chap-no">${ch.no}</div><h2 class="chap-title">${esc(ch.title)}</h2>` +
      `<div class="curve-wrap">${ch.curve}</div></div><div class="textarea"></div><div class="folio"></div>`)
    op.id = `pg-open-${ch.no}`
    fixHead(op, ch.title)
    pagesMade.push(op)
    let area = op.querySelector('.textarea')
    let k = 0
    let pendingFig = false, deferredFrom = null
    const newPage = () => { const p = bodyPage(); fixHead(p, ch.title); pagesMade.push(p); k += 1; area = p.querySelector('.textarea') }
    const placeFig = () => {
      const f = document.createElement('figure'); f.className = 'illus'
      f.innerHTML = `<img src="${ch.fig_src}" alt="">`
      // 정한 높이(62mm)가 안 들어가면 46mm 까지 줄여 본다
      for (const h of [62, 54, 46]) {
        f.style.height = h + 'mm'
        area.appendChild(f)
        if (fits(area)) { figs.push({ chapter: ch.no, page: pageNo, height_mm: h }); return true }
        f.remove()
      }
      return false
    }
    const useFigTop = ch.fig_src && figTopOf !== null
    if (useFigTop && figTopOf === 0) placeFig()
    for (let i = 0; i < ch.paras.length; i++) {
      let words = ch.paras[i].split(' ')
      let cont = false
      while (words.length) {
        if (pendingFig && area.childNodes.length === 0) { if (placeFig()) pendingFig = false }
        const p = document.createElement('p')
        p.className = 'body-p' + (cont ? ' cont' : '') + (i === 0 && !cont ? ' first' : '')
        p.textContent = words.join(' ')
        area.appendChild(p)
        if (fits(area)) { words = []; break }
        p.remove()
        let lo = 0, hi = words.length
        while (lo < hi) {
          const mid = Math.ceil((lo + hi) / 2)
          p.textContent = words.slice(0, mid).join(' ')
          area.appendChild(p)
          const ok = fits(area); p.remove()
          if (ok) lo = mid; else hi = mid - 1
        }
        if (lo > 0) { p.textContent = words.slice(0, lo).join(' '); area.appendChild(p); words = words.slice(lo); cont = true }
        newPage()
        if (useFigTop && figTopOf === k) placeFig()
      }
      if (ch.fig_src && !useFigTop && i === ch.fig_after) { if (!placeFig()) { pendingFig = true; deferredFrom = k } }
    }
    if (pendingFig) { if (!placeFig()) { newPage(); placeFig() } }
    return { startNo, pagesMade, figs, deferredFrom }
  }
  for (const ch of D.chapters) {
    let res = layChapter(ch, null)
    if (res.deferredFrom !== null) {
      for (const p of res.pagesMade) p.remove()
      pageNo = res.startNo
      res = layChapter(ch, res.deferredFrom)
      res.figs.forEach((f) => { f.moved_to_page_top = true })
    }
    report.chapter_pages.push(res.startNo + 1)
    report.figure_pages.push(...res.figs)
  }

  // 판권: 짝수 쪽(왼쪽), 속 쪽 수가 4의 배수가 되게 빈 쪽을 채운다
  while ((pageNo + 1) % 4 !== 0) addPage('blank', '')
  const col = addPage('colophon', document.getElementById('colophon-tmpl').innerHTML)
  col.id = 'pg-colophon'
  report.pages = pageNo
  for (const a of document.querySelectorAll('#book .textarea')) if (!fits(a)) report.overflow.push(a.parentElement.dataset.no)
  // 차례 쪽 번호
  for (const [i, n] of report.chapter_pages.entries()) {
    const el = document.querySelector(`.toc-row[data-ch="${i + 1}"] .toc-p`); if (el) el.textContent = n
  }
  // 펼침면: 두 쪽 모두 본문이 든 펼침면 가운데 둘째 장(없으면 첫째 장) 삽화가 든 것 (짝수 쪽이 왼쪽).
  // 빈 쪽·판권·글이 거의 없는 쪽(이야기가 왼쪽에서 끝난 경우)은 피한다
  const at = (n) => book.querySelector(`.page[data-no="${n}"]`)
  const filled = (n) => {
    const p = at(n); if (!p || !(p.classList.contains('body') || p.classList.contains('opener'))) return 0
    const t = p.querySelector('.textarea'); if (!t || !t.childNodes.length) return 0
    const used = t.lastElementChild.getBoundingClientRect().bottom - t.getBoundingClientRect().top
    return Math.min(1, used / t.clientHeight)
  }
  const figPages = new Map(report.figure_pages.map((f) => [f.page, f.chapter]))
  let best = null
  for (const floor of [0.6, 0.25]) { // 두 쪽 모두 60% 넘게 찬 펼침면을 먼저 찾고, 없으면 25%
    for (let left = 2; left + 1 <= pageNo; left += 2) {
      const a = filled(left), b = filled(left + 1)
      if (a < floor || b < floor) continue
      const ch = figPages.get(left) || figPages.get(left + 1)
      const score = (ch === 2 ? 3 : ch ? 2 : 0) + Math.min(a, b) +
        (at(left).classList.contains('body') && at(left + 1).classList.contains('body') ? 0.5 : 0)
      if (!best || score > best.score) best = { left, score }
    }
    if (best) break
  }
  let left = best ? best.left : null
  // 본문이 두 쪽 남짓인 짧은 이야기는 마주 보는 두 쪽이 모두 찬 펼침면이 없다. 이때는 글이 든 첫 두 쪽을 나란히 보여 준다
  // (펼침면이 아니라 연속 두 쪽이라는 표시를 보고서에 남긴다)
  const pair = left !== null ? [left, left + 1] : [report.chapter_pages[0], report.chapter_pages[0] + 1]
  if (left === null) report.spread_note = '연속 두 쪽 (두 쪽이 모두 찬 펼침면 없음)'
  const spread = document.getElementById('spread')
  for (const n of pair) { const c = at(n).cloneNode(true); c.removeAttribute('id'); spread.appendChild(c) }
  report.spread_pages = pair
  window.__bookReport = report
  window.__bookDone = true
})()
"""


def _colophon_html(b: dict[str, Any], title: str, author: str, made: str, art_report: dict[str, Any] | None) -> str:
    model = (art_report or {}).get("model")
    art_line = f"{model} 로 그림" if model else "팔레트와 정서 곡선으로 그린 절차적 그림"
    refs = len(b.get("references") or [])
    return (f"<div class='trim'><div class='c-mark'><b>버추얼미</b> · 기록에서 지은 이야기</div>"
            f"<div class='c-title'>{_esc(title)}</div><div class='c-author'>{_esc(author)} 지음</div><hr>"
            f"<dl><dt>만든 날</dt><dd>{_esc(made)}</dd>"
            f"<dt>장르</dt><dd>{_esc(b['genre_name'])}</dd>"
            f"<dt>표지</dt><dd>참고 표지 {refs}장과 {_esc(b['genre_name'])} 표지 문법에서 고른 디자인 ({_esc(b['archetype'])})</dd>"
            f"<dt>그림</dt><dd>{_esc(art_line)}</dd>"
            f"<dt>판형</dt><dd>152×225mm</dd></dl>"
            f"<div class='c-note'>이 책은 {_esc(author)} 님의 개인 기록을 바탕으로 생성한 이야기입니다. 기록 속 날짜·장소·활동에서 "
            f"출발했지만 사건과 대사는 지어낸 것이며, 실제 일과 다를 수 있습니다.</div></div>")


def build_html(b: dict[str, Any], title: str, author: str, paragraphs: list[str], art: Path | None,
               illustrations: dict[int, Path] | None = None, chapter_titles: list[str] | None = None,
               blurb: str = "", made: str = "", art_report: dict[str, Any] | None = None,
               bleed_mm: float = 0.0) -> tuple[str, dict[str, Any]]:
    """책 전체 HTML. illustrations: {장 번호: 그림 경로}. 반환: (HTML, 표지 제목 처리)."""
    illustrations = illustrations or {}
    tt = title_treatment(art, b)
    tf, tw, tls = title_font(b["typeface"], b["genre"])
    hand = tf in ("BkPen", "BkBrush")
    pt = SCALE_PT.get(b["title_scale"] or "large", 42) * (HANDWRITTEN_BOOST if hand else 1)
    pal = b["palette"]
    shadow = "rgba(0,0,0,0.35)" if tt["color"].lower() in ("#fbf8f2",) else "rgba(255,255,255,0.35)"
    if b["archetype"] != "full_bleed":
        shadow = "transparent"
    paper = "#fbf8f2"
    accent2 = pal["accent"] if contrast_ratio(pal["accent"], paper) >= 3 else pal["ink"] \
        if contrast_ratio(pal["ink"], paper) >= 3 else "#6b5f50"
    # 바탕색과 구별되는 강조색 (글자형 면, 나눔형 띠). 바탕과 대비 1.6 이상인 팔레트 색 가운데 채도 높은 것
    accent3 = pal["accent"]
    if contrast_ratio(accent3, pal["background"]) < 1.6:
        accent3 = max(pal["colors"], key=lambda h: contrast_ratio(h, pal["background"]))
    talign = "center" if b["archetype"] in ("pattern", "centered_motif", "framed") else "left"
    tmaxh = {"full_bleed": "72mm", "typographic": "80mm", "split": "56mm", "framed": "46mm", "centered_motif": "50mm",
             "pattern": "60mm"}.get(b["archetype"], "56mm")
    bl = max(0.0, float(bleed_mm))
    vars_ = (f"--pw:{152 + 2 * bl}mm;--ph:{225 + 2 * bl}mm;--bl:{bl}mm;"
             f"--bg:{pal['background']};--ink:{pal['ink']};--tc:{tt['color']};--mc:{tt['mark_color']};"
             f"--accent:{pal['accent']};--accent2:{accent2};--accent3:{accent3};"
             f"--tf:'{tf}';--tw:{tw};--tls:{tls};--tsize:{pt}pt;--tmaxh:{tmaxh};--shadow:{shadow};"
             f"--cnf:'{'BkSerif' if hand else tf}';--cnw:{800 if hand else tw};"
             f"--talign:{talign};--tpsize:{min(30.0, pt * 0.66) * (1.2 if hand else 1):.1f}pt;--figh:62mm")
    art_uri = art.as_uri() if art and art.exists() else None
    cover = _cover_html(b, title, author, art_uri, tt)
    total = max(1, len(paragraphs))
    chapters = b.get("chapters") or [{"index": 1, "start": 0, "end": len(paragraphs)}]
    titles = chapter_titles or []
    data_ch = []
    for i, ch in enumerate(chapters):
        a, e = ch["start"], ch["end"]
        fig = illustrations.get(ch["index"])
        data_ch.append({
            "no": ch["index"], "title": titles[i] if i < len(titles) and titles[i] else f"{ch['index']}장",
            "paras": paragraphs[a:e],
            "fig_after": (ch["illustration_after"] - a) if fig and fig.exists() and "illustration_after" in ch else -1,
            "fig_src": fig.as_uri() if fig and fig.exists() else "",
            "curve": _curve_segment_svg(b["emotion_curve"], accent2, a / total, e / total),
        })
    label = f"{b['genre_name']} 소설" if b["genre"] != "literary" else "소설"
    front = [
        f"<div class='page cover' id='pg-cover' data-no='0'>{cover}</div>",
        f"<div class='page titlepage recto' id='pg-title' data-no='1'><div class='trim'><div class='tp-label'>{_esc(label)}</div>"
        f"<h1 class='tp-title'>{_esc(title)}</h1><div class='tp-author'>{_esc(author)} 지음</div>"
        f"<div class='tp-mark'>버추얼미</div></div></div>",
        "<div class='page blank verso' data-no='2'></div>",
        "<div class='page toc recto' id='pg-toc' data-no='3'><div class='trim'><h2>차례</h2>"
        + "".join(f"<div class='toc-row' data-ch='{c['no']}'><span class='toc-no'>{c['no']}</span>"
                  f"<span class='toc-t'>{_esc(c['title'])}</span><span class='toc-p'></span></div>" for c in data_ch)
        + "</div></div>",
        "<div class='page blank verso' data-no='4'></div>",
    ]
    back = f"<div class='page cover back-page' id='pg-back' data-no='back'>{_back_html(b, title, blurb, art_uri)}</div>"
    payload = {"book": title, "front_pages": 4, "chapters": data_ch}
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{_esc(title)}</title>
<style>{_font_css()}
{CSS.replace('__FALLBACK__', FALLBACK_STACK).replace('var(--pw) var(--ph)', f'{152 + 2 * bl}mm {225 + 2 * bl}mm')}</style></head>
<body style="{vars_}">
<div id="spread"></div>
<div id="book">{''.join(front)}{back}</div>
<template id="colophon-tmpl">{_colophon_html(b, title, author, made, art_report)}</template>
<script type="application/json" id="bookdata">{data_json}</script>
<script>{JS}</script></body></html>"""
    return doc, tt


SHOTS = [("#pg-cover", "cover.png"), ("#pg-title", "title.png"), ("#pg-toc", "toc.png"), ("#pg-open-1", "opener.png"),
         ("#pg-open-2", "opener-2.png"), ("#spread", "spread.png"), ("#pg-colophon", "colophon.png"),
         ("#pg-back", "back.png")]


def run_browser(html_path: Path, out_dir: Path, timeout: int = 240) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node 가 없습니다 (virme/ 의 playwright 로 렌더합니다)")
    res = subprocess.run([node, str(HERE / "render.mjs"), str(html_path), str(out_dir), json.dumps(SHOTS)],
                         capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"렌더 실패: {res.stderr[-2000:]}")
    return json.loads(res.stdout.strip().splitlines()[-1])
