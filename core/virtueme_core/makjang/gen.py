"""막장극장 클립·BGM 생성 (OpenRouter).

팀은 Runway 웹에서 Gen-4.5 클립 5개와 Seed Audio BGM 을 손으로 만들어 내려받았다. 같은 Gen-4.5 가 OpenRouter 의
영상 API(`/api/v1/videos`, 제출 → 폴링 → 내려받기)에 있어서 그대로 자동화했다. BGM 은 OpenRouter 의 Lyria 3 클립
(30초, chat completions 의 audio 출력, stream 필수)으로 만든다.

보내는 글은 전부 `outbound.Outbound.send` 로 실명 검사를 받고 프로필의 전송 로그에 남는다.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from ..config import OPENROUTER_URL, openrouter_key
from ..llm import LLMUnavailable
from ..outbound import Outbound

VIDEOS_URL = "https://openrouter.ai/api/v1/videos"
# 팀 샘플과 같은 모델. 16:9·720p·2~10초, 초당 12센트 (2026-10-24 /videos/models).
VIDEO_MODEL = os.environ.get("VIRTUEME_MAKJANG_VIDEO_MODEL", "runway/gen-4.5")
# 첫 모델이 거절(콘텐츠 검사 등)하거나 실패하면 같은 프롬프트로 한 번 더 시도할 모델. 초당 8.4센트.
VIDEO_FALLBACK = os.environ.get("VIRTUEME_MAKJANG_VIDEO_FALLBACK", "kwaivgi/kling-v3.0-std")
# 장면은 3.0~3.6초만 쓰므로 4초면 충분하다 (5초보다 클립당 12센트 싸다).
CLIP_SECONDS = 4
MUSIC_MODEL = os.environ.get("VIRTUEME_MAKJANG_MUSIC_MODEL", "google/lyria-3-clip-preview")

PRICE_PER_SEC = {"runway/gen-4.5": 0.12, "kwaivgi/kling-v3.0-std": 0.084}

Log = Callable[[str], None]


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "HTTP-Referer": "https://github.com/DECK6/virme", "X-Title": "Virtueme makjang"}


def _key() -> str:
    key = openrouter_key()
    if not key:
        raise LLMUnavailable("OPENROUTER_API_KEY 가 없습니다")
    return key


def _cost(job: dict[str, Any], model: str, seconds: int) -> tuple[float, bool]:
    """(비용, 추정인지). 응답에 청구액이 있으면 그 값, 없으면 단가표로 추정한다."""
    usage = job.get("usage") or {}
    for v in (usage.get("cost"), job.get("cost")):
        try:
            if v is not None:
                return float(v), False
        except (TypeError, ValueError):
            pass
    return round(PRICE_PER_SEC.get(model, 0.12) * seconds, 4), True


def generate_clip(prompt: str, out: Path, outbound: Outbound, purpose: str, model: str | None = None,
                  seconds: int = CLIP_SECONDS, seed: int | None = None, timeout: float = 600.0,
                  log: Log = lambda m: None) -> dict[str, Any]:
    """글 → 16:9 720p 클립 하나. 반환: {model, cost_usd, cost_estimated, seconds, job_id}."""
    key = _key()
    model = model or VIDEO_MODEL
    _, prompt = outbound.send("core", "", prompt, model=model, purpose=purpose)
    body: dict[str, Any] = {"model": model, "prompt": prompt, "resolution": "720p", "duration": seconds,
                            "aspect_ratio": "16:9"}
    if seed is not None and model.startswith("runway/"):
        body["seed"] = seed
    res = httpx.post(VIDEOS_URL, json=body, headers=_headers(key), timeout=60)
    if res.status_code >= 400:
        raise RuntimeError(f"{model} 제출 실패 HTTP {res.status_code}: {res.text[:300]}")
    job = res.json()
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError(f"{model} 작업 번호가 없습니다: {str(job)[:300]}")
    started = time.time()
    while True:
        time.sleep(5)
        try:
            r = httpx.get(f"{VIDEOS_URL}/{job_id}", headers=_headers(key), timeout=30)
            job = r.json()
        except (httpx.HTTPError, ValueError):
            if time.time() - started > timeout:
                raise
            continue
        state = str(job.get("status") or "")
        if state == "completed":
            break
        if state in ("failed", "error", "cancelled", "canceled"):
            raise RuntimeError(f"{model} 생성 실패: {str(job.get('error') or job)[:300]}")
        if time.time() - started > timeout:
            raise RuntimeError(f"{model} {int(timeout)}초 안에 끝나지 않았습니다")
    with httpx.stream("GET", f"{VIDEOS_URL}/{job_id}/content", params={"index": 0}, headers=_headers(key),
                      timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    cost, est = _cost(job, model, seconds)
    log(f"{out.name} 완료 · {model.split('/')[-1]} · {round(time.time() - started)}초")
    return {"model": model, "cost_usd": cost, "cost_estimated": est, "seconds": seconds, "job_id": job_id}


def generate_clip_any(prompt: str, out: Path, outbound: Outbound, purpose: str, seed: int | None = None,
                      log: Log = lambda m: None) -> dict[str, Any]:
    """첫 모델 → 실패하면 대체 모델. 둘 다 안 되면 마지막 오류를 던진다."""
    errors: list[str] = []
    for model in dict.fromkeys([VIDEO_MODEL, VIDEO_FALLBACK]):
        try:
            got = generate_clip(prompt, out, outbound, purpose, model=model, seed=seed, log=log)
            if errors:
                got["fallback_from"] = errors
            return got
        except LLMUnavailable:
            raise
        except Exception as err:  # 거절·시간 초과 → 다음 모델
            errors.append(f"{model}: {str(err)[:200]}")
            log(f"{out.name} {model.split('/')[-1]} 실패, 다른 모델로 다시 시도")
    raise RuntimeError("클립을 만들지 못했습니다 — " + " / ".join(errors))


def _stream_audio(body: dict[str, Any], key: str, timeout: float) -> tuple[bytes, str, dict[str, Any]]:
    """오디오 출력은 stream: true 로만 받는다 (2026-10-24 실측: 아니면 HTTP 400 "Audio output requires stream: true").
    SSE 조각의 delta.audio.data(base64)를 이어 붙이고, 마지막 조각의 usage 를 돌려준다."""
    chunks: list[str] = []
    fmt = str((body.get("audio") or {}).get("format") or "mp3")
    usage: dict[str, Any] = {}
    with httpx.stream("POST", OPENROUTER_URL, json=body, headers=_headers(key), timeout=timeout) as res:
        if res.status_code >= 400:
            raise RuntimeError(f"HTTP {res.status_code}: {res.read()[:300]!r}")
        for line in res.iter_lines():
            if not line.startswith("data:"):
                continue  # ": OPENROUTER PROCESSING" 같은 유지 신호
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                d = json.loads(payload)
            except ValueError:
                continue
            if d.get("error"):
                raise RuntimeError(str(d["error"])[:300])
            usage = d.get("usage") or usage
            for ch in d.get("choices") or []:
                audio = (ch.get("delta") or {}).get("audio") or {}
                if audio.get("data"):
                    chunks.append(audio["data"])
                if audio.get("format"):
                    fmt = str(audio["format"])
    if not chunks:
        raise RuntimeError("응답에 오디오가 없습니다")
    return base64.b64decode("".join(chunks)), fmt, usage


def generate_music(prompt: str, out_stem: Path, outbound: Outbound, log: Log = lambda m: None) -> dict[str, Any]:
    """분위기 문장 → 30초 BGM(mp3). 반환: {path, model, cost_usd}."""
    key = _key()
    _, prompt = outbound.send("core", "", prompt, model=MUSIC_MODEL, purpose="막장극장 BGM 생성")
    body = {"model": MUSIC_MODEL, "stream": True, "modalities": ["text", "audio"], "audio": {"format": "mp3"},
            "messages": [{"role": "user", "content": prompt}]}
    last: Exception | None = None
    for attempt in range(3):
        try:
            raw, fmt, usage = _stream_audio(body, key, timeout=240)
            path = out_stem.with_suffix("." + fmt.replace("mpeg", "mp3"))
            path.write_bytes(raw)
            log(f"BGM 완료 · {MUSIC_MODEL.split('/')[-1]}")
            return {"path": str(path), "model": MUSIC_MODEL, "cost_usd": float(usage.get("cost") or 0.04)}
        except (httpx.HTTPError, RuntimeError, ValueError) as err:
            last = err
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"BGM 을 만들지 못했습니다: {last}")
