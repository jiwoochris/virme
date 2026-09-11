"""로컬 HTTP API. 기본 주소 http://127.0.0.1:8767 (Vite 개발 서버는 /api/core 로 프록시).

응답에 들어가는 텍스트는 전부 가려진 것이다. 좌표·원본 파일 경로는 내보내지 않는다.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .service import Service


class QueryBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=40)
    include_others: bool | None = None


class StoryBody(QueryBody):
    genre: int | None = Field(default=None, ge=-1, le=5)
    version: str = Field(default="v2", pattern="^v[12]$")
    variation: int = Field(default=0, ge=0, le=20)


class VerifyBody(BaseModel):
    paragraphs: list[str] = Field(max_length=60)
    evidence_ids: list[str] = Field(max_length=200)
    mode: str = Field(default="blur", pattern="^(plain|blur|rewrite)$")
    title: str | None = None
    extra_counts: list[int] | None = None
    kind: str = Field(default="story", pattern="^(story|talk)$")


class LabelBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=40)
    record_id: str = Field(pattern=r"^r[0-9a-f]{11}$")
    label: int = Field(ge=0, le=2)
    labeler: str = Field(default="", max_length=40)


class MusicBody(QueryBody):
    total_sec: int = Field(default=60, ge=20, le=180)


class TalkBody(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    history: list[str] | None = None
    version: str = Field(default="v2", pattern="^v[12]$")
    mood: int | None = Field(default=None, ge=0, le=4)


def create_app(service: Service, warm: bool = False) -> FastAPI:
    app = FastAPI(title="Virtueme core", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|null)$",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    if warm:
        def _warm() -> None:
            try:
                eng = service.engine
                if "text" in eng.spaces:
                    eng._background("text")
                if "vision" in eng.spaces:
                    eng._background("vision")
            except Exception:  # 첫 질의 때 다시 시도한다
                pass

        threading.Thread(target=_warm, daemon=True).start()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return service.health()

    @app.get("/profile")
    def profile() -> dict[str, Any]:
        return service.engine.profile()

    @app.get("/emotion")
    def emotion() -> dict[str, Any]:
        d = service.engine.daily
        return {"series": d.series() if d else []}

    @app.post("/query")
    def query(body: QueryBody) -> dict[str, Any]:
        if not service.store.count():
            raise HTTPException(409, "가져온 기록이 없습니다. virtueme ingest 를 먼저 실행하세요.")
        if not service.store.vector_ids("text"):
            raise HTTPException(409, "임베딩이 없습니다. virtueme build 를 먼저 실행하세요.")
        try:
            return service.query(body.keyword, include_others=body.include_others)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    def _ready() -> None:
        if not service.store.count():
            raise HTTPException(409, "가져온 기록이 없습니다. virtueme ingest 를 먼저 실행하세요.")
        if not service.store.vector_ids("text"):
            raise HTTPException(409, "임베딩이 없습니다. virtueme build 를 먼저 실행하세요.")

    @app.post("/story/prompt")
    def story_prompt(body: StoryBody) -> dict[str, Any]:
        _ready()
        try:
            return service.story_prompt(body.keyword, body.genre, body.version, body.variation, body.include_others)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    @app.post("/verify")
    def verify(body: VerifyBody) -> dict[str, Any]:
        return service.verify(body.paragraphs, body.evidence_ids, body.mode, body.title, body.extra_counts, body.kind)

    @app.post("/talk/context")
    def talk_context(body: TalkBody) -> dict[str, Any]:
        _ready()
        return service.talk_context(body.message, body.history, body.version, body.mood)

    @app.get("/talk/greeting")
    def talk_greeting() -> dict[str, Any]:
        _ready()
        return {"greeting": service.greeting()}

    @app.get("/clusters")
    def clusters_overview() -> dict[str, Any]:
        _ready()
        return service.clusters_overview()

    @app.get("/graph")
    def graph(keyword: str | None = None, limit: int = 40) -> dict[str, Any]:
        _ready()
        return service.graph(keyword, max(5, min(limit, 120)))

    @app.get("/gaps")
    def gaps() -> dict[str, Any]:
        _ready()
        return service.gaps()

    @app.get("/label", response_class=HTMLResponse)
    def label_page() -> str:
        return (Path(__file__).parent / "static" / "label.html").read_text(encoding="utf-8")

    @app.get("/label/candidates")
    def label_candidates(keyword: str) -> dict[str, Any]:
        _ready()
        keyword = keyword.strip()
        if not 1 <= len(keyword) <= 40:
            raise HTTPException(400, "키워드는 1~40자로 넣어 주세요")
        return service.label_candidates(keyword)

    @app.post("/label")
    def put_label(body: LabelBody) -> dict[str, Any]:
        if not service.store.record(body.record_id):
            raise HTTPException(404, "기록이 없습니다")
        service.store.put_label(body.keyword.strip(), body.record_id, body.label, body.labeler)
        return {"ok": True, "labeled": len(service.store.labels(body.keyword.strip()).get(body.keyword.strip(), {}))}

    @app.get("/label/export")
    def label_export() -> dict[str, Any]:
        return {"labels": service.store.labels()}

    @app.post("/music/plan")
    def music_plan(body: MusicBody) -> dict[str, Any]:
        _ready()
        try:
            return service.music_plan(body.keyword, body.total_sec)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    @app.post("/music")
    def music_start(body: MusicBody) -> dict[str, Any]:
        _ready()
        try:
            return service.music_start(body.keyword, body.total_sec)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    @app.get("/music/{music_id}")
    def music_status(music_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")) -> dict[str, Any]:
        return service.music_status(music_id)

    @app.get("/music/{music_id}/{name}")
    def music_file(music_id: str = PathParam(pattern=r"^[0-9a-f]{12}$"),
                   name: str = PathParam(pattern=r"^(music\.m4a|music\.wav|features\.json|plan\.json|video\.mp4)$")):
        got = service.music_file(music_id, name)
        if not got:
            raise HTTPException(404, "아직 만들지 않았습니다")
        return FileResponse(got)

    @app.get("/records/{rid}")
    def record(rid: str) -> dict[str, Any]:
        got = service.record(rid)
        if not got:
            raise HTTPException(404, "기록이 없습니다")
        return got

    return app


def app_from_env() -> FastAPI:
    profile = os.environ.get("VIRTUEME_PROFILE", "default")
    return create_app(Service(profile), warm=True)
