"""브리프 → 그림 → 소개글 → 책 HTML → PNG·PDF 를 한 번에. 결과는 `$VIRTUEME_DATA/books/<프로필>/<책 번호>/`.

폴더 안: brief.json (디자인 값과 뽑은 확률·고른 참고 표지·장 나누기·그림 프롬프트·그림 출처·비용·렌더 보고),
cover_art.png, illustration-<장>.png, blurb.json, book.html, cover.png, title.png, toc.png, opener.png, opener-2.png,
spread.png, colophon.png, back.png, book.pdf
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ..config import DATA_DIR, openrouter_key
from . import art as artmod
from .briefing import DEFAULT_TEMPERATURE, parse_story
from .briefing import brief as make_brief
from .chapters import make_blurb
from .render import SHOTS, build_html, run_browser

PNG_NAMES = [name for _, name in SHOTS]


def books_dir(profile: str) -> Path:
    safe = "".join(ch for ch in profile if ch.isalnum() or ch in "-_") or "default"
    return DATA_DIR / "books" / safe


def make_book(genre: int | str | None, story_text: str, title: str | None = None, author: str = "나",
              narrative_object: dict[str, Any] | None = None, profile: str = "default", offline: bool = False,
              refs: list[dict[str, Any]] | None = None, grammar: dict[str, Any] | None = None,
              out_root: Path | None = None, fresh: bool = False, overrides: dict[str, str] | None = None,
              temperature: float = DEFAULT_TEMPERATURE, variant: int = 0, bleed_mm: float = 0.0,
              made: date | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """책 한 권. story_text 는 /story 출력(첫 줄 TITLE:) 또는 단락 글. 반환: brief.json 내용 + 파일 경로."""
    t0 = time.time()
    story_title, paras = parse_story(story_text)
    title = title or story_title or (narrative_object or {}).get("keyword") or "제목 없음"
    if not paras:
        raise ValueError("이야기 본문이 비어 있습니다")
    b = make_brief(genre, narrative_object, story_text, refs=refs, grammar=grammar, title=title,
                   overrides=overrides, temperature=temperature, variant=variant)
    out = (out_root or books_dir(profile)) / b["brief_id"]
    out.mkdir(parents=True, exist_ok=True)
    p_arch = (b["decision"]["archetype"].get("probabilities") or {}).get(b["archetype"])
    log(f"브리프 {b['brief_id']}: {b['genre_name']} · {b['archetype']}"
        + (f" (p={p_arch:.2f})" if p_arch is not None else "") + f" · 제목 {b['title_position']}/"
        f"{b['title_scale']} · {b['typeface']} · {b['art_style']} · 분위기 {', '.join(b['moods'])} · {len(b['chapters'])}장")
    t_art = time.time()
    art_report = artmod.make_art(b, out, profile=profile, offline=offline, fresh=fresh, log=log)
    art_report["seconds"] = round(time.time() - t_art, 1)
    log(f"그림: 표지 {art_report['cover']}, 삽화 {art_report['illustrations']}, ${art_report['cost_usd']:.3f}, "
        f"{art_report['seconds']}초")
    chapters = [(c["start"], c["end"]) for c in b["chapters"]]
    blurb = make_blurb(title, b["genre_name"], paras, chapters, out, profile,
                       use_llm=not offline and openrouter_key() is not None, log=log)
    cover_art = out / "cover_art.png" if (out / "cover_art.png").exists() else None
    illus = {idx: out / f"{name}.png" for idx, name, _ in artmod.illustration_names(b) if (out / f"{name}.png").exists()}
    made = made or date.today()
    made_s = f"{made.year}년 {made.month}월 {made.day}일"
    doc, tt = build_html(b, title, author, paras, cover_art, illus, chapter_titles=blurb["chapter_titles"],
                         blurb=blurb["blurb"], made=made_s, art_report=art_report, bleed_mm=bleed_mm)
    (out / "book.html").write_text(doc, "utf-8")
    t_r = time.time()
    report = run_browser(out / "book.html", out)
    report["seconds"] = round(time.time() - t_r, 1)
    bad_fonts = [f for f in report.get("fonts", []) if f["status"] == "error"]
    if bad_fonts:
        log(f"  주의: 불러오지 못한 서체 {sorted({f['family'] for f in bad_fonts})} (대체 서체로 렌더)")
    if report.get("overflow"):
        log(f"  주의: 넘친 쪽 {report['overflow']}")
    files = [*PNG_NAMES, "book.pdf", "book.html", "cover_art.png", "blurb.json",
             *[f"{name}.png" for _, name, _ in artmod.illustration_names(b)]]
    result = {
        **b, "title": title, "author": author, "profile": profile, "made": made.isoformat(), "bleed_mm": bleed_mm,
        "chapter_titles": blurb["chapter_titles"], "blurb": {k: blurb.get(k) for k in ("blurb", "source", "model",
                                                                                        "cost_usd")},
        "art": art_report, "title_treatment": tt, "render": report,
        "cost_usd": round(art_report["cost_usd"] + float(blurb.get("cost_usd") or 0), 4),
        "files": {k: str(out / k) for k in files if (out / k).exists()},
        "seconds": round(time.time() - t0, 1),
    }
    (out / "brief.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf-8")
    log(f"→ {out} (속 {report.get('pages')}쪽, {len(b['chapters'])}장, 펼침면 {report.get('spread_pages')}, "
        f"${result['cost_usd']:.3f}, {result['seconds']}초)")
    return result
