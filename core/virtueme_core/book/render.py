"""브리프 + 그림 + 이야기 → 책 HTML → PNG·PDF (신국판 152×225mm).

만드는 것: 앞표지, 장 시작 면, 본문 펼침면(가운데쯤 삽화가 든 두 쪽), 전체 PDF(표지·장 시작·본문 모든 쪽).

- 레이아웃 원형마다 표지 틀이 하나씩 있다 (`_cover_html`). 제목 크기는 브리프의 title_scale 에서 시작해 정해진 상자를
  넘치지 않을 때까지 줄인다 (브라우저 안에서 잰다).
- 그림 위에 제목이 얹히는 원형(full_bleed)은 제목 자리의 그림 명도와 복잡도를 재서 글자색(밝은 그림이면 짙은 글자)과
  바탕막(복잡하거나 대비가 4.5 미만이면 그 쪽 가장자리에서 번지는 반투명 막)을 정한다.
- 본문은 나눔명조 10pt, 줄 간격 1.72, `word-break: keep-all`, 양끝 맞춤. 쪽 나누기는 브라우저에서 단락을 하나씩
  넣어 보며 넘치면 단락을 어절 단위로 잘라 다음 쪽으로 넘긴다. 삽화가 남은 자리에 안 들어가면 다음 쪽 맨 위로 보낸다.
- 서체는 이 맥에 있는 것만 쓴다: 명조 나눔명조, 고딕 Pretendard, 장식 배민 도현·연성, 손글씨 나눔손글씨 펜·붓.
  `local()` 로 불러서, 없는 서체는 렌더 보고서의 fonts 에 status=error 로 남는다.
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
            side = {"top": "to bottom", "middle": "to bottom", "bottom": "to top"}[pos]
            if pos == "middle":
                scrim = (f"<div class='scrim' style='background:linear-gradient(to bottom, transparent 22%, {tt['scrim']} 38%, "
                         f"{tt['scrim']} 62%, transparent 78%)'></div>")
            else:
                scrim = (f"<div class='scrim' style='background:linear-gradient({side}, {tt['scrim']} 0%, "
                         f"{tt['scrim']} 30%, transparent 55%)'></div>")
        return f"<div class='cv full pos-{pos}'>{art}{scrim}{tblock}{mark}</div>"
    if arch == "framed":
        return (f"<div class='cv framed pos-{pos}'>{tblock if pos == 'top' else ''}<div class='frame'>{art}</div>"
                f"{tblock if pos != 'top' else ''}{mark}</div>")
    if arch == "split":
        txt, pic = f"<div class='half txt-half'>{tblock}</div>", f"<div class='half art-half'>{art}</div>"
        return f"<div class='cv split pos-{pos}'>{pic + txt if pos == 'bottom' else txt + pic}{mark}</div>"
    if arch == "pattern":
        return f"<div class='cv pattern pos-{pos}'>{art}<div class='box'>{tblock}</div>{mark}</div>"
    if arch == "centered_motif":
        return (f"<div class='cv motif pos-{pos}'>{tblock if pos == 'top' else ''}<div class='motif-wrap'>{art}</div>"
                f"{tblock if pos != 'top' else ''}{mark}</div>")
    # typographic
    return (f"<div class='cv typo pos-{pos}'>{tblock}<div class='typo-curve'>{_curve_svg(b['emotion_curve'], pal['accent'])}"
            f"</div>{mark}</div>")


CSS = r"""
@page { size: 152mm 225mm; margin: 0 }
* { box-sizing: border-box; margin: 0; padding: 0 }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact }
body { background: #d8d4cc; font-family: 'BkSerif', __FALLBACK__; color: #1b1814; padding: 10mm;
       display: flex; flex-direction: column; gap: 10mm; align-items: flex-start }
.page { width: 152mm; height: 225mm; position: relative; overflow: hidden; background: #fbf8f2;
        word-break: keep-all; overflow-wrap: break-word; line-break: strict }
/* ---------- 표지 ---------- */
.cover { background: var(--bg); color: var(--tc) }
.cv { position: absolute; inset: 0; display: flex; flex-direction: column }
.tblock { position: relative; z-index: 3; padding: 0 14mm; text-align: var(--talign) }
.label { font-family: 'BkSans'; font-weight: 600; font-size: 8pt; letter-spacing: 0.08em; opacity: 0.85; margin-bottom: 4mm }
.title { font-family: var(--tf); font-weight: var(--tw); letter-spacing: var(--tls); font-size: var(--tsize);
         line-height: 1.14; max-height: var(--tmaxh); overflow: hidden; text-wrap: balance }
.author { font-family: 'BkSans'; font-weight: 600; font-size: 10pt; margin-top: 5mm; letter-spacing: 0.02em }
.mark { position: absolute; z-index: 3; left: 0; right: 0; bottom: 9mm; text-align: center; font-family: 'BkSans';
        font-weight: 700; font-size: 7.5pt; letter-spacing: 0.3em; opacity: 0.8 }
.full .art { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover }
.full .scrim { position: absolute; inset: 0; z-index: 2 }
.full .mark { color: var(--mc); text-shadow: 0 0 2mm var(--shadow) }
.full.pos-top { justify-content: flex-start; padding-top: 22mm }
.full.pos-middle { justify-content: center }
.full.pos-bottom { justify-content: flex-end; padding-bottom: 24mm }
.full .tblock { text-shadow: 0 0 6mm var(--shadow) }
.framed { padding: 20mm 0 18mm; justify-content: space-between; align-items: stretch }
.framed .frame { margin: 0 auto; width: 104mm; height: 104mm; border: 0.6pt solid var(--tc); padding: 3mm; background: var(--bg) }
.framed .frame .art { width: 100%; height: 100%; object-fit: cover; display: block }
.framed.pos-top .tblock { margin-bottom: 4mm } .framed.pos-bottom .tblock { margin-top: 4mm; margin-bottom: 8mm }
.split .half { height: 50%; position: relative; display: flex; flex-direction: column; justify-content: center }
.split .art-half .art { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover }
.split .txt-half { background: var(--bg) }
.split .mark { color: var(--tc) }
.split.pos-top .mark, .split.pos-middle .mark { color: #fbf8f2; text-shadow: 0 0 3mm rgba(0,0,0,.5) }
.pattern .art { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover }
.pattern { justify-content: center; align-items: center }
.pattern .box { position: relative; z-index: 3; background: var(--bg); width: 112mm; padding: 12mm 0 11mm;
                box-shadow: 0 0 0 1.2mm var(--bg), 0 0 0 1.6mm var(--tc) }
.pattern .tblock { padding: 0 9mm }
.pattern .mark { background: var(--bg); width: 40mm; margin: 0 auto; padding: 1.5mm 0; bottom: 8mm }
.motif { padding: 24mm 0 22mm; justify-content: space-between }
.motif .motif-wrap { margin: 0 auto; width: 92mm; height: 92mm }
.motif .art { width: 100%; height: 100%; object-fit: cover; display: block; border-radius: 50%;
              -webkit-mask-image: radial-gradient(circle, #000 58%, transparent 71%); mask-image: radial-gradient(circle, #000 58%, transparent 71%) }
.motif.pos-bottom .tblock { margin-bottom: 8mm }
.typo { justify-content: center }
.typo.pos-top { justify-content: flex-start; padding-top: 30mm } .typo.pos-bottom { justify-content: flex-end; padding-bottom: 34mm }
.typo-curve { position: absolute; left: 14mm; right: 14mm; bottom: 26mm; height: 24mm }
.typo.pos-bottom .typo-curve { top: 26mm; bottom: auto }
.curve { width: 100%; height: 100% }
/* ---------- 장 시작 면 ---------- */
.opener .head { height: 86mm; padding: 34mm 18mm 0 15mm; position: relative }
.opener .chap-no { font-family: 'BkSans'; font-weight: 600; font-size: 9pt; color: var(--accent2); letter-spacing: 0.1em }
.opener .chap-title { font-family: var(--tf); font-weight: var(--tw); letter-spacing: var(--tls); font-size: var(--ctsize);
                      line-height: 1.25; margin-top: 4mm; color: #1b1814; text-wrap: balance }
.opener .head .curve-wrap { position: absolute; left: 15mm; right: 18mm; bottom: 4mm; height: 9mm; opacity: 0.9 }
/* ---------- 본문 ---------- */
.textarea { position: absolute; left: var(--ml); right: var(--mr); top: 22mm; bottom: 24mm; overflow: hidden }
.opener .textarea { top: 92mm }
.body-p { font-family: 'BkSerif'; font-size: 10pt; line-height: 1.72; text-align: justify; text-indent: 1em; letter-spacing: -0.015em;
          hanging-punctuation: none; color: #1f1b16 }
.body-p.cont { text-indent: 0 }
.body-p + .body-p { margin-top: 0 }
figure.illus { margin: 3mm 0 4mm; height: var(--figh); display: flex; align-items: center; justify-content: center }
figure.illus img { width: 100%; height: 100%; object-fit: cover; display: block;
                   -webkit-mask-image: radial-gradient(ellipse 72% 70% at 50% 50%, #000 62%, transparent 100%);
                   mask-image: radial-gradient(ellipse 72% 70% at 50% 50%, #000 62%, transparent 100%) }
.rh { position: absolute; top: 11mm; left: var(--ml); right: var(--mr); font-family: 'BkSans'; font-size: 7pt;
      color: #8a8378; letter-spacing: 0.04em }
.folio { position: absolute; bottom: 12mm; left: var(--ml); right: var(--mr); font-family: 'BkSans'; font-size: 7.5pt; color: #8a8378 }
.verso .rh, .verso .folio { text-align: left } .recto .rh, .recto .folio { text-align: right }
.verso { --ml: 15mm; --mr: 18mm } .recto { --ml: 18mm; --mr: 15mm }
/* ---------- 펼침면 미리보기 ---------- */
#spread { display: flex; box-shadow: 0 2mm 6mm rgba(0,0,0,.18) }
#spread .page:first-child { box-shadow: inset -6mm 0 6mm -6mm rgba(0,0,0,.18) }
#spread .page:last-child { box-shadow: inset 6mm 0 6mm -6mm rgba(0,0,0,.18) }
#book { display: none }
@media print {
  body { background: none; padding: 0; display: block }
  #cover-wrap, #spread, #opener { display: none !important }
  #book { display: block }
  .page { break-after: page }
}
"""

JS = r"""
(async () => {
  await document.fonts.ready
  const report = { fits: [], pages: 0, figure_page: null, overflow: [] }
  // 제목 맞추기: 상자를 넘치거나 한 어절이 줄을 넘으면 줄인다
  for (const el of document.querySelectorAll('.fit')) {
    let size = parseFloat(getComputedStyle(el).fontSize)
    const start = size
    const tooBig = () => el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1
    while (tooBig() && size > 12) { size *= 0.94; el.style.fontSize = size + 'px' }
    report.fits.push({ text: el.textContent, start_px: Math.round(start), final_px: Math.round(size) })
  }
  // 쪽 나누기
  const paras = JSON.parse(document.getElementById('paras').textContent)
  const figAfter = parseInt(document.body.dataset.figafter)
  const figSrc = document.body.dataset.figsrc
  const book = document.getElementById('book')
  const tmpl = (n) => {
    const p = document.createElement('div')
    p.className = 'page body ' + (n % 2 ? 'recto' : 'verso')
    p.innerHTML = `<div class="rh">${n % 2 ? document.body.dataset.chap : document.body.dataset.book}</div>` +
                  `<div class="textarea"></div><div class="folio">${n}</div>`
    return p
  }
  const opener = document.getElementById('opener').cloneNode(true)
  opener.id = 'opener-print'
  book.appendChild(opener)
  let pageNo = 1
  let area = document.getElementById('opener').querySelector('.textarea')
  let areaPrint = opener.querySelector('.textarea')
  const pages = [[document.getElementById('opener'), opener]]
  const fits = (a) => a.scrollHeight <= a.clientHeight + 0.5
  const newPage = () => {
    pageNo += 1
    const p = tmpl(pageNo); const q = tmpl(pageNo)
    document.getElementById('scratch').appendChild(p); book.appendChild(q)
    pages.push([p, q])
    area = p.querySelector('.textarea'); areaPrint = q.querySelector('.textarea')
  }
  const put = (node) => { area.appendChild(node); areaPrint.appendChild(node.cloneNode(true)) }
  const pop = () => { area.lastChild.remove(); areaPrint.lastChild.remove() }
  let pendingFig = false
  const placeFig = () => {
    const f = document.createElement('figure'); f.className = 'illus'
    f.innerHTML = `<img src="${figSrc}" alt="">`
    put(f)
    if (!fits(area)) { pop(); return false }
    report.figure_page = pageNo; return true
  }
  for (let i = 0; i < paras.length; i++) {
    let words = paras[i].split(' ')
    let cont = false
    while (words.length) {
      if (pendingFig && area.childNodes.length === 0) { if (placeFig()) pendingFig = false }
      const p = document.createElement('p'); p.className = 'body-p' + (cont ? ' cont' : '')
      p.textContent = words.join(' ')
      put(p)
      if (fits(area)) { words = []; break }
      pop()
      // 들어가는 만큼만 (어절 단위 이분 탐색)
      let lo = 0, hi = words.length
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2)
        p.textContent = words.slice(0, mid).join(' ')
        put(p)
        const ok = fits(area); pop()
        if (ok) lo = mid; else hi = mid - 1
      }
      if (lo > 0) { p.textContent = words.slice(0, lo).join(' '); put(p); words = words.slice(lo); cont = true }
      newPage()
    }
    if (figSrc && i === figAfter) { if (!placeFig()) pendingFig = true }
  }
  if (pendingFig) { if (area.childNodes.length && !placeFig()) { newPage(); placeFig() } else if (!area.childNodes.length) placeFig() }
  report.pages = pageNo
  for (const [a] of pages) { const t = a.querySelector('.textarea'); if (!fits(t)) report.overflow.push(a.className) }
  // 펼침면: 삽화가 든 쪽과 마주 보는 쪽 (짝수 쪽이 왼쪽)
  const fp = report.figure_page || 2
  let left = fp % 2 === 0 ? fp : fp - 1
  if (left < 2) left = 2
  // 이야기가 왼쪽 쪽에서 끝나 오른쪽이 비면 한 장 앞 펼침면(장 시작 면 포함)을 보여 준다
  if (left + 1 > pageNo && left - 1 >= 1) left -= 1
  const spread = document.getElementById('spread')
  for (const n of [left, left + 1]) {
    const src = pages[n - 1] ? pages[n - 1][0] : tmpl(n)
    const c = src.cloneNode(true); c.removeAttribute('id'); spread.appendChild(c)
  }
  report.spread_pages = [left, left + 1]
  document.getElementById('scratch').remove()
  window.__bookReport = report
  window.__bookDone = true
})()
"""


def build_html(b: dict[str, Any], title: str, author: str, paragraphs: list[str], art: Path | None,
               illus: Path | None) -> tuple[str, dict[str, Any]]:
    tt = title_treatment(art, b)
    tf, tw, tls = title_font(b["typeface"], b["genre"])
    pt = SCALE_PT.get(b["title_scale"] or "large", 42) * (HANDWRITTEN_BOOST if tf in ("BkPen", "BkBrush") else 1)
    pal = b["palette"]
    shadow = "rgba(0,0,0,0.35)" if tt["color"].lower() in ("#fbf8f2",) else "rgba(255,255,255,0.35)"
    if b["archetype"] != "full_bleed":
        shadow = "transparent"
    accent2 = pal["accent"] if contrast_ratio(pal["accent"], "#fbf8f2") >= 3 else pal["ink"] \
        if contrast_ratio(pal["ink"], "#fbf8f2") >= 3 else "#6b5f50"
    talign = "center" if b["archetype"] in ("pattern", "centered_motif", "framed") else "left"
    tmaxh = "70mm" if b["archetype"] in ("full_bleed", "typographic") else "52mm"
    if b["archetype"] in ("split",):
        tmaxh = "60mm"
    vars_ = (f"--bg:{pal['background']};--tc:{tt['color']};--mc:{tt['mark_color']};--accent:{pal['accent']};--accent2:{accent2};"
             f"--tf:'{tf}';--tw:{tw};--tls:{tls};--tsize:{pt}pt;--tmaxh:{tmaxh};--shadow:{shadow};"
             f"--talign:{talign};--ctsize:{min(26, pt * 0.62):.1f}pt;--figh:62mm")
    art_uri = art.as_uri() if art and art.exists() else None
    cover = _cover_html(b, title, author, art_uri, tt)
    opener = (f"<div class='page opener recto' id='opener' style='--ml:18mm;--mr:15mm'><div class='head'>"
              f"<div class='chap-no'>1</div><h2 class='chap-title'>{_esc(title)}</h2>"
              f"<div class='curve-wrap'>{_curve_svg(b['emotion_curve'], accent2, stroke=1.2)}</div></div>"
              f"<div class='textarea'></div><div class='folio'>1</div></div>")
    fig_after = b.get("illustration_after_paragraph", -1) if illus and illus.exists() else -1
    paras_json = json.dumps(paragraphs, ensure_ascii=False).replace("</", "<\\/")
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{_esc(title)}</title>
<style>{_font_css()}
{CSS.replace('__FALLBACK__', FALLBACK_STACK)}</style></head>
<body style="{vars_}" data-figafter="{fig_after}" data-figsrc="{illus.as_uri() if illus and illus.exists() else ''}"
 data-book="{_esc(title)}" data-chap="1. {_esc(title)}">
<div id="cover-wrap"><div class="page cover" id="cover">{cover}</div></div>
{opener}
<div id="spread"></div>
<div id="scratch" style="position:absolute;left:-9999px;top:0"></div>
<div id="book"><div class="page cover">{cover}</div></div>
<script type="application/json" id="paras">{paras_json}</script>
<script>{JS}</script></body></html>"""
    return doc, tt


def run_browser(html_path: Path, out_dir: Path, timeout: int = 180) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node 가 없습니다 (virme/ 의 playwright 로 렌더합니다)")
    res = subprocess.run([node, str(HERE / "render.mjs"), str(html_path), str(out_dir)], capture_output=True,
                         text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(f"렌더 실패: {res.stderr[-2000:]}")
    return json.loads(res.stdout.strip().splitlines()[-1])
