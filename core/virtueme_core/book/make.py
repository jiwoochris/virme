"""브리프 → 그림 → 책 HTML → PNG·PDF 를 한 번에. 결과는 `$VIRTUEME_DATA/books/<프로필>/<책 번호>/`.

폴더 안: brief.json (디자인 값·고른 참고 표지·그림 프롬프트·그림 출처·비용·렌더 보고), cover_art.png, illustration.png,
book.html, cover.png, opener.png, spread.png, book.pdf
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from ..config import DATA_DIR
from . import art as artmod
from .briefing import brief as make_brief
from .briefing import parse_story
from .render import build_html, run_browser


def books_dir(profile: str) -> Path:
    safe = "".join(ch for ch in profile if ch.isalnum() or ch in "-_") or "default"
    return DATA_DIR / "books" / safe


def make_book(genre: int | str | None, story_text: str, title: str | None = None, author: str = "나",
              narrative_object: dict[str, Any] | None = None, profile: str = "default", offline: bool = False,
              refs: list[dict[str, Any]] | None = None, grammar: dict[str, Any] | None = None,
              out_root: Path | None = None, fresh: bool = False, overrides: dict[str, str] | None = None,
              log: Callable[[str], None] = print) -> dict[str, Any]:
    """책 한 권. story_text 는 /story 출력(첫 줄 TITLE:) 또는 단락 글. 반환: brief.json 내용 + 파일 경로."""
    t0 = time.time()
    story_title, paras = parse_story(story_text)
    title = title or story_title or (narrative_object or {}).get("keyword") or "제목 없음"
    if not paras:
        raise ValueError("이야기 본문이 비어 있습니다")
    b = make_brief(genre, narrative_object, story_text, refs=refs, grammar=grammar, title=title,
                   overrides=overrides)
    out = (out_root or books_dir(profile)) / b["brief_id"]
    out.mkdir(parents=True, exist_ok=True)
    log(f"브리프 {b['brief_id']}: {b['genre_name']} · {b['archetype']} · 제목 {b['title_position']}/{b['title_scale']} · "
        f"{b['typeface']} · {b['art_style']} · 분위기 {', '.join(b['moods'])}")
    art_report = artmod.make_art(b, out, profile=profile, offline=offline, fresh=fresh, log=log)
    log(f"그림: 표지 {art_report['cover']}, 삽화 {art_report['illustration']}, ${art_report['cost_usd']:.3f}")
    cover_art = out / "cover_art.png" if (out / "cover_art.png").exists() else None
    illus = out / "illustration.png" if (out / "illustration.png").exists() else None
    doc, tt = build_html(b, title, author, paras, cover_art, illus)
    (out / "book.html").write_text(doc, "utf-8")
    report = run_browser(out / "book.html", out)
    bad_fonts = [f for f in report.get("fonts", []) if f["status"] == "error"]
    if bad_fonts:
        log(f"  주의: 불러오지 못한 서체 {sorted({f['family'] for f in bad_fonts})} (대체 서체로 렌더)")
    if report.get("overflow"):
        log(f"  주의: 넘친 쪽 {report['overflow']}")
    result = {
        **b, "title": title, "author": author, "profile": profile,
        "art": art_report, "title_treatment": tt, "render": report,
        "files": {k: str(out / k) for k in ("cover.png", "opener.png", "spread.png", "book.pdf", "book.html",
                                            "cover_art.png", "illustration.png") if (out / k).exists()},
        "seconds": round(time.time() - t0, 1),
    }
    (out / "brief.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), "utf-8")
    log(f"→ {out} ({report.get('pages')}쪽, 펼침면 {report.get('spread_pages')}, {result['seconds']}초)")
    return result
