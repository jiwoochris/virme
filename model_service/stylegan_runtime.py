import io
import os
import sys
import threading
import time
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from device import device_report, resolve_device, resolve_precision, serializes_requests, synchronize
from latent_control import LatentControl, ModelRequest, derive_control


ROOT = Path(__file__).resolve().parents[1]
STYLEGAN_ROOT = ROOT / ".runtime" / "stylegan2-ada-pytorch"
DEFAULT_MODEL = ROOT / "models" / "stylegan2-church-config-f.pkl"
FEATURE_RESOLUTION = 128


STATE_PALETTES = {
    "stability": ((8, 15, 18), (35, 72, 70), (126, 159, 137)),
    "novelty": ((12, 15, 17), (76, 91, 66), (185, 164, 86)),
    "conflict": ((15, 12, 18), (81, 39, 51), (154, 74, 65)),
    "uncertainty": ((11, 13, 18), (52, 55, 75), (119, 111, 132)),
    "possible-self": ((8, 14, 18), (34, 78, 86), (132, 151, 128)),
    "object": ((17, 13, 12), (104, 66, 38), (236, 185, 106)),
    "situation": ((13, 10, 18), (79, 39, 82), (162, 119, 188)),
}


def _smooth_field(field: torch.Tensor) -> torch.Tensor:
    shaped = field.reshape(1, 1, *field.shape)
    for _ in range(3):
        shaped = F.avg_pool2d(shaped, kernel_size=5, stride=1, padding=2)
    return shaped[0, 0]


def _normalize_field(field: torch.Tensor, signed: bool) -> torch.Tensor:
    centered = field - field.mean()
    deviation = centered.std()
    if float(deviation) < 1e-6:
        return torch.zeros_like(field)
    if signed:
        return torch.tanh((centered / deviation) * 0.72)
    minimum = field.amin()
    span = (field.amax() - minimum).clamp_min(1e-6)
    normalized = ((field - minimum) / span).clamp(0, 1)
    return normalized.square() * (3 - 2 * normalized)


def latent_rgb_structure_fields(images: torch.Tensor, weights: torch.Tensor) -> torch.Tensor:
    """Extract inferable form from differences between progressive latent RGB stages."""
    if images.shape[0] < 2:
        return torch.zeros((3, *images.shape[-2:]), device=images.device, dtype=images.dtype)
    normalized_weights = weights / weights.sum().clamp_min(1e-8)
    shaped_all = normalized_weights.reshape(-1, 1, 1, 1).to(images.device, images.dtype)
    mean_image = (images * shaped_all).sum(dim=0)
    variance = ((images - mean_image).square() * shaped_all).sum(dim=0).mean(dim=0).sqrt()

    secondary_weights = normalized_weights[1:]
    secondary_weights = secondary_weights / secondary_weights.sum().clamp_min(1e-8)
    shaped = secondary_weights.reshape(-1, 1, 1, 1).to(images.device, images.dtype)
    primary = images[0]
    secondary = images[1:]
    secondary_mean = (secondary * shaped).sum(dim=0)
    direction_tensor = secondary_mean - primary
    luma = torch.tensor((0.2126, 0.7152, 0.0722), device=images.device, dtype=images.dtype).reshape(3, 1, 1)
    direction = (direction_tensor * luma).sum(dim=0)

    mean_luma = (mean_image * luma).sum(dim=0)
    horizontal = F.pad(mean_luma[:, 2:] - mean_luma[:, :-2], (1, 1, 0, 0))
    vertical = F.pad(mean_luma[2:, :] - mean_luma[:-2, :], (0, 0, 1, 1))
    semantic_edges = (horizontal.square() + vertical.square()).sqrt()
    mass = _normalize_field(F.avg_pool2d(variance[None, None], 3, 1, 1)[0, 0], signed=False)
    contours = _normalize_field(semantic_edges, signed=False)
    distance = (mass * 0.52 + contours * 0.9).clamp(0, 1)
    height, width = distance.shape
    edge_y = torch.linspace(-1, 1, height, device=images.device, dtype=images.dtype).abs()
    edge_x = torch.linspace(-1, 1, width, device=images.device, dtype=images.dtype).abs()
    edge_fade = ((1 - edge_y[:, None]).clamp(0, 0.16) / 0.16) * ((1 - edge_x[None, :]).clamp(0, 0.16) / 0.16)
    distance = distance * edge_fade
    curvature = ((secondary - secondary_mean).square() * shaped).sum(dim=0).mean(dim=0).sqrt()
    direction = _normalize_field(F.avg_pool2d(direction[None, None], 3, 1, 1)[0, 0], signed=True)
    curvature = _normalize_field(_smooth_field(curvature), signed=False)
    return torch.stack((distance, direction, curvature))


def structure_map_to_rgb(fields: torch.Tensor, state: str) -> torch.Tensor:
    """Colorize geometric latent statistics with a restrained, non-random palette."""
    palette = STATE_PALETTES.get(state, STATE_PALETTES["stability"])
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
    return (rgb * 255).round().clamp(0, 255).to(torch.uint8).cpu()


def synthesize_latent_stage(
    synthesis,
    ws: torch.Tensor,
    target_resolution: int,
    force_fp32: bool = True,
) -> tuple[torch.Tensor, torch.Tensor]:
    x = img = None
    w_index = 0
    for resolution in synthesis.block_resolutions:
        block = getattr(synthesis, f"b{resolution}")
        block_ws = ws.narrow(1, w_index, block.num_conv + block.num_torgb)
        x, img = block(x, img, block_ws, noise_mode="const", force_fp32=force_fp32)
        w_index += block.num_conv
        if resolution == target_resolution:
            return x, img
    raise ValueError(f"Feature resolution unavailable: {target_resolution}")


class StyleGanRuntime:
    def __init__(self, model_path: Path | None = None):
        self.model_path = model_path or Path(os.environ.get("VIRTUEME_STYLEGAN_MODEL", DEFAULT_MODEL))
        self.device = resolve_device()
        self.precision = resolve_precision(self.device)
        self.generator = None
        self.load_seconds = 0.0
        self.lock = threading.Lock()

    def load(self):
        if self.generator is not None:
            return self
        if not STYLEGAN_ROOT.exists():
            raise RuntimeError("StyleGAN runtime missing; run scripts/setup-stylegan.sh")
        if not self.model_path.exists():
            raise RuntimeError(f"StyleGAN checkpoint missing: {self.model_path}")
        sys.path.insert(0, str(STYLEGAN_ROOT))
        import legacy

        started = time.perf_counter()
        with self.model_path.open("rb") as handle:
            self.generator = legacy.load_network_pkl(handle)["G_ema"].eval().requires_grad_(False).to(self.device)
        self.load_seconds = time.perf_counter() - started
        return self

    @property
    def metadata(self) -> dict:
        report = device_report(self.device, self.precision)
        if self.generator is None:
            return {"status": "loading", "model": self.model_path.name, **report}
        return {
            "status": "ready",
            "backend": "stylegan2-ada-pytorch",
            "model": self.model_path.name,
            "resolution": int(self.generator.img_resolution),
            "render_mode": "latent-structure",
            "feature_resolution": FEATURE_RESOLUTION,
            "load_seconds": round(self.load_seconds, 3),
            **report,
        }

    def _lane(self):
        return self.lock if serializes_requests(self.device) else nullcontext()

    def _render_structures(self, controls: Sequence[LatentControl], states: Sequence[str]) -> list[torch.Tensor]:
        seed_sets = [(control.seed,) + control.secondary_seeds for control in controls]
        grouped: dict[float, list[int]] = {}
        for index, control in enumerate(controls):
            grouped.setdefault(round(control.truncation, 4), []).append(index)
        structures: list[torch.Tensor | None] = [None] * len(controls)
        with self._lane(), torch.no_grad():
            for truncation, indexes in grouped.items():
                # One mapping and one synthesis pass per truncation value, so a batch of
                # frames costs a single trip through the generator instead of one each.
                latents = np.stack([
                    np.random.RandomState(seed).randn(self.generator.z_dim)
                    for index in indexes
                    for seed in seed_sets[index]
                ])
                z = torch.from_numpy(latents).to(device=self.device, dtype=torch.float32)
                ws = self.generator.mapping(z, None, truncation_psi=truncation)
                _, progressive_images = synthesize_latent_stage(
                    self.generator.synthesis,
                    ws,
                    FEATURE_RESOLUTION,
                    force_fp32=self.precision == torch.float32,
                )
                # Structure fields stay in fp32 so a half-precision synthesis pass still
                # renders the frame the laptop renders.
                progressive_images = progressive_images.float()
                offset = 0
                for index in indexes:
                    span = len(seed_sets[index])
                    weights = torch.tensor(controls[index].weights, device=self.device, dtype=progressive_images.dtype)
                    structure = latent_rgb_structure_fields(progressive_images[offset:offset + span], weights)
                    structures[index] = structure_map_to_rgb(structure, states[index])
                    offset += span
            synchronize(self.device)
        return [structure for structure in structures if structure is not None]

    def _encode_jpeg(self, structure: torch.Tensor) -> bytes:
        array = structure.permute(1, 2, 0).numpy()
        output = io.BytesIO()
        image = Image.fromarray(array, "RGB").resize(
            (int(self.generator.img_resolution), int(self.generator.img_resolution)),
            Image.Resampling.LANCZOS,
        )
        image.save(output, format="JPEG", quality=92, optimize=True)
        return output.getvalue()

    def generate_many(self, requests: Sequence[ModelRequest]) -> tuple[list[bytes], list[dict]]:
        if not requests:
            return [], []
        self.load()
        controls = [derive_control(request) for request in requests]
        started = time.perf_counter()
        structures = self._render_structures(controls, [request.state for request in requests])
        seconds_each = (time.perf_counter() - started) / len(requests)
        payloads = [self._encode_jpeg(structure) for structure in structures]
        metadata = [
            {
                **self.metadata,
                "seed": control.seed,
                "truncation": round(control.truncation, 4),
                "generation_seconds": round(seconds_each, 3),
                "batch_size": len(requests),
            }
            for control in controls
        ]
        return payloads, metadata

    def generate_jpeg(self, request: ModelRequest) -> tuple[bytes, dict]:
        payloads, metadata = self.generate_many([request])
        return payloads[0], metadata[0]
