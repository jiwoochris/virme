"""기록 가져오기를 한곳에서: CLI(`virtueme ingest`)와 웹 화면(`/import`)이 같은 함수를 쓴다.

웹 화면에서 올린 파일은 `$VIRTUEME_DATA/uploads/<프로필>/<시각>/` 에 두고 거기서 가져온다. 사진 기록은 원본 파일 경로를
가리키므로 이 폴더를 지우면 사진 임베딩을 다시 만들 수 없다. 모든 파일은 이 컴퓨터 안에만 있다.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from .privacy import Masker, Pseudonymizer
from .store import Store

SOURCES = ("kakao", "sms", "instagram", "photos", "google", "maps", "card", "health", "spotify", "netflix")
# 흔적 기록 출처: 폴더 하나(또는 zip)를 통째로 받고, 기간(since·until)으로 거를 수 있다
TRACE_SOURCES = ("google", "maps", "card", "health", "spotify", "netflix")


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


def _out(info: dict[str, Any]) -> str:
    n = info.get("out_of_range")
    return f", 기간 밖 {n:,}건 뺌" if n else ""


def ingest(store: Store, source: str, paths: list[Path], me: list[str] | None = None,
           since: date | str | None = None, until: date | str | None = None, profile_name: str | None = None,
           ) -> dict[str, Any]:
    """가져온 결과 요약. 문장(lines)은 CLI 와 웹 화면에 그대로 보여 준다.

    since·until(YYYY-MM-DD, 그날 포함)은 흔적 기록 출처에만 쓴다. profile_name 은 넷플릭스 프로필 이름 거르기.
    """
    pseudo = Pseudonymizer(store)
    lines: list[str] = []
    total = 0
    if source in TRACE_SOURCES:
        from .ingest.traces import Window

        masker = Masker(pseudo.table)
        for p in paths:
            got, line = _ingest_trace(source, p, masker, Window.of(since, until), profile_name)
            for src, (recs, info) in got.items():
                store.upsert_records(recs)
                store.log_import(src, str(p), len(recs), json.dumps(info, ensure_ascii=False))
                total += len(recs)
            lines.append(line)
        return {"source": source, "records": total, "lines": lines, "total_records": store.count()}
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
                         f" (자동 발송 등 뺀 것: {skipped})"
                         + (f", 카드 승인 문자 → 결제 {info['payments']}건" if info.get("payments") else ""))
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


def _ingest_trace(source: str, path: Path, masker: Masker, window, profile_name: str | None = None,
                  ) -> tuple[dict[str, tuple[list, dict[str, Any]]], str]:
    """흔적 기록 한 경로 → ({출처: (레코드, 요약)}, 한 줄 요약)."""
    from .ingest import card, google, health, streaming

    if not Path(path).exists():
        raise ValueError(f"파일이나 폴더가 없습니다: {path}")
    if source == "google":
        recs, info = google.import_takeout(path, masker, window)
        if not recs:
            raise ValueError(f"{Path(path).name}: 유튜브 시청 기록·검색 기록·지도 타임라인 파일을 찾지 못했습니다 "
                             "(테이크아웃에서 JSON 형식으로 받았는지 확인해 주세요)")
        parts = []
        if "youtube" in info:
            i = info["youtube"]
            parts.append(f"유튜브 시청 {i['watched']:,}건 (광고 {i['ads']}·지운 영상 {i['removed']} 뺌{_out(i)})")
        if "search" in info:
            i = info["search"]
            parts.append(f"검색 {i['searches']:,}건 (검색 아닌 활동 {i['other']} 뺌{_out(i)})")
        if "maps" in info:
            i = info["maps"]
            parts.append(f"지도 방문 {i['visits']:,}건 (장소 이름 있음 {i['named']}{_out(i)})")
        return {k: (v, info[k]) for k, v in recs.items()}, "구글 테이크아웃: " + ", ".join(parts)
    if source == "maps":
        with google.opened(path) as base:
            recs, info = google.import_maps(base, masker, window)
        if not info["files"]:
            raise ValueError(f"{Path(path).name}: 타임라인 파일(Timeline.json, location-history.json, Semantic Location History)을 찾지 못했습니다")
        return {"maps": (recs, info)}, (f"지도 타임라인: 방문 {info['visits']:,}건 (장소 이름 있음 {info['named']}{_out(info)})")
    if source == "card":
        recs, info = card.import_statements(path, masker, window)
        if not info["files"] or (not recs and info["no_header"]):
            raise ValueError(f"{Path(path).name}: 이용일·가맹점·금액 열이 있는 카드 이용내역(xlsx·xls·csv)을 찾지 못했습니다")
        return {"card": (recs, info)}, (f"카드 이용내역: 파일 {info['files']}개, 결제 {info['payments']:,}건"
                                        f" (취소 {info['cancelled']}{_out(info)})")
    if source == "health":
        recs, info = health.import_health(path, window)
        if not info["apps"]:
            raise ValueError(f"{Path(path).name}: 삼성 헬스 CSV 나 애플 건강 export.xml 을 찾지 못했습니다")
        return {"health": (recs, info)}, (f"건강 ({', '.join(info['apps'])}): {info['days']}일 (걸음 {info['with_steps']}일,"
                                          f" 수면 {info['with_sleep']}일, 운동 {info['workouts']}회{_out(info)})")
    if source == "spotify":
        recs, info = streaming.import_spotify(path, masker, window)
        if not info["files"]:
            raise ValueError(f"{Path(path).name}: StreamingHistory·Streaming_History_Audio JSON 을 찾지 못했습니다")
        return {"spotify": (recs, info)}, (f"스포티파이: 재생 {info['plays']:,}곡 (30초 미만 {info['short']:,}·음악 아닌 것"
                                           f" {info['not_music']} 뺌{_out(info)})")
    if source == "netflix":
        recs, info = streaming.import_netflix(path, masker, window, profile_name)
        if not info["files"]:
            raise ValueError(f"{Path(path).name}: ViewingActivity.csv 를 찾지 못했습니다")
        prof = f", 프로필 {', '.join(info['profiles'])}" if len(info["profiles"]) > 1 and not profile_name else ""
        return {"netflix": (recs, info)}, (f"넷플릭스: 시청 {info['watched']:,}건 (예고편 {info['previews']}·1분 미만"
                                           f" {info['short']} 뺌{_out(info)}{prof})")
    raise ValueError(f"모르는 출처: {source}")
