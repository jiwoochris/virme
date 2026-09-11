"""프로필 하나를 여는 진입점. CLI 와 API 가 같이 쓴다."""
from __future__ import annotations

import threading
from typing import Any

from . import narrative
from .config import JUDGE_MODEL, LLM_MODEL, SETTINGS, Settings, openrouter_key, profile_db_path
from .embed import HashEmbedder, TextEmbedder, VisionEmbedder
from .privacy import Masker
from .query import QueryEngine
from .store import Store


class Service:
    def __init__(self, profile: str = "default", fake_models: bool = False, settings: Settings = SETTINGS,
                 llm_model: str = LLM_MODEL, judge_model: str = JUDGE_MODEL, store: Store | None = None):
        self.profile = profile
        self.store = store or Store(profile_db_path(profile))
        if fake_models:
            self.text, self.vision = HashEmbedder("hash-text"), HashEmbedder("hash-vision")
        else:
            self.text, self.vision = TextEmbedder(), VisionEmbedder()
        self.settings = settings
        self.llm_model = llm_model
        self.judge_model = judge_model
        self._engine: QueryEngine | None = None
        self._lock = threading.Lock()

    @property
    def engine(self) -> QueryEngine:
        with self._lock:
            if self._engine is None:
                self._engine = QueryEngine(self.store, self.text, self.vision, self.settings, self.llm_model,
                                           self.judge_model)
            return self._engine

    def invalidate(self) -> None:
        with self._lock:
            self._engine = None

    def health(self) -> dict[str, Any]:
        s = self.store.summary()
        return {
            "status": "ready" if s["records"] else "empty",
            "profile": self.profile,
            "records": s["records"],
            "date_range": [s["first"], s["last"]],
            "models": {"text": self.store.vector_model("text"), "vision": self.store.vector_model("vision"),
                       "llm": self.llm_model if openrouter_key() else None,
                       "judge": self.judge_model if openrouter_key() and self.settings.use_judge else None},
            "built": bool(s["vectors"].get("text")),
        }

    def query(self, keyword: str, include_others: bool | None = None) -> dict[str, Any]:
        eng = self.engine
        res = eng.query(keyword, include_others=include_others)
        return narrative.build(eng, res, Masker.from_store(self.store))

    def record(self, rid: str) -> dict[str, Any] | None:
        r = self.store.record(rid)
        return narrative.record_view(r, Masker.from_store(self.store), chars=2000) if r else None
