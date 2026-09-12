"""Measure how long one frame takes on whatever device this machine has.

Run it here to get the numbers that justify a GPU request, and run it again on the
rented box to see what the same checkpoint does there:

    bun run model:bench
    bun run model:bench -- --batch 1,4,8 --frames 24 --json generated/bench-gpu.json
"""

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model_service"))

from device import device_report  # noqa: E402
from latent_control import ModelRequest  # noqa: E402
from stylegan_runtime import ROOT, StyleGanRuntime  # noqa: E402


def build_requests(count: int, offset: int) -> list[ModelRequest]:
    return [
        ModelRequest(
            signals=[0.18, 0.78, 0.22, 0.41, 0.68],
            activity=0.72,
            change=0.36,
            confidence=0.88,
            intensity=0.86,
            state="novelty",
            seed=(609348 + offset + index * 7919) % (2**31),
        )
        for index in range(count)
    ]


def measure(runtime: StyleGanRuntime, batch: int, frames: int, warmup: int) -> dict:
    for index in range(warmup):
        runtime.generate_many(build_requests(batch, offset=index * 101))
    rounds = max(1, -(-frames // batch))
    per_frame: list[float] = []
    started = time.perf_counter()
    for index in range(rounds):
        round_started = time.perf_counter()
        runtime.generate_many(build_requests(batch, offset=(warmup + index) * 101))
        per_frame.append((time.perf_counter() - round_started) / batch)
    total = time.perf_counter() - started
    rendered = rounds * batch
    return {
        "batch": batch,
        "frames": rendered,
        "seconds_per_frame_median": round(statistics.median(per_frame), 4),
        "seconds_per_frame_slowest": round(max(per_frame), 4),
        "frames_per_second": round(rendered / total, 3) if total > 0 else 0.0,
        "total_seconds": round(total, 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the StyleGAN frame service")
    parser.add_argument("--batch", default="1", help="comma separated batch sizes, e.g. 1,4,8")
    parser.add_argument("--frames", type=int, default=12, help="frames to render per batch size")
    parser.add_argument("--warmup", type=int, default=2, help="warmup rounds that are not measured")
    parser.add_argument("--model", default=None, help="checkpoint path; defaults to the church model")
    parser.add_argument("--json", default=None, help="write the report to this file as well")
    arguments = parser.parse_args()

    batches = [int(value) for value in arguments.batch.split(",") if value.strip()]
    runtime = StyleGanRuntime(Path(arguments.model) if arguments.model else None)
    runtime.load()

    report = {
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            **device_report(runtime.device, runtime.precision),
        },
        "model": runtime.model_path.name,
        "resolution": int(runtime.generator.img_resolution),
        "feature_resolution": runtime.metadata["feature_resolution"],
        "load_seconds": round(runtime.load_seconds, 3),
        "runs": [measure(runtime, batch, arguments.frames, arguments.warmup) for batch in batches],
    }

    print(json.dumps(report, indent=2))
    print()
    print(f"{'batch':>6}  {'sec/frame':>10}  {'slowest':>9}  {'frames/sec':>11}")
    for run in report["runs"]:
        print(
            f"{run['batch']:>6}  {run['seconds_per_frame_median']:>10.4f}  "
            f"{run['seconds_per_frame_slowest']:>9.4f}  {run['frames_per_second']:>11.3f}"
        )

    destination = Path(arguments.json) if arguments.json else ROOT / "generated" / "stylegan-benchmark.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nreport written to {destination}")


if __name__ == "__main__":
    main()
