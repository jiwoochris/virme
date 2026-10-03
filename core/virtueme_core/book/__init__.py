"""책 디자인: 참고 표지 사례에서 장르별 디자인 문법을 세고, 그 문법으로 표지·본문 레이아웃을 만든다.

`collect` → `annotate` → `grammar` 는 참고 자료를 만드는 단계(한 번), `brief` → `make_book` 은 책마다 도는 단계다.
CLI 는 `python -m virtueme_core.book`. 자세한 설명은 같은 폴더의 README.md.
"""
from .briefing import brief, parse_story, retrieve, target_moods
from .grammar import build_grammar, load_grammar
from .make import books_dir, make_book

__all__ = ["brief", "parse_story", "retrieve", "target_moods", "build_grammar", "load_grammar", "make_book",
           "books_dir"]
