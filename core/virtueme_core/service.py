"""프로필 하나를 여는 진입점. CLI 와 API 가 같이 쓴다."""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
import uuid
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import clusters, columns, entities, importer, llm, music, narrative, patterns, story
from .config import DATA_DIR, JUDGE_MODEL, LLM_MODEL, SETTINGS, Settings, openrouter_key, profile_db_path
from .embed import HashEmbedder, TextEmbedder, VisionEmbedder
from .outbound import Outbound
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
        self._columns: tuple[tuple[int, int], dict[str, Any]] | None = None
        self._lock = threading.Lock()
        # 음악은 로컬 ACE-Step 서버가 만든다. 결과는 프로필별 캐시 폴더에 남아 발표 때 다시 쓴다
        self.music_root = Path(os.environ.get("VIRTUEME_MUSIC_DIR", DATA_DIR / "music")) / profile
        self.acestep = music.AceStepClient(os.environ.get("VIRTUEME_ACESTEP_URL", music.ACESTEP_URL))
        self.upload_root = DATA_DIR / "uploads" / profile
        self._build: dict[str, Any] = {"state": "idle"}
        # 밖으로 나가는 글은 모두 여기서 마지막 검사를 거치고 로그에 남는다 (core 직접 호출과 브라우저에 넘기는 프롬프트)
        self.outbound = Outbound(DATA_DIR / "outbound" / profile, self.store)
        llm.set_outbound(self.outbound)
        # 공유 카드·페이지 (share.py). 사용자가 고른 것만, 다듬은 글로
        from .share import Shares

        self.shares = Shares(profile, self.store, self.outbound)

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
            self._columns = None

    def columns(self) -> dict[str, Any]:
        """소설 재료 컬럼. 기록 수와 정서 채점 수가 그대로면 다시 계산하지 않는다."""
        key = (self.store.count(), len(self.store.emotions()))
        if self._columns is None or self._columns[0] != key:
            self._columns = (key, columns.compute(self.store))
        return self._columns[1]

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
            "data_id": self.data_id() if s["records"] else None,
        }

    def data_id(self) -> str:
        """이 기록 묶음의 식별자. 기록을 모두 지우면 바뀌므로, 브라우저에 저장한 이야기·대화 답이 지운 기록의 것이면 쓰지 않는다."""
        got = self.store.cache_get("data_id")
        if not got:
            got = uuid.uuid4().hex[:12]
            self.store.cache_put("data_id", got)
        return got

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
        cols = self.columns()
        # 배경 사실은 번호 붙은 요약 기록으로 넘어가고, evidence_ids 의 그 자리에는 요약 기록 ID 가 든다 (verify 가 다시 찾는다)
        prompt = story.story_prompt(result, genre, version, variation, columns=columns.note_for_story(cols))
        # 이 프롬프트는 브라우저가 OpenRouter 로 보낸다. 넘기기 전에 마지막 검사를 거치고 로그에 남긴다
        prompt["system"], prompt["user"] = self.outbound.send("browser", prompt["system"], prompt["user"], purpose="이야기 집필")
        return {"query": result, "prompt": prompt}

    # ---------- 책: 이야기 → 표지·본문 ----------
    def write_story(self, keyword: str, genre: int, model: str = "anthropic/claude-sonnet-4.5") -> str:
        """노벨 화면과 같은 프롬프트로 core 가 직접 이야기를 쓴다 (명령줄에서 책을 만들 때). 첫 줄은 TITLE:."""
        from .llm import chat_text

        p = self.story_prompt(keyword, genre)["prompt"]
        raw = chat_text(p["system"], p["user"], model=model, temperature=0.9, max_tokens=3000)
        # 근거 표시는 책 본문에 남기지 않는다. 검사는 노벨 화면에서 한다
        return re.sub(r"</?em[^>]*>", "", raw)

    def make_book(self, keyword: str, story_text: str | None = None, genre: int | None = None, title: str | None = None,
                  author: str = "나", offline: bool = False, log=print) -> dict[str, Any]:
        """genre 가 없으면 소설 재료 컬럼의 장르 추천 1위를 쓴다."""
        from . import book

        if genre is None or genre < 0:
            genre = self.columns()["genre_hint"][0]["index"]
        if not story_text:
            story_text = self.write_story(keyword, genre)
        no = self.query(keyword)["narrative_object"]
        res = book.make_book(genre, story_text, title=title, author=author, narrative_object=no,
                             profile=self.profile, offline=offline, log=log)
        story_file = Path(res["files"]["cover.png"]).with_name("story.txt")
        story_file.write_text(story_text, "utf-8")
        res["files"]["story.txt"] = str(story_file)
        return res

    def _start(self) -> date | None:
        d = self.engine.daily
        return d.dates[0] if d else None

    def verify(self, paragraphs: list[str], evidence_ids: list[str], mode: str = "blur", title: str | None = None,
               extra_counts: list[int] | None = None, kind: str = "story") -> dict[str, Any]:
        # 근거 목록은 취재 노트의 번호 순서다. 기록 ID(`r…`)는 기록으로, 요약 기록 ID(`s…`)는 지금 계산한 컬럼의 요약으로
        # 바꾸고, 찾지 못한 자리는 None 으로 남겨 뒤 번호가 밀리지 않게 한다
        summaries = {s["id"]: s for s in self.columns().get("summaries", [])} if any(
            i.startswith("s") for i in evidence_ids) else {}
        recs = [summaries.get(i) if i.startswith("s") else self.store.record(i) for i in evidence_ids]
        rewrite = context = None
        if openrouter_key():
            from .llm import chat_json, chat_text

            # 규칙 검사를 통과한 앵커를 문장째로 한 번 더 판정한다 (날짜만 인용하고 다른 사건을 붙인 경우, 남의 일을 내 일처럼
            # 쓴 경우). eval/grounding_eval.py (2026-09-12, 이야기 40편·대화 답 40개): 대화는 정밀도·재현율이 규칙만
            # 93.8%·91.6%, 문맥 검사를 더하면 97.1%·81.9%. 이야기는 86.3%·93.8% → 87.4%·83.3%
            context = lambda system, text: chat_json(system, text, model=self.judge_model, max_tokens=2500)  # noqa: E731
            if mode == "rewrite":
                rewrite = lambda system, text: chat_text(system, text, model=self.judge_model, temperature=0.4)  # noqa: E731
        return verify_story(paragraphs, recs, mode=mode, title=title, start=self._start(),
                            extra_counts=set(extra_counts or []), rewrite_fn=rewrite, context_fn=context, kind=kind,
                            people=self.store.relations())

    def profile_brief(self) -> dict[str, Any]:
        """대화 모드가 늘 알고 있을 요약."""
        eng = self.engine
        d = eng.daily
        own = [r for r in eng.records if r.is_self and not r.is_trace]  # 시간대·주요 낱말은 글로 쓴 기록으로
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
        recent = [r for r in reversed(eng.records) if r.is_self and r.text and not r.is_trace][:6]
        seen, recs = set(), []
        for r in near + recent:
            if r.id not in seen:
                seen.add(r.id)
                recs.append(r)
        recs.sort(key=lambda r: r.ts)
        views = [narrative.record_view(r, masker) for r in recs]
        people = narrative.people_in(self.store, views)
        prompt = story.avatar_prompt(self.profile_brief(), views, people, version, mood)
        # 기록은 system 에 들어 있고, 사용자가 친 말은 브라우저가 그대로 붙인다
        prompt["system"], _ = self.outbound.send("browser", prompt["system"], "", purpose="대화 답")
        return {**prompt, "records": views, "people": people}

    def greeting(self) -> str:
        return story.greeting(self.profile_brief())

    # ---------- 묶음·그래프·간극 ----------
    def clusters_overview(self) -> dict[str, Any]:
        _, info = self.store.clusters("topic")
        return {"topics": clusters.overview(self.store), "other": info.get("t-1", {}).get("size", 0),
                "other_label": clusters.OTHER_LABEL}

    def semantic_map(self, keyword: str | None = None) -> dict[str, Any]:
        """의미 지도: 본인 기록의 2차원 좌표, 주제, 키워드 근거 표시. 텍스트는 가린 것 앞부분만 싣는다."""
        m = clusters.semantic_map(self.store)
        assign, info = self.store.clusters("topic")
        want = set(m["ids"])
        recs = {r.id: r for r in self.store.records() if r.id in want}
        masker = Masker.from_store(self.store)
        ev: dict[str, str] = {}
        if keyword:
            res = self.engine.query(keyword)
            ev.update({s.record.id: "weak" for s in res.weak})
            ev.update({s.record.id: "strong" for s in res.strong})
        points = []
        for rid, (x, y) in zip(m["ids"], m["xy"]):
            r = recs.get(rid)
            if not r:
                continue
            text = masker.mask(r.text) if r.text else "사진 · " + ", ".join(t["tag"] for t in r.meta.get("auto_tags", [])[:3])
            points.append({"id": rid, "x": x, "y": y, "topic": assign.get(rid, "t-1"), "date": r.day,
                           "source": narrative.SOURCE_LABEL.get(r.source, r.source),
                           "text": (text or "").replace("\n", " / ")[:60], "ev": ev.get(rid)})
        topics = []
        for c, meta in info.items():
            mine = [p for p in points if p["topic"] == c]
            if c == "t-1" or not mine:
                continue
            topics.append({"id": c, "label": meta["label"], "size": len(mine),
                           "cx": round(sum(p["x"] for p in mine) / len(mine), 4),
                           "cy": round(sum(p["y"] for p in mine) / len(mine), 4)})
        topics.sort(key=lambda t: -t["size"])
        return {"points": points, "topics": topics, "keyword": keyword, "people": self.store.relations()}

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

    # ---------- 웹 화면에서 기록 넣기 ----------
    def new_upload_dir(self, source: str) -> Path:
        d = self.upload_root / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{source}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def import_paths(self, source: str, saved: list[Path], me: list[str] | None = None,
                     since: str | None = None, until: str | None = None) -> dict[str, Any]:
        """올린 파일을 가져온다. 압축 파일은 풀어서, 인스타그램·사진은 폴더째, 카카오톡·문자는 파일마다.
        흔적 기록(구글·카드·건강 등)은 올린 폴더 하나를 통째로 넘기고 그 안에서 파일을 찾는다."""
        if source not in importer.SOURCES:
            raise ValueError(f"모르는 출처: {source}")
        d = saved[0].parent
        if source in importer.TRACE_SOURCES:
            for f in saved:
                if f.suffix.lower() == ".zip":
                    importer.safe_extract(f, d / f.stem)
                    f.unlink()
            out = importer.ingest(self.store, source, [d], me, since=since or None, until=until or None)
            self.invalidate()
            return out
        files: list[Path] = []
        for f in saved:
            if f.suffix.lower() == ".zip":
                root = importer.safe_extract(f, d / f.stem)
                files += [root] if source in ("instagram", "photos") else sorted(
                    p for p in root.rglob("*") if p.suffix.lower() in (".txt", ".csv", ".xml", ".db"))
            else:
                files.append(f)
        if source in ("instagram", "photos"):
            dirs = [f for f in files if f.is_dir()]
            files = [dirs[0] if dirs else d]
        out = importer.ingest(self.store, source, files, me)
        self.invalidate()
        return out

    def start_build(self, pii: bool = True, cluster: bool = True, extract_entities: bool = True) -> dict[str, Any]:
        """임베딩·정서·묶기·관계 추출을 백그라운드에서 돌린다. 끝나면 검색 엔진을 새로 읽는다."""
        from .build import build

        with self._lock:
            if self._build.get("state") == "running":
                return self.build_status()
            lines: list[str] = []
            state: dict[str, Any] = {"state": "running", "lines": lines, "started": time.time()}
            self._build = state

        def log(msg: str) -> None:
            lines.append(str(msg))
            del lines[:-40]

        def run() -> None:
            try:
                state["report"] = build(self.store, self.text, self.vision, log=log, cluster=cluster,
                                        extract_entities=extract_entities, pii=pii)
                state["state"] = "done"
            except Exception as err:  # 화면에 이유를 보여 준다
                state.update(state="error", error=str(err)[:400])
            finally:
                state["elapsed"] = round(time.time() - state["started"], 1)
                self.invalidate()

        threading.Thread(target=run, daemon=True).start()
        return self.build_status()

    def build_status(self) -> dict[str, Any]:
        st = dict(self._build)
        if st.get("state") == "running":
            st["elapsed"] = round(time.time() - st["started"], 1)
        st.pop("started", None)
        st["lines"] = list(st.get("lines", []))
        return st

    def delete_all(self) -> dict[str, Any]:
        """이 프로필의 기록·가명표·계산 결과·올린 파일·음악·전송 로그를 모두 지운다."""
        if self._build.get("state") == "running":
            raise ValueError("기록을 처리하는 중에는 지울 수 없습니다. 끝난 뒤에 다시 눌러 주세요.")
        counts = self.store.wipe()
        folders = []
        for label, d in (("올린 파일", self.upload_root), ("음악·영상", self.music_root), ("전송 로그", self.outbound.root),
                         ("공유 카드", self.shares.root)):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                folders.append(label)
        self.invalidate()
        self._build = {"state": "idle"}
        return {"records": counts.get("records", 0), "people": counts.get("people", 0), "folders": folders}

    def people_local(self) -> list[dict[str, Any]]:
        """가명 대응표 (이 컴퓨터 화면에서만 쓴다). 가명마다 실명·관계·기록 수."""
        counts = Counter(r.author for r in self.store.records() if r.author)
        rel = self.store.relations()
        by_alias: dict[str, list[str]] = {}
        for name, alias in self.store.people().items():
            if alias != "나":
                by_alias.setdefault(alias, []).append(name)
        return sorted(({"alias": a, "names": sorted(set(n)), "relation": rel.get(a), "records": counts.get(a, 0)}
                       for a, n in by_alias.items()), key=lambda x: -x["records"])

    # ---------- 음악 (3단계) ----------
    def music_plan(self, keyword: str, total_sec: int = 60) -> dict[str, Any]:
        no = self.query(keyword)["narrative_object"]
        return music.plan(no, total_sec)

    def music_start(self, keyword: str, total_sec: int = 60) -> dict[str, Any]:
        p = self.music_plan(keyword, total_sec)
        st = music.status(self.music_root, p["music_id"])
        if st["state"] != "ready" and not self.acestep.health():
            return {**st, "plan": p, "state": "unavailable",
                    "error": "ACE-Step 서버가 꺼져 있습니다. acestep 폴더에서 uv run acestep-api 로 켜 주세요 (core/README.md 시연 준비)."}
        return {**music.start(self.music_root, p, self.acestep), "plan": p}

    def music_status(self, music_id: str) -> dict[str, Any]:
        return music.status(self.music_root, music_id)

    def music_file(self, music_id: str, name: str) -> Path | None:
        path = music.job_dir(self.music_root, music_id) / name
        return path if path.exists() else None

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

    # ---------- 책 만들기 (웹 화면에서, 백그라운드) ----------
    BOOK_FILES = ("cover.png", "opener.png", "spread.png", "book.pdf")

    def book_start(self, keyword: str, story_text: str, genre: int | None = None, title: str | None = None,
                   author: str | None = None, offline: bool = False) -> dict[str, Any]:
        """노벨 화면에서 쓴 이야기로 책을 만든다. 그림을 그리는 데 25~55초 걸려서 백그라운드에서 돌리고 작업 번호를 준다.
        같은 입력으로 돌고 있거나 끝난 작업이 있으면 그 작업을 그대로 돌려준다."""
        import hashlib

        jobs: dict[str, dict[str, Any]] = self.__dict__.setdefault("_book_jobs", {})
        job_id = hashlib.sha1(repr((self.profile, keyword, story_text, genre, title, author, offline)).encode()).hexdigest()[:12]
        with self._lock:
            st = jobs.get(job_id)
            if st and st["state"] in ("running", "done"):
                return self.book_status(job_id) or {}
            lines: list[str] = []
            st = {"state": "running", "lines": lines, "started": time.time(), "keyword": keyword}
            jobs[job_id] = st

        def log(msg: str) -> None:
            lines.append(str(msg))
            del lines[:-20]

        def run() -> None:
            try:
                res = self.make_book(keyword, story_text, genre=genre, title=title, author=author or "나",
                                     offline=offline, log=log)
                st["book_id"] = res["brief_id"]
                st["state"] = "done"
            except Exception as err:  # 화면에 이유를 보여 준다
                st.update(state="error", error=str(err)[:400])
            finally:
                st["elapsed"] = round(time.time() - st["started"], 1)

        threading.Thread(target=run, daemon=True).start()
        return self.book_status(job_id) or {}

    def _book_dir(self, book_id: str) -> Path:
        from .book import books_dir

        return books_dir(self.profile) / book_id

    def book_status(self, job_or_book_id: str) -> dict[str, Any] | None:
        """작업 번호(12자리)나 책 번호(10자리, 브리프 번호)로 진행 상태와 결과 요약. 파일 경로 대신 내려받을 주소를 준다."""
        import json

        st = dict(self.__dict__.get("_book_jobs", {}).get(job_or_book_id) or {})
        if not st:
            if not (self._book_dir(job_or_book_id) / "brief.json").exists():
                return None
            st = {"state": "done", "book_id": job_or_book_id}
        out: dict[str, Any] = {"id": job_or_book_id, "state": st["state"], "lines": list(st.get("lines", []))[-6:]}
        if st.get("state") == "running":
            out["elapsed"] = round(time.time() - st["started"], 1)
        elif "elapsed" in st:
            out["elapsed"] = st["elapsed"]
        if st.get("error"):
            out["error"] = st["error"]
        bid = st.get("book_id")
        if st["state"] == "done" and bid:
            d = self._book_dir(bid)
            try:
                b = json.loads((d / "brief.json").read_text("utf-8"))
            except (OSError, ValueError):
                b = {}
            art = b.get("art") or {}
            out["book_id"] = bid
            out["book"] = {
                "title": b.get("title"), "author": b.get("author"), "genre": b.get("genre_name"),
                "archetype": b.get("archetype"), "typeface": b.get("typeface"), "art_style": b.get("art_style"),
                "moods": b.get("moods", []), "pages": (b.get("render") or {}).get("pages"),
                "art": {"cover": art.get("cover"), "illustration": art.get("illustration"),
                        "cost_usd": art.get("cost_usd")},
                "seconds": b.get("seconds"),
            }
            out["files"] = {n: f"/book/{bid}/{n}" for n in self.BOOK_FILES if (d / n).exists()}
        return out

    # ---------- 웹진 (webzine/): 한 사람의 일주일 기록 → 규칙표로 고른 템플릿 꼭지들 → HTML·PDF ----------
    def webzine_start(self, end: str | None = None, days: int = 7, offline: bool = False) -> dict[str, Any]:
        """글을 쓰고 검사하고 그리는 데 1~3분 걸려서 백그라운드에서 돌리고 작업 번호를 준다."""
        import hashlib
        from datetime import date as _date

        jobs: dict[str, dict[str, Any]] = self.__dict__.setdefault("_wz_jobs", {})
        job_id = hashlib.sha1(repr((self.profile, end, days, offline, self.data_id())).encode()).hexdigest()[:12]
        with self._lock:
            st = jobs.get(job_id)
            if st and st["state"] == "running":
                return self.webzine_status(job_id) or {}
            lines: list[str] = []
            st = {"state": "running", "lines": lines, "started": time.time()}
            jobs[job_id] = st

        def log(msg: str) -> None:
            lines.append(str(msg))
            del lines[:-20]

        def run() -> None:
            from .webzine import make_issue

            try:
                res = make_issue(self, end=_date.fromisoformat(end) if end else None, days=days, offline=offline, log=log)
                st["issue_id"] = res["id"]
                st["state"] = "done"
            except Exception as err:  # 화면에 이유를 보여 준다
                st.update(state="error", error=str(err)[:400])
            finally:
                st["elapsed"] = round(time.time() - st["started"], 1)

        threading.Thread(target=run, daemon=True).start()
        return self.webzine_status(job_id) or {}

    def webzine_status(self, job_id: str) -> dict[str, Any] | None:
        st = self.__dict__.get("_wz_jobs", {}).get(job_id)
        if not st:
            return None
        return {"job": job_id, "state": st["state"], "log": st["lines"][-6:], "issue_id": st.get("issue_id"),
                "error": st.get("error"), "elapsed": st.get("elapsed", round(time.time() - st["started"], 1))}

    def webzine_list(self) -> list[dict[str, Any]]:
        from .webzine import list_issues

        return list_issues(self.profile)

    def webzine_file(self, issue_id: str, name: str) -> Path | None:
        from .webzine.issue import issues_dir

        root = (issues_dir(self.profile) / issue_id).resolve()
        got = (root / name).resolve()
        return got if got.is_file() and root in got.parents else None

    # ---------- 공유 카드 (share.py) ----------
    def share_create(self, kind: str, summary_id: str | None = None, book_id: str | None = None,
                     lines: list[str] | None = None, title: str | None = None,
                     style_book_id: str | None = None) -> dict[str, Any]:
        """발견 문장·책 표지·이야기 한 대목으로 피드·스토리 카드와 공유 페이지를 만든다. 사용자가 누를 때만 부른다."""
        from .share import ShareError

        def book_dir(bid: str | None) -> Path | None:
            if not bid:
                return None
            d = self._book_dir(bid)
            if not (d / "brief.json").exists():
                raise ShareError("그런 책이 없습니다")
            return d

        s = self.store.summary()
        return self.shares.create(kind, columns=self.columns() if kind == "discovery" else None, summary_id=summary_id,
                                  book_dir=book_dir(book_id) if kind == "book" else None, lines=lines, title=title,
                                  style_book_dir=book_dir(style_book_id), date_range=(s["first"], s["last"]))

    def book_file(self, book_id: str, name: str) -> Path | None:
        if name not in self.BOOK_FILES:
            return None
        path = self._book_dir(book_id) / name
        return path if path.exists() else None
