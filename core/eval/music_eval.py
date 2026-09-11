"""만든 음악이 계획(정서 → 음악 매핑)을 따랐는지 잰다.

캐시한 곡마다 구간 음원(part*.wav, 음악 모델이 만든 그대로)과 이은 곡을 다시 듣는다 (virtueme_core/audio_check.py).
- 템포: 계획 BPM 과 ±5% 안에 드는 구간 수. 사람 귀에도 헷갈리는 박 단위 관계(2배·절반·3/2·2/3)로 맞는 구간은 따로 센다
- 조성: 계획 조성과 같음 / 나란한조(조표가 같음) / 장단조만 같음 / 다름
- 셈여림: 구간 계획 에너지와 구간 음량(RMS dB)의 순위 상관 (곡마다 평균을 뺀 값으로). 음악 모델이 만든 그대로와,
  music.stitch 가 계획대로 구간 음량을 맞춘 뒤를 비교한다

    uv run python eval/music_eval.py                  # $VIRTUEME_DATA/music 아래 모든 곡
결과: eval/music_results.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from virtueme_core import music  # noqa: E402
from virtueme_core.audio_check import check_section  # noqa: E402
from virtueme_core.config import DATA_DIR  # noqa: E402

OUT = ROOT / "eval" / "music_results.json"
RELATIONS = {1.0: "같음", 2.0: "2배", 0.5: "절반", 1.5: "3/2", 2 / 3: "2/3"}


def tempo_relation(bpm: float, planned: float) -> str | None:
    for ratio, name in RELATIONS.items():
        if abs(bpm - planned * ratio) <= 0.05 * planned * ratio:
            return name
    return None


def section_levels(d: Path, plan: dict, gains: list[float] | None) -> list[float]:
    parts = [music.read_wav((d / f"part{i}.wav").read_bytes()) for i in range(len(plan["sections"]))]
    audio, bounds = music.stitch(parts, gains_db=gains)
    return [music._rms_db(audio[:, int(a * music.SAMPLE_RATE): int(b * music.SAMPLE_RATE)]) for a, b in bounds]


def main() -> None:
    songs = []
    for plan_path in sorted((DATA_DIR / "music").glob("*/*/plan.json")):
        d = plan_path.parent
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if not all((d / f"part{i}.wav").exists() for i in range(len(plan["sections"]))):
            continue
        gains = music.section_gains(plan)
        before, after = section_levels(d, plan, None), section_levels(d, plan, gains)
        secs = []
        for i, s in enumerate(plan["sections"]):
            x, sr = music.read_wav((d / f"part{i}.wav").read_bytes())
            chk = check_section(x, sr, plan["bpm"], s["keyscale"])
            secs.append({"name": s["name"], "planned_key": s["keyscale"], "planned_energy": s["energy"],
                         "mood": s["mood"], "bpm": chk["bpm"], "tempo_relation": tempo_relation(chk["bpm"], plan["bpm"]),
                         "key": chk["key"]["key"], "key_r": chk["key"]["r"], "key_match": chk["key_match"],
                         "onsets_per_sec": chk["onsets_per_sec"], "centroid_hz": chk["centroid_hz"],
                         "rms_db_model": round(before[i], 2), "rms_db_mixed": round(after[i], 2), "gain_db": gains[i]})
        songs.append({"profile": d.parent.name, "music_id": d.name, "keyword": plan["keyword"], "bpm": plan["bpm"],
                      "sections": secs})
        print(f"{plan['keyword']} ({plan['bpm']} BPM)")
        for s in secs:
            print(f"  {s['name']}: 계획 {s['planned_key']} · 에너지 {s['planned_energy']:.2f}  →  {s['key']} ({s['key_match']}),"
                  f" {s['bpm']} BPM ({s['tempo_relation'] or '다름'}), 음량 {s['rms_db_model']} → {s['rms_db_mixed']} dB")
    allsec = [s for song in songs for s in song["sections"]]
    if not allsec:
        print("캐시한 곡이 없습니다")
        return

    def centered(key: str) -> list[float]:
        out = []
        for song in songs:
            v = np.array([s[key] for s in song["sections"]], dtype=float)
            out += list(v - v.mean())
        return out

    energy = centered("planned_energy")
    summary = {
        "songs": len(songs), "sections": len(allsec),
        "tempo_within_5pct": sum(s["tempo_relation"] == "같음" for s in allsec),
        "tempo_metrical_relation": sum(s["tempo_relation"] not in (None, "같음") for s in allsec),
        "tempo_other": sum(s["tempo_relation"] is None for s in allsec),
        "key": {k: sum(s["key_match"] == k for s in allsec) for k in ("exact", "relative", "mode", "other")},
        "mode_agrees": sum(s["key_match"] in ("exact", "mode") for s in allsec),
        "energy_vs_loudness_model": round(float(spearmanr(energy, centered("rms_db_model")).statistic), 3),
        "energy_vs_loudness_mixed": round(float(spearmanr(energy, centered("rms_db_mixed")).statistic), 3),
        "energy_vs_onset_density": round(float(spearmanr(energy, centered("onsets_per_sec")).statistic), 3),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    OUT.write_text(json.dumps({"summary": summary, "songs": songs}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
