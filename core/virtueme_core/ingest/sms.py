"""문자(SMS·MMS·iMessage) 가져오기.

지원 형식
- 안드로이드: "SMS Backup & Restore" 앱이 만드는 XML (`<smses>` 안의 `<sms>`, `<mms>`).
  sms 의 type 1 = 받은 문자, 2 = 보낸 문자. mms 의 msg_box 1 = 받은, 2 = 보낸. 임시 저장·실패 문자는 뺀다.
- 아이폰: 아이클라우드로 맥에 동기화된 메시지 DB(`~/Library/Messages/chat.db`)를 복사한 파일.
  맥에서 원본을 바로 읽으려면 터미널에 "전체 디스크 접근 권한"이 필요하므로, 복사본을 넘기는 것을 권한다.
  최근 macOS 는 본문을 `text` 대신 `attributedBody`(NSAttributedString 직렬화)에만 두는 일이 있어 둘 다 읽는다.

카카오톡과 같은 규칙을 쓴다: 같은 사람이 3분 안에 연달아 보낸 문자는 한 발화로 합치고, 대화 상대 이름·번호는 가명(지인A …)으로,
"엄마"처럼 관계로 저장된 연락처는 그대로 둔다. 본문 속 전화번호·계좌 등은 Masker 가 가린다. 인증번호·광고 같은
자동 발송 문자는 기록으로 만들지 않는다 (번호가 짧은 발신 전용 번호이거나 [Web발신]·(광고) 표시가 있는 것).
"""
from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import DEFAULT_TZ
from ..privacy import Masker, Pseudonymizer
from ..records import Record, from_epoch, iso, record_id

TURN_GAP = timedelta(minutes=3)
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
AUTOMATED = re.compile(r"^\s*\[(?:Web발신|국외발신|국제발신)\]|\(광고\)|인증번호|승인번호|\[?본인확인\]?|결제 승인|배송 조회")


@dataclass
class Sms:
    thread: str          # 상대(또는 단체 대화) 식별자. 가명으로 바꾸기 전 원래 값
    sender: str          # "나" 또는 상대 이름·번호
    dt: datetime
    text: str
    service: str         # SMS · MMS · iMessage


def _is_service_number(address: str) -> bool:
    """1588-xxxx, 짧은 발신 전용 번호처럼 사람이 아닌 번호."""
    digits = re.sub(r"\D", "", address or "")
    return bool(digits) and (len(digits) <= 8 or digits.startswith(("15", "16", "18")))


def _contact(name: str | None, address: str | None) -> str:
    name = (name or "").strip()
    if name and name not in ("(Unknown)", "null"):
        return name
    return (address or "알 수 없는 번호").strip()


def _norm(address: str | None) -> str:
    """번호 비교용: 숫자만 남기고 +82 는 0 으로."""
    d = re.sub(r"\D", "", address or "")
    return "0" + d[2:] if d.startswith("82") else d


def parse_android_xml(path: Path) -> list[Sms]:
    root = ET.parse(path).getroot()
    # 먼저 번호 → 연락처 이름을 모은다. 단체 문자(mms)는 보낸 사람이 번호로만 적혀 있어서, 같은 사람이 이름과 번호로
    # 두 번 잡히지 않게 이 표로 이름을 찾는다
    name_of: dict[str, str] = {}
    for el in root:
        names = [n.strip() for n in (el.get("contact_name") or "").split(",") if n.strip() and n.strip() != "(Unknown)"]
        addrs = [a for a in (el.get("address") or "").split("~") if a]
        if len(names) == len(addrs):
            for a, n in zip(addrs, names):
                name_of.setdefault(_norm(a), n)
    who_is = lambda addr: name_of.get(_norm(addr)) or (addr or "알 수 없는 번호").strip()  # noqa: E731

    out: list[Sms] = []
    for el in root:
        if el.tag == "sms":
            kind = el.get("type")
            if kind not in ("1", "2"):
                continue
            who = _contact(el.get("contact_name"), el.get("address", ""))
            if who == el.get("address", ""):
                who = who_is(who)
            out.append(Sms(thread=who, sender="나" if kind == "2" else who,
                           dt=from_epoch(int(el.get("date", "0")) / 1000), text=el.get("body") or "", service="SMS"))
        elif el.tag == "mms":
            box = el.get("msg_box")
            if box not in ("1", "2"):
                continue
            texts = [p.get("text") or "" for p in el.iter("part") if (p.get("ct") or "").startswith("text/plain")]
            # addr type 137 = 보낸 사람, 151 = 받는 사람. 단체 문자는 상대 여러 명을 합쳐 한 대화로 본다
            addrs = [a.get("address", "") for a in el.iter("addr") if a.get("address") not in (None, "", "insert-address-token")]
            sender_addr = next((a.get("address", "") for a in el.iter("addr") if a.get("type") == "137"), "")
            members = sorted({who_is(a) for a in (el.get("address") or "").split("~") if a} or {who_is(a) for a in addrs})
            thread = ", ".join(members) or who_is(sender_addr)
            out.append(Sms(thread=thread, sender="나" if box == "2" else who_is(sender_addr),
                           dt=from_epoch(int(el.get("date", "0")) / 1000), text="\n".join(t for t in texts if t),
                           service="MMS"))
    return out


def decode_attributed_body(blob: bytes | None) -> str | None:
    """NSAttributedString 직렬화(typedstream)에서 본문 글자만 꺼낸다. NSString 클래스 이름 뒤 '+' 다음이 길이와 UTF-8 본문이다."""
    if not blob:
        return None
    i = blob.find(b"NSString")
    if i < 0:
        return None
    j = blob.find(b"+", i)
    if j < 0 or j + 1 >= len(blob):
        return None
    j += 1
    n = blob[j]
    if n == 0x81:
        n, j = int.from_bytes(blob[j + 1 : j + 3], "little"), j + 3
    elif n == 0x82:
        n, j = int.from_bytes(blob[j + 1 : j + 5], "little"), j + 5
    else:
        j += 1
    text = blob[j : j + n].decode("utf-8", errors="ignore").strip()
    return text or None


def _apple_time(value: int) -> datetime:
    # macOS 10.13 부터는 2001-01-01 기준 나노초, 그 전에는 초
    seconds = value / 1e9 if value > 1e12 else float(value)
    return APPLE_EPOCH + timedelta(seconds=seconds)


def parse_mac_chat_db(path: Path) -> list[Sms]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    handles = {r["ROWID"]: r["id"] for r in con.execute("SELECT ROWID, id FROM handle")}
    chat_of: dict[int, int] = {}
    chat_name: dict[int, str] = {}
    chat_members: dict[int, list[str]] = defaultdict(list)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {"chat", "chat_message_join"} <= tables:
        for r in con.execute("SELECT chat_id, message_id FROM chat_message_join"):
            chat_of[r["message_id"]] = r["chat_id"]
        for r in con.execute("SELECT ROWID, display_name FROM chat"):
            if r["display_name"]:
                chat_name[r["ROWID"]] = r["display_name"]
        if "chat_handle_join" in tables:
            for r in con.execute("SELECT chat_id, handle_id FROM chat_handle_join"):
                chat_members[r["chat_id"]].append(handles.get(r["handle_id"], ""))
    out: list[Sms] = []
    for r in con.execute("SELECT ROWID, text, attributedBody, date, is_from_me, handle_id, service FROM message "
                         "ORDER BY date"):
        text = r["text"] or decode_attributed_body(r["attributedBody"])
        if not text or text == "\ufffc":  # 첨부만 있는 메시지
            continue
        chat = chat_of.get(r["ROWID"])
        members = sorted(m for m in chat_members.get(chat, []) if m)
        other = handles.get(r["handle_id"], "")
        thread = chat_name.get(chat) or (", ".join(members) if len(members) > 1 else (members[0] if members else other))
        out.append(Sms(thread=thread or "알 수 없는 대화", sender="나" if r["is_from_me"] else (other or thread),
                       dt=_apple_time(int(r["date"] or 0)).astimezone(ZoneInfo(DEFAULT_TZ)),
                       text=text.replace("\ufffc", "").strip(),
                       service=r["service"] or "SMS"))
    con.close()
    return out


def parse_file(path: Path) -> list[Sms]:
    head = path.read_bytes()[:64]
    if head.startswith(b"SQLite format 3"):
        return parse_mac_chat_db(path)
    return parse_android_xml(path)


def to_records(messages: list[Sms], pseudo: Pseudonymizer, tz: str = DEFAULT_TZ) -> tuple[list[Record], dict]:
    """문자를 발화 단위 레코드로. 대화 상대는 가명으로, 자동 발송 문자는 뺀다."""
    skipped: Counter[str] = Counter()
    kept: list[Sms] = []
    for m in messages:
        if m.sender != "나" and _is_service_number(m.sender):
            skipped["service_number"] += 1
        elif AUTOMATED.search(m.text):
            skipped["automated"] += 1
        else:
            kept.append(m)
    people = {m.sender for m in kept if m.sender != "나"} | {p.strip() for m in kept for p in m.thread.split(",")}
    pseudo.alias("나", is_self=True)
    for name in sorted(p for p in people if p and p != "나"):
        pseudo.alias(name)
    masker = Masker(pseudo.table)
    by_thread: dict[str, list[Sms]] = defaultdict(list)
    for m in kept:
        by_thread[m.thread].append(m)
    records: list[Record] = []
    for thread, msgs in by_thread.items():
        msgs.sort(key=lambda m: m.dt)
        members = sorted({pseudo.table.get(p.strip(), p.strip()) for p in thread.split(",") if p.strip()} | {"나"})
        room = ", ".join(x for x in members if x != "나") or "문자"
        tid = "sms:" + record_id("sms-thread", thread)[1:9]
        turn: list[Sms] = []

        def flush() -> None:
            if not turn:
                return
            body = "\n".join(t.text for t in turn if t.text)
            first = turn[0]
            dt = first.dt.astimezone(ZoneInfo(tz))
            records.append(Record(
                id=record_id("sms", thread, iso(dt), first.sender, body),
                ts=iso(dt), source="sms", kind="message", modality="text",
                text=masker.mask(body), author=pseudo.table.get(first.sender, "나"), is_self=first.sender == "나",
                thread=tid, participants=members,
                meta={"room": room, "messages": len(turn), "service": first.service,
                      "end_ts": iso(turn[-1].dt.astimezone(ZoneInfo(tz)))},
            ))
            turn.clear()

        for m in msgs:
            if turn and (m.sender != turn[-1].sender or m.dt - turn[-1].dt > TURN_GAP):
                flush()
            turn.append(m)
        flush()
    info = {"messages": len(messages), "turns": len(records), "threads": len(by_thread), "skipped": dict(skipped)}
    return records, info
