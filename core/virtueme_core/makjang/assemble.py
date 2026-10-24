"""막장극장 최종 영상 조립 (팀 `2_assemble.py` v3 규격).

타이틀 카드 1.3초 + 장면 5컷(3.2/3.0/3.0/3.0/3.6초, 한/영 자막) + 엔딩 카드 1.4초 + BGM = 약 18.5초, 1280×720, 24fps.

팀 원본은 ffmpeg drawtext 로 글자를 그렸다. drawtext 가 빠진 ffmpeg 빌드가 있어서(이 맥의 Homebrew ffmpeg 가 그렇다)
글자는 PIL 로 투명 PNG 에 그리고 overlay 로 얹는다. 글꼴·크기·색·위치·외곽선·그림자 값은 원본 그대로다.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 24
BG = (0x0B, 0x0C, 0x12)
SCENE_DUR = [3.2, 3.0, 3.0, 3.0, 3.6]  # 훅은 길게, 반전은 가장 길게
TITLE_DUR, END_DUR = 1.3, 1.4
END_LINES = ("나의 기록이, 나를 기억한다", "VIRTUAL ME  ·  STORYSEED × RUNWAY")

FONTS = Path(__file__).resolve().parent / "fonts"


def total_seconds(n_scenes: int = 5) -> float:
    return round(TITLE_DUR + sum(SCENE_DUR[:n_scenes]) + END_DUR, 2)


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / ("Pretendard-Bold.otf" if bold else "Pretendard-Medium.otf")), size)


def _text(d: ImageDraw.ImageDraw, xy: tuple[float, float], s: str, font: ImageFont.FreeTypeFont,
          fill: tuple[int, ...], anchor: str, outline: tuple[int, int, int, int] | None = None) -> None:
    """drawtext 의 borderw=2 + shadowy=2(black@0.5) 를 흉내 낸다. 그림자 → 외곽선+글자 순으로 그린다."""
    if outline:
        d.text((xy[0], xy[1] + 2), s, font=font, fill=(0, 0, 0, 128), anchor=anchor, stroke_width=2,
               stroke_fill=(0, 0, 0, 128))
        d.text(xy, s, font=font, fill=fill, anchor=anchor, stroke_width=2, stroke_fill=outline)
    else:
        d.text(xy, s, font=font, fill=fill, anchor=anchor)


def _fit(s: str, bold: bool, size: int, max_w: int = W - 80) -> ImageFont.FreeTypeFont:
    """한 줄이 화면 폭을 넘으면 글자 크기를 줄인다 (원본은 넘치면 잘렸다)."""
    while size > 12:
        f = _font(bold, size)
        if f.getlength(s) <= max_w:
            return f
        size -= 1
    return _font(bold, size)


def card_png(path: Path, top: str, bottom: str, kind: str) -> Path:
    """타이틀(kind=title)·엔딩(kind=end) 카드. drawtext 의 (h-text_h)/2±k 는 글자 상자 가운데가 h/2±k 라는 뜻이다."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    if kind == "title":
        _text(d, (W / 2, H / 2 - 34), top, _fit(top, True, 76), (255, 255, 255), "mm")
        _text(d, (W / 2, H / 2 + 58), bottom.upper(), _fit(bottom.upper(), False, 22), (0xFF, 0x6E, 0xB8), "mm")
    else:
        _text(d, (W / 2, H / 2 - 24), top, _fit(top, True, 38), (255, 255, 255), "mm")
        _text(d, (W / 2, H / 2 + 42), bottom, _fit(bottom, False, 18), (0x9B, 0xA0, 0xB0), "mm")
    im.save(path)
    return path


def caption_png(path: Path, kr: str, en: str) -> Path:
    """장면 자막: 한국어 Bold 28 (위 y=h-148) + 영어 Medium 18 (아래 y=h-106), 박스 없이 외곽선+그림자."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    _text(d, (W / 2, H - 148), kr, _fit(kr, True, 28), (255, 255, 255, 255), "mt", outline=(0, 0, 0, 217))
    if en:
        _text(d, (W / 2, H - 106), en, _fit(en, False, 18), (0xED, 0xEF, 0xF4, 255), "mt", outline=(0, 0, 0, 204))
    im.save(path)
    return path


def _ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg 를 찾을 수 없습니다")
    return ff


def _run(args: list[str], cwd: Path) -> None:
    r = subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", *args], capture_output=True, text=True, cwd=str(cwd))
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 실패: " + r.stderr[-800:])


ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def assemble(story: dict[str, Any], clips: list[Path], bgm: Path | None, out: Path) -> dict[str, Any]:
    """대본 + 클립 5개 + BGM → 완성 mp4. 반환: {seconds, bytes}."""
    scenes = story["scenes"]
    if len(clips) != len(scenes):
        raise ValueError("장면 수와 클립 수가 다릅니다")
    work = Path(tempfile.mkdtemp(prefix="makjang_"))
    try:
        segs: list[Path] = []
        card_png(work / "title.png", story["title"], story.get("title_en") or "", "title")
        _run(["-loop", "1", "-i", "title.png", "-t", str(TITLE_DUR),
              "-vf", f"fade=t=in:st=0:d=0.2,fade=t=out:st={TITLE_DUR - 0.3:.2f}:d=0.3", *ENC, "seg_00.mp4"], work)
        segs.append(work / "seg_00.mp4")
        for i, (sc, clip) in enumerate(zip(scenes, clips), 1):
            d = SCENE_DUR[i - 1]
            caption_png(work / f"cap_{i}.png", sc["kr"], sc.get("en") or "")
            # 클립 크기가 모델마다 달라도 1280×720 을 꽉 채우게 맞춘다
            fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1[v];"
                  f"[v][1:v]overlay=0:0,fade=t=in:st=0:d=0.18,fade=t=out:st={d - 0.18:.2f}:d=0.18[o]")
            _run(["-i", str(Path(clip).resolve()), "-loop", "1", "-i", f"cap_{i}.png", "-t", str(d),
                  "-filter_complex", fc, "-map", "[o]", "-an", *ENC, f"seg_{i:02d}.mp4"], work)
            segs.append(work / f"seg_{i:02d}.mp4")
        card_png(work / "end.png", *END_LINES, "end")
        _run(["-loop", "1", "-i", "end.png", "-t", str(END_DUR),
              "-vf", f"fade=t=in:st=0:d=0.25,fade=t=out:st={END_DUR - 0.35:.2f}:d=0.35", *ENC, "seg_99.mp4"], work)
        segs.append(work / "seg_99.mp4")

        total = total_seconds(len(scenes))
        (work / "list.txt").write_text("\n".join(f"file '{s.name}'" for s in segs), encoding="utf-8")
        _run(["-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "silent.mp4"], work)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".part.mp4")
        if bgm and Path(bgm).exists():
            _run(["-i", "silent.mp4", "-stream_loop", "-1", "-i", str(Path(bgm).resolve()), "-filter_complex",
                  f"[1:a]volume=0.9,afade=t=in:st=0:d=0.6,afade=t=out:st={total - 1.2:.2f}:d=1.2[aa];[aa]apad[a]",
                  "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                  "-movflags", "+faststart", str(tmp)], work)
        else:
            _run(["-i", "silent.mp4", "-c", "copy", "-movflags", "+faststart", str(tmp)], work)
        tmp.replace(out)
        return {"seconds": total, "bytes": out.stat().st_size}
    finally:
        shutil.rmtree(work, ignore_errors=True)
