"""책 디자인: 팔레트 측정, 통제 어휘, 참고 표지 검색 순위, 서사 오브젝트로 만든 브리프, 디자인 값 뽑기, 장 나누기,
라벨 검수, 오프라인 렌더. 네트워크 없음."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from virtueme_core.book import briefing as B
from virtueme_core.book import chapters as C
from virtueme_core.book import review as R
from virtueme_core.book import features as F
from virtueme_core.book import vocab as V
from virtueme_core.book.grammar import build_grammar

SAMPLES = Path(__file__).resolve().parents[1] / "virtueme_core" / "book" / "samples"


def _two_tone(path: Path, top=(20, 30, 60), bottom=(240, 200, 120), split=0.7) -> Path:
    arr = np.zeros((300, 200, 3), dtype=np.uint8)
    arr[: int(300 * split)] = top
    arr[int(300 * split):] = bottom
    Image.fromarray(arr).save(path)
    return path


def test_palette_and_measures(tmp_path):
    m = F.measure(_two_tone(tmp_path / "a.png"))
    hexes = [p["hex"] for p in m["palette"]]
    assert hexes[0] == "#141e3c"  # 면적이 큰 색이 먼저
    assert abs(m["palette"][0]["share"] - 0.7) < 0.02
    assert "#f0c878" in hexes
    assert m["tone"] == "dark" and m["aspect"] == 1.5
    assert m["negative_space"] > 0.8 and m["edge_density"] < 0.05
    assert F.measure(_two_tone(tmp_path / "a.png")) == m  # 결정적


def test_lab_roundtrip_and_contrast():
    rgb = np.array([[12, 200, 90], [250, 250, 250], [0, 0, 0]], dtype=float)
    assert np.abs(F.lab_to_rgb(F.rgb_to_lab(rgb)) - rgb).max() <= 1
    assert F.contrast_ratio("#000000", "#ffffff") == pytest.approx(21, rel=1e-3)


def test_vocabulary_normalize_and_validate():
    lab = V.normalize_label({"archetype": "Full Bleed", "title_position": "center", "typeface": "sans-serif",
                             "art_style": "watercolor", "title_scale": "huge", "moods": ["Tense", "spooky", "dark"]})
    assert lab == {"archetype": "full_bleed", "title_position": "middle", "title_scale": "dominant", "typeface": "sans",
                   "art_style": "painterly", "moods": ["dark", "tense"], "is_cover": True}
    assert V.validate_label(lab) == []
    assert V.validate_label({"archetype": "poster", "moods": ["spooky"]}) == ["archetype='poster'", "moods='spooky'"]
    assert V.normalize_value("archetype", "nonsense") is None
    assert [V.genre_key(x) for x in (0, "로맨스", "SF", "문학", "comedy", None)] == \
        ["mystery", "romance", "sf", "literary", "comedy", "literary"]


def _ref(i, genre, moods, arch="full_bleed", tone="dark", guess=None, style="painterly"):
    return {"id": f"r{i}", "genre": genre, "lang": "ko",
            "provenance": {"title": f"책{i}", "source_url": f"https://openlibrary.org/books/r{i}"},
            "features": {"palette": [{"hex": "#202840", "share": 0.6, "L": 16}, {"hex": "#e0c080", "share": 0.4, "L": 80}],
                         "tone": tone, "lightness": 30, "contrast": 0.5, "colorfulness": 0.3, "edge_density": 0.1,
                         "negative_space": 0.3, "aspect": 1.5},
            "label": {"archetype": arch, "title_position": "top", "title_scale": "large", "typeface": "serif",
                      "art_style": style, "moods": moods, "is_cover": True, "genre_guess": guess or genre}}


REFS = [
    _ref(1, "mystery", ["mysterious", "dark"]),
    _ref(2, "mystery", ["warm", "mysterious"], arch="split", tone="light"),
    _ref(3, "romance", ["romantic"], guess="mystery"),
    _ref(4, "comedy", ["playful"], arch="centered_motif", style="line"),
    _ref(5, "mystery", ["tense"], guess="thriller"),
]


def test_retrieval_ranking():
    moods = {"mysterious": 1.0, "warm": 0.8}
    top = B.retrieve(REFS, "mystery", moods, valence=0.4, k=4)
    assert [t["id"] for t in top] == ["r2", "r1", "r5", "r3"]
    assert "같은 장르" in top[0]["why"] and "light 표지" in top[0]["why"]
    # 다른 장르지만 비전 모델이 미스터리로 본 표지는 낮은 점수로 들어온다
    assert top[-1]["score"] == pytest.approx(0.35)
    assert all(t["id"] != "r4" for t in top)


def test_target_moods_from_signals():
    no = {"emotion": {"mean": -0.6, "series": [{"arousal": 0.2}]},
          "signals": {"conflict": 0.7, "uncertainty": 0.1, "novelty": 0.0, "stability": 0.2, "activity": 0.1,
                      "possibility": 0.3}}
    moods, why = B.target_moods("romance", no)
    assert list(moods)[0] == "romantic"
    assert {"melancholic", "dark", "serene", "tense"} & set(moods)
    assert any("정서 평균" in w for w in why)


def test_grammar_distributions(tmp_path):
    g = build_grammar(REFS, path=tmp_path / "g.json")
    m = g["genres"]["mystery"]
    assert m["n"] == 3 and m["dist"]["archetype"] == {"full_bleed": 0.667, "split": 0.333}
    assert m["genre_agreement"] == pytest.approx(0.667, abs=1e-3)
    assert json.loads((tmp_path / "g.json").read_text())["n"] == 5


def test_brief_from_narrative_object(svc):
    no = svc.query("등산")["narrative_object"]
    grammar = build_grammar(REFS, path=None)
    story = (SAMPLES / "mystery.txt").read_text("utf-8")
    b = B.brief("미스터리", no, story, refs=REFS, grammar=grammar)
    assert b["genre"] == "mystery" and b["moods"][0] == "mysterious"
    assert b["archetype"] in V.ARCHETYPES and b["typeface"] in V.TYPEFACES
    assert b["art_style"] in V.ART_STYLES and b["art_style"] != "none"
    assert b["title_position"] in V.TITLE_POSITIONS
    assert {r["id"] for r in b["references"]} <= {r["id"] for r in REFS}
    assert len(b["palette"]["colors"]) == 5
    assert F.contrast_ratio(b["palette"]["ink"], b["palette"]["background"]) >= 4.5
    assert "Absolutely no text" in b["art_prompt"]
    assert [(c["start"], c["end"]) for c in b["chapters"]] == [(0, 5), (5, 10)]
    assert all("Absolutely no text" in c["illustration_prompt"] for c in b["chapters"])
    assert all(c["start"] <= c["illustration_after"] < c["end"] for c in b["chapters"])
    probs = b["decision"]["archetype"]["probabilities"]
    assert b["archetype"] in probs and sum(probs.values()) == pytest.approx(1, abs=1e-3)
    assert len(b["emotion_curve"]) == 32 and all(0 <= v <= 1 for v in b["emotion_curve"])
    # 같은 입력이면 같은 브리프
    assert B.brief("미스터리", no, story, refs=REFS, grammar=grammar) == b


def test_brief_without_references_uses_fallback():
    b = B.brief("코미디", None, None, refs=[], grammar={"genres": {}})
    assert (b["archetype"], b["typeface"], b["art_style"]) == ("centered_motif", "display", "flat_vector")
    assert b["title_position"] != "middle"  # 가운데 그림과 겹치지 않게
    assert b["references"] == [] and b["palette"]["genre_palette"] == B.FALLBACK_PALETTE["comedy"]


def test_shift_color_moves_with_emotion():
    warm = F.rgb_to_lab(np.array([F.rgb_of(B.shift_color("#4060a0", 0.8, 0.5))], dtype=float))[0]
    cold = F.rgb_to_lab(np.array([F.rgb_of(B.shift_color("#4060a0", -0.8, 0.5))], dtype=float))[0]
    assert warm[0] > cold[0]  # 밝은 정서일수록 밝게


def test_parse_story():
    title, paras = B.parse_story('TITLE: 제목\n\n첫 <em ref="3">7월 13일 5km</em> 단락\n\n둘째 단락')
    assert title == "제목" and paras == ["첫 7월 13일 5km 단락", "둘째 단락"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_offline_render_produces_files(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from virtueme_core.book.make import make_book

    story = (SAMPLES / "fantasy.txt").read_text("utf-8")
    try:
        res = make_book("판타지", story, author="윤서연", refs=REFS, grammar=build_grammar(REFS, path=None),
                        out_root=tmp_path, log=lambda *_: None)
    except RuntimeError as err:  # playwright 브라우저가 설치되지 않은 환경
        pytest.skip(str(err))
    for name in ("cover.png", "title.png", "toc.png", "opener.png", "spread.png", "colophon.png", "back.png",
                 "book.pdf", "brief.json", "cover_art.png", "illustration-1.png"):
        assert (Path(res["files"].get(name) or tmp_path / res["brief_id"] / name)).exists(), name
    assert res["art"]["cover"] == "procedural" and res["art"]["cost_usd"] == 0 and res["cost_usd"] == 0
    r = res["render"]
    n_ch = len(res["chapters"])
    assert r["overflow"] == [] and len(r["chapter_pages"]) == n_ch and r["chapter_pages"][0] == 5
    assert len(r["figure_pages"]) == n_ch
    assert r["pages"] % 4 == 0  # 판권까지 속 쪽 수가 4의 배수
    assert res["blurb"]["source"] == "fallback" and res["blurb"]["blurb"]
    with Image.open(res["files"]["cover.png"]) as im:
        assert abs(im.size[1] / im.size[0] - 225 / 152) < 0.02
    html_doc = Path(res["files"]["book.html"]).read_text("utf-8")
    assert "기록에서 지은 이야기" in html_doc and "개인 기록을 바탕으로 생성한 이야기" in html_doc


def test_draw_probabilities_and_seeded_draw():
    sc = {"full_bleed": 0.6, "split": 0.3, "framed": 0.1}
    assert B.draw_probabilities(sc, 1.0) == {"framed": 0.1, "full_bleed": 0.6, "split": 0.3}
    flat = B.draw_probabilities(sc, 3.0)
    assert flat["full_bleed"] < 0.6 and flat["framed"] > 0.1  # 온도가 높으면 고르게
    assert B.draw_probabilities(sc, 0) == {"full_bleed": 1.0}
    assert "split" not in B.draw_probabilities(sc, 1.0, {"split"})
    assert B.seeded_draw({"a": 1.0}, "x") == "a" and B.seeded_draw({}, "x") is None
    draws = [B.seeded_draw(B.draw_probabilities(sc, 1.0), f"id{i}|archetype") for i in range(400)]
    assert draws == [B.seeded_draw(B.draw_probabilities(sc, 1.0), f"id{i}|archetype") for i in range(400)]
    share = draws.count("full_bleed") / len(draws)
    assert 0.5 < share < 0.7 and draws.count("framed") > 15


def test_brief_draws_vary_by_variant_but_are_stable():
    refs = [_ref(i, "mystery", ["mysterious"], arch=a) for i, a in
            enumerate(["full_bleed", "full_bleed", "split", "framed", "centered_motif", "pattern"])]
    g = build_grammar(refs, path=None)
    story = (SAMPLES / "mystery.txt").read_text("utf-8")
    archs = {B.brief("mystery", None, story, refs=refs, grammar=g, variant=v)["archetype"] for v in range(12)}
    assert len(archs) >= 3
    b1 = B.brief("mystery", None, story, refs=refs, grammar=g, variant=3)
    assert B.brief("mystery", None, story, refs=refs, grammar=g, variant=3) == b1
    greedy = {B.brief("mystery", None, story, refs=refs, grammar=g, variant=v, temperature=0)["archetype"]
              for v in range(6)}
    assert greedy == {"full_bleed"}
    assert b1["draw"]["temperature"] == B.DEFAULT_TEMPERATURE


LONG = [
    "7월 13일 오후 열 시, 나는 5km를 완주했다. 다리가 후들거렸고 숨이 턱까지 찼다. 그런데 기분은 좋았다.",
    "회사 동기에게 메시지를 보냈다. 처음 보는 숫자들이 화면에 가득했다. 페이스, 심박수, 소모 칼로리.",
    "\"어?\"",
    "다음 날 아침, 기록이 지워져 있었다. 대신 4km 기록만 남아 있었다. 분명히 뛰었는데 말이 안 됐다.",
    "루프였다.",
    "열흘쯤 지났을까. 나는 루프를 빠져나가는 걸 포기하고, 루프 안에서 할 수 있는 걸 해 보기로 했다. " * 2,
    "그날 저녁, 나는 기록을 재지 않고 천천히 걸었다. 한강변 산책로를 따라 걸으며 주변을 봤다. " * 2,
    "\"왜 이렇게 매일이 똑같은지 모르겠어.\"",
    "8월이 왔다. 10km 대회에 나갔고 끝까지 뛰었다. 힘들었지만 웃음이 났다. " * 2,
    "7월 13일 기록만 스물네 개였다.",
] * 4


def test_split_chapters_and_titles():
    assert C.chapter_count(["짧은 단락"] * 3) == 1
    ch = C.split_chapters(LONG)
    assert 3 <= len(ch) <= 5 and ch[0][0] == 0 and ch[-1][1] == len(LONG)
    assert all(b - a >= 2 for a, b in ch) and all(ch[i][1] == ch[i + 1][0] for i in range(len(ch) - 1))
    assert all(not LONG[a].startswith('"') for a, _ in ch)  # 대사로 장을 열지 않는다
    assert C.split_chapters(LONG) == ch  # 결정적
    # 장면 구분 줄이 있으면 그 자리에서 자르고, 구분 줄은 어느 장에도 넣지 않는다
    marked = ["가" * 300, "나" * 300, "* * *", "다" * 300, "라" * 300]
    assert C.split_chapters(marked) == [(0, 2), (3, 5)]
    assert C.chapter_title(["긴 단락입니다. " * 5, "루프였다.", "\"뭐야?\""]) == "루프였다"
    assert C.chapter_title(["화요일 저녁 일곱 시 사십 분, 현관문을 열면 콩이가 없다."]) == "화요일 저녁 일곱 시 사십 분"
    blurb = C.fallback_blurb(LONG, ch)
    assert 20 < len(blurb) <= 160 and not blurb.startswith('"')
    res = C.make_blurb("제목", "미스터리", LONG, ch, None, None, use_llm=False)
    assert res["source"] == "fallback" and len(res["chapter_titles"]) == len(ch)


def test_review_corrections_and_checked_weight(tmp_path):
    refs = [_ref(i, "mystery", ["mysterious"], arch="full_bleed") for i in range(4)]
    path = tmp_path / "references.json"
    path.write_text(json.dumps({"items": refs}, ensure_ascii=False))
    corr = {"checked_by": "검수자", "checked_at": "2026-10-03",
            "items": [{"id": "r0", "label": {"archetype": "framed"}}, {"id": "r1", "label": {}},
                      {"id": "r2", "label": {"typeface": "sans", "archetype": "split"}}]}
    res = R.apply_corrections(corr, refs_path=path)
    assert res["applied"] == 3 and res["checked"] == 3
    assert res["per_field"]["archetype"] == pytest.approx(1 / 3, abs=1e-3)
    assert res["per_field"]["typeface"] == pytest.approx(2 / 3, abs=1e-3)
    assert res["all_design_fields"] == pytest.approx(1 / 3, abs=1e-3)
    items = {r["id"]: r for r in json.loads(path.read_text())["items"]}
    assert items["r0"]["label"]["archetype"] == "framed" and items["r0"]["label_model"]["archetype"] == "full_bleed"
    assert items["r0"]["label_corrected_fields"] == ["archetype"] and items["r1"]["label_corrected_fields"] == []
    assert items["r0"]["label_checked"] is True and "label_checked" not in items["r3"]
    # 다시 넣어도 처음 모델 라벨과 비교한다
    assert R.apply_corrections(corr, refs_path=path, write=False)["per_field"]["archetype"] == pytest.approx(1 / 3, abs=1e-3)
    # 검수한 표지는 grammar 에서 2배: framed 2 + split 2 + full_bleed(r1 2, r3 1) 3 = 7
    g = build_grammar(list(items.values()), path=None)
    assert g["genres"]["mystery"]["dist"]["archetype"] == {"full_bleed": 0.429, "framed": 0.286, "split": 0.286}
    assert g["n_checked"] == 3
    top = B.retrieve(list(items.values()), "mystery", {"mysterious": 1.0}, k=4)
    assert top[-1]["id"] == "r3" and "사람이 라벨 검수" in top[0]["why"]


def test_hard_band_detects_empty_strip_not_horizon():
    from virtueme_core.book.art import hard_band

    rng = np.random.default_rng(0)
    scene = rng.integers(0, 255, (300, 200, 3)).astype(np.uint8)
    banded = scene.copy()
    banded[:100] = (240, 235, 225)  # 위쪽 1/3 이 빈 띠
    assert hard_band(Image.fromarray(banded)) == pytest.approx(0.33, abs=0.03)
    assert hard_band(Image.fromarray(scene)) is None
    horizon = np.zeros((300, 200, 3), dtype=np.uint8)
    horizon[:150] = rng.integers(150, 255, (150, 200, 3))  # 결이 있는 하늘
    horizon[150:] = rng.integers(0, 100, (150, 200, 3))  # 결이 있는 바다
    assert hard_band(Image.fromarray(horizon)) is None


def test_brief_overrides_are_validated():
    b = B.brief("코미디", None, None, refs=REFS, grammar={"genres": {}},
                overrides={"archetype": "typographic", "typeface": "serif"})
    assert (b["archetype"], b["typeface"], b["art_style"], b["art_prompt"]) == ("typographic", "serif", "none", None)
    assert b["decision"]["archetype"]["from"] == "사람이 고름"
    with pytest.raises(ValueError):
        B.brief("코미디", None, None, refs=REFS, grammar={"genres": {}}, overrides={"archetype": "poster"})
