"""만든 음악을 다시 들어 보는 계산: 템포, 조성, 셈여림.

음악 모델에는 BPM·조성·분위기를 글로 넘길 뿐이라, 실제 소리가 그 값을 따랐는지는 따로 재야 한다.
librosa 없이 numpy 로만 계산한다 (core 의존성을 늘리지 않기 위해).

- 템포: 스펙트럼 변화량(온셋 세기)의 자기상관에서 60~180 BPM 사이 봉우리. 사람 귀에도 두 배·절반 템포는
  헷갈리므로, 계획값과 비교할 때는 두 배·절반까지 같은 템포로 보는 값을 따로 낸다.
- 조성: 65~2100Hz 스펙트럼을 12음으로 접은 크로마를 Krumhansl-Kessler 장조·단조 음계 윤곽 24개와 상관시켜 가장 높은 것.
- 셈여림: 무음을 뺀 구간의 RMS(dBFS), 초당 온셋 수, 스펙트럼 무게중심(Hz, 밝기).
"""
from __future__ import annotations

from typing import Any

import numpy as np

PITCHES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT = {"B♭": "A#", "Bb": "A#", "E♭": "D#", "Eb": "D#", "A♭": "G#", "Ab": "G#", "D♭": "C#", "Db": "C#", "G♭": "F#", "Gb": "F#"}
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _mono(x: np.ndarray) -> np.ndarray:
    return (x.mean(axis=0) if x.ndim == 2 else x).astype(np.float32)


def _frames(y: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    if len(y) < n_fft:
        y = np.pad(y, (0, n_fft - len(y)))
    n = 1 + (len(y) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    return y[idx] * np.hanning(n_fft).astype(np.float32)[None, :]


def onset_envelope(y: np.ndarray, sr: int, hop: int = 512, n_fft: int = 2048) -> tuple[np.ndarray, float]:
    """반파 정류한 로그 스펙트럼 변화량. 반환: (프레임별 세기, 초당 프레임 수)."""
    mag = np.log1p(np.abs(np.fft.rfft(_frames(y, n_fft, hop), axis=1)))
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    k = max(1, int(round(0.5 * sr / hop)))  # 0.5초 이동 평균을 빼서 느린 음량 변화를 지운다
    local = np.convolve(flux, np.ones(k) / k, mode="same")
    return np.maximum(flux - local, 0), sr / hop


def tempo(y: np.ndarray, sr: int, lo: float = 60, hi: float = 180) -> float:
    env, fps = onset_envelope(_mono(y), sr)
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    ok = (lags >= fps * 60 / hi) & (lags <= fps * 60 / lo)
    if not ok.any():
        return 0.0
    # 박 하나와 그 두 배 간격(마디의 반)이 함께 강한 주기를 고른다. 한쪽만 보면 반 박에 끌리기 쉽다
    score = ac.copy()
    for i in np.where(ok)[0]:
        j = 2 * i
        score[i] = ac[i] + (0.5 * ac[j] if j < len(ac) else 0)
    best = int(lags[ok][np.argmax(score[ok])])
    # 봉우리 양옆으로 포물선 보간
    if 0 < best < len(ac) - 1:
        a, b, c = ac[best - 1], ac[best], ac[best + 1]
        d = (a - c) / (2 * (a - 2 * b + c)) if (a - 2 * b + c) else 0.0
        best = best + float(np.clip(d, -0.5, 0.5))
    return round(60 * fps / best, 1)


def chroma(y: np.ndarray, sr: int, fmin: float = 65.0, fmax: float = 2100.0) -> np.ndarray:
    n_fft = 1 << int(np.ceil(np.log2(sr / 3)))  # 주파수 해상도 3Hz 안팎
    spec = np.abs(np.fft.rfft(_frames(_mono(y), n_fft, n_fft // 2), axis=1)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    band = (freqs >= fmin) & (freqs <= fmax)
    pcs = (np.round(12 * np.log2(freqs[band] / 440.0)) + 69).astype(int) % 12
    frame_energy = spec[:, band].sum(axis=1, keepdims=True) + 1e-12
    per = spec[:, band] / frame_energy  # 프레임마다 합이 1 (큰 소리 프레임이 전체를 덮지 않게)
    out = np.zeros(12)
    np.add.at(out, pcs, per.sum(axis=0))
    return out / (out.sum() or 1)


def key_of(y: np.ndarray, sr: int) -> dict[str, Any]:
    c = chroma(y, sr)
    scores = []
    for mode, prof in (("major", KK_MAJOR), ("minor", KK_MINOR)):
        for t in range(12):
            scores.append((float(np.corrcoef(c, np.roll(prof, t))[0, 1]), PITCHES[t], mode))
    scores.sort(reverse=True)
    r, tonic, mode = scores[0]
    return {"key": f"{tonic} {mode}", "tonic": tonic, "mode": mode, "r": round(r, 3),
            "margin": round(r - scores[1][0], 3)}


def parse_key(keyscale: str) -> tuple[str, str]:
    tonic, _, mode = keyscale.strip().partition(" ")
    return FLAT.get(tonic, tonic), mode.strip().lower()


def relative_of(tonic: str, mode: str) -> tuple[str, str]:
    i = PITCHES.index(tonic)
    return (PITCHES[(i + 9) % 12], "minor") if mode == "major" else (PITCHES[(i + 3) % 12], "major")


def key_match(planned: str, detected: dict[str, Any]) -> str:
    """exact: 으뜸음·장단조 모두 같음 / relative: 나란한조(조표가 같음) / mode: 장단조만 같음 / other."""
    pt, pm = parse_key(planned)
    dt, dm = detected["tonic"], detected["mode"]
    if (pt, pm) == (dt, dm):
        return "exact"
    if relative_of(pt, pm) == (dt, dm):
        return "relative"
    return "mode" if pm == dm else "other"


def dynamics(y: np.ndarray, sr: int) -> dict[str, float]:
    m = _mono(y)
    hop = sr // 20
    fr = _frames(m, hop * 2, hop)
    rms = np.sqrt((fr ** 2).mean(axis=1)) + 1e-9
    db = 20 * np.log10(rms)
    loud = db[db > db.max() - 40]  # 곡 안에서 가장 큰 곳보다 40dB 이상 작은 곳(무음·꼬리)은 뺀다
    env, fps = onset_envelope(m, sr)
    hi = np.percentile(env, 98) or 1.0
    e = env / hi
    peaks = [i for i in range(3, len(e) - 3) if e[i] >= 0.35 and e[i] == e[i - 3:i + 4].max()]
    spec = np.abs(np.fft.rfft(_frames(m, 4096, 2048), axis=1))
    freqs = np.fft.rfftfreq(4096, 1 / sr)
    centroid = float((spec * freqs).sum() / (spec.sum() or 1))
    dur = len(m) / sr
    return {"rms_db": round(float(loud.mean()), 2), "onsets_per_sec": round(len(peaks) / max(dur, 1e-6), 2),
            "centroid_hz": round(centroid, 1), "seconds": round(dur, 2)}


def check_section(y: np.ndarray, sr: int, planned_bpm: float, planned_key: str) -> dict[str, Any]:
    bpm = tempo(y, sr)
    ratios = {1.0: abs(bpm - planned_bpm), 2.0: abs(bpm / 2 - planned_bpm), 0.5: abs(bpm * 2 - planned_bpm)}
    best = min(ratios, key=ratios.get)
    k = key_of(y, sr)
    return {"bpm": bpm, "bpm_error": round(bpm - planned_bpm, 1), "bpm_error_octave": round(ratios[best], 1),
            "octave": best, "key": k, "key_match": key_match(planned_key, k), **dynamics(y, sr)}
