"""카카오톡 대화 내보내기 가져오기.

지원 형식 (한국어 UI 기준)
- PC·일부 안드로이드 txt: `--------------- 2024년 6월 22일 토요일 ---------------` 구분선 + `[이름] [오후 1:00] 내용`
- 모바일 txt (점 날짜): `2024. 3. 14. 오후 7:12, 이름 : 내용`
- 모바일 txt (날짜 먼저): `2014년 1월 16일 오전 3:23, 이름 : 내용`
- 모바일 txt (시각 먼저): `오후 10:50, 2011년 11월 6일, 이름 : 내용`
- Mac csv: `Date,User,Message` 헤더

같은 사람이 3분 안에 연달아 보낸 메시지는 한 발화(turn)로 합친다. "ㅋㅋ", "ㅇㅇ" 같은
짧은 메시지를 따로 두면 의미 검색에서 잡음이 되기 때문이다. 사진·이모티콘처럼 내용이
없는 첨부만 있는 발화는 기록으로 만들지 않고, 대화방 통계에만 센다.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..config import DEFAULT_TZ
from ..privacy import Masker, Pseudonymizer
from ..records import Record, iso, record_id, to_aware

AMPM = r"(?:(오전|오후) )?"
T = r"(\d{1,2}):(\d{2})"

PC_SEP = re.compile(r"^-{3,}\s*(\d{4})년 (\d{1,2})월 (\d{1,2})일 \S*요일\s*-{3,}$")
PC_MSG = re.compile(rf"^\[(.+?)\] \[{AMPM}{T}\] ?(.*)$")
DOT_MSG = re.compile(rf"^(\d{{4}})\. ?(\d{{1,2}})\. ?(\d{{1,2}})\.? {AMPM}{T}, (.+?) : ?(.*)$")
DOT_SYS = re.compile(rf"^(\d{{4}})\. ?(\d{{1,2}})\. ?(\d{{1,2}})\.? {AMPM}{T}, (.+)$")
KDATE_MSG = re.compile(rf"^(\d{{4}})년 (\d{{1,2}})월 (\d{{1,2}})일 {AMPM}{T}, (.+?) : ?(.*)$")
KDATE_SYS = re.compile(rf"^(\d{{4}})년 (\d{{1,2}})월 (\d{{1,2}})일 {AMPM}{T}, (.+)$")
TFIRST_MSG = re.compile(rf"^{AMPM}{T}, (\d{{4}})년 (\d{{1,2}})월 (\d{{1,2}})일, (.+?) : ?(.*)$")
TFIRST_SYS = re.compile(rf"^{AMPM}{T}, (\d{{4}})년 (\d{{1,2}})월 (\d{{1,2}})일, (.+)$")
DATE_LINE = re.compile(r"^(\d{4})년 (\d{1,2})월 (\d{1,2})일 \S*요일$")
HEADER = re.compile(r"^(.+?) ?(?:님과)? 카카오톡 대화$")
SAVED_AT = re.compile(r"^저장한 날짜 ?: ?(.+)$")

SYSTEM_TEXT = re.compile(
    r"(님이 (?:들어왔습니다|나갔습니다|퇴장했습니다)\.?$)"
    r"|(님을 (?:초대했습니다|초대하였습니다|내보냈습니다)\.?$)"
    r"|(님이 .+님을 (?:초대했습니다|초대하였습니다|내보냈습니다)\.?$)"
    r"|(^채팅방 관리자가)|(^방장이)|(^운영정책을 위반한)"
)

ATTACHMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^사진( \d+장)?$"), "photo"),
    (re.compile(r"^동영상$"), "video"),
    (re.compile(r"^\(?이모티콘\)?$"), "emoticon"),
    (re.compile(r"^음성메시지$"), "voice"),
    (re.compile(r"^파일: .+$"), "file"),
    (re.compile(r"^지도: .+$"), "map"),
    (re.compile(r"^삭제된 메시지입니다\.?$"), "deleted"),
    (re.compile(r"^(샵검색|#검색): .+$"), "search"),
    (re.compile(r"^(보이스톡|페이스톡) (해요|취소|종료|\d+:\d+)"), "call"),
]

TURN_GAP = timedelta(minutes=3)


@dataclass
class Message:
    dt: datetime
    sender: str
    text: str


@dataclass
class KakaoParse:
    room: str
    messages: list[Message]
    system_lines: int = 0
    warnings: list[str] = field(default_factory=list)
    fmt: str = "unknown"

    @property
    def senders(self) -> Counter[str]:
        return Counter(m.sender for m in self.messages)


def _hour(ampm: str | None, h: str) -> int:
    hh = int(h)
    if ampm == "오전":
        return 0 if hh == 12 else hh
    if ampm == "오후":
        return hh if hh == 12 else hh + 12
    return hh


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_csv(text: str, room: str) -> KakaoParse:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None) or []
    cols = [h.strip().lower() for h in header]

    def find(*names: str) -> int:
        for n in names:
            if n in cols:
                return cols.index(n)
        raise ValueError(f"카카오톡 csv 헤더를 알아볼 수 없습니다: {header}")

    i_date, i_user, i_msg = find("date", "날짜"), find("user", "사용자", "이름"), find("message", "메시지", "내용")
    out = KakaoParse(room=room, messages=[], fmt="mac-csv")
    for row in reader:
        if len(row) <= max(i_date, i_user, i_msg):
            continue
        try:
            dt = datetime.fromisoformat(row[i_date].strip().replace("/", "-"))
        except ValueError:
            out.warnings.append(f"날짜를 읽지 못함: {row[i_date]!r}")
            continue
        out.messages.append(Message(dt, row[i_user].strip(), row[i_msg]))
    return out


def parse_kakao_text(text: str, room_hint: str = "대화방") -> KakaoParse:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first = next((ln.strip() for ln in lines if ln.strip()), "")
    if re.match(r"^(﻿)?\"?(date|날짜)\"?,", first, re.IGNORECASE):
        return _parse_csv(text.lstrip("﻿"), room_hint)

    room = room_hint
    m = HEADER.match(first)
    if m:
        room = m.group(1).strip()
    out = KakaoParse(room=room, messages=[])
    current: Message | None = None
    day: tuple[int, int, int] | None = None
    fmts: Counter[str] = Counter()

    def push(msg: Message) -> None:
        nonlocal current
        out.messages.append(msg)
        current = msg

    for idx, raw in enumerate(lines):
        line = raw.rstrip()
        s = line.strip()
        if idx < 3 and (HEADER.match(s) or SAVED_AT.match(s)):
            continue
        if not s:
            if current is not None:
                current.text += "\n"
            continue
        if mm := PC_SEP.match(s):
            day = (int(mm[1]), int(mm[2]), int(mm[3]))
            current = None
            continue
        if mm := PC_MSG.match(s):
            if day is None:
                out.warnings.append(f"{idx + 1}행: 날짜 구분선 없이 시작한 메시지")
                continue
            fmts["pc"] += 1
            dt = datetime(*day, _hour(mm[2], mm[3]), int(mm[4]))
            push(Message(dt, mm[1].strip(), mm[5]))
            continue
        matched = False
        for pat, kind in ((DOT_MSG, "dot"), (KDATE_MSG, "kdate")):
            if mm := pat.match(s):
                fmts[kind] += 1
                dt = datetime(int(mm[1]), int(mm[2]), int(mm[3]), _hour(mm[4], mm[5]), int(mm[6]))
                push(Message(dt, mm[7].strip(), mm[8]))
                matched = True
                break
        if matched:
            continue
        if mm := TFIRST_MSG.match(s):
            fmts["tfirst"] += 1
            dt = datetime(int(mm[4]), int(mm[5]), int(mm[6]), _hour(mm[1], mm[2]), int(mm[3]))
            push(Message(dt, mm[7].strip(), mm[8]))
            continue
        if DATE_LINE.match(s):
            current = None
            continue
        if DOT_SYS.match(s) or KDATE_SYS.match(s) or TFIRST_SYS.match(s) or SYSTEM_TEXT.search(s):
            out.system_lines += 1
            current = None
            continue
        if current is not None:
            current.text += "\n" + line
        else:
            out.warnings.append(f"{idx + 1}행: 어느 메시지에도 속하지 않는 줄")
    for msg in out.messages:
        msg.text = msg.text.strip()
    out.fmt = fmts.most_common(1)[0][0] if fmts else "unknown"
    return out


def parse_kakao_file(path: Path) -> KakaoParse:
    text = _read_text(path)
    return parse_kakao_text(text, room_hint=path.stem)


def _attachment(text: str) -> str | None:
    for pat, kind in ATTACHMENTS:
        if pat.match(text.strip()):
            return kind
    return None


def to_records(
    parsed: KakaoParse,
    me: str | list[str],
    pseudo: Pseudonymizer,
    tz: str = DEFAULT_TZ,
) -> tuple[list[Record], dict]:
    """메시지를 발화 단위 레코드로 바꾼다. 실명은 가명으로, 본문 속 이름도 가려서 저장한다."""
    self_names = {me} if isinstance(me, str) else set(me)
    senders = parsed.senders
    if not self_names & set(senders):
        listing = ", ".join(f"{n}({c})" for n, c in senders.most_common(10))
        raise ValueError(f"'{', '.join(self_names)}' 이(가) 보낸 메시지가 없습니다. 보낸 사람: {listing}")
    for name in senders:
        pseudo.alias(name, is_self=name in self_names)
    masker = Masker(pseudo.table)
    room_alias = masker.mask(parsed.room) or "대화방"
    thread = "kakao:" + record_id("kakao-room", parsed.room)[1:9]
    participants = sorted({pseudo.table[n] for n in senders})

    records: list[Record] = []
    stats: Counter[str] = Counter()
    turn: list[Message] = []

    def flush() -> None:
        if not turn:
            return
        texts, attach = [], Counter()
        for m in turn:
            kind = _attachment(m.text)
            if kind:
                attach[kind] += 1
            elif m.text:
                texts.append(m.text)
        stats.update(attach)
        if texts:
            sender = turn[0].sender
            body = "\n".join(texts)
            dt = to_aware(turn[0].dt, tz)
            records.append(
                Record(
                    id=record_id("kakao", parsed.room, iso(dt), sender, body),
                    ts=iso(dt),
                    source="kakao",
                    kind="message",
                    modality="text",
                    text=masker.mask(body),
                    author=pseudo.table[sender],
                    is_self=sender in self_names,
                    thread=thread,
                    participants=participants,
                    meta={
                        "room": room_alias,
                        "messages": len(turn),
                        "attachments": dict(attach),
                        "end_ts": iso(to_aware(turn[-1].dt, tz)),
                    },
                )
            )
        turn.clear()

    for msg in parsed.messages:
        if turn and (msg.sender != turn[-1].sender or msg.dt - turn[-1].dt > TURN_GAP):
            flush()
        turn.append(msg)
    flush()
    info = {
        "room": room_alias,
        "format": parsed.fmt,
        "messages": len(parsed.messages),
        "turns": len(records),
        "system_lines": parsed.system_lines,
        "attachments_only": dict(stats),
        "warnings": parsed.warnings[:20],
        "participants": participants,
    }
    return records, info
