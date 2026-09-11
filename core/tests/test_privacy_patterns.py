from datetime import date, timedelta

import numpy as np

from virtueme_core import patterns
from virtueme_core.privacy import Masker, Pseudonymizer
from virtueme_core.store import Store


def test_masker_patterns_and_names(tmp_path):
    store = Store(tmp_path / "t.db")
    ps = Pseudonymizer(store)
    assert ps.alias("윤서연", is_self=True) == "나"
    assert ps.alias("김민지") == "지인A"
    assert ps.alias("박지훈") == "지인B"
    assert ps.alias("김민지") == "지인A"  # 같은 이름은 같은 가명
    assert Pseudonymizer(store).alias("박지훈") == "지인B"  # DB 에 남아 다시 열어도 유지
    m = Masker.from_store(store)
    s = ("민지야 서연이 번호 010 9876 5432, 메일 a.b@test.co.kr, 계좌 110-123-456789, "
         "카드 1234-5678-9012-3456, 주민 900101-2345678 https://blog.naver.com/abc/1 @run_crew_seoul")
    out = m.mask(s)
    for leaked in ("민지", "서연", "9876", "a.b@", "456789", "9012", "2345678", "/abc/1", "run_crew"):
        assert leaked not in out, leaked
    assert out.startswith("지인A야 (나)이 번호 [전화번호]")
    assert "[링크:blog.naver.com]" in out and "[계정]" in out
    # 이름이 다른 낱말의 일부인 경우는 건드리지 않는다
    assert m.mask("민지현 선배") == "민지현 선배"


def test_change_point_detects_step():
    rng = np.random.default_rng(0)
    x = np.r_[rng.normal(-0.2, 0.08, 40), rng.normal(0.35, 0.08, 50)]
    dates = [date(2026, 6, 1) + timedelta(days=i) for i in range(90)]
    cps = patterns.change_points(x, dates)
    assert len(cps) == 1
    assert abs(cps[0]["index"] - 40) <= 2 and cps[0]["direction"] == "상승"
    flat = patterns.change_points(rng.normal(0, 0.08, 90), dates)
    assert flat == []


def test_weekly_periodicity():
    dates = [date(2026, 6, 1) + timedelta(days=i) for i in range(84)]  # 6/1 은 월요일
    counts = np.array([8.0 if d.weekday() >= 5 else 2.0 for d in dates])
    per = patterns.periodicity(counts, dates)
    assert per["weekly_strength"] > 0.8
    assert per["lag_days"] == 7
    assert per["weekday_peak"] in ("토", "일")
    assert per["yearly_strength"] is None


def test_hour_center_wraps_midnight():
    class R:
        def __init__(self, h):
            from datetime import datetime

            self.dt = datetime(2026, 6, 1, h, 0)

    assert patterns.hour_profile([R(23), R(1)])["center"] in (0.0, 24.0)
