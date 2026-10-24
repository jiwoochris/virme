"""웹진 규칙표 매칭과 소설 쪽 나누기."""
from virtueme_core.records import Record
from virtueme_core.webzine import match, render


def rec(i, kind, source, text=None, hour=12, **meta):
    return Record(id=f"r{i:03d}", ts=f"2026-08-2{i % 7 + 3}T{hour:02d}:00:00+09:00", source=source, kind=kind,
                  modality="text", text=text, meta=meta)


def test_rules_send_records_to_matching_templates():
    rules = match.load_rules()
    rs = ([rec(i, "watch", "youtube", "유튜브 시청: 러닝", channel="러닝") for i in range(30)]
          + [rec(40 + i, "payment", "card", "결제: 무신사", category="의류", amount=30000) for i in range(3)]
          + [rec(60 + i, "post", "instagram", "혼자 걷는 밤", hour=0) for i in range(6)])
    sc = match.score(rs, rules)
    assert sc["digital_preview"]["score"] == 9.0          # 시청 30건 × 0.3
    assert sc["fashion"]["score"] == 6.0                  # 의류 결제 3건 × 2
    assert sc["poem"]["score"] == 9.0                     # 혼잣말 6건 × 1 + 밤 글 6건 × 0.5
    picked = {p["template"]: p for p in match.pick(sc, rules)}
    assert picked["fashion"]["included"] and picked["digital_preview"]["included"]
    assert not picked["documentary"]["included"]          # 방문 기록이 없다
    assert picked["poem"]["included"] and picked["novel"]["included"]   # 시 점수가 both_over(8) 이상
    assert picked["article"]["included"] and picked["data_portrait"]["included"]


def test_poem_only_when_monologue_outweighs_story():
    rules = match.load_rules()
    rs = [rec(i, "story", "instagram", "오늘도 혼자", hour=1) for i in range(3)]
    picked = {p["template"]: p for p in match.pick(match.score(rs, rules), rules)}
    assert picked["poem"]["included"] and not picked["novel"]["included"]


def test_short_dialogue_lines_merge_into_paragraphs():
    ps = ["“어디야?”", "“거의 다 왔어.”", "그날 저녁 나는 한강까지 달렸다. " * 5]
    out = render._merge_short(ps)
    assert len(out) == 2 and out[0].startswith("“어디야?”")
