from virtueme_core import pii


class FakeModel:
    """GLiNER 대신: 미리 정한 글자를 사람·동물로 돌려준다."""

    def predict_entities(self, text, labels, threshold=0.5):
        out = []
        for span, label in (("김민수 팀장님이", "person"), ("수빈이랑", "person"), ("콩이", "animal"),
                            ("옆집 할머니", "person"), ("지인A랑", "person"), ("엄마가", "person"), ("나도", "person"),
                            ("우리 딸", "person"), ("회사 동기가", "person"), ("서연씨는", "person"), ("이도", "person")):
            if span in text:
                i = text.index(span)
                out.append({"text": span, "label": label, "score": 0.9, "start": i, "end": i + len(span)})
        return out


def test_name_core():
    assert pii.name_core("김민수 팀장님이") == "김민수"
    assert pii.name_core("수빈이랑") == "수빈"
    assert pii.name_core("옆집 할머니") is None
    assert pii.name_core("엄마가") is None
    assert pii.name_core("지인A랑") is None
    assert pii.name_core("하은님도") == "하은"
    assert pii.name_core("한하은이라고") == "한하은"
    assert pii.name_core("김도은이") == "김도은"
    assert pii.name_core("나은이가") == "나은"
    for span in ("나도", "나한테는", "우리 딸", "너희", "여러분", "회사 동기가", "혼자", "이도", "내가", "저도", "러너스 하이"):
        assert pii.name_core(span) is None, span


def test_mask_texts_keeps_pets_relations_and_keep_list(monkeypatch):
    monkeypatch.setattr(pii, "_model", lambda: FakeModel())
    (t1, n1), (t2, n2), (t3, n3) = pii.mask_texts(
        ["김민수 팀장님이 칭찬해줬어 김민수 최고", "수빈이랑 콩이 산책, 옆집 할머니 만남", "엄마가 지인A랑 저녁"])
    assert t1 == "(누군가) 팀장님이 칭찬해줬어 (누군가) 최고" and n1 == ["김민수"]
    assert t2 == "(누군가)이랑 콩이 산책, 옆집 할머니 만남" and n2 == ["수빈"]
    assert t3 == "엄마가 지인A랑 저녁" and n3 == []
    (t4, _), = pii.mask_texts(["수빈이랑 카페"], keep={"수빈"})
    assert t4 == "수빈이랑 카페"


def test_mask_texts_pronouns_self_and_placeholder(monkeypatch):
    monkeypatch.setattr(pii, "_model", lambda: FakeModel())
    (t1, n1), (t2, n2) = pii.mask_texts(
        ["나도 우리 딸 회사 동기가 봐준대", "응 서연씨는? (나)이도 다음엔 꼭"], self_names={"윤서연"})
    assert t1 == "나도 우리 딸 회사 동기가 봐준대" and n1 == []
    assert t2 == "응 (나)씨는? (나)이도 다음엔 꼭" and n2 == []
