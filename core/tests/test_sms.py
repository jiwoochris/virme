"""문자 가져오기: 안드로이드 SMS Backup & Restore XML, 맥 메시지 DB(chat.db)."""
import sqlite3
from datetime import datetime, timezone

from virtueme_core.ingest import sms
from virtueme_core.privacy import Pseudonymizer
from virtueme_core.store import Store

# 2026-06-20 21:00:00 KST = 12:00:00 UTC
T0 = int(datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)

XML = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<smses count="7">
  <sms protocol="0" address="010-2345-6789" date="{T0}" type="1" body="오늘 한강 뛰었어? 내 번호 바뀌었어 010-9876-5432" contact_name="박지수" />
  <sms protocol="0" address="010-2345-6789" date="{T0 + 60_000}" type="2" body="응 5km 뛰었어" contact_name="박지수" />
  <sms protocol="0" address="010-2345-6789" date="{T0 + 90_000}" type="2" body="지수 너도 같이 가자" contact_name="박지수" />
  <sms protocol="0" address="010-1111-2222" date="{T0 + 120_000}" type="1" body="밥은 먹었니" contact_name="엄마" />
  <sms protocol="0" address="15881234" date="{T0 + 180_000}" type="1" body="[Web발신] 인증번호 123456" contact_name="(Unknown)" />
  <sms protocol="0" address="010-3333-4444" date="{T0 + 200_000}" type="3" body="임시 저장" contact_name="박지수" />
  <mms date="{T0 + 300_000}" msg_box="1" address="010-2345-6789~010-5555-6666" contact_name="박지수, 최민호">
    <parts><part ct="application/smil" text="" /><part ct="text/plain" text="주말에 셋이 등산 어때" /></parts>
    <addrs><addr address="010-2345-6789" type="137" /><addr address="010-5555-6666" type="151" /></addrs>
  </mms>
</smses>"""


def test_android_xml_turns_pseudonyms_and_filters(tmp_path):
    path = tmp_path / "sms.xml"
    path.write_text(XML, encoding="utf-8")
    store = Store(tmp_path / "t.db")
    recs, info = sms.to_records(sms.parse_file(path), Pseudonymizer(store))
    assert info["skipped"] == {"service_number": 1}  # 인증번호 문자는 발신 전용 번호라 여기서 걸린다
    mine = [r for r in recs if r.is_self]
    assert len(mine) == 1 and mine[0].meta["messages"] == 2  # 3분 안에 연달아 보낸 두 문자는 한 발화
    assert mine[0].text == "응 5km 뛰었어\n지인A 너도 같이 가자"  # 본문 속 성을 뗀 이름("지수")도 가명으로
    assert all(r.source == "sms" and r.ts.startswith("2026-06-20T21:") for r in recs)
    friend = next(r for r in recs if "한강" in (r.text or ""))
    assert friend.author.startswith("지인") and "[전화번호]" in friend.text and "010-9876" not in friend.text
    mom = next(r for r in recs if "밥은" in (r.text or ""))
    assert mom.author == "엄마"  # 관계로 저장된 연락처는 그대로
    group = next(r for r in recs if "등산" in (r.text or ""))
    assert len(group.participants) == 3 and group.meta["service"] == "MMS"


def test_mac_chat_db_attributed_body_and_apple_time(tmp_path):
    db = tmp_path / "chat.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, date INTEGER,
                              is_from_me INTEGER, handle_id INTEGER, service TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, display_name TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
    """)
    con.execute("INSERT INTO handle VALUES (1, '+821023456789')")
    body = "콩이 산책 다녀왔어".encode()
    blob = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92" \
           b"\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + bytes([len(body)]) + body + b"\x86\x84\x02iI\x01"
    apple_ns = int((datetime(2026, 7, 22, 11, 35, tzinfo=timezone.utc) - sms.APPLE_EPOCH).total_seconds() * 1e9)
    con.execute("INSERT INTO message VALUES (1, NULL, ?, ?, 0, 1, 'iMessage')", (blob, apple_ns))
    con.execute("INSERT INTO message VALUES (2, '좋겠다', NULL, ?, 1, 1, 'iMessage')", (apple_ns + 30 * 10**9,))
    con.execute("INSERT INTO chat VALUES (1, NULL)")
    con.executemany("INSERT INTO chat_message_join VALUES (1, ?)", [(1,), (2,)])
    con.execute("INSERT INTO chat_handle_join VALUES (1, 1)")
    con.commit()
    con.close()
    msgs = sms.parse_file(db)
    assert [m.text for m in msgs] == ["콩이 산책 다녀왔어", "좋겠다"]
    assert msgs[0].dt.strftime("%Y-%m-%d %H:%M") == "2026-07-22 20:35"  # KST
    store = Store(tmp_path / "t.db")
    recs, info = sms.to_records(msgs, Pseudonymizer(store))
    assert info["turns"] == 2 and recs[0].author.startswith("지인") and recs[1].is_self
    assert "+8210" not in str(store.relations())  # 번호는 가명표에만 있고 관계 목록에는 가명만
