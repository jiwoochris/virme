"""표지 이미지에서 결정적으로 잴 수 있는 값.

같은 이미지면 언제나 같은 값이 나온다 (k-means 시드 고정, 축소 크기 고정).

- palette        : 주요 색 5개와 면적 비율. 128px 로 줄인 픽셀을 CIELAB 에서 k-means(k=5)
- lightness      : 평균 명도 L* (0..100). 45 미만이면 dark, 65 초과면 light, 그 사이 mid
- contrast       : 명도 표준편차 / 50 (0..1 근처, 높을수록 명암 대비가 큼)
- colorfulness   : Hasler–Süsstrunk 색채도 / 100 (0 무채색 .. 1 매우 화려)
- edge_density   : 소벨 기울기가 큰 픽셀 비율 (그림·글자가 빽빽할수록 큼)
- negative_space : 16×16 칸 가운데 거의 평평한(명도 표준편차 < 4) 칸의 비율
- aspect         : 높이 / 너비
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

PALETTE_K = 5
THUMB = 128


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(N,3) 0..255 sRGB → (N,3) CIELAB (D65)."""
    c = _srgb_to_linear(np.asarray(rgb, dtype=float) / 255.0)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    lab = np.atleast_2d(np.asarray(lab, dtype=float))
    fy = (lab[:, 0] + 16) / 116
    fx, fz = fy + lab[:, 1] / 500, fy - lab[:, 2] / 200

    def inv(t: np.ndarray) -> np.ndarray:
        return np.where(t ** 3 > 0.008856, t ** 3, (t - 16 / 116) / 7.787)

    xyz = np.stack([inv(fx), inv(fy), inv(fz)], axis=1) * np.array([0.95047, 1.0, 1.08883])
    m = np.array([[3.2406, -1.5372, -0.4986], [-0.9689, 1.8758, 0.0415], [0.0557, -0.2040, 1.0570]])
    lin = np.clip(xyz @ m.T, 0, 1)
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)
    return np.clip(np.round(srgb * 255), 0, 255)


def hex_of(rgb: Any) -> str:
    r, g, b = (int(round(float(x))) for x in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def rgb_of(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def kmeans(x: np.ndarray, k: int, iters: int = 25, seed: int = 0, weights: np.ndarray | None = None
           ) -> tuple[np.ndarray, np.ndarray]:
    """작고 결정적인 k-means (k-means++ 초기화, 시드 고정). 반환: (중심, 각 점의 묶음 번호)."""
    rng = np.random.default_rng(seed)
    n = len(x)
    w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)
    k = max(1, min(k, n))
    centers = [x[rng.choice(n, p=w / w.sum())]]
    for _ in range(1, k):
        d = np.min(((x[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1) * w
        if d.sum() <= 0:
            break
        centers.append(x[rng.choice(n, p=d / d.sum())])
    c = np.array(centers)
    lab = np.zeros(n, dtype=int)
    for _ in range(iters):
        lab = ((x[:, None, :] - c[None]) ** 2).sum(-1).argmin(1)
        new = np.array([np.average(x[lab == j], axis=0, weights=w[lab == j]) if (lab == j).any() else c[j]
                        for j in range(len(c))])
        if np.allclose(new, c):
            break
        c = new
    return c, lab


def load_rgb(path: Path | str, size: int = THUMB) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    w, h = im.size
    scale = size / max(w, h)
    im = im.resize((max(8, round(w * scale)), max(8, round(h * scale))), Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=float)


def palette(arr: np.ndarray, k: int = PALETTE_K) -> list[dict[str, Any]]:
    """주요 색과 면적 비율 (비율이 큰 순)."""
    px = arr.reshape(-1, 3)
    lab = rgb_to_lab(px)
    centers, labels = kmeans(lab, k)
    share = np.bincount(labels, minlength=len(centers)) / len(labels)
    rgb = lab_to_rgb(centers)
    out = [{"hex": hex_of(rgb[i]), "share": round(float(share[i]), 3), "L": round(float(centers[i, 0]), 1)}
           for i in range(len(centers)) if share[i] > 0]
    return sorted(out, key=lambda p: -p["share"])


def _gray(arr: np.ndarray) -> np.ndarray:
    return rgb_to_lab(arr.reshape(-1, 3))[:, 0].reshape(arr.shape[:2])


def measure_array(arr: np.ndarray, aspect: float | None = None) -> dict[str, Any]:
    L = _gray(arr)
    mean_l = float(L.mean())
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    rg, yb = r - g, 0.5 * (r + g) - b
    colorfulness = (np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean())) / 100
    gx = np.zeros_like(L)
    gy = np.zeros_like(L)
    gx[1:-1, 1:-1] = (L[:-2, 2:] + 2 * L[1:-1, 2:] + L[2:, 2:]) - (L[:-2, :-2] + 2 * L[1:-1, :-2] + L[2:, :-2])
    gy[1:-1, 1:-1] = (L[2:, :-2] + 2 * L[2:, 1:-1] + L[2:, 2:]) - (L[:-2, :-2] + 2 * L[:-2, 1:-1] + L[:-2, 2:])
    edge = float((np.hypot(gx, gy)[1:-1, 1:-1] > 40).mean())
    h, w = L.shape
    flat = 0
    for i in range(16):
        for j in range(16):
            cell = L[i * h // 16:(i + 1) * h // 16, j * w // 16:(j + 1) * w // 16]
            flat += int(cell.size > 0 and cell.std() < 4)
    return {
        "palette": palette(arr),
        "lightness": round(mean_l, 1),
        "tone": "dark" if mean_l < 45 else "light" if mean_l > 65 else "mid",
        "contrast": round(float(L.std()) / 50, 3),
        "colorfulness": round(float(colorfulness), 3),
        "edge_density": round(edge, 3),
        "negative_space": round(flat / 256, 3),
        "aspect": round(aspect if aspect is not None else h / w, 3),
    }


def measure(path: Path | str) -> dict[str, Any]:
    with Image.open(path) as im:
        w, h = im.size
    return measure_array(load_rgb(path), aspect=h / w)


def region_stats(path: Path | str, box: tuple[float, float, float, float]) -> dict[str, float]:
    """그림의 한 영역(비율 좌표 x0,y0,x1,y1)의 평균 명도와 복잡도. 제목 글자색·바탕막을 정할 때 쓴다."""
    arr = load_rgb(path, 256)
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = box
    crop = arr[int(y0 * h):max(int(y0 * h) + 1, int(y1 * h)), int(x0 * w):max(int(x0 * w) + 1, int(x1 * w))]
    L = _gray(crop)
    return {"lightness": float(L.mean()), "busy": float(L.std() / 50)}


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG 명도 대비."""
    def lum(h: str) -> float:
        c = _srgb_to_linear(np.array(rgb_of(h)) / 255.0)
        return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])

    a, b = sorted((lum(hex_a), lum(hex_b)), reverse=True)
    return (a + 0.05) / (b + 0.05)
