"""임베딩 모델 비교: 한국어 문장끼리 검색(MAP)과 한국어 키워드 → 사진 검색(top-1).

- 문장: eval/ko_mini.jsonl (66문장, 주제 12개)
- 사진: 위키미디어 공용에서 주제별로 한 장씩 받는다 (data/bench_imgs/, 저장소에는 없음)

    uv run python eval/embed_bench.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from virtueme_core.config import DATA_DIR  # noqa: E402
from virtueme_core.embed import device, load_image  # noqa: E402

UA = {"User-Agent": "virtueme-bench/0.1 (https://github.com/DECK6/virme; embedding benchmark)"}
IMAGES = {  # 파일 이름: (위키미디어 검색어, 관련 주제)
    "family_dinner": ("family eating dinner together table", ["가족", "음식"]),
    "dog_walk": ("dog walking leash park", ["반려동물"]),
    "cat_sleep": ("cat sleeping", ["반려동물", "휴식"]),
    "running": ("marathon runners road race", ["건강"]),
    "beach": ("sandy beach sea waves", ["여행", "휴식"]),
    "rain_umbrella": ("people umbrella rain street", ["비"]),
    "concert": ("rock concert crowd stage", ["음악"]),
    "ramen": ("ramen noodle bowl", ["음식"]),
    "office_meeting": ("business meeting office people table", ["일"]),
    "friends_party": ("friends birthday party celebration", ["친구"]),
    "hiking": ("hiking trail mountain hikers", ["건강", "여행"]),
    "graduation": ("graduation ceremony caps gowns", ["성장"]),
    "sofa_relax": ("woman relaxing on sofa living room", ["휴식"]),
    "books_reading": ("person reading book library", ["성장", "휴식"]),
    "guitar": ("playing acoustic guitar hands", ["음악"]),
}


def fetch_images(folder: Path) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    got = []
    for name, (q, _) in IMAGES.items():
        path = folder / f"{name}.jpg"
        if not path.exists():
            r = httpx.get("https://commons.wikimedia.org/w/api.php", params={
                "action": "query", "generator": "search", "gsrsearch": f"filetype:bitmap {q}", "gsrnamespace": 6,
                "gsrlimit": 5, "prop": "imageinfo", "iiprop": "url|mime", "iiurlwidth": 448, "format": "json"},
                headers=UA, timeout=30).json()
            pages = sorted(r.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
            for p in pages:
                ii = p.get("imageinfo", [{}])[0]
                if ii.get("mime") in ("image/jpeg", "image/png") and ii.get("thumburl"):
                    path.write_bytes(httpx.get(ii["thumburl"], headers=UA, timeout=60).content)
                    break
        if path.exists():
            got.append(name)
    return got


def ap(scores: np.ndarray, labels: list[bool]) -> float:
    order = np.argsort(-scores)
    hits, s = 0, 0.0
    for i, idx in enumerate(order, 1):
        if labels[idx]:
            hits += 1
            s += hits / i
    return s / max(1, sum(labels))


def norm(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def main() -> None:
    import torch
    from sentence_transformers import SentenceTransformer
    from transformers import AutoModel, AutoProcessor

    mini = [json.loads(line) for line in (ROOT / "eval/ko_mini.jsonl").read_text(encoding="utf-8").splitlines()]
    queries = json.loads((ROOT / "eval/ko_mini.meta.json").read_text(encoding="utf-8"))["queries"]
    texts = [m["text"] for m in mini]
    names = fetch_images(DATA_DIR / "bench_imgs")
    imgs = [load_image(DATA_DIR / "bench_imgs" / f"{n}.jpg") for n in names]
    dev = device()

    def report(name: str, qv: np.ndarray, rv: np.ndarray, iv: np.ndarray | None, secs: float) -> None:
        s = norm(qv) @ norm(rv).T
        text_map = np.mean([ap(s[i], [q in m["labels"] for m in mini]) for i, q in enumerate(queries)])
        line = f"{name:40s} 문장 MAP {text_map:.3f}"
        if iv is not None:
            si = norm(qv) @ norm(iv).T
            maps, top1 = [], []
            for i, q in enumerate(queries):
                lab = [q in IMAGES[n][1] for n in names]
                if any(lab):
                    maps.append(ap(si[i], lab))
                    top1.append(lab[int(np.argmax(si[i]))])
            line += f"  사진 MAP {np.mean(maps):.3f}  top-1 {np.mean(top1):.3f}"
        print(line + f"  ({secs:.0f}초)")

    for mid in ("google/siglip2-base-patch16-224", "google/siglip2-so400m-patch14-384"):
        t0 = time.time()
        model = AutoModel.from_pretrained(mid, dtype=torch.float16 if dev != "cpu" else torch.float32).to(dev).eval()
        proc = AutoProcessor.from_pretrained(mid)
        out = lambda o: (o.pooler_output if hasattr(o, "pooler_output") else o).float().cpu().numpy()  # noqa: E731
        with torch.no_grad():
            tx = lambda xs: out(model.get_text_features(**proc(text=xs, padding="max_length", max_length=64,  # noqa: E731
                                                                  truncation=True, return_tensors="pt").to(dev)))
            inp = proc(images=imgs, return_tensors="pt").to(dev)
            inp = {k: (v.to(model.dtype) if v.is_floating_point() else v) for k, v in inp.items()}
            report(mid, tx(queries), tx(texts), out(model.get_image_features(**inp)), time.time() - t0)
        model = None  # 다음 모델을 올리기 전에 메모리를 비운다
    for mid, qp, dp in (("intfloat/multilingual-e5-base", "query: ", "passage: "), ("BAAI/bge-m3", "", "")):
        t0 = time.time()
        m = SentenceTransformer(mid, device=dev)
        report(mid, m.encode([qp + q for q in queries]), m.encode([dp + t for t in texts]), None, time.time() - t0)


if __name__ == "__main__":
    main()
