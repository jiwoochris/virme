"""샘플 프로필(윤서연)에서 검색 방식 비교.

정답 두 가지
- 은(silver) 라벨: 강한 모델(claude-sonnet-4.5)이 본인 기록 전부를 키워드마다 0·1·2 로 판정한 것.
  판정 줄에 대화 상대의 관계("지인A(대학 친구)")를 함께 보여 준다 (관계를 모르면 "친구" 라벨이 흔들린다).
  eval/<프로필>_silver2.json 에 저장해 두고 다시 만들지 않는다.
- 금(gold) 라벨 (--gold): 사람이 core 서버의 /label 화면에서 붙인 것. 라벨을 붙인 기록만 채점한다.
  판정 단계의 실제 성능은 금 라벨로 잰 값을 믿는다.

비교하는 방식
- 임베딩: LLM 판정 없이 임베딩·직접 일치만
- 판정 3단계: 관계 키워드("친구")도 다른 키워드처럼 찾는다 (판정 기준 v4, 판정은 캐시에서 다시 읽는다)
- 판정 4단계: 관계 키워드면 기록 넣기 화면에서 붙인 관계로 후보를 더하고 판정 줄에 관계를 보여 준다 (v5r). 다른 키워드는 3단계와 같다

    uv run virtueme -p seoyeon build            # 먼저 샘플을 가져와 빌드
    uv run python eval/profile_eval.py -p seoyeon
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from virtueme_core.query import judge  # noqa: E402
from virtueme_core.service import Service  # noqa: E402

KEYWORDS = ["외로움", "러닝", "강아지", "바다", "야근", "엄마", "제주", "친구", "피곤", "행복"]
SILVER_MODEL = "anthropic/claude-sonnet-4.5"
SILVER_FILE = "{profile}_silver2.json"
# (이름, LLM 판정, 관계 키워드를 붙인 관계로 찾기)
METHODS = [("임베딩", False, True), ("판정 3단계", True, False), ("판정 4단계", True, True)]


def prf(pred: set[str], gold: set[str]) -> tuple[float, float, float]:
    tp = len(pred & gold)
    p = tp / len(pred) if pred else (1.0 if not gold else 0.0)
    r = tp / len(gold) if gold else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--profile", default="seoyeon")
    ap.add_argument("--judge-model", default=None, help="판정 모델 (기본: config.JUDGE_MODEL)")
    ap.add_argument("--gold", action="store_true", help="사람이 붙인 라벨(/label)로 채점")
    args = ap.parse_args()
    svc = Service(args.profile, **({"judge_model": args.judge_model} if args.judge_model else {}))
    eng = svc.engine
    own = [r for r in eng.records if r.is_self]
    path = ROOT / "eval" / SILVER_FILE.format(profile=args.profile)
    silver: dict[str, dict[str, int]] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    keywords = KEYWORDS
    if args.gold:
        silver = {k: v for k, v in svc.store.labels().items() if len(v) >= 20}
        keywords = sorted(silver)
        if not keywords:
            print("라벨이 20건 이상 붙은 키워드가 없습니다. /label 화면에서 먼저 라벨을 붙이세요.")
            return
    for kw in keywords:
        if args.gold:
            continue
        if kw not in silver:
            print(f"은 라벨 생성: {kw} ({len(own)}건)")
            silver[kw] = judge(svc.store, kw, own, SILVER_MODEL)
            path.write_text(json.dumps(silver, ensure_ascii=False, indent=0), encoding="utf-8")

    rows = []
    for kw in keywords:
        g2 = {rid for rid, v in silver[kw].items() if v == 2}
        g1 = {rid for rid, v in silver[kw].items() if v >= 1}
        labeled = set(silver[kw])
        line = [f"{kw:4s} 정답(직접 {len(g2):3d}, 간접 포함 {len(g1):3d})"]
        for name, use, rel_aware in METHODS:
            eng.relation_aware = rel_aware
            try:
                res = eng.query(kw, use_judge=use)
            finally:
                eng.relation_aware = True
            s = {x.record.id for x in res.strong} & labeled
            sw = (s | {x.record.id for x in res.weak}) & labeled
            p, r, f = prf(s, g2)
            p1, r1, f1 = prf(sw, g1)
            rows.append((name, f, f1, p, r))
            line.append(f"{name}: 강 P{p:.2f} R{r:.2f} F1 {f:.2f} | 약이상 F1 {f1:.2f} | {res.grade}")
        print("  ".join(line))
    for name, *_ in METHODS:
        sel = [x for x in rows if x[0] == name]
        print(f"평균 {name}: 강한 근거 F1 {sum(x[1] for x in sel) / len(sel):.3f}"
              f" (P {sum(x[3] for x in sel) / len(sel):.3f}, R {sum(x[4] for x in sel) / len(sel):.3f}),"
              f" 약한 근거 이상 F1 {sum(x[2] for x in sel) / len(sel):.3f}")


if __name__ == "__main__":
    main()
