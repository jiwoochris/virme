"""기록 가져오기를 한곳에서: CLI(`virtueme ingest`)와 웹 화면(`/import`)이 같은 함수를 쓴다.

웹 화면에서 올린 파일은 `$VIRTUEME_DATA/uploads/<프로필>/<시각>/` 에 두고 거기서 가져온다. 사진 기록은 원본 파일 경로를
가리키므로 이 폴더를 지우면 사진 임베딩을 다시 만들 수 없다. 모든 파일은 이 컴퓨터 안에만 있다.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from .privacy import Pseudonymizer
from .store import Store

SOURCES = ("kakao", "sms", "instagram", "photos")


def safe_extract(zip_path: Path, dest: Path) -> Path:
    """압축을 풀되 폴더 밖으로 나가는 경로(../)는 막는다. 풀린 폴더를 돌려준다."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(root)):
                raise ValueError(f"압축 파일 안에 폴더 밖을 가리키는 경로가 있습니다: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
    # 압축 안에 폴더가 하나만 있으면 그 폴더가 실제 내보내기 폴더다
    items = [p for p in dest.iterdir() if not p.name.startswith(("__MACOSX", "."))]
    return items[0] if len(items) == 1 and items[0].is_dir() else dest


def ingest(store: Store, source: str, paths: list[Path], me: list[str] | None = None) -> dict[str, Any]:
    """가져온 결과 요약. 문장(lines)은 CLI 와 웹 화면에 그대로 보여 준다."""
    pseudo = Pseudonymizer(store)
    lines: list[str] = []
    total = 0
    if source == "kakao":
        from .ingest import kakao

        for p in paths:
            parsed = kakao.parse_kakao_file(p)
            if not me:
                listing = ", ".join(f"{n}({c})" for n, c in parsed.senders.most_common(10))
                raise ValueError(f"본인 이름을 알려 주세요. 보낸 사람: {listing}")
            recs, info = kakao.to_records(parsed, me, pseudo)
            store.upsert_records(recs)
            store.log_import("kakao", str(p), len(recs), json.dumps(info, ensure_ascii=False))
            total += len(recs)
            lines.append(f"카카오톡 {p.name}: 형식 {info['format']}, 메시지 {info['messages']}건 → 발화 {info['turns']}건"
                         f" (참여자 {', '.join(info['participants'])})")
            if info["warnings"]:
                lines.append(f"  경고 {len(info['warnings'])}건: {info['warnings'][0]}")
    elif source == "sms":
        from .ingest import sms

        for p in paths:
            recs, info = sms.to_records(sms.parse_file(p), pseudo)
            store.upsert_records(recs)
            store.log_import("sms", str(p), len(recs), json.dumps(info, ensure_ascii=False))
            total += len(recs)
            skipped = ", ".join(f"{k} {v}" for k, v in info["skipped"].items()) or "없음"
            lines.append(f"문자 {p.name}: 메시지 {info['messages']}건 → 발화 {info['turns']}건, 대화 {info['threads']}개"
                         f" (자동 발송 등 뺀 것: {skipped})")
    elif source == "instagram":
        from .ingest import instagram

        recs, info = instagram.import_instagram(paths[0], pseudo, me=(me or [None])[0])
        store.upsert_records(recs)
        store.log_import("instagram", str(paths[0]), len(recs), json.dumps(info, ensure_ascii=False))
        total += len(recs)
        lines.append(f"인스타그램: 게시물 {info['posts']}, 스토리 {info['stories']}, 릴스 {info['reels']}, 댓글 {info['comments']},"
                     f" DM 발화 {info['dm_messages']} (사진 포함 {info['with_media']}, GPS {info['with_gps']})")
    elif source == "photos":
        from .ingest import photos

        recs, info = photos.import_photos(paths[0])
        store.upsert_records(recs)
        store.log_import("photo", str(paths[0]), len(recs), json.dumps(info, ensure_ascii=False))
        total += len(recs)
        lines.append(f"사진: {info['records']}장 (EXIF 시각 {info['time_source']['exif']}, 파일 시각 {info['time_source']['mtime']},"
                     f" GPS {info['with_gps']}, 건너뜀 {info['skipped']})")
    else:
        raise ValueError(f"모르는 출처: {source}")
    return {"source": source, "records": total, "lines": lines, "total_records": store.count()}
