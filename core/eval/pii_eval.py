"""제3자 이름 가리기(pii.py) 평가: 한국어 40문장 (이름 있는 문장 20, 대명사·관계어·반려동물만 있는 문장 20).

    uv run python eval/pii_eval.py
"""
from __future__ import annotations

import json
from pathlib import Path

from virtueme_core import pii


def main() -> None:
    rows = [json.loads(line) for line in (Path(__file__).parent / "pii_ko.jsonl").open(encoding="utf-8")]
    tp = fp = fn = 0
    for r, (_, names) in zip(rows, pii.mask_texts([r["text"] for r in rows])):
        gold, pred = set(r["names"]), set(names)
        tp, fp, fn = tp + len(gold & pred), fp + len(pred - gold), fn + len(gold - pred)
        if gold != pred:
            print("놓침" if gold - pred else "잘못 가림", r["text"], sorted(gold), sorted(pred))
    print(f"이름 {tp + fn}개 중 {tp}개 가림, 잘못 가린 낱말 {fp}개 "
          f"(정밀도 {tp / max(tp + fp, 1):.3f}, 재현율 {tp / max(tp + fn, 1):.3f})")


if __name__ == "__main__":
    main()
