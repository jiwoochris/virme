"""노벨 한 편 → 막장극장 쇼츠 한 편 (대본 → 클립 5개·BGM 병렬 생성 → 조립).

결과는 `$VIRTUEME_DATA/makjang/<프로필>/<영상 번호>/` 에 남는다.
- story.json: 대본 (팀 story JSON 형식 + nationality·seed·model)
- scene1.mp4 ~ scene5.mp4, bgm.<형식>: 생성물. 다시 돌리면 있는 파일은 건너뛰어 돈을 두 번 쓰지 않는다
- makjang.mp4: 완성본
- report.json: 모델·비용·걸린 시간
같은 소설로 「다른 에피소드」를 만들면 take 가 올라가고 영상 번호가 바뀐다 (국적·대본이 새로 나온다).
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from ..book.net import outbound_for
from ..config import DATA_DIR
from . import assemble, gen, story as story_mod

Log = Callable[[str], None]
FINAL = "makjang.mp4"


def makjang_dir(profile: str) -> Path:
    safe = "".join(ch for ch in profile if ch.isalnum() or ch in "-_") or "default"
    return DATA_DIR / "makjang" / safe


def video_id(profile: str, keyword: str, novel: str, title: str | None, take: int) -> str:
    return hashlib.sha1(repr((profile, keyword, novel.strip(), title or "", take)).encode()).hexdigest()[:10]


def _bgm(d: Path) -> Path | None:
    for p in sorted(d.glob("bgm.*")):
        if p.suffix in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"):
            return p
    return None


def make_makjang(profile: str, keyword: str, novel: str, title: str | None = None, take: int = 0,
                 log: Log = lambda m: None, story: dict[str, Any] | None = None) -> dict[str, Any]:
    """반환: report.json 내용 (+ video_id). story 를 주면 대본 생성을 건너뛴다 (팀 샘플 대본 재현용)."""
    vid = video_id(profile, keyword, novel, title, take)
    d = makjang_dir(profile) / vid
    d.mkdir(parents=True, exist_ok=True)
    started = time.time()
    outbound = outbound_for(profile)
    report: dict[str, Any] = {"video_id": vid, "keyword": keyword, "take": take, "clips": [], "cost_usd": 0.0}

    sp = d / "story.json"
    if story is not None:
        story = story_mod.check(story) | {k: story[k] for k in ("nationality", "seed", "model") if k in story}
        sp.write_text(json.dumps(story, ensure_ascii=False, indent=1), "utf-8")
    elif sp.exists():
        story = json.loads(sp.read_text("utf-8"))
    else:
        log("대본 쓰는 중")
        story = story_mod.write_story(novel, keyword, title, rng=random.Random(vid))
        sp.write_text(json.dumps(story, ensure_ascii=False, indent=1), "utf-8")
    assert story is not None
    log(f"대본 「{story['title']}」 · {story.get('nationality', '')} · 클립 5개와 BGM 만드는 중")

    seed = int(vid[:6], 16)
    clips = [d / f"scene{i + 1}.mp4" for i in range(len(story["scenes"]))]

    def clip_job(i: int) -> dict[str, Any]:
        out = clips[i]
        if out.exists() and out.stat().st_size > 10_000:
            return {"name": out.name, "cached": True}
        tmp = out.with_suffix(".part.mp4")
        got = gen.generate_clip_any(story_mod.clip_prompt(story, i), tmp, outbound, f"막장극장 장면 {i + 1} 영상 생성",
                                    seed=seed, log=log)
        tmp.replace(out)
        return {"name": out.name, **got}

    def music_job() -> dict[str, Any]:
        if _bgm(d):
            return {"cached": True}
        return gen.generate_music(story_mod.music_prompt(story), d / "bgm", outbound, log=log)

    with ThreadPoolExecutor(max_workers=6) as ex:
        fut_clips = [ex.submit(clip_job, i) for i in range(len(clips))]
        fut_music = ex.submit(music_job)
        errors: list[str] = []
        for f in fut_clips:
            try:
                report["clips"].append(f.result())
            except Exception as err:
                errors.append(str(err)[:300])
        try:
            report["music"] = fut_music.result()
        except Exception as err:  # BGM 이 없어도 영상은 만든다
            report["music"] = {"error": str(err)[:300]}
            log("BGM 없이 조립합니다")
    if errors:
        _write_report(d, report, started)
        raise RuntimeError(f"클립 {len(errors)}개를 만들지 못했습니다. 다시 누르면 만든 클립은 그대로 두고 나머지만 다시 만듭니다. {errors[0]}")

    log("자막을 얹어 이어 붙이는 중")
    got = assemble.assemble(story, clips, _bgm(d), d / FINAL)
    report.update(got)
    _write_report(d, report, started)
    return report


def _write_report(d: Path, report: dict[str, Any], started: float) -> None:
    spent = [c.get("cost_usd") or 0 for c in report.get("clips", [])] + [(report.get("music") or {}).get("cost_usd") or 0]
    # 이전 실행에서 이미 낸 비용은 이전 report 에 있다. 다시 돌린 회차는 새로 쓴 몫만 더한다.
    prev = {}
    if (d / "report.json").exists():
        try:
            prev = json.loads((d / "report.json").read_text("utf-8"))
        except ValueError:
            prev = {}
    report["cost_usd"] = round(float(prev.get("cost_usd") or 0) + sum(spent), 4)
    report["cost_estimated"] = any(c.get("cost_estimated") for c in report.get("clips", []))
    report["elapsed"] = round(time.time() - started, 1)
    report["models"] = sorted({c["model"] for c in report.get("clips", []) if c.get("model")})
    (d / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
