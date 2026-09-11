"""OpenRouter 호출. 키가 없으면 `LLMUnavailable` 을 던지고, 호출하는 쪽이 로컬 대체 경로로 간다.

여기로 들어오는 텍스트는 이미 privacy.Masker 를 거친 것이어야 한다.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from .config import LLM_MODEL, OPENROUTER_URL, openrouter_key


class LLMUnavailable(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i >= 0 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"JSON 을 찾지 못했습니다: {text[:200]!r}")


def chat_json(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 2000,
    retries: int = 2,
    timeout: float = 90.0,
) -> Any:
    key = openrouter_key()
    if not key:
        raise LLMUnavailable("OPENROUTER_API_KEY 가 없습니다")
    body = {
        "model": model or LLM_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://github.com/DECK6/virme",
        "X-Title": "Virtueme core",
    }
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            res = httpx.post(OPENROUTER_URL, json=body, headers=headers, timeout=timeout)
            if res.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(f"HTTP {res.status_code}", request=res.request, response=res)
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"] or ""
            return _extract_json(content)
        except (httpx.HTTPError, ValueError, KeyError) as err:
            last = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM 호출 실패: {last}")
