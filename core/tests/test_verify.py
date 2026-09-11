from virtueme_core.records import Record
from virtueme_core.verify import parse_claims, verify_story, whitelist_v1


def rec(i, ts, text, **kw):
    return Record(id=f"r{i:011x}", ts=ts, source="kakao", kind="message", modality="text", text=text, **kw)


EVIDENCE = [
    rec(1, "2026-07-01T11:35:00+09:00", "첫 러닝 3km 완주. 힘들었지만 개운하다"),
    rec(2, "2026-07-05T21:42:00+09:00", "한강에서 또 뛰었어"),
    rec(3, "2026-07-13T22:48:00+09:00", "나 5km 완주했어"),
    rec(4, "2026-06-07T13:42:00+09:00", "등산 가고 싶다"),
]


def test_parse_claims_kinds():
    kinds = {c.kind: c.value for c in parse_claims("7월 5일 밤 9시 한강 러닝 5km 세 번")}
    assert kinds["date"] == (7, 5)
    assert kinds["time"][0] == [21]
    assert kinds["count"] in ((5.0, "km"), (3.0, "번"))
    terms = {c.value for c in parse_claims("7월 5일 한강 러닝") if c.kind == "term"}
    assert {"한강", "러닝"} <= terms


def test_joint_check_catches_pair_error_that_v1_misses():
    # "7월 5일"(기록 2)과 "등산"(기록 4)은 각각 있지만 한 기록에 함께 있지 않다
    story = '<em ref="2">7월 5일 등산</em>을 떠올렸다.'
    out = verify_story([story], EVIDENCE, mode="plain")
    assert out["anchors"][0]["status"] == "failed"
    assert "등산" in out["anchors"][0]["failed_claims"]
    # v1 화이트리스트는 값을 하나씩만 보므로 둘 다 있으면 통과시킨다
    assert whitelist_v1('<em>7월 5일</em> <em>등산</em>', {"7월 5일", "등산"}) == [True, True]


def test_ok_ref_fixed_and_unreferenced():
    paras = [
        '<em ref="1">7월 1일</em> 처음으로 <em ref="1">3km</em>를 뛰었다.',
        '<em ref="1">5km</em>를 끝까지 달렸다.',          # 번호만 틀림 → 기록 3 으로 고쳐 준다
        '<em>밤 10시</em>, <em>5km</em>였다.',             # 번호 없이도 맞는 기록이 있으면 통과
    ]
    out = verify_story(paras, EVIDENCE, mode="blur")
    st = [a["status"] for a in out["anchors"]]
    assert st == ["ok", "ok", "ref_fixed", "unreferenced_ok", "unreferenced_ok"]
    assert out["anchors"][2]["record_id"] == EVIDENCE[2].id
    assert 'class="vx vx-ok"' in out["paragraphs_html"][0]


def test_blur_and_plain_downgrade():
    story = '그날은 <em ref="2">7월 9일 오후 3시</em>였다.'
    blur = verify_story([story], EVIDENCE, mode="blur")["paragraphs_html"][0]
    assert "어느 날" in blur and "오후 무렵" in blur and "7월 9일" not in blur
    plain = verify_story([story], EVIDENCE, mode="plain")["paragraphs_html"][0]
    assert "7월 9일 오후 3시" in plain and "<em" not in plain


def test_counts_from_reference_count():
    story = '<em ref="1,2,3">세 번</em>이나 뛰었다.'
    assert verify_story([story], EVIDENCE)["anchors"][0]["status"] == "ok"


def test_loose_dates_outside_anchors_are_checked_and_blurred():
    story = "8월 30일에 무슨 일이 있었는지 아무도 몰랐다. 7월 5일 밤은 조용했다."
    out = verify_story([story], EVIDENCE, mode="blur")
    loose = {x["text"]: x["supported"] for x in out["loose"]}
    assert loose == {"8월 30일": False, "7월 5일": True}
    assert "8월 30일" not in out["paragraphs_html"][0] and "7월 5일" in out["paragraphs_html"][0]


def test_html_from_model_is_escaped():
    out = verify_story(['<script>alert(1)</script><em ref="1">7월 1일</em>'], EVIDENCE)
    assert "<script>" not in out["paragraphs_html"][0] and "&lt;script&gt;" in out["paragraphs_html"][0]


def test_rewrite_mode_uses_llm_and_rechecks():
    story = '그날은 <em ref="2">7월 9일</em>이었다.'
    calls = []

    def fake_rewrite(system, marked):
        calls.append(marked)
        return "그날은 여름 어느 저녁이었다."

    out = verify_story([story], EVIDENCE, mode="rewrite", rewrite_fn=fake_rewrite)
    assert calls and "[[7월 9일]]" in calls[0]
    assert out["paragraphs_html"][0] == "그날은 여름 어느 저녁이었다."
    assert out["stats"]["rewritten_paragraphs"] == 1


def test_blur_collapses_runs():
    from virtueme_core.verify import _blur

    assert _blur("7월 20일, 7월 27일, 3일 한강에") == "어느 날, 며칠 한강에"
    assert _blur("8월 29일 토요일 아침") == "어느 날 아침"
    assert _blur("7월 13일과 7월 20일에") == "어느 날에"


def test_context_check_sees_every_cited_record_and_relations():
    """"7월 1일과 5일" 처럼 기록 여러 건을 인용한 앵커는 문맥 검사에도 그 기록을 모두 넘긴다 (3단계에는 한 건만 넘겨 맞는 앵커를 떨어뜨렸다)."""
    seen = []

    def fn(system, body):
        seen.append(body)
        return {"checks": [{"c": 1, "v": "ok"}, {"c": 2, "v": "wrong", "why": "뛴 사람은 지인A"}]}

    evidence = EVIDENCE + [rec(5, "2026-07-06T20:00:00+09:00", "나 오늘 한강 뛰었어", author="지인A",
                               participants=["나", "지인A"])]
    story = '<em ref="1,2">7월 1일과 5일</em>에 뛰었다. 너는 <em ref="5">7월 6일</em>에 한강을 뛰었지.'
    out = verify_story([story], evidence, mode="blur", context_fn=fn, kind="talk", people={"지인A": "러닝 크루 친구"})
    body = seen[0]
    assert "대화 답" in body and "첫 러닝 3km" in body and "한강에서 또 뛰었어" in body
    assert "지인A(러닝 크루 친구)" in body
    assert [a["status"] for a in out["anchors"]] == ["ok", "context_mismatch"]
    assert [a["context"] for a in out["anchors"]] == ["ok", "wrong"]
    assert "뛴 사람은 지인A" in out["paragraphs_html"][0]


def test_missing_verdict_keeps_the_anchor():
    """기록에 없어서 확인할 수 없는 것("missing")은 규칙 검사가 값을 확인했으므로 통과시킨다."""
    fn = lambda s, b: {"checks": [{"c": 1, "v": "missing", "why": "산책 얘기는 기록에 없음"}]}  # noqa: E731
    out = verify_story(['<em ref="2">7월 5일</em>에 산책도 했다.'], EVIDENCE, context_fn=fn)
    assert out["anchors"][0]["status"] == "ok" and out["anchors"][0]["context"] == "missing"


def test_rules_accept_relations_split_messages_and_place_suffix():
    evidence = [
        rec(1, "2026-08-18T12:28:00+09:00", "근데 엄마 나 고양이 키우기로 했어", participants=["나", "엄마"]),
        rec(2, "2026-08-18T12:32:00+09:00", "응 콩이 / 정식으로 입양하기로 했어", participants=["나", "엄마"]),
        rec(3, "2026-08-10T11:34:00+09:00", "내일부터 제주 가는데 콩이 부탁해도 되지?", participants=["나", "지인D"]),
    ]
    paras = ['<em ref="1,2">8월 18일, 고양이 키우기로 했고 정식 입양</em>한다고 말했다.',   # 같은 날 이어 보낸 두 메시지
             '<em ref="3">8월 10일, 회사 동기에게 콩이를 맡기고 제주도</em>로 떠났다.']      # 관계 · 제주도 ↔ 제주
    out = verify_story(paras, evidence, mode="plain", people={"지인D": "회사 동기"})
    assert [a["status"] for a in out["anchors"]] == ["ok", "ok"]
    # 관계를 모르면 "회사 동기"를 확인할 수 없다
    assert verify_story(paras[1:], evidence, mode="plain")["anchors"][0]["status"] == "failed"
    # 다른 날 메시지의 낱말은 합쳐 주지 않는다 (짝 오류)
    far = '<em ref="1,3">8월 18일, 고양이와 제주</em>'
    assert verify_story([far], evidence, mode="plain")["anchors"][0]["status"] == "failed"
