import os

import torch


SUPPORTED_DEVICES = ("cuda", "mps", "cpu")
SUPPORTED_PRECISIONS = ("auto", "fp16", "fp32")


def _available(name: str) -> bool:
    if name == "cuda":
        return torch.cuda.is_available()
    if name == "mps":
        return torch.backends.mps.is_available()
    return True


def resolve_device() -> torch.device:
    # VIRME_DEVICE pins the backend; anything else walks cuda -> mps -> cpu so the
    # same service runs on a rented GPU box and on the laptop without code edits.
    requested = os.environ.get("VIRME_DEVICE", "auto").strip().lower() or "auto"
    if requested != "auto":
        if requested not in SUPPORTED_DEVICES:
            raise ValueError(f"VIRME_DEVICE must be auto or one of {'/'.join(SUPPORTED_DEVICES)}, got {requested!r}")
        if not _available(requested):
            raise RuntimeError(f"VIRME_DEVICE={requested} is not available on this machine")
        return torch.device(requested)
    for name in SUPPORTED_DEVICES:
        if _available(name):
            return torch.device(name)
    return torch.device("cpu")


def resolve_precision(device: torch.device) -> torch.dtype:
    # Half precision only pays off on CUDA. MPS keeps hitting fp32 fallbacks and CPU
    # has no fp16 kernels for these blocks, so both stay on fp32.
    requested = os.environ.get("VIRME_PRECISION", "auto").strip().lower() or "auto"
    if requested not in SUPPORTED_PRECISIONS:
        raise ValueError(f"VIRME_PRECISION must be one of {'/'.join(SUPPORTED_PRECISIONS)}, got {requested!r}")
    if requested == "fp16":
        if device.type != "cuda":
            raise RuntimeError(f"VIRME_PRECISION=fp16 needs a cuda device, current device is {device.type}")
        return torch.float16
    if requested == "fp32":
        return torch.float32
    return torch.float16 if device.type == "cuda" else torch.float32


def serializes_requests(device: torch.device) -> bool:
    # MPS command encoders are not safe to create concurrently across FastAPI worker
    # threads, so those devices keep every operation in one lane. CUDA streams handle
    # concurrent work, where the same lock would only cap throughput.
    return device.type != "cuda"


def synchronize(device: torch.device) -> None:
    if device.type == "mps":
        torch.mps.synchronize()
    elif device.type == "cuda":
        torch.cuda.synchronize()


def device_report(device: torch.device, precision: torch.dtype) -> dict:
    report = {
        "device": device.type,
        "precision": "fp16" if precision == torch.float16 else "fp32",
        "concurrent_requests": not serializes_requests(device),
    }
    if device.type == "cuda":
        index = device.index or 0
        properties = torch.cuda.get_device_properties(index)
        report["gpu_name"] = properties.name
        report["gpu_memory_gb"] = round(properties.total_memory / 1024**3, 1)
        report["cuda_version"] = torch.version.cuda or "unknown"
    return report
