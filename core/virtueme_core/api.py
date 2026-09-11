"""로컬 HTTP API. 기본 주소 http://127.0.0.1:8767 (Vite 개발 서버는 /api/core 로 프록시).

응답에 들어가는 텍스트는 전부 가려진 것이다. 좌표·원본 파일 경로는 내보내지 않는다.
"""
from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .service import Service


class QueryBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=40)
    include_others: bool | None = None


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
