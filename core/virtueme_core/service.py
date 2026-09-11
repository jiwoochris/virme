"""프로필 하나를 여는 진입점. CLI 와 API 가 같이 쓴다."""
from __future__ import annotations

import threading
from typing import Any

from datetime import date

from . import clusters, entities, narrative, patterns, story
from .config import JUDGE_MODEL, LLM_MODEL, SETTINGS, Settings, openrouter_key, profile_db_path
from .embed import HashEmbedder, TextEmbedder, VisionEmbedder
from .privacy import Masker
from .query import QueryEngine
from .store import Store
from .textproc import top_nouns
from .verify import verify_story


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

    # ---------- 근거 계약 v2: 이야기·대화 ----------
    def story_prompt(self, keyword: str, genre: int | None = None, version: str = "v2", variation: int = 0,
                     include_others: bool | None = None) -> dict[str, Any]:
        result = self.query(keyword, include_others=include_others)
        return {"query": result, "prompt": story.story_prompt(result, genre, version, variation)}

    def _start(self) -> date | None:
        d = self.engine.daily
        return d.dates[0] if d else None

    def verify(self, paragraphs: list[str], evidence_ids: list[str], mode: str = "blur", title: str | None = None,
               extra_counts: list[int] | None = None) -> dict[str, Any]:
        recs = [r for r in (self.store.record(i) for i in evidence_ids) if r is not None]
        rewrite = context = None
        if openrouter_key():
            from .llm import chat_json, chat_text

            # 규칙 검사를 통과한 앵커를 문장째로 한 번 더 판정한다 (날짜만 인용하고 다른 사건을 붙인 경우)
            context = lambda system, text: chat_json(system, text, model=self.judge_model, max_tokens=1500)  # noqa: E731
            if mode == "rewrite":
                rewrite = lambda system, text: chat_text(system, text, model=self.judge_model, temperature=0.4)  # noqa: E731
        return verify_story(paragraphs, recs, mode=mode, title=title, start=self._start(),
                            extra_counts=set(extra_counts or []), rewrite_fn=rewrite, context_fn=context)

    def profile_brief(self) -> dict[str, Any]:
        """대화 모드가 늘 알고 있을 요약."""
        eng = self.engine
        d = eng.daily
        own = [r for r in eng.records if r.is_self]
        hp = patterns.hour_profile(own)
        return {
            "date_range": [patterns.ko_date(d.dates[0]), patterns.ko_date(d.dates[-1])] if d else None,
            "records": len(eng.records),
            "time_center": hp["center"],
            "top_terms": [t for t, _ in top_nouns((r.text for r in own if r.text), k=12)],
            "turning_points": [{"date": patterns.ko_date(c["date"]), "direction": c["direction"]}
                               for c in (patterns.change_points(d.valence, d.dates) if d else [])],
        }

    def talk_context(self, message: str, history: list[str] | None = None, version: str = "v2",
                     mood: int | None = None) -> dict[str, Any]:
        eng = self.engine
        masker = Masker.from_store(self.store)
        query_text = " ".join([*(history or [])[-2:], message])
        near = eng.retrieve(query_text, k=16)
        recent = [r for r in reversed(eng.records) if r.is_self and r.text][:6]
        seen, recs = set(), []
        for r in near + recent:
            if r.id not in seen:
                seen.add(r.id)
                recs.append(r)
        recs.sort(key=lambda r: r.ts)
        views = [narrative.record_view(r, masker) for r in recs]
        people = narrative.people_in(self.store, views)
        prompt = story.avatar_prompt(self.profile_brief(), views, people, version, mood)
        return {**prompt, "records": views, "people": people}

    def greeting(self) -> str:
        return story.greeting(self.profile_brief())

    # ---------- 묶음·그래프·간극 ----------
    def clusters_overview(self) -> dict[str, Any]:
        _, info = self.store.clusters("topic")
        return {"topics": clusters.overview(self.store), "other": info.get("t-1", {}).get("size", 0),
                "other_label": clusters.OTHER_LABEL}

    def graph(self, keyword: str | None = None, limit: int = 40) -> dict[str, Any]:
        eng = self.engine
        ids = None
        if keyword:
            res = eng.query(keyword)
            ids = {s.record.id for s in res.strong + res.weak}
        return {**entities.graph(self.store, eng.emotions, ids, limit), "people": self.store.relations()}

    def gaps(self) -> dict[str, Any]:
        # 습관 단정과 그날 일을 가르는 일은 지시를 잘 따르는 판정 모델에 맡긴다
        return {"gaps": entities.find_gaps(self.store, self.engine, model=self.judge_model), "people": self.store.relations()}

    # ---------- 사람이 붙이는 정답 (평가용) ----------
    def label_candidates(self, keyword: str, n: int = 80) -> dict[str, Any]:
        """임베딩 관련도 상위 60건 + 무작위 20건을 섞어서 준다. 판정 모델 결과는 보여 주지 않는다 (편향 방지)."""
        import random

        from .query import expand_keyword

        eng = self.engine
        rel, _, _, _ = eng.scores(keyword, expand_keyword(self.store, keyword, self.llm_model))
        own = [i for i, r in enumerate(eng.records) if r.is_self and (r.text or r.meta.get("auto_tags"))]
        top = sorted(own, key=lambda i: -rel[i])[: int(n * 0.75)]
        rng = random.Random(keyword)
        rest = [i for i in own if i not in set(top)]
        pick = top + rng.sample(rest, min(len(rest), n - len(top)))
        rng.shuffle(pick)
        done = self.store.labels(keyword).get(keyword, {})
        masker = Masker.from_store(self.store)
        items = []
        for i in pick:
            v = narrative.record_view(eng.records[i], masker, chars=400)
            v["label"] = done.get(v["id"])
            items.append(v)
        return {"keyword": keyword, "items": items, "labeled": len(done)}
