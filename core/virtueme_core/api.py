"""로컬 HTTP API. 기본 주소 http://127.0.0.1:8767 (Vite 개발 서버는 /api/core 로 프록시).

응답에 들어가는 텍스트는 전부 가려진 것이다. 좌표·원본 파일 경로는 내보내지 않는다.
다른 컴퓨터에서도 접속하게 열면(`serve --host 0.0.0.0`) 이 컴퓨터가 아닌 곳에서 오는 요청은 토큰이 있어야 받는다.
기록 넣기 화면은 원래 이름을 보여 주기 때문이다.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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


class BookBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=40)
    story: str = Field(min_length=20, max_length=20000)
    genre: int | None = Field(default=None, ge=-1, le=6)
    title: str | None = Field(default=None, max_length=80)
    author: str | None = Field(default=None, max_length=40)
    offline: bool = False


class ScheduleBody(BaseModel):
    enabled: bool
    every: str = Field(default="week", pattern="^(week|day)$")
    weekday: int = Field(default=0, ge=0, le=6)
    hour: int = Field(default=9, ge=0, le=23)


class WebzineBody(BaseModel):
    end: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    days: int = Field(default=7, ge=1, le=31)
    offline: bool = False


class PlaceNameBody(BaseModel):
    key: str = Field(min_length=3, max_length=80, pattern=r"^(id|pt):")
    name: str | None = Field(default=None, max_length=40)


class RelationBody(BaseModel):
    alias: str = Field(min_length=1, max_length=20)
    relation: str | None = Field(default=None, max_length=30)


class BuildBody(BaseModel):
    pii: bool = True
    cluster: bool = True
    entities: bool = True


class DeleteBody(BaseModel):
    confirm: str = Field(min_length=1, max_length=60)


class ShareBody(BaseModel):
    kind: str = Field(pattern="^(discovery|book|passage|webzine)$")
    webzine_id: str | None = Field(default=None, pattern=r"^\d{8}-\d{5}$")
    summary_id: str | None = Field(default=None, pattern=r"^s[0-9a-f]{11}$")
    book_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{10}$")
    lines: list[str] | None = Field(default=None, max_length=3)
    title: str | None = Field(default=None, max_length=80)
    style_book_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{10}$")


class TalkBody(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    history: list[str] | None = None
    version: str = Field(default="v2", pattern="^v[12]$")
    mood: int | None = Field(default=None, ge=0, le=4)


LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# 공유 페이지(/s/<번호>)는 링크를 받은 사람이 여는 곳이라 토큰 없이 열리고, 다른 주소에 올린 사본도 반응을 보낼 수 있다
PUBLIC_SHARE = re.compile(r"^/s/[0-9a-f]{12}(/(feed|story|cover)\.png|/react|/counts)?$")


def _client(request: Request) -> str:
    """반응 횟수 제한에 쓰는 보낸 곳. 이 컴퓨터의 터널(cloudflared 등)을 거쳐 오면 X-Forwarded-For 첫 값을 쓴다."""
    host = request.client.host if request.client else ""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if host in LOOPBACK and fwd else host


def create_app(service: Service, warm: bool = False, token: str | None = None) -> FastAPI:
    app = FastAPI(title="Virtueme core", version="0.1.0")

    if token:
        @app.middleware("http")
        async def remote_guard(request: Request, call_next):
            client = request.client.host if request.client else ""
            if client not in LOOPBACK and not PUBLIC_SHARE.match(request.url.path):
                given = request.headers.get("x-virtueme-token") or request.query_params.get("token") or ""
                if not hmac.compare_digest(given, token):
                    return JSONResponse({"detail": "다른 컴퓨터에서 접속할 때는 토큰이 필요합니다 (서버를 켤 때 화면에 나온 값)"},
                                        status_code=401)
            return await call_next(request)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|null)$",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def share_cors(request: Request, call_next):
        # 공유 페이지의 반응·반응 수는 어느 주소에 올린 사본에서도 부를 수 있다 (쿠키·토큰 없이, 공개된 카드 내용만)
        if PUBLIC_SHARE.match(request.url.path) and request.url.path.endswith(("/react", "/counts")):
            if request.method == "OPTIONS":
                return JSONResponse({}, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST",
                                                 "Access-Control-Allow-Headers": "Content-Type"})
            resp = await call_next(request)
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
        return await call_next(request)

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

    @app.get("/map")
    def semantic_map(keyword: str | None = None) -> dict[str, Any]:
        _ready()
        return service.semantic_map(keyword.strip() if keyword else None)

    @app.get("/graph")
    def graph(keyword: str | None = None, limit: int = 40) -> dict[str, Any]:
        _ready()
        return service.graph(keyword, max(5, min(limit, 120)))

    @app.get("/columns")
    def columns_() -> dict[str, Any]:
        return service.columns()

    @app.post("/places/name")
    def name_place(body: PlaceNameBody) -> dict[str, Any]:
        from . import columns as columns_mod

        columns_mod.set_place_name(service.store, body.key, body.name)
        service.invalidate()
        return {"ok": True, "places": service.columns()["places"]}

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
                   name: str = PathParam(pattern=r"^(music\.m4a|music\.wav|features\.json|plan\.json|video(-object|-situation)?\.mp4)$")):
        got = service.music_file(music_id, name)
        if not got:
            raise HTTPException(404, "아직 만들지 않았습니다")
        return FileResponse(got)

    # ---------- 책: 노벨 화면의 이야기 → 표지·장 첫 쪽·펼침면·PDF (그림 때문에 25~55초, 백그라운드) ----------
    @app.post("/book")
    def book_start(body: BookBody) -> dict[str, Any]:
        _ready()
        try:
            return service.book_start(body.keyword.strip(), body.story, body.genre, (body.title or "").strip() or None,
                                      (body.author or "").strip() or None, body.offline)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    @app.get("/book/{book_id}")
    def book_status(book_id: str = PathParam(pattern=r"^[0-9a-f]{10}([0-9a-f]{2})?$")) -> dict[str, Any]:
        got = service.book_status(book_id)
        if not got:
            raise HTTPException(404, "그런 책이 없습니다")
        return got

    @app.get("/book/{book_id}/{name}")
    def book_file(book_id: str = PathParam(pattern=r"^[0-9a-f]{10}$"),
                  name: str = PathParam(pattern=r"^(cover|opener|spread)\.png$|^book\.pdf$")):
        got = service.book_file(book_id, name)
        if not got:
            raise HTTPException(404, "아직 만들지 않았습니다")
        return FileResponse(got)

    # ---------- 웹진 (webzine/): 한 호 만들기·목록·파일 ----------
    @app.post("/webzine")
    def webzine_start(body: WebzineBody) -> dict[str, Any]:
        _ready()
        return service.webzine_start(body.end, body.days, body.offline)

    @app.get("/webzine")
    def webzine_list() -> dict[str, Any]:
        from .webzine.match import load_rules

        return {"issues": service.webzine_list(), "rules": load_rules()}

    @app.get("/webzine/schedule")
    def webzine_schedule_get() -> dict[str, Any]:
        return service.webzine_schedule()

    @app.post("/webzine/schedule")
    def webzine_schedule_set(body: ScheduleBody) -> dict[str, Any]:
        return service.webzine_schedule(body.model_dump())

    def _webzine_loop() -> None:
        import time as _t

        while True:
            try:
                service.webzine_tick()
            except Exception:  # noqa: BLE001 — 다음 차례에 다시 본다
                pass
            _t.sleep(300)

    threading.Thread(target=_webzine_loop, daemon=True).start()

    @app.get("/webzine/job/{job_id}")
    def webzine_status(job_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")) -> dict[str, Any]:
        got = service.webzine_status(job_id)
        if not got:
            raise HTTPException(404, "그런 작업이 없습니다")
        return got

    @app.get("/webzine/{issue_id}/{name:path}")
    def webzine_file(issue_id: str = PathParam(pattern=r"^\d{8}-\d{5}$"),
                     name: str = PathParam(pattern=r"^(issue\.(html|pdf|json)|pg-[a-z_]+\.png|img/[a-z]\d{2}\.jpg)$")):
        got = service.webzine_file(issue_id, name)
        if not got:
            raise HTTPException(404, "그런 파일이 없습니다")
        return FileResponse(got)

    # ---------- 공유 카드·페이지 (share.py): 만들기·목록·지우기는 주인, /s/ 아래는 링크를 받은 사람 ----------
    from .share import REACTIONS, ShareError

    @app.post("/share")
    def share_create(body: ShareBody) -> dict[str, Any]:
        if body.kind == "discovery" and not body.summary_id:
            raise HTTPException(422, "발견 문장 번호(summary_id)가 필요합니다")
        if body.kind == "book" and not body.book_id:
            raise HTTPException(422, "책 번호(book_id)가 필요합니다")
        if body.kind == "passage" and not body.lines:
            raise HTTPException(422, "고른 글(lines)이 필요합니다")
        if body.kind == "webzine" and not body.webzine_id:
            raise HTTPException(422, "웹진 번호(webzine_id)가 필요합니다")
        if body.lines and any(len(x) > 160 for x in body.lines):
            raise HTTPException(422, "한 줄은 160자까지입니다")
        try:
            return service.share_create(body.kind, body.summary_id, body.book_id, body.lines, body.title,
                                        body.style_book_id, body.webzine_id)
        except ShareError as err:
            raise HTTPException(400, str(err)) from err
        except RuntimeError as err:
            raise HTTPException(500, str(err)) from err

    @app.get("/share")
    def share_list() -> dict[str, Any]:
        return {"shares": service.shares.list(), "reactions": REACTIONS}

    @app.get("/share/{share_id}")
    def share_get(share_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")) -> dict[str, Any]:
        got = service.shares.view(share_id, recent=100)
        if not got:
            raise HTTPException(404, "그런 공유가 없습니다")
        return got

    @app.post("/share/{share_id}/delete")
    def share_delete(share_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")) -> dict[str, Any]:
        if not service.shares.delete(share_id):
            raise HTTPException(404, "그런 공유가 없습니다")
        return {"ok": True, "shares": service.shares.list()}

    @app.get("/s/{share_id}", response_class=HTMLResponse)
    def share_page(request: Request, share_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")) -> HTMLResponse:
        page = service.shares.page(share_id, str(request.base_url))
        if page is None:
            raise HTTPException(404, "지워졌거나 없는 공유입니다")
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                           "Referrer-Policy": "no-referrer",
                                           "Content-Security-Policy": "default-src 'none'; img-src 'self'; "
                                           "style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"})

    @app.get("/s/{share_id}/counts")
    def share_counts(share_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")) -> dict[str, Any]:
        if not service.shares.get(share_id):
            raise HTTPException(404, "지워졌거나 없는 공유입니다")
        return {"counts": service.shares.counts(share_id)}

    @app.get("/s/{share_id}/{name}")
    def share_file(share_id: str = PathParam(pattern=r"^[0-9a-f]{12}$"),
                   name: str = PathParam(pattern=r"^(feed|story|cover)\.png$")):
        got = service.shares.file(share_id, name)
        if not got:
            raise HTTPException(404, "없는 그림입니다")
        return FileResponse(got, media_type="image/png", headers={"Cache-Control": "no-cache"})

    @app.post("/s/{share_id}/react")
    async def share_react(request: Request, share_id: str = PathParam(pattern=r"^[0-9a-f]{12}$")) -> dict[str, Any]:
        raw = await request.body()
        if len(raw) > 2000:
            raise HTTPException(413, "너무 깁니다")
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
            reaction, comment = str(data.get("reaction") or ""), str(data.get("comment") or "")
        except (ValueError, UnicodeDecodeError, AttributeError) as err:
            raise HTTPException(400, "보낸 값을 읽지 못했습니다") from err
        try:
            return {"ok": True, "counts": service.shares.react(share_id, reaction, comment, _client(request))}
        except LookupError as err:
            raise HTTPException(404, str(err)) from err
        except PermissionError as err:
            raise HTTPException(429, str(err)) from err
        except ShareError as err:
            raise HTTPException(400, str(err)) from err

    # ---------- 웹 화면에서 기록 넣기 (이 컴퓨터에서만: 서버는 127.0.0.1 에만 뜬다) ----------
    @app.get("/import", response_class=HTMLResponse)
    def import_page() -> str:
        return (Path(__file__).parent / "static" / "import.html").read_text(encoding="utf-8")

    @app.post("/import/{source}")
    def import_files(source: str = PathParam(pattern="^(kakao|sms|instagram|photos|google|maps|card|health|spotify|netflix)$"),
                     files: list[UploadFile] = File(...), me: str = Form(default=""),
                     since: str = Form(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$"),
                     until: str = Form(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$")) -> dict[str, Any]:
        d = service.new_upload_dir(source)
        saved = []
        for f in files:
            name = Path(f.filename or "upload").name  # 경로는 버리고 파일 이름만
            target = d / name
            with target.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            saved.append(target)
        try:
            names = [n.strip() for n in me.split(",") if n.strip()] or None
            return service.import_paths(source, saved, names, since=since or None, until=until or None)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err

    @app.post("/build")
    def start_build(body: BuildBody | None = None) -> dict[str, Any]:
        b = body or BuildBody()
        return service.start_build(pii=b.pii, cluster=b.cluster, extract_entities=b.entities)

    @app.get("/build/status")
    def build_status() -> dict[str, Any]:
        return service.build_status()

    @app.get("/people/local")
    def people_local() -> dict[str, Any]:
        return {"people": service.people_local()}

    @app.post("/people/relation")
    def set_relation(body: RelationBody) -> dict[str, Any]:
        n = service.store.set_relation(body.alias.strip(), (body.relation or "").strip() or None)
        if not n:
            raise HTTPException(404, "그런 가명이 없습니다")
        return {"ok": True, "people": service.people_local()}

    @app.get("/outbound")
    def outbound_summary(days: int = 7) -> dict[str, Any]:
        return service.outbound.summary(max(1, min(days, 30)))

    @app.get("/outbound/item")
    def outbound_item(id: str) -> dict[str, Any]:  # noqa: A002
        got = service.outbound.item(id)
        if not got:
            raise HTTPException(404, "그런 기록이 없습니다")
        return got

    @app.post("/profile/delete")
    def delete_profile(body: DeleteBody) -> dict[str, Any]:
        if body.confirm.strip() != service.profile:
            raise HTTPException(400, f"확인란에 프로필 이름({service.profile})을 그대로 적어 주세요")
        try:
            return service.delete_all()
        except ValueError as err:
            raise HTTPException(409, str(err)) from err

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
