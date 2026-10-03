"""책 디자인 단계의 OpenRouter 호출 (비전 라벨·그림 생성·그림 속 글자 검사).

`llm._chat` 은 글만 보내므로 이미지를 함께 보내는 호출을 여기 따로 둔다. 모든 요청은 보내기 직전에
`outbound.Outbound.send` 를 거쳐 실명 검사를 받고 `$VIRTUEME_DATA/outbound/<프로필>/<날짜>.jsonl` 에 남는다.
이미지는 로그에 넣지 않고 "[첨부 이미지 N장: …]" 한 줄로 적는다. 참고 표지 라벨처럼 프로필이 없는 요청은
`outbound/_book/` 에 남긴다.
"""
from __future__ import annotations

import base64
import io
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from ..config import DATA_DIR, OPENROUTER_URL, openrouter_key, profile_db_path
from ..llm import LLMUnavailable, _extract_json
from ..outbound import Outbound


class _NoPeople:
    """가명표가 없는 요청(공개 표지 라벨)용. 전화·이메일 모양 검사만 한다."""

    def people(self) -> dict[str, str]:
        return {}


def outbound_for(profile: str | None) -> Outbound:
    if profile:
        db = profile_db_path(profile)
        if db.exists():
            from ..store import Store

            return Outbound(DATA_DIR / "outbound" / profile, Store(db))
        return Outbound(DATA_DIR / "outbound" / profile, _NoPeople())  # type: ignore[arg-type]
    return Outbound(DATA_DIR / "outbound" / "_book", _NoPeople())  # type: ignore[arg-type]


def image_data_url(path: Path | str, max_side: int = 768, quality: int = 85) -> str:
    """로컬 이미지를 줄여 JPEG data URL 로. 원본 해상도는 보내지 않는다."""
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "HTTP-Referer": "https://github.com/DECK6/virme", "X-Title": "Virtueme book"}


def _post(body: dict[str, Any], timeout: float, retries: int) -> dict[str, Any]:
    key = openrouter_key()
    if not key:
        raise LLMUnavailable("OPENROUTER_API_KEY 가 없습니다")
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            res = httpx.post(OPENROUTER_URL, json=body, headers=_headers(key), timeout=timeout)
            if res.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"HTTP {res.status_code}", request=res.request, response=res)
            res.raise_for_status()
            return res.json()
        except (httpx.HTTPError, ValueError, KeyError) as err:
            last = err
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"OpenRouter 호출 실패: {last}")


def _content(text: str, images: list[str]) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}] + [{"type": "image_url", "image_url": {"url": u}} for u in images]


def vision_json(system: str, text: str, images: list[str], model: str, purpose: str,
                outbound: Outbound, image_note: str, max_tokens: int = 4000, timeout: float = 120.0,
                retries: int = 2) -> tuple[Any, dict[str, Any]]:
    """이미지 + 지시문 → JSON. 반환: (파싱한 JSON, usage)."""
    if not openrouter_key():
        raise LLMUnavailable("OPENROUTER_API_KEY 가 없습니다")
    system, logged = outbound.send("core", system, f"{text}\n[첨부 이미지 {len(images)}장: {image_note}]",
                                   model=model, purpose=purpose)
    text = logged.rsplit("\n[첨부 이미지", 1)[0]
    body = {"model": model, "temperature": 0, "max_tokens": max_tokens, "usage": {"include": True},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": _content(text, images)}]}
    last: Exception | None = None
    for _ in range(2):
        data = _post(body, timeout, retries)
        try:
            return _extract_json(data["choices"][0]["message"]["content"] or ""), data.get("usage") or {}
        except (ValueError, KeyError) as err:
            last = err
    raise RuntimeError(f"비전 응답을 읽지 못했습니다: {last}")


def generate_image(prompt: str, refs: list[str], model: str, aspect: str, purpose: str, outbound: Outbound,
                   image_note: str, size: str = "1K", timeout: float = 240.0) -> tuple[bytes, dict[str, Any]]:
    """그림 생성. refs 는 스타일 참고용 data URL. 반환: (PNG/JPEG 바이트, usage)."""
    if not openrouter_key():
        raise LLMUnavailable("OPENROUTER_API_KEY 가 없습니다")
    _, prompt = outbound.send("core", "", f"{prompt}\n[첨부 이미지 {len(refs)}장: {image_note}]", model=model,
                              purpose=purpose)
    prompt = prompt.rsplit("\n[첨부 이미지", 1)[0]
    body = {"model": model, "modalities": ["image", "text"], "usage": {"include": True},
            "image_config": {"aspect_ratio": aspect, "image_size": size},
            "messages": [{"role": "user", "content": _content(prompt, refs)}]}
    last: Exception | None = None
    for _ in range(3):
        data = _post(body, timeout, 1)
        try:
            url = data["choices"][0]["message"]["images"][0]["image_url"]["url"]
            return base64.b64decode(url.split(",", 1)[1]), data.get("usage") or {}
        except (KeyError, IndexError, TypeError, ValueError) as err:
            last = err
    raise RuntimeError(f"그림이 오지 않았습니다: {last}")
