"""명령줄 도구.

    uv run virtueme -p 서연 ingest kakao 대화.txt --me 윤서연
    uv run virtueme -p 서연 ingest instagram ~/Downloads/instagram-export
    uv run virtueme -p 서연 ingest sms sms-20260911.xml            # 안드로이드 SMS Backup & Restore, 또는 맥 chat.db 복사본
    uv run virtueme -p 서연 ingest photos ~/Pictures/2026-summer
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
    from .privacy import Pseudonymizer

    svc = _svc(args, fake=True)
    store = svc.store
    pseudo = Pseudonymizer(store)
    if args.source == "kakao":
        from .ingest import kakao

        total = 0
        for p in args.paths:
            parsed = kakao.parse_kakao_file(Path(p))
            if not args.me:
                listing = ", ".join(f"{n}({c})" for n, c in parsed.senders.most_common(10))
                print(f"본인 이름을 --me 로 지정하세요. 보낸 사람: {listing}", file=sys.stderr)
                return 2
            recs, info = kakao.to_records(parsed, args.me, pseudo)
            store.upsert_records(recs)
            store.log_import("kakao", str(p), len(recs), json.dumps(info, ensure_ascii=False))
            total += len(recs)
            print(f"카카오톡 {Path(p).name}: 형식 {info['format']}, 메시지 {info['messages']}건 → 발화 {info['turns']}건"
                  f" (참여자 {', '.join(info['participants'])})")
            if info["warnings"]:
                print(f"  경고 {len(info['warnings'])}건: {info['warnings'][0]}")
    elif args.source == "sms":
        from .ingest import sms

        for p in args.paths:
            recs, info = sms.to_records(sms.parse_file(Path(p)), pseudo)
            store.upsert_records(recs)
            store.log_import("sms", str(p), len(recs), json.dumps(info, ensure_ascii=False))
            skipped = ", ".join(f"{k} {v}" for k, v in info["skipped"].items()) or "없음"
            print(f"문자 {Path(p).name}: 메시지 {info['messages']}건 → 발화 {info['turns']}건, 대화 {info['threads']}개"
                  f" (자동 발송 등 뺀 것: {skipped})")
    elif args.source == "instagram":
        from .ingest import instagram

        recs, info = instagram.import_instagram(Path(args.paths[0]), pseudo, me=(args.me or [None])[0])
        store.upsert_records(recs)
        store.log_import("instagram", args.paths[0], len(recs), json.dumps(info, ensure_ascii=False))
        print(f"인스타그램: 게시물 {info['posts']}, 스토리 {info['stories']}, 릴스 {info['reels']}, 댓글 {info['comments']},"
              f" DM 발화 {info['dm_messages']} (사진 포함 {info['with_media']}, GPS {info['with_gps']})")
    elif args.source == "photos":
        from .ingest import photos

        recs, info = photos.import_photos(Path(args.paths[0]))
        store.upsert_records(recs)
        store.log_import("photo", args.paths[0], len(recs), json.dumps(info, ensure_ascii=False))
        print(f"사진: {info['records']}장 (EXIF 시각 {info['time_source']['exif']}, 파일 시각 {info['time_source']['mtime']},"
              f" GPS {info['with_gps']}, 건너뜀 {info['skipped']})")
    elif args.source == "synthetic":
        from .ingest import synthetic

        persona, recs = synthetic.synthetic_dataset(args.seed, args.persona, date.fromisoformat(args.start))
        store.upsert_records(recs)
        store.cache_put("persona", persona)
        store.log_import("synthetic", f"seed={args.seed}", len(recs))
        print(f"합성 페르소나 {persona['name']} ({persona['age']}세, {persona['occupation']}) 90일 기록 {len(recs)}건")
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
    uvicorn.run(create_app(svc, warm=True), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_demo(args) -> int:
    """발표용 키워드마다 검색 결과(판정 캐시)·음악·오디오 반응 영상과 간극을 미리 만들어 둔다. 이미 있으면 건너뛴다.

    ACE-Step 서버는 아직 음악이 없는 키워드가 있을 때만 필요하다.
    """
    import os
    import subprocess

    from . import music

    svc = _svc(args)
    virme = Path(__file__).resolve().parents[2]
    team_py = virme / ".venv" / "bin" / "python"
    acestep_up = None  # 처음 필요할 때 한 번만 확인
    missing = []
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
        print(f"    음악: {d / st['audio']}")
        if args.video and not (d / "video.mp4").exists():
            if not team_py.exists():
                print("    영상: 팀 모델 환경이 없습니다. virme 에서 bun run model:setup 을 먼저 실행하세요.")
                continue
            subprocess.run([str(team_py), str(virme / "scripts" / "render-audio-reactive.py"), "--music-dir", str(d)],
                           cwd=virme, env={**os.environ, "PYTHONPATH": str(virme / "model_service"),
                                           "PYTORCH_ENABLE_MPS_FALLBACK": "1"}, check=True)
        if (d / "video.mp4").exists():
            print(f"    영상: {d / 'video.mp4'}")
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


def cmd_reset(args) -> int:
    path = profile_db_path(args.profile)
    if not args.yes:
        print(f"{path} 를 지우려면 --yes 를 붙이세요", file=sys.stderr)
        return 2
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    print(f"삭제: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="virtueme", description="Virtueme 기록 처리·키워드 검색")
    ap.add_argument("-p", "--profile", default="default", help="프로필 이름 (사람 한 명 = DB 파일 하나)")
    ap.add_argument("--fake-models", action="store_true", help="모델 대신 해시 임베더 사용 (시험용)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="기록 가져오기")
    p.add_argument("source", choices=["kakao", "sms", "instagram", "photos", "synthetic"])
    p.add_argument("paths", nargs="*")
    p.add_argument("--me", action="append", help="본인 이름 (카카오톡·인스타그램 DM). 여러 번 지정 가능")
    p.add_argument("--seed", type=int, default=20260711)
    p.add_argument("--persona", type=int, default=None)
    p.add_argument("--start", default="2026-06-01")
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
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("demo", help="발표용 키워드의 음악·오디오 반응 영상 미리 만들기")
    p.add_argument("keywords", nargs="+")
    p.add_argument("--seconds", type=int, default=60, help="음악 길이(초)")
    p.add_argument("--video", action="store_true", help="팀 StyleGAN 환경으로 오디오 반응 영상도 만든다")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("people", help="가명 대응표 (로컬에서만 확인), 관계 붙이기")
    p.add_argument("--relation", action="append", metavar="가명=관계",
                   help="예: --relation '지인D=회사 동기'. 이야기에서 가명 대신 이 관계로 부른다. 실명은 쓰지 말 것")
    p.add_argument("--keep", action="append", metavar="이름",
                   help="제3자 이름 가리기에서 빼 둘 낱말 (예: 반려동물 이름이 사람으로 잡힐 때)")
    p.set_defaults(func=cmd_people)

    p = sub.add_parser("reset", help="프로필 DB 삭제")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_reset)

    args = ap.parse_args(argv)
    if args.cmd == "ingest" and args.source in ("kakao", "sms", "instagram", "photos") and not args.paths:
        ap.error("가져올 파일이나 폴더 경로를 적으세요")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
