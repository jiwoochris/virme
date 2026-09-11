"""사진 폴더 가져오기. 촬영 시각과 GPS 는 EXIF 에서 읽는다.

EXIF 촬영 시각이 없으면 파일 수정 시각을 쓰고 `meta.time_source="mtime"` 으로 표시한다.
수정 시각은 복사·백업 과정에서 바뀌므로 정서 곡선과 변화점 계산에서 신뢰도가 낮다.
사진 파일 자체는 밖으로 나가지 않는다. 사진의 의미는 SigLIP 2 로 로컬에서 계산한다.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from ..config import DEFAULT_TZ
from ..records import Record, iso, record_id, to_aware

try:  # HEIC (아이폰 기본 형식)
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF = True
except ImportError:  # pragma: no cover
    HEIF = False

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
EXIF_IFD = 0x8769
GPS_IFD = 0x8825
DATETIME_ORIGINAL = 0x9003
OFFSET_ORIGINAL = 0x9011
DATETIME = 0x0132


def _ratio(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        num, den = v
        return num / den if den else 0.0


def _dms(values, ref: str | None) -> float | None:
    try:
        d, m, s = (_ratio(x) for x in values)
    except (TypeError, ValueError):
        return None
    deg = d + m / 60 + s / 3600
    if ref in ("S", "W"):
        deg = -deg
    return round(deg, 6)


def read_exif(path: Path) -> dict:
    out: dict = {}
    with Image.open(path) as img:
        exif = img.getexif()
        out["size"] = img.size
    if not exif:
        return out
    sub = exif.get_ifd(EXIF_IFD)
    raw = sub.get(DATETIME_ORIGINAL) or exif.get(DATETIME)
    if raw:
        try:
            dt = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")
            offset = sub.get(OFFSET_ORIGINAL)
            if offset and len(str(offset)) >= 6:
                sign = 1 if str(offset)[0] == "+" else -1
                hh, mm = str(offset)[1:].split(":")[:2]
                dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(hh), minutes=int(mm))))
            out["dt"] = dt
        except ValueError:
            pass
    gps = exif.get_ifd(GPS_IFD)
    if gps:
        lat = _dms(gps.get(2), gps.get(1))
        lon = _dms(gps.get(4), gps.get(3))
        if lat is not None and lon is not None and (lat or lon):
            out["lat"], out["lon"] = lat, lon
    out["camera"] = " ".join(str(exif.get(t, "")).strip() for t in (0x010F, 0x0110)).strip() or None
    return out


def import_photos(root: Path, tz: str = DEFAULT_TZ) -> tuple[list[Record], dict]:
    root = root.resolve()
    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in EXTS and not p.name.startswith("."))
    records: list[Record] = []
    seen: set[str] = set()
    skipped = {"duplicate": 0, "unreadable": 0, "heic_unsupported": 0}
    by_time = {"exif": 0, "mtime": 0}
    for p in files:
        if p.suffix.lower() in (".heic", ".heif") and not HEIF:
            skipped["heic_unsupported"] += 1
            continue
        digest = hashlib.sha1(p.read_bytes()).hexdigest()
        if digest in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(digest)
        try:
            ex = read_exif(p)
        except Exception:  # 손상된 파일
            skipped["unreadable"] += 1
            continue
        if "dt" in ex:
            dt, source = to_aware(ex["dt"], tz), "exif"
        else:
            dt, source = to_aware(datetime.fromtimestamp(p.stat().st_mtime), tz), "mtime"
        by_time[source] += 1
        records.append(
            Record(
                id=record_id("photo", digest),
                ts=iso(dt), source="photo", kind="photo", modality="image",
                media=[str(p)], author="나", is_self=True,
                lat=ex.get("lat"), lon=ex.get("lon"),
                meta={"time_source": source, "camera": ex.get("camera"), "size": ex.get("size"),
                      "file": p.name},
            )
        )
    info = {"files": len(files), "records": len(records), "time_source": by_time, "skipped": skipped,
            "with_gps": sum(1 for r in records if r.lat is not None)}
    return records, info
