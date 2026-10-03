"""명령줄 도구.

    uv run virtueme -p 서연 ingest kakao 대화.txt --me 윤서연
    uv run virtueme -p 서연 ingest instagram ~/Downloads/instagram-export
    uv run virtueme -p 서연 ingest sms sms-20260911.xml            # 안드로이드 SMS Backup & Restore, 또는 맥 chat.db 복사본
    uv run virtueme -p 서연 ingest photos ~/Pictures/2026-summer
    uv run virtueme -p 서연 ingest google takeout-20260901.zip --since 2026-06-01   # 유튜브·검색·지도 (흔적 기록)
    uv run virtueme -p 서연 ingest card 신한카드_이용내역.xlsx
    uv run virtueme -p 서연 build
    uv run virtueme -p 서연 query 혼자
    uv run virtueme -p 서연 serve
    uv run virtueme -p 서연 demo 러닝 콩이 외로움 --video   # 발표용 키워드의 음악·영상을 미리 만든다
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .config import profile_db_path


def _svc(args, fake: bool = False):
    from .service import Service

    return Service(args.profile, fake_models=fake or args.fake_models)


def cmd_ingest(args) -> int:
    from .importer import ingest

    svc = _svc(args, fake=True)
    store = svc.store
    if args.source == "synthetic":
        from .ingest import synthetic

        persona, recs = synthetic.synthetic_dataset(args.seed, args.persona, date.fromisoformat(args.start))
        store.upsert_records(recs)
        store.cache_put("persona", persona)
        store.log_import("synthetic", f"seed={args.seed}", len(recs))
        print(f"합성 페르소나 {persona['name']} ({persona['age']}세, {persona['occupation']}) 90일 기록 {len(recs)}건")
    else:
        try:
            out = ingest(store, args.source, [Path(p) for p in args.paths], args.me,
                         since=args.since, until=args.until, profile_name=args.netflix_profile)
        except ValueError as err:
            print(err, file=sys.stderr)
            return 2
        print("\n".join(out["lines"]))
    print(f"프로필 '{args.profile}': 기록 {store.count()}건 → 다음 단계: virtueme -p {args.profile} build")
    return 0


def cmd_build(args) -> int:
    from .build import build

    svc = _svc(args)
    report = build(svc.store, svc.text, None if args.no_vision else svc.vision, emotion_backend=args.emotion,
                   cluster=not args.no_cluster, extract_entities=not args.no_entities, pii=not args.no_pii)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def cmd_status(args) -> int:
    svc = _svc(args, fake=True)
    print(json.dumps({"db": str(svc.store.path), **svc.store.summary()}, ensure_ascii=False, indent=2))
    return 0


def cmd_query(args) -> int:
    svc = _svc(args)
    out = svc.query(args.keyword, include_others=args.others)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    no = out["narrative_object"]
    b = no["grade_basis"]
    print(f"[{no['keyword']}] {no['evidence_grade']}  강한 근거 {b['strong_records']}건 / {b['strong_days']}일"
          f" (기록 있는 날의 {b['strong_day_share'] * 100:.0f}%), 약한 근거 {b['weak_records']}건")
    if no.get("expansions", {}).get("forms"):
        print("  확장:", ", ".join(no["expansions"]["forms"][:8]))
    if "stats" in no:
        s = no["stats"]
        print(f"  기간 {s['first_date']} ~ {s['last_date']}, 시간 중심 {s['time_center']}시({s['time_band']}),"
              f" 정서 평균 {no['emotion']['mean']} (전체 {no['emotion']['baseline']})")
        print("  주요 낱말:", ", ".join(t["term"] for t in no["top_terms"]))
        print("  signals:", no["signals"])
    for r in out["evidence_records"][: args.show]:
        print(f"  {r['date']} {r['time']} [{r['source']}/{r['author']}] {r.get('relevance')}  {r.get('text') or r.get('photo_tags')}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app

    svc = _svc(args)
    token = args.token
    if args.host not in ("127.0.0.1", "localhost", "::1") and not token:
        import secrets

        token = secrets.token_urlsafe(12)
    if token:
        print(f"다른 컴퓨터에서 접속하려면 토큰이 필요합니다: {token}\n"
              f"  예: http://<이 컴퓨터 주소>:{args.port}/import?token={token}\n"
              "  기록 넣기 화면은 원래 이름·번호를 보여 주므로, 토큰은 기록 주인에게만 알려 주세요.")
    uvicorn.run(create_app(svc, warm=True, token=token), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_demo(args) -> int:
    """발표용 키워드마다 검색 결과(판정 캐시)·음악·오디오 반응 영상과 간극을 미리 만들어 둔다. 이미 있으면 건너뛴다.

    두 단계로 돈다: 먼저 모든 키워드의 음악, 그다음 영상. ACE-Step 서버는 음악이 없는 키워드가 있을 때만 필요하고,
    영상 단계에서는 필요 없다. 메모리가 부족한 맥이면 음악 단계가 끝난 뒤 ACE-Step 서버를 꺼도 된다
    (24GB 맥에서 ACE-Step 과 영상 렌더를 함께 돌리다 시스템이 작업을 끊은 적이 있다, 2026-09-11).
    """
    import os
    import subprocess

    from . import music

    svc = _svc(args)
    virme = Path(__file__).resolve().parents[2]
    team_py = virme / ".venv" / "bin" / "python"
    acestep_up = None  # 처음 필요할 때 한 번만 확인
    missing: list[str] = []
    ready: list[tuple[str, Path]] = []
    # 인사이트 화면의 "스스로 말한 나와 기록"은 처음 한 번 LLM 을 불러 1분 넘게 걸리므로 미리 계산해 둔다
    gaps = svc.gaps()["gaps"]
    print(f"- 간극 {len(gaps)}건 미리 계산 (" + ", ".join(f"{g['verdict']} {g['topic']}" for g in gaps[:4]) + ")")
    for kw in args.keywords:
        no = svc.query(kw)["narrative_object"]
        if no["evidence_grade"] == "근거 부족":
            print(f"- {kw}: 근거 부족이라 음악을 만들지 않습니다")
            continue
        plan = music.plan(no, args.seconds)
        d = music.job_dir(svc.music_root, plan["music_id"])
        print(f"- {kw}: #{no['narrative_id']} 음악 {plan['music_id']} ({len(plan['sections'])}구간, {plan['bpm']} BPM)")
        if not (d / "features.json").exists():
            if acestep_up is None:
                acestep_up = svc.acestep.health()
            if not acestep_up:
                print(f"    ACE-Step 서버({svc.acestep.base})가 꺼져 있어 건너뜁니다. acestep 폴더에서 uv run acestep-api 로 켜 주세요.")
                missing.append(kw)
                continue
        st = music.make(svc.music_root, plan, svc.acestep,
                        on_progress=lambda x: print(f"    {x['stage']} {x['done'] + 1}/{x['total']}", flush=True))
        # 예전 방식으로 이은 곡은 구간 음원에서 다시 잇는다 (예전 소리가 든 영상은 지워지고 아래에서 다시 만든다)
        rm = music.remix(svc.music_root, plan["music_id"])
        if rm["remixed"]:
            print(f"    구간 음량을 계획대로 맞춰 다시 이음 ({music.MIX_VERSION}), 지운 영상: {', '.join(rm['removed_videos']) or '없음'}")
        feats = json.loads((d / "features.json").read_text(encoding="utf-8"))
        for s in feats.get("sections", []):
            m = s.get("measured")
            if m:
                print(f"    구간 {s['index'] + 1}: 계획 {plan['sections'][s['index']]['keyscale']} {plan['bpm']} BPM"
                      f" → 측정 {m['key']} ({m['key_match']}) {m['bpm']} BPM, 음량 {m['rms_db']} dB ({s['gain_db']:+} dB)")
        print(f"    음악: {d / st['audio']}")
        ready.append((kw, d))
    todo = [(kw, d, mode) for kw, d in ready for mode in (args.video_modes if args.video else [])
            if not (d / music.VIDEO_FILES[mode]).exists()]
    if todo:
        if not team_py.exists():
            print("영상: 팀 모델 환경이 없습니다. virme 에서 bun run model:setup 을 먼저 실행하세요.")
            todo = []
        elif acestep_up:
            print("음악을 다 만들었습니다. 이제부터는 ACE-Step 서버가 필요 없으니, 메모리가 부족하면 꺼도 됩니다.")
    for kw, d, mode in todo:
        print(f"- {kw}: 영상({mode}) 렌더", flush=True)
        subprocess.run([str(team_py), str(virme / "scripts" / "render-audio-reactive.py"), "--music-dir", str(d),
                        "--mode", mode],
                       cwd=virme, env={**os.environ, "PYTHONPATH": str(virme / "model_service"),
                                       "PYTORCH_ENABLE_MPS_FALLBACK": "1"}, check=True)
    for kw, d in ready:
        for mode, name in music.VIDEO_FILES.items():
            if (d / name).exists():
                print(f"- {kw}: 영상({mode}) {d / name}")
    if missing:
        print(f"음악을 만들지 못한 키워드: {', '.join(missing)}")
    return 1 if missing else 0


def cmd_people(args) -> int:
    svc = _svc(args, fake=True)
    if args.keep:
        keep = sorted(set(svc.store.cache_get("pii_keep") or []) | set(args.keep))
        svc.store.cache_put("pii_keep", keep)
        print("이름 가리기 예외:", ", ".join(keep))
    for item in args.relation or []:
        alias, _, relation = item.partition("=")
        n = svc.store.set_relation(alias.strip(), relation.strip() or None)
        print(f"{alias.strip()} → {relation.strip() or '(관계 지움)'}" + ("" if n else "  (그런 가명이 없습니다)"))
    rel = svc.store.relations()
    for name, alias in svc.store.people().items():
        print(f"{alias}\t{rel.get(alias) or '-'}\t{name}")
    return 0


def cmd_columns(args) -> int:
    from . import columns

    svc = _svc(args, fake=True)
    cols = svc.columns()
    if args.json:
        print(json.dumps(cols, ensure_ascii=False, indent=1))
        return 0
    ds = cols["data_scope"]
    print(f"기록 {ds['records']}건 ({ds['date_range'][0]} ~ {ds['date_range'][1]}), 흔적 비율 {ds['trace_share']:.0%}")
    print("\n[컬럼]")
    for c in cols["catalog"]:
        print(f"  {'O' if c['available'] else '-'} {c['element']}  {c['name']}" + ("" if c["available"] else f"  ← {c['how_to_get']}"))
    print("\n[인물]")
    for c in cols["cast"][:8]:
        reply = f", 내 답장 {c['reply_min_mine']}분" if c["reply_min_mine"] is not None else ""
        print(f"  {c['alias']}({c['relation'] or '관계 없음'}) 발화 {c['turns']}, 밤 {c['night_share']:.0%}{reply}"
              f"  {', '.join(c['roles'])}")
    print("\n[장소]  이름이 없는 곳은 `virtueme places --name 키=이름` 으로 붙입니다")
    for p in cols["places"]["anchors"] + cols["places"]["regular"][:6]:
        print(f"  {p['name']}  {p['visits']}일, {p['hours']}시간, 주로 {p['time_band']}  ({p['key']})")
    print("\n[발견]")
    for d in cols["discoveries"]:
        print(f"  - {d['text']}")
    print("\n[장르 추천]")
    for g in cols["genre_hint"]:
        print(f"  {g['genre']} {g['score']:.2f}  {g['reason']}")
    return 0


def cmd_places(args) -> int:
    from . import columns

    svc = _svc(args, fake=True)
    for item in args.name or []:
        key, _, name = item.partition("=")
        columns.set_place_name(svc.store, key.strip(), name.strip() or None)
        print(f"{key.strip()} → {name.strip() or '(이름 지움)'}")
    svc.invalidate()
    cols = svc.columns()
    for group in ("anchors", "regular", "first_visits"):
        for p in cols["places"][group]:
            print(f"{p['key']}\t{p['name']}\t{p['visits']}일\t{p['first']}~{p['last']}\t주로 {p['time_band']}")
    return 0


def cmd_book(args) -> int:
    svc = _svc(args)
    story_text = Path(args.story).read_text("utf-8") if args.story else None
    res = svc.make_book(args.keyword, story_text, genre=args.genre, title=args.title, author=args.author,
                        offline=args.offline)
    for k, v in res["files"].items():
        print(f"{k}\t{v}")
    return 0


def cmd_share(args) -> int:
    """공유 카드 목록·지우기·정적 사본 내보내기 (virtueme_core/share.py, COLUMNS.md 「공유 카드」)."""
    svc = _svc(args)
    sh = svc.shares
    if args.action == "list":
        rows = sh.list()
        if not rows:
            print("만든 공유 카드가 없습니다")
        for s in rows:
            c = s["reactions"]
            print(f"{s['id']}\t{s['created']}\t{s['kind_ko']}\t반응 {c['total']}\t{(s.get('title') or '')[:30]}")
        return 0
    if not args.id:
        print("공유 번호를 적어 주세요 (virtueme share list 로 확인)", file=sys.stderr)
        return 2
    if args.action == "delete":
        ok = sh.delete(args.id)
        print("지웠습니다" if ok else "그런 공유가 없습니다")
        return 0 if ok else 1
    if not args.dir:
        print("내보낼 폴더를 적어 주세요: virtueme share export <번호> <폴더>", file=sys.stderr)
        return 2
    try:
        files = sh.export(args.id, Path(args.dir), base_url=args.base_url, react_url=args.react_url)
    except LookupError as err:
        print(err, file=sys.stderr)
        return 1
    for f in files:
        print(f)
    if not args.base_url:
        print("--base-url 이 없어 링크 미리보기 이미지가 상대 주소입니다. 메신저 미리보기를 보려면 올릴 주소를 붙여 다시 내보내세요.")
    if not args.react_url:
        print("--react-url 이 없어 이 사본은 반응을 받지 않습니다 (반응 수만 보여 줍니다).")
    return 0


def cmd_reset(args) -> int:
    path = profile_db_path(args.profile)
    if not args.yes:
        print(f"{path} 를 지우려면 --yes 를 붙이세요", file=sys.stderr)
        return 2
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    print(f"삭제: {path}")
    return 0


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"날짜는 YYYY-MM-DD 로 적어 주세요: {value}") from err


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="virtueme", description="Virtueme 기록 처리·키워드 검색")
    ap.add_argument("-p", "--profile", default="default", help="프로필 이름 (사람 한 명 = DB 파일 하나)")
    ap.add_argument("--fake-models", action="store_true", help="모델 대신 해시 임베더 사용 (시험용)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="기록 가져오기")
    p.add_argument("source", choices=["kakao", "sms", "instagram", "photos", "google", "maps", "card", "health",
                                      "spotify", "netflix", "synthetic"])
    p.add_argument("paths", nargs="*")
    p.add_argument("--me", action="append", help="본인 이름 (카카오톡·인스타그램 DM). 여러 번 지정 가능")
    p.add_argument("--seed", type=int, default=20260711)
    p.add_argument("--persona", type=int, default=None)
    p.add_argument("--start", default="2026-06-01")
    p.add_argument("--since", type=_day, default=None, help="흔적 기록을 이 날짜부터 (YYYY-MM-DD, 그날 포함)")
    p.add_argument("--until", type=_day, default=None, help="흔적 기록을 이 날짜까지 (YYYY-MM-DD, 그날 포함)")
    p.add_argument("--netflix-profile", default=None, help="넷플릭스 프로필 이름 (가족이 같은 계정을 쓸 때)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("build", help="임베딩·정서 계산")
    p.add_argument("--emotion", default="auto", choices=["auto", "llm", "anchor", "truth"])
    p.add_argument("--no-vision", action="store_true")
    p.add_argument("--no-cluster", action="store_true", help="BERTopic 묶기 건너뛰기")
    p.add_argument("--no-entities", action="store_true", help="개체·관계 추출 건너뛰기")
    p.add_argument("--no-pii", action="store_true", help="제3자 이름 가리기(GLiNER) 건너뛰기")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("status", help="프로필 요약")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("query", help="키워드 검색")
    p.add_argument("keyword")
    p.add_argument("--json", action="store_true")
    p.add_argument("--others", action="store_true", default=None, help="다른 사람 메시지도 근거로 센다")
    p.add_argument("--show", type=int, default=10)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("serve", help="로컬 API 서버")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8767)
    p.add_argument("--token", default=None, help="다른 컴퓨터에서 올 때 요구할 토큰 (--host 를 바깥으로 열면 없을 때 새로 만든다)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("demo", help="발표용 키워드의 음악·오디오 반응 영상 미리 만들기")
    p.add_argument("keywords", nargs="+")
    p.add_argument("--seconds", type=int, default=60, help="음악 길이(초)")
    p.add_argument("--video", action="store_true", help="팀 StyleGAN 환경으로 오디오 반응 영상도 만든다")
    p.add_argument("--video-modes", nargs="+", default=["place"], choices=["place", "object", "situation"],
                   help="영상 모드 (장소는 StyleGAN2 약 7분, 사물·상황은 StyleGAN-XL)")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("people", help="가명 대응표 (로컬에서만 확인), 관계 붙이기")
    p.add_argument("--relation", action="append", metavar="가명=관계",
                   help="예: --relation '지인D=회사 동기'. 이야기에서 가명 대신 이 관계로 부른다. 실명은 쓰지 말 것")
    p.add_argument("--keep", action="append", metavar="이름",
                   help="제3자 이름 가리기에서 빼 둘 낱말 (예: 반려동물 이름이 사람으로 잡힐 때)")
    p.set_defaults(func=cmd_people)

    p = sub.add_parser("columns", help="소설 재료 컬럼: 인물·장소·리듬·취향·전환점·발견·장르 추천")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_columns)

    p = sub.add_parser("places", help="장소 목록과 이름 붙이기 (휴대폰 타임라인 내보내기에는 장소 이름이 없다)")
    p.add_argument("--name", action="append", metavar="키=이름", help="예: --name 'id:ChIJ...=망원한강공원'. 집 주소는 쓰지 말 것")
    p.set_defaults(func=cmd_places)

    p = sub.add_parser("book", help="키워드 이야기로 책 표지·장 첫 쪽·본문 펼침면 만들기 (virtueme_core/book/README.md)")
    p.add_argument("keyword")
    p.add_argument("--story", help="이야기 파일 (첫 줄 TITLE: 가능). 없으면 노벨 화면과 같은 프롬프트로 core 가 직접 쓴다")
    p.add_argument("--genre", type=int, default=None, help="0 미스터리 1 로맨스 2 SF 3 판타지 4 스릴러 5 코미디. 없으면 장르 추천 1위")
    p.add_argument("--title")
    p.add_argument("--author", default="나")
    p.add_argument("--offline", action="store_true", help="그림 모델 없이 팔레트로 그린 무늬를 쓴다")
    p.set_defaults(func=cmd_book)

    p = sub.add_parser("share", help="공유 카드 목록·지우기·정적 사본 내보내기 (index.html + 카드 PNG)")
    p.add_argument("action", choices=["list", "delete", "export"])
    p.add_argument("id", nargs="?", help="공유 번호 (12자리)")
    p.add_argument("dir", nargs="?", help="export: 사본을 쓸 폴더")
    p.add_argument("--base-url", default=None, help="export: 사본을 올릴 주소 (링크 미리보기 og:image 를 절대 주소로)")
    p.add_argument("--react-url", default=None, help="export: 반응을 받을 core 서버 주소 (예: 터널 주소). 없으면 반응을 받지 않음")
    p.set_defaults(func=cmd_share)

    p = sub.add_parser("reset", help="프로필 DB 삭제")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_reset)

    args = ap.parse_args(argv)
    if args.cmd == "ingest" and args.source != "synthetic" and not args.paths:
        ap.error("가져올 파일이나 폴더 경로를 적으세요")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
