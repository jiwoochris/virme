"""인스타그램 "내 정보 다운로드"(JSON 형식) 가져오기.

내보내기 폴더 구조는 시기마다 조금씩 다르다. 파일 이름 패턴으로 찾는다.
- 게시물: `**/content/posts_*.json`
- 스토리: `**/content/stories.json` (`ig_stories`)
- 릴스: `**/content/reels.json` (`ig_reels_media`)
- DM: `**/messages/inbox/*/message_*.json`
- 댓글: `**/comments/post_comments_*.json`
- 계정 이름: `**/personal_information/personal_information.json`

인스타그램 JSON 은 한글을 UTF-8 바이트를 latin-1 로 읽은 모양(`\\u00ec\\u0095…`)으로 적는다.
`fix_text` 가 이것을 되돌린다.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import DEFAULT_TZ
from ..privacy import Masker, Pseudonymizer
from ..records import Record, from_epoch, iso, record_id


def fix_text(s: Any) -> Any:
    if not isinstance(s, str):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _media_path(root: Path, uri: str | None) -> str | None:
    if not uri or uri.startswith("http"):
        return None
    p = (root / uri).resolve()
    return str(p) if p.exists() else None


def _gps(media: dict) -> tuple[float | None, float | None]:
    meta = media.get("media_metadata") or {}
    for key in ("photo_metadata", "video_metadata"):
        for exif in (meta.get(key) or {}).get("exif_data", []) or []:
            lat, lon = exif.get("latitude"), exif.get("longitude")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and (lat or lon):
                return float(lat), float(lon)
    return None, None


def find_owner(root: Path) -> str | None:
    for p in root.rglob("personal_information.json"):
        try:
            data = _load(p)
            user = (data.get("profile_user") or [{}])[0].get("string_map_data", {})
            for key in ("Name", "이름", "Username", "사용자 이름"):
                val = (user.get(key) or {}).get("value")
                if val:
                    return fix_text(val)
        except (OSError, ValueError, AttributeError, IndexError):
            continue
    return None


def _post_records(root: Path, tz: str, masker: Masker) -> list[Record]:
    out: list[Record] = []
    for p in sorted(root.rglob("posts_*.json")):
        if "content" not in p.parts:
            continue
        for post in _load(p):
            media = post.get("media") or []
            ts = post.get("creation_timestamp") or (media[0].get("creation_timestamp") if media else None)
            if not ts:
                continue
            caption = fix_text(post.get("title") or (media[0].get("title") if media else "") or "").strip()
            files = [f for f in (_media_path(root, m.get("uri")) for m in media) if f]
            lat, lon = _gps(media[0]) if media else (None, None)
            dt = from_epoch(ts, tz)
            modality = "text+image" if caption and files else ("image" if files else "text")
            if modality == "text" and not caption:
                continue
            out.append(
                Record(
                    id=record_id("instagram", "post", ts, caption, len(media)),
                    ts=iso(dt), source="instagram", kind="post", modality=modality,
                    text=masker.mask(caption) or None, media=files, author="나", is_self=True,
                    lat=lat, lon=lon, meta={"media_count": len(media)},
                )
            )
    return out


def _story_like(root: Path, filename: str, key: str, kind: str, tz: str, masker: Masker) -> list[Record]:
    out: list[Record] = []
    for p in sorted(root.rglob(filename)):
        data = _load(p)
        items = data.get(key, []) if isinstance(data, dict) else data
        for item in items:
            medias = item.get("media") if isinstance(item.get("media"), list) else [item]
            for m in medias:
                ts = m.get("creation_timestamp")
                if not ts:
                    continue
                caption = fix_text(m.get("title") or "").strip()
                file = _media_path(root, m.get("uri"))
                lat, lon = _gps(m)
                if not file and not caption:
                    continue
                modality = "text+image" if caption and file else ("image" if file else "text")
                out.append(
                    Record(
                        id=record_id("instagram", kind, ts, m.get("uri"), caption),
                        ts=iso(from_epoch(ts, tz)), source="instagram", kind=kind, modality=modality,
                        text=masker.mask(caption) or None, media=[file] if file else [], author="나",
                        is_self=True, lat=lat, lon=lon,
                    )
                )
    return out


def _comment_records(root: Path, tz: str, masker: Masker) -> list[Record]:
    out: list[Record] = []
    for p in sorted(root.rglob("post_comments_*.json")):
        data = _load(p)
        items = data if isinstance(data, list) else data.get("comments_media_comments", [])
        for item in items:
            smd = item.get("string_map_data", {})
            text = fix_text((smd.get("Comment") or smd.get("댓글") or {}).get("value") or "").strip()
            ts = (smd.get("Time") or smd.get("시간") or {}).get("timestamp")
            if not text or not ts:
                continue
            out.append(
                Record(
                    id=record_id("instagram", "comment", ts, text),
                    ts=iso(from_epoch(ts, tz)), source="instagram", kind="comment", modality="text",
                    text=masker.mask(text), author="나", is_self=True,
                )
            )
    return out


def _dm_records(root: Path, owner: str | None, pseudo: Pseudonymizer, tz: str) -> tuple[list[Record], Counter[str]]:
    threads = sorted(root.rglob("message_*.json"))
    senders: Counter[str] = Counter()
    raw: list[tuple[Path, dict]] = []
    for p in threads:
        if "inbox" not in p.parts:
            continue
        data = _load(p)
        raw.append((p, data))
        for msg in data.get("messages", []):
            senders[fix_text(msg.get("sender_name", ""))] += 1
    if not raw:
        return [], senders
    if not owner:
        raise ValueError(
            "인스타그램 DM 이 있지만 본인 이름을 알 수 없습니다. --me 로 지정하세요. "
            f"보낸 사람: {', '.join(n for n, _ in senders.most_common(8))}"
        )
    for name in senders:
        pseudo.alias(name, is_self=(name == owner))
    masker = Masker(pseudo.table)
    out: list[Record] = []
    for p, data in raw:
        title = fix_text(data.get("title") or p.parent.name)
        thread = "ig:" + record_id("ig-thread", p.parent.name)[1:9]
        parts = sorted({pseudo.table.get(fix_text(x.get("name", "")), "지인") for x in data.get("participants", [])})
        msgs = []
        for msg in data.get("messages", []):
            text = fix_text(msg.get("content") or "").strip()
            ts_ms = msg.get("timestamp_ms")
            if not text or not ts_ms or msg.get("is_unsent"):
                continue
            msgs.append((ts_ms, fix_text(msg.get("sender_name", "")), text))
        msgs.sort()  # 내보내기 파일은 최신 메시지가 먼저 온다
        # 같은 사람이 3분 안에 연달아 보낸 메시지는 카카오톡과 같은 규칙으로 한 발화로 합친다
        turns: list[list[tuple[int, str, str]]] = []
        for m in msgs:
            if turns and turns[-1][-1][1] == m[1] and m[0] - turns[-1][-1][0] <= 180_000:
                turns[-1].append(m)
            else:
                turns.append([m])
        for turn in turns:
            ts_ms, sender, _ = turn[0]
            body = "\n".join(t for _, _, t in turn)
            out.append(
                Record(
                    id=record_id("instagram", "dm", p.parent.name, ts_ms, sender, body),
                    ts=iso(from_epoch(ts_ms / 1000, tz)), source="instagram", kind="message", modality="text",
                    text=masker.mask(body), author=pseudo.table.get(sender, "지인"), is_self=(sender == owner),
                    thread=thread, participants=parts,
                    meta={"room": masker.mask(title), "messages": len(turn),
                          "end_ts": iso(from_epoch(turn[-1][0] / 1000, tz))},
                )
            )
    return out, senders


def import_instagram(root: Path, pseudo: Pseudonymizer, me: str | None = None, tz: str = DEFAULT_TZ) -> tuple[list[Record], dict]:
    root = root.resolve()
    owner = me or find_owner(root)
    if owner:
        pseudo.alias(owner, is_self=True)
    masker = Masker(pseudo.table)
    posts = _post_records(root, tz, masker)
    stories = _story_like(root, "stories.json", "ig_stories", "story", tz, masker)
    reels = _story_like(root, "reels.json", "ig_reels_media", "reel", tz, masker)
    comments = _comment_records(root, tz, masker)
    dms, senders = _dm_records(root, owner, pseudo, tz)
    records = posts + stories + reels + comments + dms
    info = {
        "owner_found": bool(owner),
        "posts": len(posts), "stories": len(stories), "reels": len(reels),
        "comments": len(comments), "dm_messages": len(dms),
        "with_media": sum(1 for r in records if r.media),
        "with_gps": sum(1 for r in records if r.lat is not None),
    }
    return records, info
