"""근거 계약 v1 과 v2 비교 (자동 채점).

두 단계로 나눈다.

1. generate: 같은 키워드·장르로 v1 프롬프트(Storyseed 원래 규칙)와 v2 프롬프트(기록 번호 앵커)로 이야기를 쓴다.
2. score   : 저장한 이야기를 다시 채점한다. 이야기는 다시 쓰지 않으므로 검사기를 고친 뒤 몇 번이고 다시 잴 수 있다.

채점 기준(정답)은 **판정 모델(claude-sonnet-4.5)이 근거 기록 목록을 보고 앵커마다 내린 판단**이다. 검사기 자신을
기준으로 삼으면 순환이 되므로, 이 판단에 비춰 v1 화이트리스트와 v2 검사기가 각각 얼마나 맞게 판정했는지 잰다.
판정 모델도 틀릴 수 있으므로 절대 정확도가 아니라 두 방식의 비교로 읽는다. 판정은 결과 파일에 저장해 다시 사지 않는다.

지표
- 날조율: 판정 모델이 "기록에 없다"고 본 앵커 비율 (v1 이야기 / v2 이야기)
- 검사기 판정 정확도: v1 화이트리스트, v2 검사기가 판정 모델과 같은 판단을 낸 비율, 거짓 통과·거짓 탈락 수
- 인용 정확도 (v2 이야기): 앵커의 ref 가 판정 모델이 고른 기록과 같은 비율
- 앵커 밖 날짜·시각: `<em>` 없이 쓴 날짜·시각 가운데 근거 기록에 없는 것 (편당)
- 강등 뒤 남은 틀린 날짜·시각: 틀린 앵커를 plain·blur·rewrite 로 처리한 뒤 본문에 남은 수

    uv run python eval/grounding_eval.py generate            # 윤서연 + 합성 페르소나, 키워드 5개 × 장르 2개 × v1·v2
    uv run python eval/grounding_eval.py score --rewrite     # 판정 모델 채점 + 강등 비교
결과: eval/grounding_results.json (이야기 원문, 판정, 요약)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from virtueme_core import patterns, story  # noqa: E402
from virtueme_core.config import JUDGE_MODEL  # noqa: E402
from virtueme_core.llm import chat_json, chat_text  # noqa: E402
from virtueme_core.service import Service  # noqa: E402
from virtueme_core.verify import ANCHOR_RE, Verifier, check_anchor, parse_claims, verify_story  # noqa: E402

KEYWORDS = {"seoyeon": ["러닝", "콩이", "엄마", "제주", "야근"], "synth": ["등산", "사람들", "휴식", "기록", "탐색"]}
WRITER = "anthropic/claude-sonnet-4.5"
ORACLE = "anthropic/claude-sonnet-4.5"
OUT = ROOT / "eval" / "grounding_results.json"
SCORE_VERSION = "s5"  # 채점 방식을 바꾸면 올린다 (강등 뒤 남은 값은 다시 센다)

ORACLE_SYSTEM = """너는 소설 속 사실 주장을 실제 기록과 대조하는 검수자다. 번호 붙은 기록 목록과, 소설에서 [A번호: …] 로 표시한
주장 목록이 주어진다. 주장마다 판단한다.
- supported=true: 주장 안의 사실 값(날짜, 요일, 시각, 횟수, 활동·장소·사람·물건)이 기록에 있고, 주장이 한 사건으로 묶어 말한
  값들은 같은 기록(또는 그 주장이 가리키는 여러 기록)에 함께 있다. 날짜 표현이 기록 날짜와 하루라도 다르면 false.
- 과장된 수식어·감정·해석("최고의", "운명처럼")은 판단에서 뺀다. 사실 값만 본다.
- record: 주장을 뒷받침하는 기록 번호 하나 (없으면 null)
JSON 하나만 출력한다: {"verdicts":[{"a":1,"supported":true,"record":3}]}"""


def v1_whitelist(result: dict) -> set[str]:
    """노벨 화면 v1(내 기록 모드)과 같은 방식의 화이트리스트."""
    s: set[str] = set()
    add = lambda v: v and s.add(str(v).strip())  # noqa: E731
    for r in result["evidence_records"] + result.get("adjacent_records", []):
        add(r["date_ko"])
        _, mm, dd = r["date"].split("-")
        add(f"{int(mm)}월"), add(f"{int(dd)}일"), add(r["weekday"] + "요일"), add(r["time"])
        h = r["hour"]
        h12 = h % 12 or 12
        for x in (f"{h}시", f"{h12}시", f"{'오전' if h < 12 else '오후'} {h12}시", f"{'아침' if h < 12 else '저녁'} {h12}시",
                  f"새벽 {h12}시", f"밤 {h12}시"):
            add(x)
        for t in r.get("photo_tags", []):
            add(t)
        if r.get("day_index"):
            add(f"{r['day_index']}일째")
        for m in re.finditer(r"\d+(?:\.\d+)?\s?(?:km|킬로|분|시간|개|번|회|잔|명|층|kg)", r.get("text") or ""):
            add(m.group(0)), add(m.group(0).replace(" ", ""))
    no = result["narrative_object"]
    st, b = no.get("stats") or {}, no.get("grade_basis") or {}
    for t in no.get("top_terms", []):
        add(t["term"])
    for t in no.get("top_tags", []):
        add(t["tag"])
    for n in (st.get("distinct_days"), st.get("span_days"), st.get("peak_count"), b.get("strong_records"), b.get("strong_days")):
        if n is not None:
            for u in ("일", "번", "회", "개", "건", "명", "주", "달"):
                add(f"{n}{u}")
    for d in (st.get("first_date"), st.get("last_date"), st.get("peak_date")):
        if d:
            add(patterns.ko_date(d))
    for c in no.get("change_points", []):
        add(patterns.ko_date(c["date"]))
    add(no["keyword"])
    return s


def parse_story(text: str) -> tuple[str, list[str]]:
    body = (text or "").strip()
    m = re.search(r"^\s*(?:TITLE|제목)\s*[:：]\s*(.+)$", body, re.M)
    title = m.group(1).strip() if m else ""
    if m:
        body = body.replace(m.group(0), "").strip()
    return title, [p.replace("\n", " ").strip() for p in re.split(r"\n\s*\n+", body) if p.strip()]


def extra_counts(result: dict) -> set[int]:
    no = result["narrative_object"]
    return {x for x in ((no.get("stats") or {}).get("distinct_days"), (no.get("grade_basis") or {}).get("strong_records"))
            if x is not None}


def residual_false(final_html: list[str], ver: Verifier) -> int:
    """강등 뒤 본문(HTML 을 걷어 낸 글)에 남은, 근거 기록 어디에도 없는 날짜·시각 수."""
    n = 0
    for h in final_html:
        text = html.unescape(re.sub(r"<[^>]+>", "", h))
        for c in parse_claims(text):
            if c.kind in ("date", "dom", "day_index", "time"):
                n += 0 if any(check_anchor(c.text, [f])[0] for f in ver.facts) else 1
    return n


def generate(args) -> None:
    """이야기를 만들어 결과 파일에 덧붙인다. --label 로 저장 이름을 바꿀 수 있다 (예: 규칙을 고친 v2 → v2b)."""
    rows: list[dict] = json.loads(OUT.read_text(encoding="utf-8"))["rows"] if OUT.exists() else []
    for prof in args.profiles:
        svc = Service(prof)
        jobs = [(k, g, v) for k in KEYWORDS.get(prof, KEYWORDS["seoyeon"]) for g in args.genres for v in args.versions]
        # 검색은 임베딩 모델을 쓰므로 순서대로, 이야기 생성만 병렬로 돌린다
        results = {k: svc.query(k) for k in dict.fromkeys(k for k, _, _ in jobs)}

        def run(job):
            k, g, v = job
            p = story.story_prompt(results[k], genre=g, version=v, seed=zlib.crc32(f"{k}:{g}".encode()))
            raw = chat_text(p["system"], p["user"], model=WRITER, temperature=0.9, max_tokens=3000)
            # 판정 모델에게는 v1·v2 모두 같은 번호 목록을 보여 주기 위해 v2 번호 순 기록 ID 를 남긴다
            ids = story.story_prompt(results[k], genre=g, version="v2", seed=0)["evidence_ids"]
            label = args.label if (args.label and v == "v2") else v
            return {"profile": prof, "keyword": k, "genre": g, "version": label, "prompt_version": v, "raw": raw,
                    "evidence_ids": ids, "grade": results[k]["narrative_object"]["evidence_grade"]}

        with ThreadPoolExecutor(max_workers=4) as pool:
            rows += list(pool.map(run, jobs))
        print(prof, len(jobs), "편 생성")
    OUT.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


def oracle(recs, anchors: list[dict]) -> list[dict]:
    if not anchors:
        return []
    lines = []
    for i, r in enumerate(recs, 1):
        who = [p for p in r.participants if p != "나"]
        body = r.text or " ".join(t["tag"] for t in r.meta.get("auto_tags", []))
        lines.append(f"{i}. {patterns.ko_date(r.day)} {patterns.WEEKDAYS[r.dt.weekday()]}요일 {r.dt.strftime('%H:%M')} "
                     f"[{r.source}{' · ' + ', '.join(who) if who else ''}] {body.replace(chr(10), ' / ')[:160]}")
    claims = "\n".join(f"[A{j + 1}: {a['text']}] (문장: {a['sentence'][:120]})" for j, a in enumerate(anchors))
    data = chat_json(ORACLE_SYSTEM, "기록:\n" + "\n".join(lines) + f"\n\n주장:\n{claims}", model=ORACLE, max_tokens=3000)
    got = {int(v.get("a", 0)): v for v in data.get("verdicts", [])}
    return [{"supported": bool(got.get(j + 1, {}).get("supported", False)), "record": got.get(j + 1, {}).get("record")}
            for j in range(len(anchors))]


def score_row(svc: Service, row: dict, result: dict, rewrite: bool) -> dict:
    title, paras = parse_story(row["raw"])
    recs = [svc.store.record(i) for i in row["evidence_ids"]]
    start = svc._start()
    extra = extra_counts(result)
    ver = Verifier(recs, start, extra)
    allow = v1_whitelist(result)
    anchors = []
    for para in paras:
        sentences = re.split(r"(?<=[.!?…])\s+", para)
        for m, a in zip(ANCHOR_RE.finditer(para), ver.verify_text(para)):
            inner = re.sub(r"<[^>]+>", "", m.group(2))
            sent = next((s for s in sentences if m.group(0) in s), para)
            anchors.append({"text": inner, "sentence": re.sub(r"<[^>]+>", "", sent), "refs": a.refs,
                            "v2_pass": a.status in ("ok", "ref_fixed", "unreferenced_ok"), "status": a.status,
                            "failed": a.failed_claims, "v1_pass": inner.strip() in allow})
    if "oracle" not in row or len(row["oracle"]) != len(anchors):
        row["oracle"] = oracle(recs, anchors)
    for a, o in zip(anchors, row["oracle"]):
        a.update({"truth": o["supported"], "truth_record": o["record"]})
    # 규칙 검사 + 문맥 판정(판정 모델) 결과. LLM 호출이므로 저장해 두고 다시 쓴다 (채점 방식이 바뀌면 새로 받는다)
    if row.get("scored") != SCORE_VERSION or len(row.get("context_status", [])) != len(anchors):
        ctx_fn = lambda s_, t_: chat_json(s_, t_, model=JUDGE_MODEL, max_tokens=1500)  # noqa: E731
        vc = verify_story(paras, recs, mode="plain", start=start, extra_counts=extra, context_fn=ctx_fn)
        row["context_status"] = [a["status"] for a in vc["anchors"]]
    for a, st in zip(anchors, row["context_status"]):
        a["v2c_pass"] = st in ("ok", "ref_fixed", "unreferenced_ok")
    row["anchors"] = anchors
    if row.get("scored") != SCORE_VERSION:
        for k in [k for k in row if k.startswith("residual_")]:
            row.pop(k)
    for mode in ("plain", "blur") + (("rewrite",) if rewrite else ()):
        if mode == "rewrite" and "residual_rewrite" in row:
            continue  # 다시 쓰기는 LLM 호출이라 한 번만 잰다
        fn = (lambda s_, t_: chat_text(s_, t_, model=svc.judge_model, temperature=0.4)) if mode == "rewrite" else None
        v = verify_story(paras, recs, mode=mode, title=title, start=start, extra_counts=extra, rewrite_fn=fn)
        row[f"residual_{mode}"] = residual_false(v["paragraphs_html"], ver)
        row["loose_unsupported"] = v["stats"]["loose_unsupported"]
    row["scored"] = SCORE_VERSION
    return row


def confusion(anchors: list[dict], key: str) -> dict:
    tp = sum(1 for a in anchors if a[key] and a["truth"])
    fp = sum(1 for a in anchors if a[key] and not a["truth"])
    fn = sum(1 for a in anchors if not a[key] and a["truth"])
    tn = sum(1 for a in anchors if not a[key] and not a["truth"])
    n = max(1, tp + fp + fn + tn)
    return {"accuracy": round((tp + tn) / n, 3), "false_pass": fp, "false_reject": fn, "caught": tn}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for version in ("v1", "v2", "v2b"):
        rs = [r for r in rows if r["version"] == version]
        a = [x for r in rs for x in r["anchors"]]
        if not rs:
            continue
        d = {"stories": len(rs), "anchors_per_story": round(len(a) / len(rs), 2),
             "fabricated_rate": round(mean(not x["truth"] for x in a), 3) if a else None,
             "v1_whitelist_vs_truth": confusion(a, "v1_pass"), "v2_checker_vs_truth": confusion(a, "v2_pass"),
             "v2_checker_context_vs_truth": confusion(a, "v2c_pass"),
             "loose_unsupported_per_story": round(mean(r["loose_unsupported"] for r in rs), 2)}
        if version != "v1":
            cited = [x for x in a if x["refs"] and x["truth_record"]]
            d["ref_matches_truth"] = round(mean(x["truth_record"] in x["refs"] for x in cited), 3) if cited else None
        for mode in ("plain", "blur", "rewrite"):
            if all(f"residual_{mode}" in r for r in rs):
                d[f"residual_false_{mode}"] = sum(r[f"residual_{mode}"] for r in rs)
        out[version] = d
    return out


def score(args) -> None:
    data = json.loads(OUT.read_text(encoding="utf-8"))
    rows = data["rows"]
    for prof in sorted({r["profile"] for r in rows}):
        svc = Service(prof)
        results = {k: svc.query(k) for k in dict.fromkeys(r["keyword"] for r in rows if r["profile"] == prof)}
        svc.verify([], [])
        mine = [r for r in rows if r["profile"] == prof]
        for r in mine:  # 첫 실행 결과 파일에는 번호 목록이 없으므로 다시 만든다
            if "evidence_ids" not in r:
                r["evidence_ids"] = story.story_prompt(results[r["keyword"]], genre=r["genre"], version="v2",
                                                       seed=0)["evidence_ids"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda r: score_row(svc, r, results[r["keyword"]], args.rewrite), mine))
        print(prof, json.dumps(summarize(mine), ensure_ascii=False))
    summary = summarize(rows)
    print("전체", json.dumps(summary, ensure_ascii=False, indent=1))
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--profiles", nargs="+", default=["seoyeon", "synth"])
    g.add_argument("--genres", type=int, nargs="+", default=[0, 5])
    g.add_argument("--versions", nargs="+", default=["v1", "v2"])
    g.add_argument("--label", default=None, help="v2 로 만든 이야기를 이 이름으로 저장 (예: v2b)")
    s = sub.add_parser("score")
    s.add_argument("--rewrite", action="store_true")
    args = ap.parse_args()
    generate(args) if args.cmd == "generate" else score(args)


if __name__ == "__main__":
    main()
