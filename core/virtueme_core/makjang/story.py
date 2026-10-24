"""노벨 → 막장극장 대본 (5장면 JSON).

팀 파이프라인(`막장극장_파이프라인/1_gen_story.py`)의 규칙과 출력 형식을 그대로 쓰고, 재료만 바꿨다.
팀 원본은 데모 페르소나의 90일 기록을 지시문에 박아 두었고, 여기서는 노벨 화면에서 검사를 마친 이야기
(가명은 관계로 바뀌고, 기록과 맞지 않은 값은 흐린 그대로)를 재료로 준다. 등장인물 국적은 원본처럼 12개국에서
추첨한다. 실사 배우가 나오는 영상이라 사용자 본인처럼 보이지 않게 하는 효과도 있다.
"""
from __future__ import annotations

import os
import random
from typing import Any

from ..llm import chat_json

# 팀 원본과 같은 모델·온도. 막장 문법(과장된 반전·클리프행어)은 온도를 낮추면 밋밋해진다.
STORY_MODEL = os.environ.get("VIRTUEME_MAKJANG_STORY_MODEL", "anthropic/claude-sonnet-4.5")

NATIONALITIES = ["Italian", "Nigerian", "Japanese", "Brazilian", "French", "Indian", "Mexican", "Swedish",
                 "Turkish", "Thai", "Korean", "Egyptian"]

MAX_NOVEL_CHARS = 6000

SYSTEM = """너는 막장드라마 전문 작가다. 사용자가 자기 기록으로 지은 아래 소설을 재료로, 짧고 자극적인 막장 쇼츠 대본을 쓴다. 이번 에피소드의 씨앗 단어는 '{seed}'이다.

규칙:
1. 등장인물은 전원 {nat} 사람이다. 배경 도시도 그 나라의 도시로 한다. 단, 막장드라마 문법(과장된 반전, 운명적 우연, 클리프행어)은 그대로 유지한다.
2. 소설의 핵심 사건·장소의 성격·관계·감정을 뼈대로 삼는다. 소설을 모르는 사람도 따라갈 수 있게 쓰되, 소설을 읽은 사람은 "내 이야기가 이렇게 됐네" 하고 알아볼 수 있어야 한다.
3. 정확히 5개 장면. 첫 장면은 시청자를 1초 만에 붙잡는 훅, 마지막 장면은 소름 돋는 반전.
4. 각 장면의 자막은 한국어 한 줄(28자 이내, 끝까지 읽게 만드는 문장)과 영어 한 줄(60자 이내)로.
5. 주인공과 상대역의 외모를 고정 묘사(영어, 나이·머리·특징·옷차림까지 구체적으로)로 정의해 모든 장면에서 동일하게 쓴다.
6. 범죄·장기매매·납치·폭력 소재는 금지. 반전은 어이없고 기발하게 — 초자연, 운명, 정체 반전, 시간 비틀기는 환영.
7. 실존 인물 이름·전화번호·주소·회사명은 쓰지 않는다. 소설 속 사람은 역할(엄마, 친구, 팀장 등)로만 부른다.
8. visual 은 반드시 영어 한 문장(인물 행동·장소·시간대·카메라)으로 쓴다. 화면에 글자가 크게 나오는 장면(문자 메시지 전문, 간판 문구)은 피한다.
9. 출력은 JSON만:
{{"title":"한국어 제목","title_en":"English Title","mood":"BGM 분위기 설명 (영어 한 문장)","protagonist":"영어 외모 고정 묘사","antagonist":"영어 외모 고정 묘사","scenes":[{{"kr":"한국어 자막 한 줄","en":"English caption one line","visual":"English scene description, one sentence"}} x5]}}"""

USER = """씨앗 단어: {seed}
소설 제목: {title}

소설:
{novel}

써라."""


def _clip(s: Any, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def check(story: dict[str, Any]) -> dict[str, Any]:
    """모델 응답을 조립기가 믿고 쓸 수 있는 모양으로 맞춘다. 장면이 5개가 아니면 실패로 본다."""
    scenes = story.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 5:
        raise ValueError(f"장면이 5개가 아닙니다: {len(scenes) if isinstance(scenes, list) else scenes!r}")
    out = {
        "title": _clip(story.get("title"), 24) or "막장극장",
        "title_en": _clip(story.get("title_en"), 48),
        "mood": _clip(story.get("mood"), 240) or "Dramatic soap-opera orchestral with tense strings",
        "protagonist": _clip(story.get("protagonist"), 400),
        "antagonist": _clip(story.get("antagonist"), 400),
        "scenes": [],
    }
    for sc in scenes:
        if not isinstance(sc, dict) or not sc.get("kr") or not sc.get("visual"):
            raise ValueError(f"장면 형식이 맞지 않습니다: {sc!r}")
        out["scenes"].append({"kr": _clip(sc["kr"], 32), "en": _clip(sc.get("en"), 70), "visual": _clip(sc["visual"], 600)})
    return out


def write_story(novel: str, seed: str, title: str | None = None, nationality: str | None = None,
                rng: random.Random | None = None) -> dict[str, Any]:
    """소설 → 막장 대본. 반환 형식은 팀 원본의 story JSON 과 같고 `nationality`·`seed`·`model` 이 붙는다."""
    nat = nationality or (rng or random).choice(NATIONALITIES)
    novel = novel.strip()
    if len(novel) > MAX_NOVEL_CHARS:
        novel = novel[:MAX_NOVEL_CHARS].rstrip() + " …"
    last: Exception | None = None
    for _ in range(2):
        raw = chat_json(SYSTEM.format(seed=seed, nat=nat), USER.format(seed=seed, title=title or "(제목 없음)", novel=novel),
                        model=STORY_MODEL, temperature=1.0, max_tokens=2500, timeout=180.0)
        try:
            story = check(raw if isinstance(raw, dict) else {})
            break
        except ValueError as err:
            last = err
    else:
        raise RuntimeError(f"대본을 받지 못했습니다: {last}")
    story.update(nationality=nat, seed=seed, model=STORY_MODEL)
    return story


def clip_prompt(story: dict[str, Any], i: int) -> str:
    """팀 README 의 프롬프트 공식. 외모 묘사는 장면마다 한 글자도 바꾸지 않고 반복해야 같은 얼굴이 나온다."""
    sc = story["scenes"][i]
    return (f"{sc['visual'].rstrip('.')}. The protagonist: {story['protagonist']}. "
            f"The other character: {story['antagonist']}. "
            "Photorealistic live-action, cinematic soap-opera lighting, dramatic. No on-screen text, no subtitles.")


def music_prompt(story: dict[str, Any]) -> str:
    return (f"{story['mood'].rstrip('.')}. Instrumental soundtrack for a 20-second soap-opera teaser, "
            "no vocals, no lyrics, strong hook in the first second, dramatic hit near the end.")
