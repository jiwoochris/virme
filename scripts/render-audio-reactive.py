"""기록의 음악에 맞춰 움직이는 잠재공간 영상 (Lucid Sonic Dreams 방식을 팀 StyleGAN 런타임에 이식).

입력은 core 가 만든 음악 캐시 폴더 하나다(`$VIRTUEME_DATA/music/<프로필>/<music_id>/`).
- plan.json     : 구간(시작·끝 초, 분위기, 시드)과 서사 오브젝트의 signals
- features.json : 프레임마다 음량(rms)·온셋(onset), 실제 구간 경계
- music.m4a|wav : 영상에 붙일 소리

규칙
- 구간마다 잠재 기준점(keyframe) 하나. 시드는 음악 계획의 구간 시드를 그대로 써서, 같은 서사면 같은 영상이 나온다.
- 이동(motion): 구간 안에서 다음 기준점까지 가는 진행률을 음량 누적에 비례시킨다. 소리가 클 때 빨리 움직이고,
  구간이 끝나는 순간에는 반드시 다음 기준점에 닿는다. 기준점 둘레를 도는 원운동(구간당 2.5바퀴)을 더해 조용한 구간도
  멈추지 않게 한다.
- 박동(pulse): 온셋이 오면 주 잠재점과 보조 잠재점 사이 간격을 순간적으로 넓히고(최대 6배), 주 잠재점을 한 방향으로
  민다. 팀의 잠재 구조 렌더링은 이 간격의 차이에서 윤곽을 뽑으므로, 박자마다 윤곽이 번쩍인다. 0.3초에 걸쳐 잦아든다.
- 색: 구간 분위기 → 팀 팔레트 (밝고 힘찬=novelty, 따뜻하고 잔잔한=possible-self, 긴장된=conflict, 쓸쓸하고 느린=uncertainty).
  구간 경계에서는 크로스페이드 길이만큼 두 팔레트를 섞는다.
- 잘림 정도(truncation): 확신도 신호가 높을수록 형태가 또렷하다 (0.55~0.8).

사물·상황 모드(--mode object|situation)는 기본으로 팀이 StyleGAN-XL 로 미리 렌더한 12초 잠재 루프(public/assets)를
음악에 맞춰 다시 편집한다. 음량이 클수록 루프를 빨리 돌리고(0.55~1.85배, 앞뒤 프레임을 섞어 부드럽게), 온셋마다 밝기·대비를
올린다. 비주얼 화면이 재생 중에 하는 일과 같은 규칙이라, 화면과 영상이 같은 리듬으로 움직인다. 1분 안에 끝난다.
--xl 을 주면 StyleGAN-XL 로 구간마다 클래스 기준점을 새로 만들어 직접 합성한다(한 프레임에 합성 한 번, 윤곽은 앞뒤 프레임
차이에서, 8fps 로 만들고 24fps 로 보간). 24GB M4 맥에서 128px 한 프레임에 7~50초가 걸려 한 곡에 몇 시간이 걸리므로,
메모리가 넉넉한 장비에서만 쓴다. 결과는 video-object.mp4 처럼 모드 이름을 붙여 저장한다.

    PYTHONPATH=model_service PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python scripts/render-audio-reactive.py \\
        --music-dir /tmp/vm6data/music/seoyeon/1929fccf9bbd [--mode object]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from latent_modes import MULTI_CLASS_MODES
from stylegan_runtime import STATE_PALETTES, StyleGanRuntime, latent_rgb_structure_fields, synthesize_latent_stage

MOOD_PALETTE = {"up": "novelty", "calm": "possible-self", "tense": "conflict", "down": "uncertainty"}
FEATURE_RES = 128


def colorize(fields: torch.Tensor, palette: np.ndarray) -> np.ndarray:
    """팀의 structure_map_to_rgb 와 같은 식. 팔레트를 이름 대신 섞은 값으로 받는다."""
    colors = torch.tensor(palette, device=fields.device, dtype=torch.float32).reshape(3, 3, 1, 1) / 255
    density = fields[0].clamp(0, 1)
    body = ((density - 0.18) / 0.16).clamp(0, 1)
    body = body.square() * (3 - 2 * body)
    ridge = ((density - 0.5) / 0.12).clamp(0, 1)
    ridge = ridge.square() * (3 - 2 * ridge)
    tone = (0.72 * (fields[1].clamp(-1, 1) * 0.5 + 0.5) + 0.28 * fields[2].clamp(0, 1)).clamp(0, 1)
    tone = torch.round(tone * 2) / 2
    inner = colors[1] * (1 - tone) + colors[2] * tone
    inner = inner * (1 - ridge * 0.58) + colors[2] * ridge * 0.58
    rgb = colors[0] * (1 - body) + inner * body
    return (rgb * 255).round().clamp(0, 255).to(torch.uint8).cpu().permute(1, 2, 0).numpy()


def pulse_envelope(onset: np.ndarray, fps: int, release: float = 0.3) -> np.ndarray:
    """온셋 → 바로 올라가고 release 초에 걸쳐 잦아드는 박동 값 (0..1)."""
    decay = np.exp(-1 / (release * fps))
    out = np.zeros_like(onset)
    level = 0.0
    for i, v in enumerate(onset):
        level = max(float(v), level * decay)
        out[i] = level
    return out


def progress_by_loudness(rms: np.ndarray, bounds: list[tuple[int, int]]) -> np.ndarray:
    """프레임마다 (구간 번호 + 구간 안 진행률). 구간 안에서는 음량 누적에 비례해 0 → 1."""
    pos = np.zeros(len(rms))
    for k, (a, b) in enumerate(bounds):
        seg = 0.35 + rms[a:b]  # 조용해도 조금은 움직인다
        cum = np.cumsum(seg)
        pos[a:b] = k + cum / cum[-1]
    return pos


ROOT = Path(__file__).resolve().parents[1]
XL_ROOT = ROOT / ".runtime" / "stylegan-xl"
XL_MODEL = ROOT / "models" / "stylegan-xl-imagenet128.pkl"
XL_FPS = 8


def load_xl():
    """팀 render-multimode-loops.py 와 같은 방식으로 StyleGAN-XL 을 올린다 (legacy 모듈이 작업 폴더를 본다)."""
    previous = Path.cwd()
    os.chdir(XL_ROOT)
    sys.path.insert(0, str(XL_ROOT))
    try:
        import legacy

        with XL_MODEL.open("rb") as handle:
            gen = legacy.load_network_pkl(handle)["G_ema"].eval().requires_grad_(False)
    finally:
        os.chdir(previous)
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    return gen.to(dev), dev


def render_xl(args, mode_id: str) -> None:
    d = args.music_dir
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    feats = json.loads((d / "features.json").read_text(encoding="utf-8"))
    audio = d / "music.m4a" if (d / "music.m4a").exists() else d / "music.wav"
    fps = int(feats["fps"])
    step = max(1, fps // XL_FPS)
    rms = np.asarray(feats["rms"], dtype=np.float64)
    onset = np.asarray(feats["onset"], dtype=np.float64)
    sections = plan["sections"]
    real = feats.get("sections") or [{"start_sec": s["start_sec"], "end_sec": s["end_sec"]} for s in sections]
    cuts = [int(round(r["start_sec"] * fps)) for r in real[1:]]
    bounds = list(zip([0, *cuts], [*cuts, len(rms)]))
    pos_all = progress_by_loudness(rms, bounds)
    pulse_all = pulse_envelope(onset, fps)
    total = len(rms) if args.seconds is None else min(len(rms), int(args.seconds * fps))
    frames_idx = list(range(0, total, step))
    sig = plan.get("signals") or {}
    mode = next(m for m in MULTI_CLASS_MODES if m.mode_id == mode_id)

    G, dev = load_xl()
    trunc = 0.55 + 0.25 * float(sig.get("confidence", 0.6))
    seeds = [int(s["seed"]) for s in sections] + [int(sections[-1]["seed"]) + 101]
    classes = [mode.classes[sd % len(mode.classes)] for sd in seeds]

    def mapped(seed_list: list[int], class_list: list[int]) -> torch.Tensor:
        z = torch.from_numpy(np.stack([np.random.RandomState(sd % (2**31)).randn(G.z_dim) for sd in seed_list]))
        c = torch.nn.functional.one_hot(torch.tensor(class_list), G.c_dim)
        with torch.no_grad():
            return G.mapping(z.to(dev, torch.float32), c.to(dev), truncation_psi=trunc)

    key_ws = mapped(seeds, classes)
    aux_ws = mapped([sd + 1000 for sd in seeds], classes)   # 원운동 방향 (같은 클래스 안에서)
    kick_ws = mapped([sd + 2000 for sd in seeds], classes)  # 박동 방향
    palettes = [np.array(STATE_PALETTES[MOOD_PALETTE.get(s["mood"], "stability")], dtype=np.float32) for s in sections]
    fade = int(plan.get("crossfade_sec", 2.5) * fps)

    def w_at(j: int) -> torch.Tensor:
        p = pos_all[j]
        k = int(min(len(sections) - 1, max(0, np.floor(p))))
        t = float(np.clip(p - k, 0, 1))
        e = t * t * t * (t * (t * 6 - 15) + 10)
        base = key_ws[k].lerp(key_ws[k + 1], e)
        wander = 0.22 * float(np.sin(2 * np.pi * p * 2.5)) * (aux_ws[k] - key_ws[k])
        kick = 0.45 * float(pulse_all[j]) * (kick_ws[k] - key_ws[k])
        return base + wander + kick

    def palette_at(j: int) -> np.ndarray:
        k = int(min(len(sections) - 1, np.floor(pos_all[j])))
        if k + 1 < len(sections) and bounds[k + 1][0] - j < fade:
            m = 1 - (bounds[k + 1][0] - j) / fade
            return palettes[k] * (1 - m) + palettes[k + 1] * m
        return palettes[k]

    out = d / (f"video-{mode_id}.mp4" if args.seconds is None else f"video-{mode_id}_test.mp4")
    part = out.with_name(out.stem + ".part.mp4")
    enc = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{args.size}x{args.size}",
        "-framerate", str(fps / step), "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a", "-shortest",
        "-vf", f"minterpolate=fps={fps}:mi_mode=blend",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part),
    ], stdin=subprocess.PIPE)
    assert enc.stdin is not None
    weights = torch.tensor((0.5, 0.25, 0.25), dtype=torch.float32)
    t0 = time.time()
    window: list[torch.Tensor] = []  # 윤곽은 앞·지금·뒤 세 프레임의 차이에서 뽑는다

    def emit(prev: torch.Tensor, cur: torch.Tensor, nxt: torch.Tensor, j: int) -> None:
        fields = latent_rgb_structure_fields(torch.stack((cur, prev, nxt)), weights)
        rgb = colorize(fields, palette_at(j))
        frame = Image.fromarray(rgb, "RGB").resize((args.size, args.size), Image.Resampling.LANCZOS)
        enc.stdin.write(np.asarray(frame).tobytes())

    with torch.no_grad():
        _ = G.synthesis(key_ws[:1], noise_mode="const", force_fp32=True)
        for n_done, j in enumerate(frames_idx):
            img = G.synthesis(w_at(j).unsqueeze(0), noise_mode="const", force_fp32=True)[0]
            window.append(((img + 1) * 0.5).clamp(0, 1).cpu())
            if dev.type == "mps":
                torch.mps.synchronize()
            if len(window) == 2 and n_done == 1:
                emit(window[0], window[0], window[1], frames_idx[0])  # 첫 프레임은 앞 프레임 대신 자기 자신
            if len(window) == 3:
                emit(window[0], window[1], window[2], frames_idx[n_done - 1])
                window.pop(0)
            if n_done % 60 == 0:
                print(f"{mode_id}: {n_done + 1}/{len(frames_idx)} 프레임, {time.time() - t0:.0f}초", flush=True)
        if len(window) >= 2:
            emit(window[-2], window[-1], window[-1], frames_idx[-1])
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg 실패")
    part.replace(out)
    meta = {"mode": mode_id, "frames": len(frames_idx), "fps": fps / step, "size": args.size,
            "seconds": round(time.time() - t0, 1), "classes": classes}
    (d / f"video-{mode_id}.json").write_text(json.dumps(meta), encoding="utf-8")
    print(f"완료: {out} ({meta})")


LOOP_FILES = {"object": "latent-object-loop.mp4", "situation": "latent-situation-loop.mp4"}


def render_loop(args, mode_id: str) -> None:
    """팀의 사물·상황 잠재 루프를 음악의 음량·온셋에 맞춰 시간을 늘이고 줄여 다시 엮는다."""
    import imageio.v2 as iio

    d = args.music_dir
    feats = json.loads((d / "features.json").read_text(encoding="utf-8"))
    audio = d / "music.m4a" if (d / "music.m4a").exists() else d / "music.wav"
    fps = int(feats["fps"])
    rms = np.asarray(feats["rms"], dtype=np.float64)
    pulse = pulse_envelope(np.asarray(feats["onset"], dtype=np.float64), fps, release=0.25)
    n = len(rms) if args.seconds is None else min(len(rms), int(args.seconds * fps))
    reader = iio.get_reader(ROOT / "public" / "assets" / LOOP_FILES[mode_id])
    loop_fps = float(reader.get_meta_data().get("fps", 12))
    src = [np.asarray(Image.fromarray(f).resize((args.size, args.size), Image.Resampling.LANCZOS), dtype=np.float32)
           for f in reader]
    reader.close()
    out = d / (f"video-{mode_id}.mp4" if args.seconds is None else f"video-{mode_id}_test.mp4")
    part = out.with_name(out.stem + ".part.mp4")
    enc = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{args.size}x{args.size}", "-framerate", str(fps),
        "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part),
    ], stdin=subprocess.PIPE)
    assert enc.stdin is not None
    t0 = time.time()
    pos = 0.0
    for i in range(n):
        speed = min(1.85, 0.55 + 1.3 * rms[i])  # 비주얼 화면(StyleGanLayer)과 같은 식
        pos = (pos + speed * loop_fps / fps) % len(src)
        a = int(pos)
        frac = pos - a
        img = src[a] * (1 - frac) + src[(a + 1) % len(src)] * frac
        p = float(pulse[i])
        img = np.clip((img - 128.0) * (1 + 0.25 * p) + 128.0, 0, 255) * (1 + 0.4 * p)
        enc.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg 실패")
    part.replace(out)
    meta = {"mode": mode_id, "method": "loop-timewarp", "frames": n, "fps": fps, "size": args.size,
            "seconds": round(time.time() - t0, 1)}
    (d / f"video-{mode_id}.json").write_text(json.dumps(meta), encoding="utf-8")
    print(f"완료: {out} ({meta})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--music-dir", required=True, type=Path)
    ap.add_argument("--mode", choices=["place", "object", "situation"], default="place")
    ap.add_argument("--xl", action="store_true", help="사물·상황을 StyleGAN-XL 로 직접 합성 (매우 느림)")
    ap.add_argument("--size", type=int, default=512, help="출력 한 변 픽셀")
    ap.add_argument("--batch", type=int, default=6)
    ap.add_argument("--seconds", type=float, default=None, help="앞부분만 시험 렌더")
    args = ap.parse_args()
    if args.mode != "place":
        (render_xl if args.xl else render_loop)(args, args.mode)
        return

    d = args.music_dir
    plan = json.loads((d / "plan.json").read_text(encoding="utf-8"))
    feats = json.loads((d / "features.json").read_text(encoding="utf-8"))
    audio = d / "music.m4a" if (d / "music.m4a").exists() else d / "music.wav"
    fps = int(feats["fps"])
    rms = np.asarray(feats["rms"], dtype=np.float64)
    onset = np.asarray(feats["onset"], dtype=np.float64)
    n = len(rms) if args.seconds is None else min(len(rms), int(args.seconds * fps))
    sections = plan["sections"]
    real = feats.get("sections") or [{"start_sec": s["start_sec"], "end_sec": s["end_sec"]} for s in sections]
    cuts = [int(round(r["start_sec"] * fps)) for r in real[1:]]
    bounds = list(zip([0, *cuts], [*cuts, len(rms)]))
    pos = progress_by_loudness(rms, bounds)[:n]
    pulse = pulse_envelope(onset, fps)[:n]
    sig = plan.get("signals") or {}

    runtime = StyleGanRuntime().load()
    G, dev = runtime.generator, runtime.device
    trunc = 0.55 + 0.25 * float(sig.get("confidence", 0.6))
    seeds = [int(s["seed"]) for s in sections] + [int(sections[-1]["seed"]) + 101]
    with torch.no_grad():
        z = torch.from_numpy(np.stack([np.random.RandomState(s % (2**31)).randn(G.z_dim) for s in seeds])).to(dev, torch.float32)
        key_ws = G.mapping(z, None, truncation_psi=trunc)  # [구간+1, num_ws, w_dim]
        rs = np.random.RandomState(seeds[0] + 7)
        dirs = torch.from_numpy(rs.randn(3, G.z_dim)).to(dev, torch.float32)
        dir_ws = G.mapping(dirs, None, truncation_psi=trunc) - G.mapping.w_avg  # 원운동·박동 방향 (w 공간)
    dir_ws = dir_ws / dir_ws[:, 0].norm(dim=-1, keepdim=True).reshape(3, 1, 1)
    w_scale = float((key_ws[:, 0] - G.mapping.w_avg).norm(dim=-1).mean())

    palettes = [np.array(STATE_PALETTES[MOOD_PALETTE.get(s["mood"], "stability")], dtype=np.float32) for s in sections]
    fade = int(plan.get("crossfade_sec", 2.5) * fps)

    def w_at(i: int, dp: float) -> torch.Tensor:
        """프레임 i 의 잠재점. dp 는 경로 위에서 앞뒤로 비켜 선 거리(구간 단위)."""
        p = pos[i] + dp
        k = int(min(len(sections) - 1, max(0, np.floor(p))))
        t = float(np.clip(p - k, 0, 1))
        e = t * t * t * (t * (t * 6 - 15) + 10)
        base = key_ws[k].lerp(key_ws[k + 1], e)
        phase = 2 * np.pi * p * 2.5
        wander = 0.25 * w_scale * (np.cos(phase) * dir_ws[0] + np.sin(phase) * dir_ws[1])
        return base + wander

    def palette_at(i: int) -> np.ndarray:
        k = int(min(len(sections) - 1, np.floor(pos[i])))
        if k + 1 < len(sections) and bounds[k + 1][0] - i < fade:
            m = 1 - (bounds[k + 1][0] - i) / fade
            return palettes[k] * (1 - m) + palettes[k + 1] * m
        return palettes[k]

    out = d / ("video.mp4" if args.seconds is None else "video_test.mp4")
    part = out.with_name(out.stem + ".part.mp4")  # 다 만들기 전에는 화면이 영상이 있다고 보지 않게 다른 이름으로 쓴다
    enc = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{args.size}x{args.size}", "-framerate", str(fps),
        "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part),
    ], stdin=subprocess.PIPE)
    assert enc.stdin is not None
    t0 = time.time()
    weights = torch.tensor((0.5, 0.25, 0.25), device=dev)
    with torch.no_grad():
        for b0 in range(0, n, args.batch):
            idx = list(range(b0, min(n, b0 + args.batch)))
            ws = []
            for i in idx:
                # 보조 잠재점은 경로 위 앞뒤 0.03구간(팀 루프 영상의 ±0.8프레임과 같은 폭). 박동이 오면 최대 6배로 벌어진다
                spread = 0.03 * (1 + 5.0 * pulse[i])
                kick = (0.5 * w_scale * pulse[i]) * dir_ws[2]
                ws += [w_at(i, 0) + kick, w_at(i, -spread), w_at(i, spread)]
            _, prog = synthesize_latent_stage(G.synthesis, torch.stack(ws), FEATURE_RES)
            prog = prog.reshape(len(idx), 3, 3, FEATURE_RES, FEATURE_RES)
            for i, images in zip(idx, prog):
                fields = latent_rgb_structure_fields(images, weights.to(images.dtype))
                rgb = colorize(fields, palette_at(i))
                frame = Image.fromarray(rgb, "RGB").resize((args.size, args.size), Image.Resampling.LANCZOS)
                enc.stdin.write(np.asarray(frame).tobytes())
            if dev.type == "mps":
                torch.mps.synchronize()
            if (b0 // args.batch) % 20 == 0:
                done = idx[-1] + 1
                print(f"{done}/{n} 프레임, {time.time() - t0:.0f}초", flush=True)
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg 실패")
    part.replace(out)
    meta = {"frames": n, "fps": fps, "size": args.size, "seconds": round(time.time() - t0, 1), "truncation": round(trunc, 3)}
    (d / "video.json").write_text(json.dumps(meta), encoding="utf-8")
    print(f"완료: {out} ({meta})")


if __name__ == "__main__":
    main()
