"""uv run python -m virtueme_core.book <collect|annotate|grammar|brief|render> ...

예)
  uv run python -m virtueme_core.book collect --per-genre 30
  uv run python -m virtueme_core.book annotate            # 키가 없으면 측정값만
  uv run python -m virtueme_core.book grammar
  uv run python -m virtueme_core.book brief --genre 미스터리 --narrative query.json --story story.txt
  uv run python -m virtueme_core.book render --genre 미스터리 --story story.txt --author 윤서연 -p seoyeon
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _read_story(path: str | None) -> str | None:
    if not path:
        return None
    return sys.stdin.read() if path == "-" else Path(path).read_text("utf-8")


def _read_narrative(path: str | None) -> dict[str, Any] | None:
    """서사 오브젝트 JSON, 또는 /query 응답 전체({narrative_object: ...})."""
    if not path:
        return None
    path, _, key = path.partition("#")  # 예시 묶음: samples/seoyeon_narratives.json#콩이
    data = json.loads(Path(path).read_text("utf-8"))
    if key:
        data = data["items"][key]
    return data.get("narrative_object", data)


def _overrides(items: list[str]) -> dict[str, str]:
    return dict(i.split("=", 1) for i in items if "=" in i)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m virtueme_core.book", description="참고 표지로 배운 장르 문법으로 책 표지·본문 만들기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="Open Library 에서 참고 표지 받기 ($VIRTUEME_DATA/book_refs)")
    c.add_argument("--per-genre", type=int, default=30)
    c.add_argument("--genres", nargs="*", help="mystery romance sf fantasy thriller comedy literary")
    c.add_argument("--korean-share", type=float, default=0.5)
    a = sub.add_parser("annotate", help="측정값 + 비전 모델 라벨 → references.json")
    a.add_argument("--no-llm", action="store_true", help="측정값만 (라벨 없음)")
    a.add_argument("--limit", type=int)
    sub.add_parser("grammar", help="장르별 분포 → grammar.json, 표 출력")
    for name in ("brief", "render"):
        p = sub.add_parser(name, help="디자인 브리프" if name == "brief" else "브리프 + 그림 + PNG·PDF")
        p.add_argument("--genre", default=None, help="번호(0~6)·한국어 이름·영문 키")
        p.add_argument("--narrative", help="서사 오브젝트 JSON 또는 /query 응답. 묶음 파일은 경로#키")
        p.add_argument("--story", help="이야기 파일 (/story 출력: 첫 줄 TITLE:). - 이면 표준 입력")
        p.add_argument("--title")
        p.add_argument("-k", type=int, default=6, help="참고 표지 수")
        p.add_argument("--set", action="append", default=[], metavar="칸=값",
                       help="브리프 값을 사람이 고른 값으로 (예: --set archetype=framed --set typeface=serif)")
        if name == "render":
            p.add_argument("--author", default="나")
            p.add_argument("-p", "--profile", default="default")
            p.add_argument("--offline", action="store_true", help="그림을 절차적 그림으로 (네트워크 없음)")
            p.add_argument("--fresh", action="store_true", help="같은 프롬프트로 그린 그림이 있어도 새로 그림")
            p.add_argument("--copy-to", help="cover/opener/spread PNG 를 이 경로 접두어로 복사 (예: ../shots/stage6-book-mystery)")
    args = ap.parse_args(argv)

    if args.cmd == "collect":
        from .collect import collect

        corpus = collect(args.per_genre, args.genres, args.korean_share)
        print(f"표지 {len(corpus['items'])}장")
    elif args.cmd == "annotate":
        from .annotate import annotate

        annotate(use_llm=not args.no_llm, limit=args.limit)
    elif args.cmd == "grammar":
        from .grammar import build_grammar, table_markdown

        print(table_markdown(build_grammar()))
    elif args.cmd == "brief":
        from .briefing import brief

        b = brief(args.genre, _read_narrative(args.narrative), _read_story(args.story), k=args.k, title=args.title,
                  overrides=_overrides(args.set))
        print(json.dumps(b, ensure_ascii=False, indent=1))
    elif args.cmd == "render":
        import shutil

        from .make import make_book

        story = _read_story(args.story)
        if not story:
            ap.error("--story 가 필요합니다")
        res = make_book(args.genre, story, title=args.title, author=args.author,
                        narrative_object=_read_narrative(args.narrative), profile=args.profile, offline=args.offline,
                        fresh=args.fresh, overrides=_overrides(args.set))
        if args.copy_to:
            for name in ("cover", "opener", "spread"):
                src = res["files"].get(f"{name}.png")
                if src:
                    dst = Path(f"{args.copy_to}-{name}.png")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(src, dst)
                    print(dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
