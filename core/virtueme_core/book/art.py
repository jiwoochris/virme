"""표지 그림과 본문 삽화.

키가 있으면 OpenRouter 이미지 모델(기본 `google/gemini-3.1-flash-image-preview`, 1K, 장당 약 $0.068)로 그린다.
- 표지 그림에는 브리프가 고른 참고 표지 가운데 그림 양식이 같은 것 1~3장을 512px 로 줄여 스타일 참고로 넘긴다.
- 본문 삽화에는 방금 그린 표지 그림을 참고로 넘겨 표지와 같은 그림체로 맞춘다.
- 그림에는 글자를 넣지 않게 지시하고, 받은 그림은 비전 모델(gemini-2.5-flash)이 글자가 있는지 한 번 더 본다.
  글자가 보이면 두 번까지 다시 그리고, 그래도 있으면 아래 오프라인 그림으로 바꾼다.

키가 없거나 실패하면 팔레트와 정서 곡선으로 절차적 그림을 만든다 (`procedural`). 시드는 브리프 번호로 정하므로
같은 브리프면 같은 그림이 나온다. 정서 곡선을 겹친 물결로 그리므로, 이 그림도 기록에서 나온 그림이다.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..config import LLM_MODEL, openrouter_key
from ..llm import LLMUnavailable
from . import net
from .collect import refs_dir
from .features import rgb_of

IMAGE_MODEL = os.environ.get("VIRTUEME_BOOK_IMAGE_MODEL", "google/gemini-3.1-flash-image-preview")
CHECK_MODEL = os.environ.get("VIRTUEME_BOOK_VISION_MODEL", LLM_MODEL)
COVER_SIZE = (1024, 1536)
ILLUS_SIZE = (1536, 1024)

TEXT_CHECK_SYSTEM = (
    "너는 그림 속 글자를 찾는다. 사람이 읽을 수 있는 글자(어느 언어든 문자·숫자·낱말·서명)가 하나라도 그려져 있으면 "
    "has_text 를 true 로 하라. 글자가 없는 로고 모양(줄무늬·곡선 마크), 무늬, 읽을 수 없는 흐린 질감은 글자가 아니다. "
    'JSON 만 답하라: {"has_text": true|false, "where": "어디에 무슨 글자"}'
)


# ---------------- 절차적 그림 (오프라인) ----------------
def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def procedural(brief: dict[str, Any], size: tuple[int, int], kind: str = "cover") -> Image.Image:
    """팔레트 + 정서 곡선 그림. kind: cover | illustration."""
    seed = int(hashlib.sha1(f"{brief.get('brief_id')}|{kind}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    pal = [rgb_of(h) for h in brief["palette"]["colors"]]
    bg = rgb_of(brief["palette"]["background"])
    accent = rgb_of(brief["palette"]["accent"])
    w, h = size
    arch = brief.get("archetype") if kind == "cover" else "illustration"
    # 바탕: 위에서 아래로 바탕색 → 두 번째 색
    top, bottom = bg, _mix(bg, pal[1] if len(pal) > 1 else accent, 0.55)
    if kind == "illustration":
        top, bottom = (246, 242, 234), _mix((246, 242, 234), bg, 0.35)
    grad = np.linspace(0, 1, h)[:, None, None]
    arr = (np.array(top)[None, None] * (1 - grad) + np.array(bottom)[None, None] * grad) * np.ones((h, w, 3))
    img = Image.fromarray(arr.astype(np.uint8))
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    curve = brief.get("emotion_curve") or [0.5] * 8
    if arch == "pattern":
        step = w // 7
        for yi, y in enumerate(range(-step // 2, h + step, step)):
            for xi, x in enumerate(range(-step // 2, w + step, step)):
                c = pal[(xi + yi) % len(pal)]
                r = step * (0.18 + 0.22 * curve[(xi + yi * 3) % len(curve)])
                ox = step / 2 if yi % 2 else 0
                d.ellipse([x + ox - r, y - r, x + ox + r, y + r], fill=(*c, 230))
                d.ellipse([x + ox - r * 0.35, y - r * 0.35, x + ox + r * 0.35, y + r * 0.35], fill=(*bg, 255))
    elif arch == "centered_motif":
        cx, cy, R = w / 2, h * 0.5, w * 0.2
        for i in range(7, 0, -1):
            rr = R * (0.4 + 0.1 * i) * (0.9 + 0.2 * curve[i * 3 % len(curve)])
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=(*accent, 120 + 15 * i), width=max(2, w // 300))
        d.ellipse([cx - R * 0.42, cy - R * 0.42, cx + R * 0.42, cy + R * 0.42], fill=(*accent, 255))
    else:
        # 정서 곡선을 높이를 바꿔 겹친 물결. 아래 물결일수록 진하다
        bands = 6
        for bi in range(bands):
            c = pal[(bi + 1) % len(pal)] if bi < bands - 1 else accent
            base_y = h * (0.42 + 0.1 * bi)
            amp = h * (0.12 - 0.012 * bi)
            phase = rng.uniform(0, 0.25)
            pts = []
            for i in range(0, w + 8, 8):
                t = (i / w + phase) % 1.0
                v = float(np.interp(t * (len(curve) - 1), np.arange(len(curve)), curve))
                pts.append((i, base_y - (v - 0.5) * 2 * amp + math.sin(i / w * math.pi * 2 + bi) * h * 0.012))
            d.polygon(pts + [(w, h), (0, h)], fill=(*c, 150 + 15 * bi))
        if kind == "cover":
            r = w * 0.09
            cx, cy = w * (0.68 + rng.uniform(-0.1, 0.1)), h * 0.3
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*_mix(accent, (255, 255, 255), 0.35), 235))
    layer = layer.filter(ImageFilter.GaussianBlur(radius=max(1, w // 400)))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    noise = rng.normal(0, 7, (h, w, 1))
    arr = np.clip(np.asarray(img, dtype=float) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


# ---------------- AI 그림 ----------------
def _ref_images(brief: dict[str, Any], n: int = 3) -> tuple[list[str], list[str]]:
    root = refs_dir()
    refs = brief.get("references") or []
    same = [r for r in refs if (r.get("label") or {}).get("art_style") == brief.get("art_style")]
    picked = (same + [r for r in refs if r not in same])[:n]
    urls, ids = [], []
    for r in picked:
        p = root / "img" / f"{r['id']}.jpg"
        if p.exists():
            urls.append(net.image_data_url(p, max_side=512, quality=80))
            ids.append(r["id"])
    return urls, ids


def has_text(img_bytes: bytes, outbound: Any) -> tuple[bool, str, float]:
    """그림 속 글자 검사. 반환: (글자 있음, 어디, 비용)."""
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    im.thumbnail((768, 768))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    import base64

    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    data, usage = net.vision_json(TEXT_CHECK_SYSTEM, "이 그림에 글자가 있는가?", [url], CHECK_MODEL,
                                  "그림 속 글자 검사", outbound, image_note="생성한 그림", max_tokens=300)
    return bool(data.get("has_text")), str(data.get("where") or ""), float(usage.get("cost") or 0)


def make_art(brief: dict[str, Any], out_dir: Path, profile: str | None = None, offline: bool = False,
             fresh: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    """cover_art.png 와 illustration.png 를 만든다. 반환: 무엇으로 만들었는지와 비용."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"model": None, "cost_usd": 0.0, "cover": None, "illustration": None, "text_checks": []}
    use_ai = not offline and openrouter_key() is not None
    outbound = net.outbound_for(profile) if use_ai else None

    cache_path = out_dir / "art_cache.json"
    cache = json.loads(cache_path.read_text("utf-8")) if cache_path.exists() and not fresh else {}

    def gen(prompt: str, refs: list[str], ref_ids: list[str], aspect: str, name: str) -> bool:
        assert outbound is not None
        key = hashlib.sha1(f"{IMAGE_MODEL}|{aspect}|{prompt}|{ref_ids}".encode()).hexdigest()
        if cache.get(name) == key and (out_dir / f"{name}.png").exists():
            report["text_checks"].append({"image": name, "cached": True})
            return True  # 같은 프롬프트로 이미 그린 AI 그림은 다시 사지 않는다
        p = prompt
        for attempt in range(3):
            try:
                data, usage = net.generate_image(p, refs, IMAGE_MODEL, aspect, f"책 {name} 그림 생성", outbound,
                                                 image_note=", ".join(ref_ids) or "없음")
            except (LLMUnavailable, RuntimeError) as err:
                log(f"  {name} 그림 실패: {err}")
                return False
            report["cost_usd"] += float(usage.get("cost") or 0)
            try:
                found, where, c = has_text(data, outbound)
                report["cost_usd"] += c
            except (LLMUnavailable, RuntimeError) as err:
                found, where = False, f"검사 실패: {err}"
            report["text_checks"].append({"image": name, "attempt": attempt + 1, "has_text": found, "where": where})
            if not found:
                Image.open(io.BytesIO(data)).convert("RGB").save(out_dir / f"{name}.png")
                cache[name] = key
                cache_path.write_text(json.dumps(cache), "utf-8")
                return True
            log(f"  {name} 그림에 글자가 있음({where}), 다시 그림")
            p = prompt + " IMPORTANT: the previous attempt contained lettering. Remove every letter and glyph."
        return False

    arch = brief.get("archetype")
    if brief.get("art_prompt"):
        aspect = "2:3" if arch in ("full_bleed", "pattern") else "1:1"
        ok = False
        if use_ai:
            refs, ids = _ref_images(brief)
            ok = gen(brief["art_prompt"], refs, ids, aspect, "cover_art")
            report["cover_refs"] = ids
        if ok:
            report["cover"] = "ai"
            report["model"] = IMAGE_MODEL
        else:
            size = COVER_SIZE if aspect == "2:3" else (1200, 1200)
            procedural(brief, size, "cover").save(out_dir / "cover_art.png")
            cache.pop("cover_art", None)
            report["cover"] = "procedural"
    if brief.get("illustration_prompt"):
        ok = False
        if use_ai:
            cover = out_dir / "cover_art.png"
            if report["cover"] == "ai" and cover.exists():
                refs, ids = [net.image_data_url(cover, 512)], ["cover_art"]
            else:
                refs, ids = _ref_images(brief, 1)
            ok = gen(brief["illustration_prompt"] + " Match the rendering style of the attached image.", refs, ids,
                     "3:2", "illustration")
        if ok:
            report["illustration"] = "ai"
            report["model"] = IMAGE_MODEL
        else:
            procedural(brief, ILLUS_SIZE, "illustration").save(out_dir / "illustration.png")
            cache.pop("illustration", None)
            report["illustration"] = "procedural"
    if use_ai or cache_path.exists():
        cache_path.write_text(json.dumps(cache), "utf-8")
    report["cost_usd"] = round(report["cost_usd"], 4)
    return report
