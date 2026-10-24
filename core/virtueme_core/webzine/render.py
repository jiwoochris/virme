"""웹진 한 호를 HTML 한 장으로 그린다. 꼭지마다 문지현 선생님의 VirtualME 스타일 가이드 템플릿(2026-10-03·06)을 따른다.

- 템플릿 원본 PDF 일곱 종은 `capstone/6_VirtualMe/webzine-templates/` 에 있다. 여기서는 판형(A4 세로), 바탕색, 강조색,
  서체 계열(명조 본문·이탤릭 세리프 제목·모노 라벨·굵은 산세리프), 꼭지별 구성(큰 제목·사진 격자·숫자 카드·어두운 띠)을
  옮겼다. 그림 자리는 그 주에 찍은 사진, 없으면 팀 StyleGAN 잠재공간 영상에서 뽑은 프레임으로 채운다.
- 글 속 `<em class="vx …">` 은 근거 검사를 통과한 값이다. 화면에서는 밑줄로 보이고 마우스를 올리면 근거가 뜬다.
"""
from __future__ import annotations

import html
import json
from datetime import date
from typing import Any

from .. import patterns

ACCENT = "#b8362c"
CREAM = "#f7f4ef"
SAND = "#ebe6dd"
INK = "#1c1a17"
FOREST = "#2d3b30"
NIGHT = "#0a1615"
ORANGE = "#ff5a1f"
TEAL = "#2fd4c0"

ORDER_LABEL = {
    "article": ("A —", "ARTICLES", "Self-Portrait"),
    "documentary": ("B —", "DOCUMENTARY", "Feature Story"),
    "fashion": ("C —", "FASHION", "Trend Report"),
    "digital_preview": ("D —", "DIGITAL PREVIEW", "Pop Culture"),
    "data_portrait": ("E —", "DATA PORTRAIT", "Information Graphic"),
    "novel": ("F —", "LITERATURE", "Novel"),
    "poem": ("G —", "POEM", "Poetry"),
}


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


CSS = r"""
@page { size: 210mm 297mm; margin: 0 }
* { box-sizing: border-box }
html { background: #d9d4cb }
body { margin: 0; font-family: 'AppleMyungjo', 'Nanum Myeongjo', 'Noto Serif KR', serif; color: #1c1a17; word-break: keep-all;
       overflow-wrap: break-word; -webkit-font-smoothing: antialiased }
.issue { display: flex; flex-direction: column; align-items: center; gap: 10mm; padding: 10mm 0 20mm }
.page { width: 210mm; min-height: 297mm; background: #f7f4ef; position: relative; overflow: hidden; display: flex; flex-direction: column;
        box-shadow: 0 10px 40px rgba(0,0,0,.18) }
@media print { html { background: none } .issue { padding: 0; gap: 0 } .page { box-shadow: none; break-after: page; height: 297mm; min-height: 0 } }
.serif-it { font-family: 'Times New Roman', 'Georgia', 'AppleMyungjo', serif; font-style: italic; font-weight: 400 }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; letter-spacing: .06em; text-transform: uppercase }
.sans { font-family: 'Pretendard', 'Apple SD Gothic Neo', sans-serif }
.red { color: #b8362c }
/* 상단 띠 */
.mast { display: grid; grid-template-columns: 30mm repeat(5, 1fr) 24mm; border-bottom: 1px solid #cfc7bb; font-size: 6.2pt; min-height: 12mm }
.mast > div { padding: 2.2mm 2mm; border-right: 1px solid #ded7cc; line-height: 1.25 }
.mast .brand { font-family: 'Times New Roman', serif; font-style: italic; font-size: 10.5pt; display: flex; align-items: center }
.mast .brand b { font-style: normal; font-weight: 700 }
.mast .on { color: #b8362c; border-bottom: 2px solid #b8362c }
.mast .k { font-family: 'JetBrains Mono', monospace; font-size: 5.6pt; letter-spacing: .08em; display: block; color: #8b8378 }
.mast .t { font-family: 'Times New Roman', serif; font-style: italic; font-size: 7.4pt }
.dark .mast { border-color: #2c3b38 } .dark .mast > div { border-color: #1f2e2c; color: #c9d2cf } .dark .mast .k { color: #6f8582 }
.strip { font-family: 'JetBrains Mono', monospace; font-size: 6pt; letter-spacing: .1em; color: #8b8378; padding: 2.5mm 12mm;
         border-bottom: 1px solid #ded7cc; display: flex; justify-content: space-between; text-transform: uppercase }
.foot { margin-top: auto; background: #ebe6dd; padding: 6mm 12mm 7mm; display: grid; grid-template-columns: 1.2fr 1fr 1fr; gap: 6mm; font-size: 7pt }
.foot .h { font-family: 'JetBrains Mono', monospace; font-size: 5.6pt; letter-spacing: .1em; color: #b8362c; text-transform: uppercase; margin-bottom: 1.5mm }
.foot .bar { grid-column: 1 / -1; border-top: 1px solid #d3cbbf; padding-top: 2.5mm; display: flex; justify-content: space-between;
             font-family: 'JetBrains Mono', monospace; font-size: 5.6pt; letter-spacing: .1em; color: #8b8378; text-transform: uppercase }
.dark .foot { background: #08110f; color: #c9d2cf } .dark .foot .bar { border-color: #22322f }
.pad { padding: 8mm 12mm }
.img { background: #2b2a28 center / cover no-repeat; position: relative }
.img .cap { position: absolute; left: 0; bottom: 0; background: rgba(247,244,239,.95); color: #1c1a17; font-family: 'JetBrains Mono', monospace;
            font-size: 5.6pt; letter-spacing: .08em; padding: 1.4mm 2.2mm; text-transform: uppercase }
.label { font-family: 'JetBrains Mono', monospace; font-size: 6pt; letter-spacing: .12em; color: #b8362c; text-transform: uppercase }
.body p { font-size: 9.3pt; line-height: 1.9; margin: 0 0 3.2mm; letter-spacing: -.01em }
em.vx { font-style: normal; background: linear-gradient(transparent 82%, rgba(184,54,44,.35) 0); cursor: help }
.dark em.vx { background: linear-gradient(transparent 82%, rgba(255,90,31,.55) 0) }
.vx-blur { color: inherit; opacity: .78 }
.why { font-family: 'JetBrains Mono', monospace; font-size: 5.6pt; color: #8b8378; letter-spacing: .04em; margin-top: 2mm; text-transform: none }
.dark .why { color: #6f8582 }
/* 표지 */
.cover-h { padding: 6mm 12mm 0; display: grid; grid-template-columns: 70mm 1fr; gap: 7mm }
.cover-h .big { font-family: 'Pretendard', sans-serif; font-weight: 900; font-size: 56pt; line-height: .88; letter-spacing: -.02em; margin: 2mm 0 4mm }
.cover-h .lead { font-size: 8.6pt; line-height: 1.75; margin: 0 0 4mm }
.chips { display: flex; gap: 1.5mm; flex-wrap: wrap }
.chips span { font-family: 'JetBrains Mono', monospace; font-size: 5.6pt; letter-spacing: .08em; padding: 1.2mm 2.2mm; border: 1px solid #1c1a17 }
.chips span.on { background: #b8362c; color: #fff; border-color: #b8362c }
.toc { padding: 5mm 12mm 0 }
.toc h3 { font-family: 'Times New Roman', serif; font-style: italic; font-weight: 400; font-size: 15pt; margin: 0 0 3mm }
.toc .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3mm }
.toc .tile { position: relative }
.toc .tile .img { aspect-ratio: 3 / 4 }
.toc .tile .no { position: absolute; top: 2mm; left: 2mm; background: #b8362c; color: #fff; font-family: 'JetBrains Mono', monospace; font-size: 6pt; padding: .8mm 1.6mm }
.toc .tile h4 { font-family: 'Times New Roman', 'AppleMyungjo', serif; font-style: italic; font-weight: 400; font-size: 10pt; margin: 2mm 0 .6mm }
.toc .tile p { font-size: 6.8pt; line-height: 1.55; margin: 0; color: #4a453f }
.stats { margin-top: 6mm; display: grid; grid-template-columns: repeat(6, 1fr); background: #ebe6dd }
.stats div { padding: 3.2mm 3mm; border-right: 1px solid #d3cbbf }
.stats .k { font-family: 'JetBrains Mono', monospace; font-size: 5.4pt; letter-spacing: .1em; color: #8b8378; text-transform: uppercase }
.stats .v { font-family: 'Times New Roman', serif; font-size: 20pt; margin-top: 1mm }
.cover-lines { list-style: none; padding: 0; margin: 4mm 0 0; border-top: 1px solid #1c1a17 }
.cover-lines li { font-size: 8.6pt; line-height: 1.5; padding: 2.2mm 0; border-bottom: 1px solid #d3cbbf }
.cover-lines li b { font-family: 'JetBrains Mono', monospace; font-size: 6pt; color: #b8362c; margin-right: 2mm }
/* A 기사 */
.a-head { display: grid; grid-template-columns: 1fr 64mm; gap: 6mm; padding: 6mm 12mm 0 }
.a-head h1 { font-family: 'Times New Roman', 'AppleMyungjo', serif; font-weight: 400; font-size: 38pt; line-height: .98; margin: 1mm 0 4mm; letter-spacing: -.01em }
.a-head h1 i { color: #b8362c }
.a-head .deck { font-size: 9pt; line-height: 1.75; margin: 0 }
.a-grid { display: grid; grid-template-columns: 1.3fr 1fr 1fr; gap: 3mm; padding: 5mm 12mm 0 }
.a-grid .img { height: 52mm } .a-grid .img:first-child { grid-row: span 2; height: 100% ; min-height: 74mm }
.a-reads { padding: 4mm 12mm 0; display: grid; grid-template-columns: repeat(3, 1fr); gap: 4mm; background: transparent }
.a-reads .card { background: #ebe6dd; padding: 4mm }
.a-reads .card .label { margin-bottom: 2mm }
.a-reads .card p { font-size: 8.2pt; line-height: 1.7; margin: 0 }
.a-people { margin: 4mm 12mm 0; display: grid; grid-template-columns: 54mm 1fr; background: #ebe6dd }
.a-people .l { padding: 5mm }
.a-people .l h3 { font-family: 'Times New Roman', serif; font-weight: 400; font-size: 20pt; line-height: 1.05; margin: 0 }
.a-people .l h3 i { color: #b8362c }
.a-people ol { list-style: none; margin: 0; padding: 0; border-left: 1px solid #d3cbbf }
.a-people li { display: grid; grid-template-columns: 10mm 1fr auto; padding: 3mm 4mm; border-bottom: 1px solid #d3cbbf; font-size: 8.6pt; align-items: baseline }
.a-people li .n { font-family: 'JetBrains Mono', monospace; font-size: 6pt; color: #8b8378 }
.a-people li .c { font-family: 'JetBrains Mono', monospace; font-size: 6.4pt; color: #b8362c }
/* B 다큐 */
.b-top { display: grid; grid-template-columns: 1fr 1fr; min-height: 78mm }
.b-top .red { background: #b8362c; color: #fff; padding: 7mm 10mm; display: flex; flex-direction: column }
.b-top .red .mono { font-size: 5.8pt; opacity: .85 }
.b-top .red h1 { font-family: 'Times New Roman', 'AppleMyungjo', serif; font-style: italic; font-weight: 700; font-size: 28pt; line-height: 1.02; margin: 4mm 0 3mm }
.b-top .red p { font-size: 8pt; line-height: 1.7; margin: 0; opacity: .95 }
.b-mid { display: grid; grid-template-columns: 60mm 1fr; gap: 7mm; padding: 8mm 12mm 0 }
.b-mid .img { height: 80mm }
.b-mid h2 { font-size: 17pt; line-height: 1.4; font-weight: 400; margin: 0 0 4mm }
.b-quote { border-left: 2px solid #b8362c; padding: 1mm 0 1mm 4mm; font-family: 'Times New Roman', 'AppleMyungjo', serif; font-style: italic; font-size: 10pt; line-height: 1.6; margin: 0 0 4mm }
.b-cols { column-count: 2; column-gap: 6mm }
.b-list { margin: 7mm 12mm 0; border: 1px solid #d3cbbf }
.b-list .hd { padding: 3mm 4mm; border-bottom: 1px solid #d3cbbf; font-family: 'Times New Roman', serif; font-style: italic; font-size: 12pt }
.b-list .row { display: grid; grid-template-columns: 1fr 1fr 1fr; }
.b-list .row div { padding: 3mm 4mm; border-right: 1px solid #d3cbbf; border-bottom: 1px solid #d3cbbf; font-size: 8.4pt }
.b-list .row .mono { font-size: 5.6pt; color: #b8362c; display: block; margin-top: 1mm }
.b-dark { margin: 7mm 0 0; background: #1c1a17; color: #f1ece4; display: grid; grid-template-columns: 1fr 1fr; min-height: 58mm }
.b-dark .img { min-height: 58mm }
.b-dark .t { padding: 7mm 9mm }
.b-dark .t .q { font-family: 'Times New Roman', 'AppleMyungjo', serif; font-style: italic; font-size: 13pt; line-height: 1.45; margin: 3mm 0 }
/* C 패션 */
.c-head { display: grid; grid-template-columns: 62mm 1fr; gap: 6mm; padding: 6mm 12mm 0 }
.c-head .img { height: 92mm }
.c-head .big { font-family: 'Pretendard', sans-serif; font-weight: 900; font-size: 54pt; line-height: .9; margin: 4mm 0 4mm; letter-spacing: -.02em }
.c-looks { padding: 7mm 12mm 0 }
.c-looks h3 { font-family: 'Times New Roman', serif; font-style: italic; font-weight: 400; font-size: 14pt; margin: 0 0 3mm }
.c-looks .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 2.5mm }
.c-looks .img { aspect-ratio: 3 / 4.4 }
.c-looks .no { position: absolute; top: 2mm; left: 2mm; background: #b8362c; color: #fff; font-family: 'JetBrains Mono', monospace; font-size: 6pt; padding: .8mm 1.6mm }
.c-looks h4 { font-family: 'Times New Roman', 'AppleMyungjo', serif; font-style: italic; font-weight: 400; font-size: 10pt; margin: 2mm 0 .5mm }
.c-looks p { font-size: 6.8pt; line-height: 1.55; margin: 0; color: #4a453f }
/* D 디지털 프리뷰 */
.dark { background: #0a1615; color: #e8efed }
.d-head { display: grid; grid-template-columns: 1fr 1fr; min-height: 112mm }
.d-head .t { padding: 8mm 10mm }
.d-head .big { font-family: 'Pretendard', sans-serif; font-weight: 900; font-size: 46pt; line-height: .92; margin: 4mm 0 2mm; color: #fff }
.d-head .big b { color: #2fd4c0; font-weight: 900 }
.d-head .rule { width: 12mm; height: 1.4mm; background: #ff5a1f; margin: 4mm 0 }
.d-head p { font-size: 8.4pt; line-height: 1.75; color: #c9d2cf; margin: 0 0 3mm }
.d-tiles { display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr }
.d-tiles .img { min-height: 56mm }
.d-tiles .n { position: absolute; top: 2mm; left: 3mm; font-family: 'Pretendard', sans-serif; font-weight: 900; font-size: 22pt; color: #ff5a1f }
.d-tiles .cap { background: rgba(10,22,21,.86); color: #e8efed }
.d-next { background: linear-gradient(135deg, #0f3b36 0%, #0a1615 55%, #3a1c12 100%); display: grid; grid-template-columns: 1fr 1.2fr; gap: 6mm; padding: 9mm 10mm }
.d-next h2 { font-family: 'Pretendard', sans-serif; font-weight: 900; font-size: 30pt; line-height: .95; margin: 2mm 0 3mm; color: #fff }
.d-next h2 b { color: #ff5a1f }
.d-next .items { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm }
.d-next .it { border: 1px solid rgba(255,255,255,.22); padding: 3mm; background: rgba(255,255,255,.04) }
.d-next .it .n { font-family: 'Pretendard', sans-serif; font-weight: 800; color: #ff5a1f; font-size: 11pt }
.d-next .it h4 { font-family: 'Pretendard', sans-serif; font-size: 8.4pt; margin: 1mm 0; color: #fff }
.d-next .it p { font-size: 7pt; line-height: 1.55; margin: 0; color: #b9c5c2 }
.d-lists { display: grid; grid-template-columns: repeat(3, 1fr); gap: 5mm; padding: 7mm 10mm }
.d-lists h5 { font-family: 'JetBrains Mono', monospace; font-size: 6pt; letter-spacing: .12em; color: #2fd4c0; margin: 0 0 2mm; text-transform: uppercase }
.d-lists ol { margin: 0; padding: 0; list-style: none; counter-reset: x }
.d-lists li { font-family: 'Pretendard', sans-serif; font-size: 7.6pt; padding: 1.6mm 0; border-bottom: 1px solid #1f2e2c; display: flex; justify-content: space-between; gap: 2mm }
.d-lists li span:last-child { font-family: 'JetBrains Mono', monospace; color: #ff5a1f; font-size: 6.4pt }
/* E 데이터 포트레이트 */
.e-head { display: grid; grid-template-columns: 1fr 66mm; gap: 6mm; padding: 6mm 12mm 0 }
.e-head h1 { font-family: 'Times New Roman', serif; font-weight: 700; font-size: 34pt; line-height: .95; margin: 1mm 0 0; letter-spacing: -.01em }
.e-head h1 i { color: #b8362c; font-weight: 400 }
.e-head .side { border-left: 2px solid #b8362c; padding-left: 4mm; font-size: 8pt; line-height: 1.7 }
.e-hero { margin: 4mm 12mm 0; height: 38mm; position: relative }
.e-hero .over { position: absolute; left: 4mm; bottom: 4mm; color: #fff; font-family: 'Times New Roman', serif; font-size: 13pt; font-weight: 700; line-height: 1.1; text-shadow: 0 1px 6px rgba(0,0,0,.5) }
.e-hero .over i { color: #ff8f80 }
.e-nums { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0 6mm; padding: 3mm 12mm 0 }
.e-num { border-top: 1px solid #1c1a17; padding: 2.5mm 0 3mm; display: grid; grid-template-columns: 1fr auto; align-items: end }
.e-num .k { font-family: 'JetBrains Mono', monospace; font-size: 5.8pt; color: #8b8378; letter-spacing: .1em }
.e-num .v { font-family: 'Times New Roman', serif; font-size: 19pt; line-height: 1 }
.e-num .u { font-family: 'JetBrains Mono', monospace; font-size: 6pt; margin-left: 1mm }
.e-num h4 { grid-column: 1 / -1; font-family: 'Times New Roman', 'AppleMyungjo', serif; font-style: italic; font-weight: 400; font-size: 9.6pt; margin: 1.5mm 0 .5mm }
.e-num p { grid-column: 1 / -1; font-size: 7pt; line-height: 1.6; margin: 0; color: #4a453f }
.e-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 6mm; padding: 3mm 12mm 0 }
.e-charts h5 { font-family: 'JetBrains Mono', monospace; font-size: 6pt; letter-spacing: .12em; color: #b8362c; margin: 0 0 2mm; text-transform: uppercase }
.e-green { margin-top: 4mm; background: #2d3b30; color: #eef0ea; padding: 5mm 12mm; display: grid; grid-template-columns: 1fr 1fr; gap: 6mm }
.e-green h3 { font-family: 'Times New Roman', serif; font-size: 16pt; font-weight: 400; margin: 0 0 2mm }
.e-green h3 i { color: #e4a39a }
.e-green .tiles { display: grid; grid-template-columns: 1fr 1fr; gap: 2.5mm }
.e-green .img { height: 24mm }
.e-green .facts li { font-size: 7pt; line-height: 1.55; margin-bottom: 1.2mm }
.e-green .facts { padding-left: 4mm; margin: 0 }
/* F 소설 */
.f-top { padding: 6mm 12mm 0 }
.f-top .img { height: 82mm; margin: 0 14mm }
.f-title { text-align: left; padding: 7mm 26mm 0 }
.f-title .label { margin-bottom: 3mm }
.f-title h1 { font-size: 30pt; font-weight: 400; line-height: 1.25; margin: 0 0 4mm }
.f-title h1 i { color: #b8362c; font-style: italic }
.f-title .lead { font-size: 8.4pt; line-height: 1.8; color: #4a453f; max-width: 110mm }
.f-body { padding: 7mm 12mm 0; display: grid; grid-template-columns: 26mm 1fr; gap: 6mm; border-top: 1px solid #d3cbbf; margin: 7mm 12mm 0; padding-left: 0; padding-right: 0 }
.f-body .drop { font-size: 52pt; line-height: .9; color: #b8362c }
.f-body .txt { column-count: 2; column-gap: 7mm }
.f-body .txt p { font-size: 8.8pt; line-height: 1.95; margin: 0 0 3mm; text-indent: 1em }
.f-dark { margin-top: 7mm; background: #1c1a17; color: #f1ece4; display: grid; grid-template-columns: 1fr 1fr; min-height: 70mm }
.f-dark .img { min-height: 70mm }
.f-dark .q { padding: 9mm; font-size: 15pt; line-height: 1.55; display: flex; flex-direction: column; justify-content: flex-end }
.f-dark .q i { color: #e4a39a }
.f-cont { padding: 7mm 12mm 0; column-count: 2; column-gap: 7mm }
.f-cont p { font-size: 8.8pt; line-height: 1.95; margin: 0 0 3mm; text-indent: 1em }
/* G 시 */
.g-head { padding: 7mm 12mm 0; display: grid; grid-template-columns: 1fr 50mm }
.g-head h1 { font-size: 30pt; font-weight: 400; line-height: 1.3; margin: 0 }
.g-head h1 i { color: #b8362c; font-style: italic }
.g-head .meta { font-family: 'JetBrains Mono', monospace; font-size: 6pt; color: #8b8378; border-top: 1px solid #1c1a17; padding-top: 2mm; line-height: 2 }
.g-main { margin-top: 6mm; background: #ebe6dd; display: grid; grid-template-columns: 70mm 1fr; gap: 10mm; padding: 10mm 12mm }
.g-main .img { height: 82mm }
.g-main .lines p { font-size: 11pt; line-height: 2.1; margin: 0 }
.g-main .lines .st { margin-bottom: 7mm }
.g-green { background: #2d3b30; color: #eef0ea; display: grid; grid-template-columns: 1fr 1fr; min-height: 56mm }
.g-green .img { min-height: 56mm; filter: grayscale(1) contrast(.9) brightness(1.1) }
.g-green .v { padding: 10mm; writing-mode: vertical-rl; font-size: 13pt; line-height: 2; letter-spacing: .1em; margin-left: auto }
.g-epi { padding: 5mm 26mm; font-family: 'Times New Roman', 'AppleMyungjo', serif; font-style: italic; font-size: 10pt; color: #4a453f; text-align: center }
"""


def _img_style(src: str | None) -> str:
    return f"background-image:url('{esc(src)}')" if src else "background:linear-gradient(135deg,#3a3633,#1c1a17)"


def mast(current: str, issue: dict[str, Any]) -> str:
    cells = []
    for key in ("article", "documentary", "fashion", "digital_preview", "data_portrait"):
        a, b, c = ORDER_LABEL[key]
        on = " on" if key == current or (current in ("novel", "poem") and False) else ""
        cells.append(f"<div class='{on.strip()}'><span class='k'>{esc(a)} {esc(b)}</span><span class='t'>{esc(c)}</span></div>")
    d = date.fromisoformat(issue["range"][1])
    return (f"<div class='mast'><div class='brand'><span><b>Virtual</b>ME</span></div>{''.join(cells)}"
            f"<div><span class='k'>{d.strftime('%B').upper()}</span><span class='t'>{d.year}</span></div></div>")


def strip(left: str, right: str) -> str:
    return f"<div class='strip'><span>{esc(left)}</span><span>{esc(right)}</span></div>"


def foot(issue: dict[str, Any], slot: str, cols: list[tuple[str, str]]) -> str:
    cells = "".join(f"<div><div class='h'>{esc(h)}</div>{t}</div>" for h, t in cols[:3])
    return (f"<div class='foot'>{cells}<div class='bar'><span>VirtualME · {esc(issue['title'])}</span>"
            f"<span>{esc(slot)}</span><span>{esc(issue['period_ko'])}</span></div></div>")


def why_line(sec: dict[str, Any]) -> str:
    sig = ", ".join(f"{s['label']} {s['count']}건" for s in sec.get("signals", [])[:3])
    return (f"<div class='why'>템플릿 매칭: {esc(sec['label'])} · {esc(sec['why'])}" + (f" · 신호 {esc(sig)}" if sig else "")
            + "</div>")


def paras(ps: list[str], cls: str = "") -> str:
    return "".join(f"<p class='{cls}'>{p}</p>" for p in ps)


def _first_text(ps: list[str]) -> str:
    import re

    return re.sub(r"<[^>]+>", "", ps[0]) if ps else ""


# ---------------------------------------------------------------- 꼭지
def page_cover(issue, ctx) -> str:
    cv = ctx["cover"]
    imgs = ctx["images"]
    secs = [s for s in issue["sections"] if s["included"]]
    tiles = []
    for i, s in enumerate(secs[:4]):
        a, b, c = ORDER_LABEL[s["template"]]
        tiles.append(f"<div class='tile'><div class='img' style=\"{_img_style(imgs.pick(i + 1))}\"></div>"
                     f"<span class='no'>{i + 1:02d}</span><h4>{esc(s['slot'])}</h4><p>{esc(b.title())} · {esc(c)}</p></div>")
    lines = "".join(f"<li><b>{i + 1:02d}</b>{esc(t)}</li>" for i, t in enumerate(cv.get("lines", [])[:3]))
    stats = "".join(f"<div><div class='k'>{esc(k)}</div><div class='v'>{esc(v)}</div></div>" for k, v in ctx["stats"][:6])
    chips = "".join(f"<span class='{'on' if i == 0 else ''}'>{esc(s['slot'])}</span>" for i, s in enumerate(secs))
    return f"""<section class="page" id="pg-cover">{mast('article', issue)}{strip('Weekly self-portrait · ' + issue['title'], issue['period_ko'])}
<div class="cover-h"><div class="img" style="{_img_style(imgs.pick(0))};min-height:96mm"></div>
<div><div class="label">이번 호 · {esc(issue['period_ko'])}</div><div class="big">VIRTUAL<br>ME</div>
<p class="lead"><b style="font-size:13pt;font-weight:400">{esc(cv.get('headline', ''))}</b><br>{esc(issue['subtitle'])}</p>
<div class="chips">{chips}</div><ul class="cover-lines">{lines}</ul></div></div>
<div class="toc"><h3>In This Issue <span class="mono" style="font-size:6pt;color:#8b8378;font-style:normal">{len(secs)} sections</span></h3>
<div class="grid">{''.join(tiles)}</div></div>
<div class="stats">{stats}</div>
{foot(issue, 'Cover', [('기간', esc(issue['period_ko'])), ('기록', esc(f"{issue['records']}건 · 출처 {issue['sources']}종")), ('만든 방법', '규칙표로 템플릿을 고르고, 글은 기록과 대조했습니다')])}</section>"""


def page_article(issue, ctx, sec) -> str:
    d = ctx["text"].get("article") or {}
    imgs = ctx["images"]
    head = d.get("headline") or "이번 주의 나"
    words = head.split()
    h1 = (" ".join(words[:-1]) + f" <i>{esc(words[-1])}</i>") if len(words) > 1 else f"<i>{esc(head)}</i>"
    if len(words) > 1:
        h1 = esc(" ".join(words[:-1])) + f"<br><i>{esc(words[-1])}</i>"
    body = d.get("body") or []
    cards = "".join(f"<div class='card'><div class='label'>{esc(lab)}</div><p>{p}</p></div>"
                    for lab, p in zip(("Long read 01", "Long read 02", "Long read 03"), body[:3]))
    people = "".join(f"<li><span class='n'>{i + 1:02d}</span><span>{esc(c['who'])}</span><span class='c'>{c['messages']} msgs</span></li>"
                     for i, c in enumerate(ctx["fx"]["cast"][:4]))
    quote = ctx.get("quote")
    return f"""<section class="page" id="pg-article">{mast('article', issue)}{strip('A — Articles · Self-Portrait', d.get('kicker') or sec['slot'])}
<div class="a-head"><div><div class="label">{esc(sec['slot'])}</div><h1>{h1}</h1><p class="deck">{esc(d.get('deck', ''))}</p></div>
<div style="border-left:2px solid #b8362c;padding-left:4mm;align-self:end">{f'<p class="serif-it" style="font-size:10pt;line-height:1.6;margin:0">“{esc(quote)}”</p><div class="label" style="margin-top:2mm;color:#8b8378">내가 쓴 글에서</div>' if quote else ''}</div></div>
<div class="a-grid"><div class="img" style="{_img_style(imgs.pick(1))}"><span class="cap">Cover story</span></div>
<div class="img" style="{_img_style(imgs.pick(2))}"></div><div class="img" style="{_img_style(imgs.pick(3))}"></div>
<div class="body" style="grid-column:span 2;padding-top:1mm">{paras(body[:1])}</div></div>
<div class="a-reads">{cards}</div>
<div class="a-people"><div class="l"><div class="label" style="color:#8b8378">이번 주 대화 상대</div><h3>Who was<br><i>with me?</i></h3>{why_line(sec)}</div><ol>{people}</ol></div>
{foot(issue, sec['slot'], ctx['foot_a'])}</section>"""


def page_documentary(issue, ctx, sec) -> str:
    d = ctx["text"].get("documentary") or {}
    imgs = ctx["images"]
    body = d.get("body") or []
    rows = []
    for p in ctx["fx"]["places"][:6]:
        name = "집" if p["home"] else "직장" if p["work"] else (p["name"] if p["named"] else "이름 없는 장소")
        rows.append(f"<div>{esc(name)}<span class='mono'>{p['days']}일 · {p['minutes'] // 60}시간 {p['minutes'] % 60}분</span></div>")
    while len(rows) % 3:
        rows.append("<div></div>")
    head = d.get("headline") or "다녀온 곳"
    return f"""<section class="page" id="pg-documentary">{mast('documentary', issue)}
<div class="b-top"><div class="red"><span class="mono">B — Documentary · Feature story</span><h1>#{esc(head)}</h1>
<span class="mono" style="margin-top:auto">{esc(issue['period_ko'])}</span></div>
<div class="img" style="{_img_style(imgs.pick(4))}"></div></div>
<div class="b-mid"><div><div class="img" style="{_img_style(imgs.pick(5))}"></div><div class="why">{esc(ctx['photo_note'])}</div></div>
<div><div class="label">{esc(sec['slot'])}</div><h2>{esc(d.get('deck') or _first_text(body)[:42])}</h2>
<div class="b-quote">{esc(ctx.get('place_line') or '')}</div><div class="body b-cols">{paras(body)}</div></div></div>
<div class="b-list"><div class="hd">In the Places <span class="mono" style="font-size:5.6pt;color:#8b8378;font-style:normal">머문 날 · 머문 시간</span></div>
<div class="row">{''.join(rows)}</div></div>
<div style="padding:0 12mm">{why_line(sec)}</div>
{foot(issue, sec['slot'], ctx['foot_b'])}</section>"""


def page_fashion(issue, ctx, sec) -> str:
    d = ctx["text"].get("fashion") or {}
    imgs = ctx["images"]
    looks = d.get("looks") or [{"title": t, "line": ""} for t in ctx["fx"]["photo_tags"]][:4]
    tiles = "".join(f"<div><div class='img' style=\"{_img_style(imgs.pick(6 + i, prefer='fashion'))}\"><span class='no'>{i + 1}</span></div>"
                    f"<h4>{esc(l.get('title'))}</h4><p>{esc(l.get('line'))}</p></div>" for i, l in enumerate(looks[:4]))
    return f"""<section class="page" id="pg-fashion">{mast('fashion', issue)}{strip('C — Fashion · Trend report', sec['slot'])}
<div class="c-head"><div class="img" style="{_img_style(imgs.pick(6, prefer='fashion'))}"></div>
<div><div class="label">{esc(sec['slot'])}</div><div class="big">{esc(d.get('headline') or 'VIRTUAL ME')}</div>
<p style="font-size:9pt;line-height:1.75">{esc(d.get('deck', ''))}</p><div class="body">{paras(d.get('body') or [])}</div>{why_line(sec)}</div></div>
<div class="c-looks"><h3>Trend Looks</h3><div class="grid">{tiles}</div></div>
{foot(issue, sec['slot'], ctx['foot_c'])}</section>"""


def page_digital(issue, ctx, sec) -> str:
    d = ctx["text"].get("digital_preview") or {}
    imgs = ctx["images"]
    t = ctx["fx"]["taste"]
    tiles = []
    labels = [c["name"] for c in t.get("channels", [])[:2]] + [a["name"] for a in t.get("artists", [])[:2]]
    for i, lab in enumerate((labels + ["", "", "", ""])[:4]):
        tiles.append(f"<div class='img' style=\"{_img_style(imgs.latent(i))}\"><span class='n'>{i + 1:02d}</span>"
                     + (f"<span class='cap'>{esc(lab)}</span>" if lab else "") + "</div>")
    items = d.get("items") or []
    its = "".join(f"<div class='it'><span class='n'>{i + 1:02d}</span><h4>{esc(x.get('title'))}</h4><p>{esc(x.get('line'))}</p></div>"
                  for i, x in enumerate(items[:4]))

    def ol(rows):
        return "<ol>" + "".join(f"<li><span>{esc(a)}</span><span>{esc(b)}</span></li>" for a, b in rows) + "</ol>"
    lists = (f"<div><h5>Channels</h5>{ol([(c['name'], c['count']) for c in t.get('channels', [])[:5]])}</div>"
             f"<div><h5>Artists</h5>{ol([(a['name'], a['count']) for a in t.get('artists', [])[:5]])}</div>"
             f"<div><h5>Searches</h5>{ol([(q['query'], q['count']) for q in t.get('queries', [])[:5]])}</div>")
    head = d.get("headline") or "PREVIEW ON!"
    return f"""<section class="page dark" id="pg-digital_preview">{mast('digital_preview', issue)}
<div class="d-head"><div class="t"><span class="mono" style="font-size:6pt;color:#2fd4c0">D — Digital preview · Pop culture</span>
<div class="big">PREVIEW<br><b>ON!</b></div><div class="rule"></div><p style="font-size:12pt;color:#fff">{esc(head)}</p>
<p>{esc(d.get('deck', ''))}</p><div class="body">{paras(d.get('body') or [])}</div></div>
<div class="d-tiles">{''.join(tiles)}</div></div>
<div class="d-next"><div><span class="mono" style="font-size:6pt;color:#2fd4c0">This week on screen</span><h2>WHERE<br>TO<br><b>NEXT?</b></h2>{why_line(sec)}</div>
<div class="items">{its}</div></div>
<div class="d-lists">{lists}</div>
{foot(issue, sec['slot'], ctx['foot_d'])}</section>"""


def _hour_svg(hist: list[int], color: str = ACCENT) -> str:
    mx = max(hist) or 1
    w, h = 82, 26
    bars = "".join(f"<rect x='{i * w / 24 + .3:.2f}' y='{h - 2 - (v / mx) * (h - 6):.2f}' width='{w / 24 - .6:.2f}' "
                   f"height='{(v / mx) * (h - 6):.2f}' fill='{color if i in (23, 0, 1, 2, 3, 4) else '#1c1a17'}'/>" for i, v in enumerate(hist))
    ticks = "".join(f"<text x='{i * w / 24:.1f}' y='{h + 2.6}' font-size='2.2' font-family='JetBrains Mono'>{i:02d}</text>" for i in (0, 6, 12, 18))
    return f"<svg viewBox='0 0 {w} {h + 4}' width='100%'>{bars}{ticks}</svg>"


def _daily_svg(daily: list[dict[str, Any]], key: str, color: str = "#1c1a17") -> str:
    vals = [d.get(key) or 0 for d in daily]
    mx = max(vals) or 1
    n = len(vals)
    w, h = 82, 26
    bw = w / n
    bars = "".join(f"<rect x='{i * bw + bw * .18:.2f}' y='{h - 2 - (v / mx) * (h - 7):.2f}' width='{bw * .64:.2f}' "
                   f"height='{(v / mx) * (h - 7):.2f}' fill='{color}'/>"
                   f"<text x='{i * bw + bw / 2:.2f}' y='{h - 3.4 - (v / mx) * (h - 7):.2f}' font-size='2.1' text-anchor='middle' font-family='JetBrains Mono'>{v:,}</text>"
                   f"<text x='{i * bw + bw / 2:.2f}' y='{h + 2.6}' font-size='2.3' text-anchor='middle' font-family='JetBrains Mono'>{d['label']}</text>"
                   for i, (v, d) in enumerate(zip(vals, daily)))
    return f"<svg viewBox='0 0 {w} {h + 4}' width='100%'>{bars}</svg>"


def page_data(issue, ctx, sec) -> str:
    fx = ctx["fx"]
    imgs = ctx["images"]
    nums = "".join(f"<div class='e-num'><span class='k'>{esc(k)}</span><span><span class='v'>{esc(v)}</span><span class='u'>{esc(u)}</span></span>"
                   f"<h4>{esc(h)}</h4><p>{esc(p)}</p></div>" for k, v, u, h, p in ctx["numbers"][:6])
    facts = "".join(f"<li>{esc(f['text'])}</li>" for f in ctx["data_facts"][:5])
    steps_chart = (f"<div><h5>Daily steps</h5>{_daily_svg(fx['daily'], 'steps')}</div>" if any(d.get('steps') for d in fx['daily'])
                   else f"<div><h5>Daily records</h5>{_daily_svg(fx['daily'], 'records')}</div>")
    return f"""<section class="page" id="pg-data_portrait">{mast('data_portrait', issue)}{strip('E — Data portrait · Information graphic', 'Data portrait · ' + issue['period_ko'])}
<div class="e-head"><div><div class="label">Information graphic · {esc(sec['slot'])}</div><h1>ANATOMY<br>OF <i>A VIRTUAL<br>SELF</i></h1></div>
<div class="side">이 쪽의 숫자는 모두 이번 주 기록을 세어 얻은 값입니다. AI 모델이 만든 값은 없습니다. 기록 {fx and len(fx['records'])}건, 출처 {issue['sources']}종에서 셌습니다.{why_line(sec)}</div></div>
<div class="e-hero img" style="{_img_style(imgs.latent(4))}"><div class="over">THE SELF IS<br><i>NOT A FILE.</i></div></div>
<div class="e-nums">{nums}</div>
<div class="e-charts"><div><h5>Hour of day · 밤 11시~새벽 5시 빨강</h5>{_hour_svg(fx['hour_histogram'])}</div>{steps_chart}</div>
<div class="e-green"><div><span class="mono" style="font-size:6pt;color:#9fb3a3">Behind the numbers</span><h3>The images behind<br><i>the numbers</i></h3>
<ul class="facts">{facts}</ul></div><div class="tiles"><div class="img" style="{_img_style(imgs.latent(5))}"></div><div class="img" style="{_img_style(imgs.latent(6))}"></div>
<div class="img" style="{_img_style(imgs.latent(7))}"></div><div class="img" style="{_img_style(imgs.latent(8))}"></div></div></div>
{foot(issue, sec['slot'], ctx['foot_e'])}</section>"""


def page_novel(issue, ctx, sec) -> str:
    n = ctx["text"].get("novel") or {}
    imgs = ctx["images"]
    ps = n.get("paragraphs") or []
    if not ps:
        return ""
    title = n.get("title") or "무제"
    words = title.split()
    h1 = (esc(" ".join(words[:-1])) + f"<br><i>{esc(words[-1])}</i>") if len(words) > 1 else f"<i>{esc(title)}</i>"
    first = ps[0]
    import re

    plain = re.sub(r"<[^>]+>", "", first)
    drop = plain[:1]
    first_rest = first[1:] if first.startswith(drop) else first
    ps = _merge_short(ps)
    first_n, used = 0, 0
    for p_ in ps:   # 첫 쪽에는 그림·제목 아래 두 단으로 약 700자
        if first_n and used + _plen(p_) > 700:
            break
        used += _plen(p_)
        first_n += 1
    if first_n == 0:
        first_n = 1
    first = ps[0]
    plain = re.sub(r"<[^>]+>", "", first)
    drop = plain[:1]
    first_rest = first[1:] if first.startswith(drop) else first
    rest = ps[first_n:]
    quote_src = re.sub(r"<[^>]+>", "", rest[0] if rest else ps[-1])
    quote = (re.split(r"(?<=[.!?])\s", quote_src)[0][:70]) if quote_src else ""
    foot_cols = [('씨앗 단어', esc(n.get('keyword'))), ('장르', esc(n.get('genre') or '')), ('근거 검사', esc(ctx['novel_check']))]
    out = [f"""<section class="page" id="pg-novel">{mast('novel', issue)}{strip('F — Literature · Novel', (n.get('genre') or '') + ' · 씨앗 단어 ' + (n.get('keyword') or ''))}
<div class="f-top"><div class="img" style="{_img_style(imgs.latent(9))}"></div></div>
<div class="f-title"><div class="label">{esc(sec['slot'])} · {esc(n.get('genre') or '')}</div><h1>{h1}</h1>
<p class="lead">이번 주 기록에서 가장 자주 나온 말 「{esc(n.get('keyword'))}」을 씨앗으로 지은 이야기입니다. 밑줄 친 값은 기록과 대조해 맞은 것입니다.</p>{why_line(sec)}</div>
<div class="f-body"><div class="drop">{esc(drop)}</div><div class="txt body"><p style="text-indent:0">{first_rest}</p>{paras(ps[1:first_n])}</div></div>
{foot(issue, sec['slot'], foot_cols)}</section>"""]
    # 이어지는 쪽: 첫 이어짐 쪽은 어두운 인용 띠 아래 약 2,000자, 그다음 쪽부터 약 3,300자
    chunks, cur, size, budget = [], [], 0, 2000
    for p_ in rest:
        if cur and size + _plen(p_) > budget:
            chunks.append(cur)
            cur, size, budget = [], 0, 3300
        cur.append(p_)
        size += _plen(p_)
    if cur:
        chunks.append(cur)
    for k, chunk in enumerate(chunks):
        pid = "pg-novel2" if k == 0 else f"pg-novel{k + 2}"
        dark = (f"""<div class="f-dark" style="margin-top:0"><div class="img" style="{_img_style(imgs.latent(10))}"></div><div class="q">{esc(quote)}<span class="mono" style="font-size:5.6pt;margin-top:3mm;color:#a59d92">— {esc(title)}</span></div></div>"""
                if k == 0 else "")
        out.append(f"""<section class="page" id="{pid}">{mast('novel', issue)}{strip('F — Literature · Novel', title + ' · 이어서')}
{dark}<div class="f-cont body">{paras(chunk)}</div>
{foot(issue, sec['slot'], foot_cols)}</section>""")
    return "".join(out)


def _plen(p: str) -> int:
    import re

    return len(re.sub(r"<[^>]+>", "", p)) + 40   # 문단 사이 여백


def _merge_short(ps: list[str], short: int = 60) -> list[str]:
    """대사 한 줄씩 끊긴 문단을 이어 붙여 문단 수를 줄인다 (짧은 줄이 이어지면 한 문단으로)."""
    import re

    out: list[str] = []
    for p_ in ps:
        n = len(re.sub(r"<[^>]+>", "", p_))
        if out and n < short and len(re.sub(r"<[^>]+>", "", out[-1])) < 260:
            out[-1] = out[-1] + " " + p_
        else:
            out.append(p_)
    return out


def page_poem(issue, ctx, sec) -> str:
    p = ctx["text"].get("poem") or {}
    if not p.get("stanzas"):
        return ""
    imgs = ctx["images"]
    title = p.get("title") or "무제"
    words = title.split()
    h1 = (esc(" ".join(words[:-1])) + f" <i>{esc(words[-1])}</i>") if len(words) > 1 else f"<i>{esc(title)}</i>"
    st = "".join("<div class='st'>" + "".join(f"<p>{esc(l)}</p>" for l in s) + "</div>" for s in p["stanzas"][:3])
    last = p["stanzas"][-1][-3:] if p["stanzas"] else []
    vert = "<br>".join(esc(l) for l in last)
    return f"""<section class="page" id="pg-poem">{mast('poem', issue)}{strip('G — Poem', sec['slot'])}
<div class="g-head"><div><div class="label">{esc(sec['slot'])}</div><h1>{h1}</h1>{why_line(sec)}</div>
<div class="meta">재료 · 혼잣말 기록 {p.get('sources', 0)}건<br>형식 · {'기록 문장을 그대로 옮긴 발견 시' if p.get('offline') else '기록의 낱말로 지은 시'}<br>{esc(issue['period_ko'])}</div></div>
<div class="g-main"><div class="img" style="{_img_style(imgs.pick(9))}"></div><div class="lines">{st}</div></div>
<div class="g-green"><div class="img" style="{_img_style(imgs.pick(10))}"></div><div class="v">{vert}</div></div>
<div class="g-epi">“{esc(p.get('epigraph'))}”</div>
{foot(issue, sec['slot'], [('재료', '이번 주 게시물·스토리·밤에 쓴 글'), ('규칙', '숫자·날짜가 든 행은 싣지 않습니다'), ('형식', 'Poem')])}</section>"""


PAGES = {"article": page_article, "documentary": page_documentary, "fashion": page_fashion,
         "digital_preview": page_digital, "data_portrait": page_data, "novel": page_novel, "poem": page_poem}


def issue_html(issue: dict[str, Any], ctx: dict[str, Any]) -> str:
    pages = [page_cover(issue, ctx)]
    for sec in issue["sections"]:
        if sec["included"] and sec["template"] in PAGES:
            pages.append(PAGES[sec["template"]](issue, ctx, sec))
    data = json.dumps({"title": issue["title"], "pages": [s["template"] for s in issue["sections"] if s["included"]]}, ensure_ascii=False)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VirtualME · {esc(issue['title'])}</title><style>{CSS}</style></head>
<body><main class="issue">{''.join(p for p in pages if p)}</main>
<script>window.__issue={data};
// 휴대폰처럼 A4 폭(210mm)보다 좁은 화면에서는 쪽을 화면 폭에 맞춰 줄인다. 인쇄·PDF 는 그대로
function fit(){{const z=Math.min(1,(innerWidth-12)/794);document.querySelectorAll('.page').forEach(p=>{{p.style.zoom=z<1?z:''}});document.querySelector('.issue').style.gap=z<1?'12px':''}}
if(!matchMedia('print').matches){{fit();addEventListener('resize',fit)}}
document.fonts.ready.then(()=>{{window.__wzDone=true}})</script></body></html>"""
