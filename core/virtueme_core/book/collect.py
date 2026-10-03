"""참고 표지 모으기: Open Library 검색 API + 표지 API.

장르마다 주제어로 검색해 한국어판(language:kor) 표지를 먼저 채우고, 모자라면 원서 표지로 채운다. 이미지는
`$VIRTUEME_DATA/book_refs/img/` 에만 받고 저장소에는 넣지 않는다. 출처(제목·저자·출판사·연도·URL)는
`$VIRTUEME_DATA/book_refs/corpus.json` 에 남기고, `annotate` 단계가 측정값·라벨과 함께
`virtueme_core/book/references.json` 으로 옮긴다.

Open Library 표지는 이용자가 올린 것이라 표지마다 저작권자가 따로 있다. 여기서는 디자인 경향을 재는 데만 쓰고,
그림 생성에 넘기는 참고 이미지는 이 컴퓨터에서 줄인 사본이다.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import httpx
from PIL import Image

from ..config import DATA_DIR
from .vocab import GENRE_KEYS

SEARCH = "https://openlibrary.org/search.json"
COVER = "https://covers.openlibrary.org/b/id/{id}-L.jpg?default=false"
UA = {"User-Agent": "virtueme-book-research/0.1 (KAIST capstone; https://github.com/DECK6/virme)"}

# 장르 → Open Library 주제어. 앞의 것부터 채운다
SUBJECTS: dict[str, list[str]] = {
    "mystery": ["detective and mystery stories", "mystery"],
    "romance": ["love stories", "romance"],
    "sf": ["science fiction"],
    "fantasy": ["fantasy"],
    "thriller": ["thriller", "suspense"],
    "comedy": ["humorous fiction", "humor"],
    "literary": ["korean fiction", "essays", "literary fiction"],
}
HANGUL = re.compile(r"[가-힣]")


def refs_dir() -> Path:
    d = DATA_DIR / "book_refs"
    (d / "img").mkdir(parents=True, exist_ok=True)
    return d


def ref_image(ref_id: str) -> Path | None:
    """참고 표지 이미지 경로. $VIRTUEME_DATA 에 없으면 core/data/book_refs (기본 데이터 폴더)에서 찾는다."""
    from ..config import CORE_ROOT

    for root in (DATA_DIR / "book_refs", CORE_ROOT / "data" / "book_refs"):
        p = root / "img" / f"{ref_id}.jpg"
        if p.exists():
            return p
    return None


def _search(client: httpx.Client, q: str, korean: bool, limit: int, page: int = 1) -> list[dict[str, Any]]:
    fields = "key,title,author_name,publisher,first_publish_year,cover_i"
    if korean:
        fields += ",editions,editions.key,editions.title,editions.cover_i,editions.publisher,editions.publish_date,editions.language"
    params = {"q": (f'language:kor subject:"{q}"' if korean else f'subject:"{q}" first_publish_year:[1985 TO 2026]'),
              "fields": fields, "limit": str(limit), "page": str(page)}
    if not korean:
        params["sort"] = "readinglog"
    for attempt in range(3):
        try:
            res = client.get(SEARCH, params=params, timeout=60)
            res.raise_for_status()
            return res.json().get("docs", [])
        except httpx.HTTPError:
            time.sleep(3 * (attempt + 1))
    return []


def _year(s: str | None) -> int | None:
    m = re.search(r"(1[89]\d\d|20\d\d)", s or "")
    return int(m.group(1)) if m else None


def candidates(genre: str, per_genre: int, korean_share: float = 0.5,
               log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """한 장르의 후보 목록 (아직 이미지는 받지 않음). 한국어판을 먼저, 나머지는 원서."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    want_ko = int(per_genre * korean_share)
    with httpx.Client(headers=UA, follow_redirects=True) as client:
        for subject in SUBJECTS[genre]:
            for page in (1, 2):
                for d in _search(client, subject, True, 100, page):
                    ed = ((d.get("editions") or {}).get("docs") or [{}])[0]
                    cid = ed.get("cover_i")
                    if not cid or cid in seen or "kor" not in (ed.get("language") or []):
                        continue
                    seen.add(cid)
                    out.append({
                        "cover_id": cid, "genre": genre, "subject": subject, "lang": "ko",
                        "title": ed.get("title") or d.get("title"), "original_title": d.get("title"),
                        "author": ", ".join((d.get("author_name") or [])[:2]) or None,
                        "publisher": (ed.get("publisher") or [None])[0],
                        "year": _year((ed.get("publish_date") or [None])[0]),
                        "source_url": f"https://openlibrary.org{ed.get('key') or d['key']}",
                    })
                if sum(1 for c in out if c["lang"] == "ko") >= want_ko * 2:
                    break
        ko = [c for c in out if c["lang"] == "ko"]
        # 한글 제목이 실제로 찍힌 판을 먼저 (로마자 표기만 있는 판은 원서 표지를 재사용한 경우가 많다)
        ko.sort(key=lambda c: (not HANGUL.search(c["title"] or ""), -(c["year"] or 0)))
        out = ko[: want_ko * 2]
        for subject in SUBJECTS[genre]:
            for d in _search(client, subject, False, 100):
                cid = d.get("cover_i")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                out.append({
                    "cover_id": cid, "genre": genre, "subject": subject, "lang": "other",
                    "title": d.get("title"), "original_title": d.get("title"),
                    "author": ", ".join((d.get("author_name") or [])[:2]) or None,
                    "publisher": (d.get("publisher") or [None])[0], "year": d.get("first_publish_year"),
                    "source_url": f"https://openlibrary.org{d['key']}",
                })
    log(f"  {genre}: 후보 {len(out)} (한국어판 {sum(1 for c in out if c['lang'] == 'ko')})")
    return out


def _download(c: dict[str, Any], img_dir: Path) -> dict[str, Any] | None:
    path = img_dir / f"ol{c['cover_id']}.jpg"
    if not path.exists():
        try:
            res = httpx.get(COVER.format(id=c["cover_id"]), headers=UA, follow_redirects=True, timeout=60)
            if res.status_code != 200 or len(res.content) < 8000:
                return None
            path.write_bytes(res.content)
        except httpx.HTTPError:
            return None
    try:
        with Image.open(path) as im:
            w, h = im.size
    except OSError:
        path.unlink(missing_ok=True)
        return None
    # 표지 비율(세로가 1.2~1.75배)이 아니면 스캔 잘림·책등·상자 사진으로 보고 버린다
    if w < 180 or not (1.2 <= h / w <= 1.75):
        return None
    return {**c, "id": f"ol{c['cover_id']}", "file": f"img/{path.name}", "width": w, "height": h,
            "cover_url": COVER.format(id=c["cover_id"]).split("?")[0], "source": "Open Library"}


def collect(per_genre: int = 30, genres: list[str] | None = None, korean_share: float = 0.5,
            log: Callable[[str], None] = print) -> dict[str, Any]:
    """장르마다 per_genre 장까지 받아 corpus.json 에 쓴다. 이미 받은 파일은 다시 받지 않는다."""
    root = refs_dir()
    genres = genres or GENRE_KEYS
    corpus_path = root / "corpus.json"
    corpus: dict[str, Any] = json.loads(corpus_path.read_text("utf-8")) if corpus_path.exists() else {"items": []}
    keep = [it for it in corpus["items"] if it["genre"] not in genres]
    taken = {it["cover_id"] for it in keep}
    for g in genres:
        cands = [c for c in candidates(g, per_genre, korean_share, log) if c["cover_id"] not in taken]
        want_ko = int(per_genre * korean_share)
        got: list[dict[str, Any]] = []
        with ThreadPoolExecutor(6) as ex:
            for lang, want in (("ko", want_ko), ("other", per_genre)):
                pool = [c for c in cands if c["lang"] == lang]
                i = 0
                while len(got) < want and i < len(pool):
                    batch = pool[i:i + 12]
                    i += 12
                    for r in ex.map(lambda c: _download(c, root / "img"), batch):
                        if r and len(got) < want and r["cover_id"] not in taken:
                            got.append(r)
                            taken.add(r["cover_id"])
        log(f"  {g}: 받음 {len(got)} (한국어판 {sum(1 for r in got if r['lang'] == 'ko')})")
        keep.extend(got)
    corpus = {"source": "Open Library search + covers API", "collected": time.strftime("%Y-%m-%d"),
              "items": keep}
    corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=1), "utf-8")
    return corpus
