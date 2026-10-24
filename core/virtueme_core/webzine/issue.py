"""웹진 한 호 만들기: 사실 세기 → 규칙표로 꼭지 고르기 → 글 쓰기·근거 검사 → 그림 고르기 → HTML·PDF·쪽별 PNG.

결과는 `$VIRTUEME_DATA/webzine/<프로필>/<호 ID>/` 에 남는다.
- issue.html  : 웹진 한 호 (브라우저에서 바로 열림, 그림은 img/ 상대 경로)
- issue.pdf   : A4 세로, 꼭지마다 한 쪽
- pg-*.png    : 쪽별 이미지 (SNS 공유·발표 자료용)
- issue.json  : 기간, 꼭지별 매칭 점수와 이유, 글, 근거 검사 결과, 쓴 사실 목록
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any

from .. import patterns
from ..config import DATA_DIR
from ..privacy import Masker
from . import facts as F
from . import match, render, write

HERE = Path(__file__).parent
VIRME = HERE.parents[2]
LATENT = [VIRME / "public" / "assets" / f"latent-{k}-loop.mp4" for k in ("landscape", "object", "situation")]
FASHION_TAGS = {"셀카", "인물 사진", "옷", "신발", "쇼핑"}


def issues_dir(profile: str) -> Path:
    return DATA_DIR / "webzine" / profile


class Images:
    """그림 자리 채우기. 그 주 사진을 먼저 쓰고, 모자라면 팀 StyleGAN 잠재공간 영상의 프레임을 쓴다."""

    def __init__(self, out: Path, photos: list, seed: str):
        self.dir = out / "img"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.photos: list[tuple[str, set[str]]] = []
        for r in photos:
            src = next((Path(m) for m in r.media if Path(m).exists()), None)
            if not src:
                continue
            dst = self.dir / f"p{len(self.photos):02d}.jpg"
            try:
                from PIL import Image, ImageOps

                with Image.open(src) as im:
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((1400, 1400))
                    im.save(dst, quality=86)
            except Exception:  # noqa: BLE001 — 열리지 않는 사진은 건너뛴다
                continue
            tags = {t["tag"] for t in r.meta.get("auto_tags", []) if t.get("score", 0) >= 0.3}
            self.photos.append((f"img/{dst.name}", tags))
        self.seed = int(hashlib.sha1(seed.encode()).hexdigest()[:8], 16)
        self.frames = self._frames(12)

    def _frames(self, n: int) -> list[str]:
        out = []
        ff = shutil.which("ffmpeg")
        for i in range(n):
            src = LATENT[i % len(LATENT)]
            dst = self.dir / f"l{i:02d}.jpg"
            if ff and src.exists():
                t = ((self.seed >> (i % 16)) % 1100) / 100 + 0.2
                subprocess.run([ff, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                                "-vf", "scale=768:768:flags=lanczos", "-q:v", "3", str(dst)], timeout=30)
            if dst.exists():
                out.append(f"img/{dst.name}")
        return out

    def pick(self, i: int, prefer: str | None = None) -> str | None:
        pool = self.photos
        if prefer == "fashion":
            fav = [p for p in pool if p[1] & FASHION_TAGS]
            pool = fav + [p for p in pool if p not in fav]
        if pool:
            return pool[i % len(pool)][0]
        return self.latent(i)

    def latent(self, i: int) -> str | None:
        return self.frames[i % len(self.frames)] if self.frames else None


def _issue_id(profile: str, start: str, end: str) -> str:
    return f"{end.replace('-', '')}-{int(time.time()) % 100000:05d}"


def _quote(fx: dict[str, Any], masker: Masker) -> str | None:
    best = None
    for r in fx["monologue"] + [r for r in fx["records"] if r.is_self and r.kind == "message"]:
        t = re.sub(r"#\S+|[\U0001F000-\U0001FAFF\u2600-\u27BF\u200d\ufe0f]", "", masker.mask(r.text or "")).strip()
        for s in re.split(r"(?<=[.!?])\s+|\n", t):
            s = s.strip(" ~ㅠㅋ")
            if 14 <= len(s) <= 60 and not re.search(r"지인|\(나\)|\(누군가\)", s):
                if best is None or (r.kind in ("post", "story") and len(s) > len(best)):
                    best = s
        if best and r.kind in ("post", "story"):
            break
    return best


def _numbers(fx: dict[str, Any]) -> list[tuple[str, str, str, str, str]]:
    b, out = fx["body"], []
    out.append(("Records", f"{len(fx['records'])}", "건", "남긴 기록", "대화·사진·시청·결제·방문을 모두 센 수"))
    out.append(("Night", f"{round(fx['night_share'] * 100)}", "%", "밤의 비율", "밤 11시~새벽 5시에 남긴 기록의 비율"))
    if b.get("steps"):
        out.append(("Steps", f"{b['steps']:,}", "보", "하루 걸음", "삼성 헬스 하루 평균 걸음 수"))
    if b.get("sleep_h"):
        out.append(("Sleep", f"{b['sleep_h']}", "h", "하루 수면", "하루 평균 수면 시간"))
    spend = sum(int(float(r.meta.get("amount") or 0)) for r in fx["pays"])
    if spend:
        out.append(("Spend", f"{spend:,}", "원", "이번 주 결제", f"결제 {len(fx['pays'])}번의 합"))
    if b.get("run_km"):
        out.append(("Run", f"{b['run_km']}", "km", "달린 거리", "달리기 운동 기록의 합"))
    t = fx["taste"]
    if len(out) < 6 and t.get("channels"):
        out.append(("Watch", f"{sum(c['count'] for c in t['channels'])}", "편", "본 영상", "상위 채널의 영상 수"))
    return out


def make_issue(service, end: date | None = None, days: int = 7, offline: bool = False, render_files: bool = True,
               log=print) -> dict[str, Any]:
    t0 = time.time()
    fx = F.collect(service.store, end=end, days=days)
    rules = match.load_rules()
    scores = match.score(fx["records"], rules)
    sections = match.pick(scores, rules)
    included = [s["template"] for s in sections if s["included"]]
    log(f"기간 {fx['range'][0]}~{fx['range'][1]}, 기록 {len(fx['records'])}건, 꼭지 {', '.join(included)}")

    text: dict[str, Any] = {}
    checks: dict[str, Any] = {}
    cover: dict[str, Any] = {}
    if not offline:
        ed = write.editorial(service, fx, included, log=log)
        text.update({k: v for k, v in ed.get("sections", {}).items() if k != "cover"})
        cover = ed.get("sections", {}).get("cover") or {}
        checks.update(ed.get("checks", {}))
        if ed.get("offline"):
            offline = True
    # 표지·항목의 짧은 문장은 앵커가 없으므로 앵커 밖 수치 검사로 거른다. 맞는 값이 없는 문장은 싣지 않는다
    _note, evidence = write._note(service, fx, included)
    cover["lines"] = write.safe_lines(cover.get("lines") or [], evidence, service)
    heads = [f["text"] for f in fx["facts"] if f["headline"]] + [f["text"] for f in fx["facts"] if len(f["text"]) <= 45]
    for h in heads:
        if len(cover["lines"]) >= 3:
            break
        nums = set(re.findall(r"\d[\d,.]*", h))
        if h in cover["lines"] or (nums and nums <= set(re.findall(r"\d[\d,.]*", " ".join(cover["lines"])))):
            continue   # 같은 숫자를 이미 다른 말로 실었다
        cover["lines"].append(h)
    # 이름을 모르는 장소를 이름처럼 쓴 제목은 꼭지 이름으로 바꾼다
    for k, v in text.items():
        if isinstance(v, dict) and re.search(r"이름\s*(없는|을 붙이지 않은)", str(v.get("headline") or "")):
            v["headline"] = next((s["slot"] for s in sections if s["template"] == k), "다녀온 곳")
    if not cover.get("headline"):
        cover["headline"] = "이번 주의 나"
    for k in ("digital_preview", "fashion"):
        if text.get(k):
            for lst in ("items", "looks"):
                if text[k].get(lst):
                    for x in text[k][lst]:
                        if x.get("line") and not write.safe_lines([x["line"]], evidence, service):
                            x["line"] = ""
    for k in included:
        if k in ("novel", "poem", "data_portrait") or text.get(k):
            continue
        text[k] = write.offline_section(k, fx)
    if "poem" in included:
        text["poem"] = write.poem(service, fx, log=log) if not offline else {**write._found_poem(
            [r.text for r in fx["monologue"] + fx["night_written"] + fx["records"] if r.text and r.is_self
             and r.kind in ("post", "story", "message")]), "offline": True}
    if "novel" in included:
        text["novel"] = write.novel(service, fx, log=log) if not offline else {}
        if text["novel"].get("check"):
            checks["novel"] = text["novel"]["check"]
    if "novel" in included and not (text.get("novel") or {}).get("paragraphs"):
        # 소설을 못 썼으면 시로 바꿔 싣는다 (시는 키 없이도 기록 문장으로 만든다)
        for s in sections:
            if s["template"] == "novel":
                s["included"], s["why"] = False, "소설을 쓰지 못해 시로 대신합니다"
            if s["template"] == "poem" and not s["included"]:
                s["included"], s["why"] = True, "소설 대신 싣습니다"
                text["poem"] = write.poem(service, fx, log=log) if not offline else {**write._found_poem(
                    [r.text for r in fx["monologue"] + fx["night_written"] + fx["records"] if r.text and r.is_self
             and r.kind in ("post", "story", "message")]), "offline": True}
        included = [s["template"] for s in sections if s["included"]]

    start, end_d = (date.fromisoformat(x) for x in fx["range"])
    iso = end_d.isocalendar()
    issue_id = _issue_id(service.profile, *fx["range"])
    out = issues_dir(service.profile) / issue_id
    out.mkdir(parents=True, exist_ok=True)
    masker = Masker.from_store(service.store)
    images = Images(out, fx["photos"], f"{service.profile}:{fx['range'][1]}")
    sources = len({r.source for r in fx["records"]})
    issue = {
        "id": issue_id, "profile": service.profile, "range": fx["range"], "days": days,
        "title": f"{iso.year} · W{iso.week:02d}" if days == 7 else f"{end_d.isoformat()}",
        "period_ko": f"{patterns.ko_date(start)} – {patterns.ko_date(end_d)}" if days > 1 else patterns.ko_date(end_d),
        "subtitle": f"{patterns.ko_date(start)}부터 {days}일 동안 남은 기록으로 만든 한 호",
        "records": len(fx["records"]), "sources": sources, "offline": offline,
        "sections": sections, "cover": cover, "text": text, "checks": checks,
        "facts": [{k: v for k, v in f.items() if k != "values"} for f in fx["facts"]],
        "rules_version": rules.get("version"),
    }
    data_facts = [f for f in fx["facts"] if f["section"] == "data_portrait"]
    pl = next((f["text"] for f in fx["facts"] if f["column"] == "issue.place"), "")
    novel_ck = (text.get("novel") or {}).get("check") or {}
    ctx = {
        "fx": fx, "text": text, "cover": cover, "images": images, "quote": _quote(fx, masker),
        "stats": [(k, v) for k, v in (("대화", fx["counts"].get("message", 0)), ("사진", len(fx["photos"])),
                                      ("시청", fx["counts"].get("watch", 0)), ("음악", fx["counts"].get("listen", 0)),
                                      ("결제", len(fx["pays"])), ("방문", fx["counts"].get("visit", 0)))],
        "numbers": _numbers(fx), "data_facts": data_facts, "place_line": pl,
        "photo_note": f"이번 주 사진 {len(images.photos)}장" + (" · 사진이 없는 자리는 잠재공간 영상 프레임" if len(images.photos) < 6 else ""),
        "novel_check": (f"앵커 {novel_ck.get('anchors', 0)}개 가운데 {novel_ck.get('ok', 0)}개가 기록과 맞음" if novel_ck else "검사 없음"),
        "foot_a": [("대화 상대", esc_list([c["who"] for c in fx["cast"]])), ("근거 검사", _ck(checks.get("article"))),
                   ("다음 호", "다음 주 기록으로 새 호가 나옵니다")],
        "foot_b": [("장소", esc_list([p["name"] for p in fx["places"][:4]])), ("근거 검사", _ck(checks.get("documentary"))),
                   ("좌표", "좌표와 집 주소는 싣지 않습니다")],
        "foot_c": [("재료", "셀카·옷 사진, 옷·화장품 결제와 검색"), ("근거 검사", _ck(checks.get("fashion"))), ("형식", "Trend report")],
        "foot_d": [("영상", f"{fx['counts'].get('watch', 0)}편"), ("음악", f"{fx['counts'].get('listen', 0)}곡 재생"),
                   ("근거 검사", _ck(checks.get("digital_preview")))],
        "foot_e": [("세는 법", "개수·평균·비율만 셉니다"), ("비교", "앞선 4주 기록과 견줍니다" if fx["prev"] else "앞선 기록이 없어 비교하지 않습니다"),
                   ("출처", f"{sources}종")],
    }
    page = render.issue_html(issue, ctx)
    (out / "issue.html").write_text(page, "utf-8")
    issue["files"] = {"html": "issue.html"}
    if render_files:
        try:
            rep = run_render(out / "issue.html", out, [f"pg-{k}" for k in ["cover", *included]]
                             + _novel_pages(page))
            issue["files"].update({"pdf": "issue.pdf", "pages": rep.get("shots", [])})
            issue["render"] = {"fonts": rep.get("fonts", [])}
        except Exception as err:  # noqa: BLE001 — 렌더가 안 돼도 HTML 은 남긴다
            log(f"PDF·PNG 렌더 실패: {err}")
            issue["render_error"] = str(err)[-600:]
    issue["seconds"] = round(time.time() - t0, 1)
    (out / "issue.json").write_text(json.dumps(issue, ensure_ascii=False, indent=1, default=str), "utf-8")
    log(f"웹진 {issue_id} 완료 ({issue['seconds']}초): {out}")
    return issue


def _novel_pages(page: str) -> list[str]:
    return [f"pg-novel{n}" for n in re.findall(r'id="pg-novel(\d+)"', page)]


def esc_list(xs: list[str]) -> str:
    seen = list(dict.fromkeys(x for x in xs if x))
    return render.esc(", ".join(seen[:4]) or "없음")


def _ck(c: dict[str, Any] | None) -> str:
    if not c:
        return "앵커 없음 (사실 문장만 실음)"
    return f"앵커 {c['anchors']}개 중 {c['ok']}개 맞음" + (f", {c['blurred']}개 흐림" if c.get("blurred") else "")


def run_render(html_path: Path, out_dir: Path, ids: list[str], timeout: int = 240) -> dict[str, Any]:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node 가 없습니다 (virme/ 의 playwright 로 그립니다)")
    shots = [[f"#{i}", f"{i}.png"] for i in ids]
    res = subprocess.run([node, str(HERE / "render.mjs"), str(html_path), str(out_dir), json.dumps(shots)],
                         capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError(res.stderr[-1500:])
    return json.loads(res.stdout.strip().splitlines()[-1])


def list_issues(profile: str) -> list[dict[str, Any]]:
    out = []
    root = issues_dir(profile)
    if not root.exists():
        return out
    for d in sorted(root.iterdir(), reverse=True):
        j = d / "issue.json"
        if j.exists():
            x = json.loads(j.read_text("utf-8"))
            out.append({"id": x["id"], "title": x["title"], "range": x["range"], "period_ko": x["period_ko"],
                        "sections": [s["template"] for s in x["sections"] if s["included"]], "files": x.get("files", {}),
                        "cover": (x.get("cover") or {}).get("headline")})
    return out
