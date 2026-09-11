import json
import shutil
import subprocess
from pathlib import Path

import pytest

from virtueme_core.ingest import synthetic as S

HTML = Path(__file__).resolve().parents[2] / "public" / "storyseed-novel.html"
TOOL = Path(__file__).parent / "tools" / "storyseed_synth.mjs"


def test_mulberry32_and_hash_known_values():
    r = S.mulberry32(20260711)
    first = [r() for _ in range(3)]
    assert all(0 <= v < 1 for v in first)
    assert S.hash_str("abc") == 440920331  # FNV-1a 32비트 기준값
    assert S.js_round(2.5) == 3 and S.js_round(-2.5) == -2


def test_v8_sort_is_permutation():
    r = S.mulberry32(1)
    out = S.v8_sort(list(range(35)), lambda a, b: r() - 0.5)
    assert sorted(out) == list(range(35)) and out != list(range(35))


@pytest.mark.skipif(shutil.which("node") is None or not HTML.exists(), reason="node 또는 Storyseed 원본이 없음")
@pytest.mark.parametrize("seed,idx", [(20260711, None), (42, 3), (777, 29), (20260822, None)])
def test_matches_storyseed_javascript(seed, idx):
    args = ["node", str(TOOL), str(HTML), str(seed)] + ([] if idx is None else [str(idx)])
    js = json.loads(subprocess.check_output(args))
    pool = S.build_personas(seed, 30)
    k = S.default_persona_index(seed) if idx is None else idx
    me = pool[k]
    ev = S.synth_events(me, seed)
    assert js["k"] == k
    for key in ("name", "sex", "age", "province", "occupation", "hobbies", "uuid"):
        assert js["persona"][key] == me[key], key
    assert len(js["events"]) == len(ev)
    for a, b in zip(js["events"], ev):
        assert (a["d"], a["tag"], a["gi"], a["ch"]) == (b["d"], b["tag"], b["gi"], b["ch"])
        assert abs(a["h"] - b["h"]) < 1e-9


def test_dataset_records_carry_truth():
    persona, recs = S.synthetic_dataset()
    assert persona["name"] and len(recs) > 100
    assert all(r.meta["truth"]["group_id"] in r.tags for r in recs)
    assert recs[0].ts.startswith("2026-06-")
