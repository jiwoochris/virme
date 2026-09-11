"""서사 오브젝트의 정서 곡선으로 음악 만들기 (ACE-Step 1.5).

흐름
1. plan(): 근거 기간의 정서 곡선을 변화점에서 구간으로 나누고, 구간마다 조성·분위기·편성을 정한다.
   숫자로 정해지는 값(BPM, 조성, 박자, 길이, 시드)은 전부 여기서 계산하고, 음악 모델에는 그 값과 영어 설명만 넘긴다.
2. AceStepClient: 로컬 ACE-Step REST 서버(`uv run acestep-api`, 기본 127.0.0.1:8001)에 구간마다 한 곡씩 요청한다.
3. stitch(): 구간 음원을 같은 BPM·관계조로 만들었으므로 등전력 크로스페이드로 이어 붙인다. ACE-Step 은 곡 끝에
   몇 초씩 무음을 두는 일이 있어서(12초 요청에 3초), 잇기 전에 앞뒤 무음을 잘라 낸다.
4. features(): 이어 붙인 음원에서 영상 프레임마다 음량(RMS)과 온셋(스펙트럼 변화량)을 뽑는다. 비주얼 모드와
   오디오 반응 영상 스크립트(scripts/render-audio-reactive.py)가 이 값으로 잠재공간 이동 속도와 박동을 정한다.

매핑표 (정서가 v: -1..1, 각성도 a: 0..1)
- BPM      : 62 + 60 × (근거 기록 날의 평균 각성도) + 45 × (활동량 신호), 60~140
             각성도는 LLM 채점값이 0.3~0.5 에 몰려 있어서, 기록이 얼마나 자주 나오는지(활동량)를 함께 쓴다
- 조성     : 키워드로 으뜸음을 고르고(키워드마다 다른 조, 근거가 조금 바뀌어도 같은 조), 구간 v ≥ 0.05 는 장조,
             v ≤ -0.05 는 나란한조(단조),
             그 사이는 전체 평균을 따른다. 장조와 나란한조는 같은 음을 쓰므로 구간을 이어도 어긋나지 않는다.
- 분위기   : v·a 사분면 → 밝고 힘찬 / 따뜻하고 잔잔한 / 긴장된 / 쓸쓸하고 느린
- 셈여림   : 구간 각성도 + 0.2 × (구간의 하루 근거 수 ÷ 전체 평균 - 1). 0.35 미만 여리게, 0.55 초과 강한 리듬
             근거 기록이 몰린 구간(예: 10km 완주 주간)이 더 세게 들린다
- 흐름     : 첫 구간은 조용히 시작. 마지막 구간은 가능성 신호가 높거나 근거가 가장 몰린 구간이면 고조, 아니면 잦아들며 끝
- 편성     : 근거 기록이 대부분 혼자일 때 피아노 독주, 함께가 많으면 현악·가벼운 타악 합주
- 장르 바탕: 안정 신호가 높으면 앰비언트, 새로움이 높으면 로파이 전자음, 그 밖에는 시네마틱
- 박자     : 안정 신호가 높고 각성도가 낮으면 3/4, 그 밖에는 4/4
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import numpy as np

from . import patterns

MUSIC_VERSION = "m2"
ACESTEP_URL = "http://127.0.0.1:8001"
SAMPLE_RATE = 48000
CROSSFADE_SEC = 2.5
FEATURE_FPS = 24

# 장조 으뜸음과 나란한조(단조) 으뜸음. ACE-Step 이 받는 표기("B♭ major", "F# minor")를 쓴다
KEY_PAIRS = [("C", "A"), ("G", "E"), ("D", "B"), ("A", "F#"), ("F", "D"), ("B♭", "G"), ("E♭", "C"), ("E", "C#")]
SECTION_NAMES = {1: ["전체"], 2: ["앞", "뒤"], 3: ["도입", "전개", "마무리"], 4: ["도입", "전개", "고조", "마무리"]}
MOOD = {
    "up": ("밝고 힘찬", "bright, uplifting, energetic"),
    "calm": ("따뜻하고 잔잔한", "warm, gentle, hopeful"),
    "tense": ("긴장된", "tense, restless, pulsing"),
    "down": ("쓸쓸하고 느린", "melancholic, lonely, slow, spacious reverb"),
}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _quadrant(v: float, a: float) -> str:
    if v >= 0:
        return "up" if a >= 0.5 else "calm"
    return "tense" if a >= 0.5 else "down"


def _energy(e: float) -> tuple[str, str]:
    if e < 0.35:
        return "여리게", "soft dynamics, sparse"
    if e > 0.55:
        return "강한 리듬", "driving rhythm, full arrangement"
    return "보통", "moderate groove"


def _segments(series: list[dict[str, Any]], cps: list[dict[str, Any]], max_sections: int = 4,
              min_days: int = 5) -> list[tuple[int, int]]:
    """근거 기간을 변화점에서 자른 [시작, 끝) 인덱스 목록. 변화점이 없으면 기간을 셋으로 나눈다."""
    n = len(series)
    if n < 9:
        return [(0, n)]
    dates = [s["date"] for s in series]
    cuts = sorted({dates.index(c["date"]) for c in cps if c.get("date") in dates})
    cuts = [c for c in cuts if min_days <= c <= n - min_days]
    if not cuts:
        cuts = [n // 3, (2 * n) // 3]
    kept: list[int] = []
    for c in cuts:
        if not kept or c - kept[-1] >= min_days:
            kept.append(c)
    kept = kept[: max_sections - 1]
    bounds = [0, *kept, n]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def plan(no: dict[str, Any], total_sec: int = 60) -> dict[str, Any]:
    """서사 오브젝트 → 음악 계획. 근거 부족이면 ValueError."""
    if no.get("evidence_grade") == "근거 부족" or not (no.get("emotion") or {}).get("series"):
        raise ValueError("근거가 부족한 키워드는 음악을 만들지 않습니다")
    series = no["emotion"]["series"]
    sig = no.get("signals") or {}
    segs = _segments(series, no.get("change_points") or [])
    names = SECTION_NAMES.get(len(segs), [f"{i + 1}구간" for i in range(len(segs))])

    ev_days = [s for s in series if s.get("evidence")] or series
    a_mean = float(np.mean([s["arousal"] for s in ev_days]))
    v_mean = float(np.mean([s["valence"] for s in ev_days]))
    bpm = int(round(_clip(62 + 60 * a_mean + 45 * sig.get("activity", 0.3), 60, 140)))
    ev_total = sum(s.get("evidence", 0) for s in series)
    per_day = ev_total / max(1, len(series))
    three = sig.get("stability", 0.5) > 0.7 and a_mean < 0.35
    nid = str(no.get("narrative_id") or "0")
    kw_hash = hashlib.sha1(str(no.get("keyword") or "").encode("utf-8")).hexdigest()
    major, minor = KEY_PAIRS[int(kw_hash[:6], 16) % len(KEY_PAIRS)]

    social = (no.get("social_mix") or {})
    alone = (social.get("혼자") or {}).get("evidence", 0)
    together = (social.get("함께") or {}).get("evidence", 0)
    if alone >= 0.6:
        band_ko, band_en = "피아노 독주", "solo piano, intimate, close-mic"
    elif together >= 0.4:
        band_ko, band_en = "현악·타악 합주", "warm ensemble, strings, acoustic guitar, light percussion"
    else:
        band_ko, band_en = "피아노와 신스 패드", "piano with soft synth pads"
    if sig.get("stability", 0.5) > 0.6:
        genre_ko, genre_en = "앰비언트", "ambient"
    elif sig.get("novelty", 0.0) > 0.6:
        genre_ko, genre_en = "로파이 전자음", "lo-fi electronic"
    else:
        genre_ko, genre_en = "시네마틱", "cinematic"

    # 길이: 기간(일수)에 비례, 구간당 최소 12초 (ACE-Step 최소 10초 + 크로스페이드)
    days = [hi - lo for lo, hi in segs]
    raw = [max(12.0, total_sec * d / sum(days)) for d in days]
    scale = (total_sec + CROSSFADE_SEC * (len(segs) - 1)) / sum(raw)
    durs = [int(round(max(12.0, r * scale))) for r in raw]

    dens = [(sum(s.get("evidence", 0) for s in series[lo:hi]) / max(1, hi - lo)) / per_day if per_day else 1.0
            for lo, hi in segs]
    climax = sig.get("possibility", 0.5) >= 0.6 or (len(dens) > 1 and dens[-1] >= max(dens) and dens[-1] > 1.2)
    sections = []
    t = 0.0
    seed0 = int(kw_hash[8:16], 16) % (2**31 - 16)
    for i, ((lo, hi), dur) in enumerate(zip(segs, durs)):
        part = series[lo:hi]
        v = float(np.mean([s["valence"] for s in part]))
        a = float(np.mean([s["arousal"] for s in part]))
        evn = int(sum(s.get("evidence", 0) for s in part))
        mode = "major" if v >= 0.05 else "minor" if v <= -0.05 else ("major" if v_mean >= 0 else "minor")
        keyscale = f"{major} major" if mode == "major" else f"{minor} minor"
        q = _quadrant(v, a)
        density = (evn / max(1, hi - lo)) / per_day if per_day else 1.0
        energy = a + 0.2 * (min(density, 3.0) - 1)
        en_ko, en_en = _energy(energy)
        arc = ""
        if len(segs) > 1 and i == 0:
            arc = ", gentle intro"
        elif len(segs) > 1 and i == len(segs) - 1:
            arc = ", building to a hopeful climax" if climax else ", fading outro"
        caption = f"instrumental {genre_en}, {band_en}, {MOOD[q][1]}, {en_en}{arc}, {bpm} bpm, no vocals"
        start = t
        t += dur - (CROSSFADE_SEC if i < len(segs) - 1 else 0)
        sections.append({
            "index": i, "name": names[i], "start_date": part[0]["date"], "end_date": part[-1]["date"],
            "days": hi - lo, "evidence": evn, "valence": round(v, 3), "arousal": round(a, 3),
            "mood": q, "mood_ko": MOOD[q][0], "energy": round(energy, 3), "energy_ko": en_ko, "keyscale": keyscale,
            "caption": caption,
            "duration": dur, "seed": seed0 + i, "start_sec": round(start, 2), "end_sec": round(start + dur, 2),
        })
    core = {"v": MUSIC_VERSION, "bpm": bpm, "ts": 3 if three else 4,
            "sections": [(s["keyscale"], s["caption"], s["duration"], s["seed"]) for s in sections]}
    music_id = hashlib.sha1(json.dumps(core, ensure_ascii=False).encode()).hexdigest()[:12]
    kd = lambda iso: patterns.ko_date(date.fromisoformat(iso))  # noqa: E731
    notes = [f"BPM {bpm}: 근거 기록이 있는 날의 평균 각성도 {a_mean:.2f}, 활동량 {sig.get('activity', 0.3):.2f}",
             f"조성 {major} 장조 / {minor} 단조: 키워드 '{no.get('keyword')}'로 고른 으뜸음. 정서가가 0보다 낮은 구간은 나란한조(단조)",
             f"편성 {band_ko}: 근거 기록 가운데 혼자 {alone:.0%}, 함께 {together:.0%}",
             f"장르 바탕 {genre_ko}: 안정 {sig.get('stability', 0):.2f}, 새로움 {sig.get('novelty', 0):.2f}"]
    notes += [f"{s['name']} {kd(s['start_date'])}~{kd(s['end_date'])}: 정서가 {s['valence']:+.2f}, 각성도 {s['arousal']:.2f}"
              f" → {s['keyscale']}, {s['mood_ko']}, {s['energy_ko']}" for s in sections]
    return {
        "schema": "virtueme.music/1", "music_id": music_id, "version": MUSIC_VERSION,
        "keyword": no.get("keyword"), "narrative_id": nid, "bpm": bpm, "time_signature": 3 if three else 4,
        "keys": {"major": f"{major} major", "minor": f"{minor} minor"}, "band": band_ko, "genre": genre_ko,
        "sections": sections, "total_sec": round(t, 2), "crossfade_sec": CROSSFADE_SEC, "notes": notes,
        "signals": {k: sig[k] for k in sorted(sig)},  # 오디오 반응 영상이 잠재 영역·색을 고를 때 쓴다
    }


# ---------------------------------------------------------------- ACE-Step REST 호출

class AceStepError(RuntimeError):
    pass


@dataclass
class AceStepClient:
    base: str = ACESTEP_URL
    timeout: float = 600.0
    inference_steps: int = 8

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        import httpx

        r = httpx.post(self.base + path, json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        if data.get("code", 200) != 200 or data.get("error"):
            raise AceStepError(str(data.get("error")))
        return data.get("data")

    def health(self) -> bool:
        import httpx

        try:
            return httpx.get(self.base + "/health", timeout=3).status_code == 200
        except Exception:
            return False

    def generate(self, section: dict[str, Any], bpm: int, time_signature: int) -> bytes:
        """구간 하나를 만들어 wav 바이트로 돌려준다."""
        import httpx

        task = self._post("/release_task", {
            "prompt": section["caption"], "lyrics": "[Instrumental]", "thinking": False,
            "bpm": bpm, "key_scale": section["keyscale"], "time_signature": str(time_signature),
            "audio_duration": float(section["duration"]), "inference_steps": self.inference_steps,
            "use_random_seed": False, "seed": int(section["seed"]), "batch_size": 1, "audio_format": "wav",
            "use_cot_caption": False, "use_cot_language": False,
        })
        tid = task["task_id"]
        t0 = time.time()
        while time.time() - t0 < self.timeout:
            time.sleep(2)
            got = self._post("/query_result", {"task_id_list": [tid]})
            item = (got or [{}])[0]
            if item.get("status") == 2:
                raise AceStepError(f"생성 실패: {item.get('result')}")
            if item.get("status") == 1:
                res = json.loads(item["result"]) if isinstance(item.get("result"), str) else item["result"]
                url = res[0]["file"]
                r = httpx.get(self.base + url, timeout=120)
                r.raise_for_status()
                return r.content
        raise AceStepError("시간 초과")


# ---------------------------------------------------------------- 음원 다루기

def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """wav 바이트 → (float32 [채널, 샘플], 표본율). 16·24·32비트 정수 PCM 과 32비트 부동소수를 읽는다."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            ch, sr, width, n = w.getnchannels(), w.getframerate(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(n)
        if width == 2:
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
        elif width == 3:
            b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
            x = ((b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
                 << 8 >> 8).astype(np.float32) / 8388608
        else:
            x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648
        return x.reshape(-1, ch).T.copy(), sr
    except wave.Error:
        import soundfile as sf  # 부동소수 wav (ACE-Step "wav32")

        x, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        return x.T.copy(), sr


def _resample(x: np.ndarray, sr: int, target: int) -> np.ndarray:
    if sr == target:
        return x
    n = int(round(x.shape[1] * target / sr))
    t_old = np.linspace(0, 1, x.shape[1], endpoint=False)
    t_new = np.linspace(0, 1, n, endpoint=False)
    return np.stack([np.interp(t_new, t_old, c) for c in x]).astype(np.float32)


def trim_silence(x: np.ndarray, sr: int, thresh_db: float = -45.0, keep: float = 0.15) -> np.ndarray:
    """앞뒤 무음(최대 음량 대비 thresh_db 아래)을 잘라 낸다. 끝은 짧게 줄여 잘린 소리가 튀지 않게 한다."""
    hop = max(1, sr // 50)
    mono = np.abs(x).max(axis=0)
    n = len(mono) // hop
    if n < 3:
        return x
    env = mono[: n * hop].reshape(n, hop).max(axis=1)
    ref = float(env.max()) or 1.0
    loud = np.where(20 * np.log10(np.maximum(env / ref, 1e-9)) > thresh_db)[0]
    if not len(loud):
        return x
    a = max(0, loud[0] * hop - int(keep * sr))
    b = min(x.shape[1], (loud[-1] + 1) * hop + int(keep * sr))
    y = x[:, a:b].copy()
    k = min(int(0.05 * sr), y.shape[1] // 4)
    if k > 1:
        y[:, -k:] *= np.linspace(1, 0, k, dtype=np.float32)
    return y


MIX_VERSION = "mix2"  # 잇는 방식을 바꾸면 올린다. 캐시한 곡은 remix() 로 구간 음원에서 다시 잇는다
GAIN_PER_ENERGY_DB = 10.0
GAIN_LIMIT_DB = 4.0


def section_gains(music_plan: dict[str, Any]) -> list[float]:
    """구간마다 더할 음량(dB). 음악 모델은 구간마다 비슷한 음량으로 만들어서(eval/music_eval.py) 셈여림 계획이 소리에
    남지 않는다. 그래서 구간 음량을 먼저 같게 맞춘 뒤, 곡 평균 에너지보다 높은 구간은 키우고 낮은 구간은 줄인다 (±4dB)."""
    energy = [float(s.get("energy", 0.3)) for s in music_plan.get("sections", [])]
    if not energy:
        return []
    mean = sum(energy) / len(energy)
    return [round(float(np.clip(GAIN_PER_ENERGY_DB * (e - mean), -GAIN_LIMIT_DB, GAIN_LIMIT_DB)), 2) for e in energy]


def _rms_db(x: np.ndarray) -> float:
    """무음·꼬리를 뺀 RMS (dBFS). 20ms 창 가운데 가장 큰 곳보다 40dB 이상 작은 창은 뺀다."""
    mono = x.mean(axis=0)
    hop = SAMPLE_RATE // 50
    n = max(1, len(mono) // hop)
    rms = np.sqrt(np.mean(mono[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    return float(db[db > db.max() - 40].mean())


def stitch(parts: list[tuple[np.ndarray, int]], crossfade: float = CROSSFADE_SEC, sr: int = SAMPLE_RATE,
           gains_db: list[float] | None = None) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """구간 음원의 앞뒤 무음을 자르고 등전력 크로스페이드로 잇는다. 최대 음량은 -1 dBFS.

    gains_db 를 주면 구간 음량을 먼저 같은 RMS 로 맞춘 뒤 구간마다 그만큼 더한다 (셈여림 계획).
    반환: (음원 [2, 샘플], 구간마다 실제 (시작 초, 끝 초)). 크로스페이드 동안은 두 구간이 겹친다.
    """
    clips = []
    for x, s in parts:
        x = _resample(x, s, sr)
        if x.shape[0] == 1:
            x = np.repeat(x, 2, axis=0)
        clips.append(trim_silence(x[:2], sr))
    if gains_db:
        levels = [_rms_db(c) for c in clips]
        ref = float(np.mean(levels))
        clips = [c * np.float32(10 ** ((ref - lv + g) / 20)) for c, lv, g in zip(clips, levels, gains_db)]
    out = clips[0]
    bounds = [(0.0, out.shape[1] / sr)]
    n_fade = int(crossfade * sr)
    for x in clips[1:]:
        k = min(n_fade, out.shape[1], x.shape[1])
        t = np.linspace(0, np.pi / 2, k, dtype=np.float32)
        mixed = out[:, -k:] * np.cos(t) + x[:, :k] * np.sin(t)
        start = (out.shape[1] - k) / sr
        out = np.concatenate([out[:, :-k], mixed, x[:, k:]], axis=1)
        bounds.append((start, out.shape[1] / sr))
    peak = float(np.max(np.abs(out))) or 1.0
    return (out * (0.891 / peak)).astype(np.float32), [(round(a, 2), round(b, 2)) for a, b in bounds]


def write_wav(path: Path, x: np.ndarray, sr: int = SAMPLE_RATE) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").T.tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[0])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


def to_m4a(wav: Path) -> Path | None:
    """브라우저용 AAC. ffmpeg 가 없으면 wav 를 그대로 쓴다."""
    if not shutil.which("ffmpeg"):
        return None
    out = wav.with_suffix(".m4a")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "192k", str(out)],
                   check=True)
    return out


def features(x: np.ndarray, sr: int = SAMPLE_RATE, fps: int = FEATURE_FPS) -> dict[str, Any]:
    """영상 프레임마다 음량(RMS)과 온셋 세기(반파 정류 스펙트럼 변화량). 둘 다 0..1 로 맞춘다."""
    mono = x.mean(axis=0)
    hop = sr // fps
    n_fft = 2048
    frames = max(1, len(mono) // hop)
    pad = np.concatenate([np.zeros(n_fft // 2, dtype=np.float32), mono, np.zeros(n_fft, dtype=np.float32)])
    win = np.hanning(n_fft).astype(np.float32)
    rms = np.empty(frames, dtype=np.float32)
    flux = np.zeros(frames, dtype=np.float32)
    prev = None
    for i in range(frames):
        seg = pad[i * hop : i * hop + n_fft]
        rms[i] = np.sqrt(np.mean(mono[i * hop : (i + 1) * hop] ** 2)) if (i + 1) * hop <= len(mono) else 0.0
        mag = np.log1p(np.abs(np.fft.rfft(seg * win)))
        if prev is not None:
            flux[i] = np.maximum(mag - prev, 0).sum()
        prev = mag

    def norm(v: np.ndarray) -> np.ndarray:
        hi = float(np.percentile(v, 98)) or 1.0
        return np.clip(v / hi, 0, 1)

    onset = norm(flux)
    # 온셋 봉우리: 앞뒤 3프레임보다 크고 0.5 이상
    peaks = [i for i in range(3, frames - 3) if onset[i] >= 0.5 and onset[i] == onset[i - 3 : i + 4].max()]
    return {"fps": fps, "frames": frames, "rms": [round(float(v), 3) for v in norm(rms)],
            "onset": [round(float(v), 3) for v in onset], "peaks": peaks}


# ---------------------------------------------------------------- 만들기와 캐시

# 오디오 반응 영상: 장소(StyleGAN2)·사물·상황(StyleGAN-XL) 모드마다 한 파일
VIDEO_FILES = {"place": "video.mp4", "object": "video-object.mp4", "situation": "video-situation.mp4"}
_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def job_dir(root: Path, music_id: str) -> Path:
    return root / music_id


def status(root: Path, music_id: str) -> dict[str, Any]:
    d = job_dir(root, music_id)
    if (d / "features.json").exists():
        videos = [m for m, name in VIDEO_FILES.items() if (d / name).exists()]
        out = {"music_id": music_id, "state": "ready", "plan": json.loads((d / "plan.json").read_text(encoding="utf-8")),
               "audio": "music.m4a" if (d / "music.m4a").exists() else "music.wav",
               "video": "place" in videos, "videos": videos}
        return out
    with _LOCK:
        job = dict(_JOBS.get(music_id) or {})
    return {"music_id": music_id, **job} if job else {"music_id": music_id, "state": "missing"}


def make(root: Path, music_plan: dict[str, Any], client: AceStepClient | None = None,
         on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """계획대로 구간을 만들고 이어 붙여 캐시 폴더에 저장한다. 이미 있으면 바로 돌려준다."""
    mid = music_plan["music_id"]
    d = job_dir(root, mid)
    if (d / "features.json").exists():
        return status(root, mid)
    client = client or AceStepClient()
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps(music_plan, ensure_ascii=False, indent=1), encoding="utf-8")
    parts = []
    n = len(music_plan["sections"])
    t0 = time.time()
    for i, sec in enumerate(music_plan["sections"]):
        state = {"state": "running", "stage": "음악 생성 중", "done": i, "total": n, "elapsed": round(time.time() - t0, 1)}
        with _LOCK:
            _JOBS[mid] = state
        if on_progress:
            on_progress(state)
        cached = d / f"part{i}.wav"
        data = cached.read_bytes() if cached.exists() else client.generate(sec, music_plan["bpm"], music_plan["time_signature"])
        cached.write_bytes(data)
        parts.append(read_wav(data))
    with _LOCK:
        _JOBS[mid] = {"state": "running", "stage": "구간 연결 중", "done": n, "total": n, "elapsed": round(time.time() - t0, 1)}
    _mix(d, music_plan, parts, extra={"generation_seconds": round(time.time() - t0, 1)})
    with _LOCK:
        _JOBS.pop(mid, None)
    return status(root, mid)


def _mix(d: Path, music_plan: dict[str, Any], parts: list[tuple[np.ndarray, int]], extra: dict[str, Any] | None = None) -> None:
    """구간 음원을 이어 music.wav·m4a·features.json 을 쓴다. 구간마다 만든 소리를 다시 재서(audio_check) 함께 남긴다."""
    from .audio_check import check_section

    gains = section_gains(music_plan)
    audio, bounds = stitch(parts, gains_db=gains)
    write_wav(d / "music.wav", audio)
    to_m4a(d / "music.wav")
    feats = features(audio)
    feats["duration"] = round(audio.shape[1] / SAMPLE_RATE, 2)
    feats["mix"] = MIX_VERSION
    feats["sections"] = []
    for i, ((a, b), sec, (x, sr)) in enumerate(zip(bounds, music_plan["sections"], parts)):
        # 음악 모델이 만든 구간 음원 그대로(잇기 전) 템포·조성을 재고, 음량은 이은 뒤 구간에서 잰다
        chk = check_section(x, sr, music_plan["bpm"], sec["keyscale"])
        seg = audio[:, int(a * SAMPLE_RATE): int(b * SAMPLE_RATE)]
        feats["sections"].append({"index": i, "start_sec": a, "end_sec": b, "gain_db": gains[i] if gains else 0.0,
                                  "measured": {"bpm": chk["bpm"], "octave": chk["octave"], "key": chk["key"]["key"],
                                               "key_match": chk["key_match"], "model_rms_db": chk["rms_db"],
                                               "rms_db": round(_rms_db(seg), 2) if seg.size else None}})
    feats.update(extra or {})
    (d / "features.json").write_text(json.dumps(feats), encoding="utf-8")


def remix(root: Path, music_id: str) -> dict[str, Any]:
    """캐시한 곡을 구간 음원(part*.wav)에서 지금 방식으로 다시 잇는다. 음악 모델은 다시 부르지 않는다.
    예전 소리가 들어 있는 오디오 반응 영상은 지운다 (virtueme demo --video 가 다시 만든다)."""
    d = job_dir(root, music_id)
    feats_path = d / "features.json"
    if not feats_path.exists():
        return {"music_id": music_id, "remixed": False, "reason": "아직 만들지 않은 곡"}
    old = json.loads(feats_path.read_text(encoding="utf-8"))
    if old.get("mix") == MIX_VERSION:
        return {"music_id": music_id, "remixed": False, "reason": "이미 지금 방식"}
    plan_ = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    parts = [read_wav((d / f"part{i}.wav").read_bytes()) for i in range(len(plan_["sections"]))]
    _mix(d, plan_, parts, extra={k: old[k] for k in ("generation_seconds",) if k in old})
    removed = []
    for name in VIDEO_FILES.values():
        for p in (d / name, d / name.replace(".mp4", ".json")):
            if p.exists():
                p.unlink()
                removed.append(p.name)
    return {"music_id": music_id, "remixed": True, "removed_videos": [n for n in removed if n.endswith(".mp4")]}


def start(root: Path, music_plan: dict[str, Any], client: AceStepClient | None = None) -> dict[str, Any]:
    """백그라운드에서 만들기 시작. 이미 만들고 있거나 끝났으면 상태만 돌려준다."""
    mid = music_plan["music_id"]
    st = status(root, mid)
    if st["state"] in ("ready", "running"):
        return st
    with _LOCK:
        _JOBS[mid] = {"state": "running", "stage": "음악 생성 준비 중", "done": 0, "total": len(music_plan["sections"])}

    def run() -> None:
        try:
            make(root, music_plan, client)
        except Exception as err:  # 화면에 이유를 보여 준다
            with _LOCK:
                _JOBS[mid] = {"state": "error", "error": str(err)[:300]}

    threading.Thread(target=run, daemon=True).start()
    return status(root, mid)
