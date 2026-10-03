"""책 디자인: 팔레트 측정, 통제 어휘, 참고 표지 검색 순위, 서사 오브젝트로 만든 브리프, 오프라인 렌더. 네트워크 없음."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from virtueme_core.book import briefing as B
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
    assert "Absolutely no text" in b["art_prompt"] and "Absolutely no text" in b["illustration_prompt"]
    assert b["illustration_after_paragraph"] == 5
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

    try:
        res = make_book("판타지", (SAMPLES / "fantasy.txt").read_text("utf-8"), author="윤서연",
                        refs=REFS, grammar=build_grammar(REFS, path=None), out_root=tmp_path, log=lambda *_: None)
    except RuntimeError as err:  # playwright 브라우저가 설치되지 않은 환경
        pytest.skip(str(err))
    for name in ("cover.png", "opener.png", "spread.png", "book.pdf", "brief.json", "cover_art.png", "illustration.png"):
        assert (Path(res["files"].get(name) or tmp_path / res["brief_id"] / name)).exists(), name
    assert res["art"]["cover"] == "procedural" and res["art"]["cost_usd"] == 0
    assert res["render"]["pages"] >= 2 and res["render"]["overflow"] == []
    assert res["render"]["figure_page"] is not None
    with Image.open(res["files"]["cover.png"]) as im:
        assert abs(im.size[1] / im.size[0] - 225 / 152) < 0.02


def test_brief_overrides_are_validated():
    b = B.brief("코미디", None, None, refs=REFS, grammar={"genres": {}},
                overrides={"archetype": "typographic", "typeface": "serif"})
    assert (b["archetype"], b["typeface"], b["art_style"], b["art_prompt"]) == ("typographic", "serif", "none", None)
    assert b["decision"]["archetype"]["from"] == "사람이 고름"
    with pytest.raises(ValueError):
        B.brief("코미디", None, None, refs=REFS, grammar={"genres": {}}, overrides={"archetype": "poster"})
