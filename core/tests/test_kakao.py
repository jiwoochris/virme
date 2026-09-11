from datetime import datetime
from pathlib import Path

import pytest

from virtueme_core.ingest import kakao
from virtueme_core.privacy import Pseudonymizer
from virtueme_core.store import Store

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "t.db")


def test_pc_format_parses_dates_multiline_and_system_lines():
    p = kakao.parse_kakao_file(FIX / "kakao_pc.txt")
    assert p.fmt == "pc"
    assert p.room == "대학친구들"
    assert p.system_lines == 1  # "박지훈님이 들어왔습니다."
    texts = [m.text for m in p.messages]
    assert "헐 나 우산 없는데\n여러 줄로 보낸 메시지\n두 번째 줄" in texts
    midnight = next(m for m in p.messages if m.text.startswith("아니 야근"))
    assert midnight.dt == datetime(2026, 6, 23, 0, 5)  # 오전 12:05 → 00:05
    late = next(m for m in p.messages if m.text == "다들 자?")
    assert late.dt == datetime(2026, 6, 22, 23, 58)


def test_mobile_dot_format():
    p = kakao.parse_kakao_file(FIX / "kakao_mobile_dot.txt")
    assert p.fmt == "dot"
    assert p.room == "엄마"
    assert [m.sender for m in p.messages] == ["엄마", "윤서연", "윤서연", "엄마", "윤서연"]
    assert p.messages[3].text == "반찬 보냈다\n택배로 내일 도착할거야"
    assert p.system_lines == 1


def test_date_first_and_time_first_formats():
    p = kakao.parse_kakao_file(FIX / "kakao_datefirst.txt")
    assert p.fmt == "kdate" and len(p.messages) == 3
    assert p.messages[1].text == "반가워 : 콜론 포함"  # 본문 속 콜론은 보낸 사람 구분자로 오인하지 않는다
    assert p.messages[2].dt == datetime(2014, 1, 16, 12, 1)
    t = kakao.parse_kakao_file(FIX / "kakao_timefirst.txt")
    assert t.fmt == "tfirst"
    assert t.messages[1].dt == datetime(2011, 11, 7, 0, 10)


def test_mac_csv_with_quoted_newline():
    p = kakao.parse_kakao_file(FIX / "kakao_mac.csv")
    assert p.fmt == "mac-csv"
    assert p.messages[1].text == "ㅇㅇ\n몇 시?"


def test_turns_merge_mask_and_pseudonymize(store):
    parsed = kakao.parse_kakao_file(FIX / "kakao_pc.txt")
    recs, info = kakao.to_records(parsed, "윤서연", Pseudonymizer(store))
    # 윤서연이 1분 간격으로 보낸 두 메시지는 한 발화
    first = next(r for r in recs if r.author == "나")
    assert first.text == "오늘 비 엄청 온다\n우산 챙겨" and first.meta["messages"] == 2
    # 사진·이모티콘만 있는 발화는 기록이 되지 않고 통계로만 남는다
    assert info["attachments_only"] == {"photo": 1, "emoticon": 1}
    # 실명·전화번호는 저장 전에 가려진다
    joined = " ".join(r.text for r in recs)
    assert "김민지" not in joined and "010-1234-5678" not in joined
    assert "[전화번호]" in joined
    assert any("지인" in r.text for r in recs if "제주" in r.text)  # "김민지 이번 주말에" → 가명
    assert {r.author for r in recs} <= {"나", "지인A", "지인B", "지인C"}
    assert all(r.ts.endswith("+09:00") for r in recs)
    # 같은 파일을 다시 가져와도 ID 가 같다
    recs2, _ = kakao.to_records(kakao.parse_kakao_file(FIX / "kakao_pc.txt"), "윤서연", Pseudonymizer(store))
    assert [r.id for r in recs] == [r.id for r in recs2]


def test_missing_me_lists_senders(store):
    parsed = kakao.parse_kakao_file(FIX / "kakao_pc.txt")
    with pytest.raises(ValueError, match="보낸 사람"):
        kakao.to_records(parsed, "없는사람", Pseudonymizer(store))
