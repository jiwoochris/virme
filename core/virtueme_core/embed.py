"""임베딩. 두 공간을 쓴다.

- text 공간 (bge-m3): 모든 기록. 사진은 SigLIP 자동 태그를 "사진: 바다, 노을" 같은 문장으로 바꿔 넣는다.
- vision 공간 (SigLIP 2): 사진 기록. 한국어 키워드를 SigLIP 텍스트 탑으로 바꿔 사진과 직접 비교한다.

두 공간의 코사인 값은 크기가 다르므로(텍스트끼리 0.4~0.9, 사진-텍스트 0.0~0.2) 그대로 섞지 않는다.
query 모듈이 공간마다 배경 질의 대비 z 점수로 바꾼 뒤 합친다.

모델은 처음 쓸 때 내려받는다(bge-m3 약 2.3GB, SigLIP 2 base 약 1.5GB). 테스트는 `HashEmbedder` 를 쓴다.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .config import TEXT_MODEL, VISION_MODEL

# 사진 자동 태그 사전: (한국어 태그, SigLIP 에 넣을 영어 설명)
PHOTO_VOCAB: list[tuple[str, str]] = [
    ("바다", "the sea"), ("해변", "a sandy beach"), ("산", "mountains"), ("등산", "people hiking on a trail"),
    ("하늘", "the sky and clouds"), ("노을", "a sunset"), ("야경", "a city at night"), ("도시 거리", "a city street"),
    ("비", "a rainy day"), ("눈", "snow"), ("꽃", "flowers"), ("나무", "trees"), ("공원", "a park"),
    ("강", "a river"), ("호수", "a lake"), ("캠핑", "camping with a tent"), ("여행", "travel sightseeing"),
    ("공항", "an airport"), ("비행기", "an airplane"), ("기차", "a train"), ("지하철", "a subway"),
    ("버스", "a bus"), ("자동차", "a car"), ("자전거", "a bicycle"), ("카페", "a cafe interior"),
    ("커피", "a cup of coffee"), ("디저트", "a dessert"), ("케이크", "a cake"), ("빵", "bread"),
    ("음식", "a plate of food"), ("라면", "a bowl of ramen noodles"), ("고기", "grilled meat"),
    ("한식", "korean food"), ("술", "drinks with alcohol"), ("맥주", "a glass of beer"), ("요리", "cooking in a kitchen"),
    ("집밥", "a home-cooked meal"), ("식당", "a restaurant"), ("강아지", "a dog"), ("고양이", "a cat"),
    ("아기", "a baby"), ("아이", "a child"), ("가족", "a family together"), ("친구들", "a group of friends"),
    ("연인", "a couple"), ("셀카", "a selfie"), ("인물 사진", "a portrait of a person"), ("단체 사진", "a group photo"),
    ("파티", "a party"), ("생일", "a birthday celebration"), ("선물", "a gift"), ("결혼식", "a wedding"),
    ("졸업식", "a graduation ceremony"), ("공연", "a live concert"), ("전시", "an art exhibition"),
    ("영화관", "a movie theater"), ("책", "books"), ("공부", "studying at a desk"), ("노트북", "a laptop computer"),
    ("사무실", "an office"), ("회의", "a meeting"), ("학교", "a school"), ("운동", "exercise"),
    ("헬스장", "a gym"), ("달리기", "people running"), ("수영", "swimming"), ("요가", "yoga"), ("축구", "soccer"),
    ("병원", "a hospital"), ("침대", "a bed"), ("방", "a bedroom"), ("거실", "a living room"),
    ("반려식물", "a potted plant"), ("쇼핑", "shopping"), ("옷", "clothes"), ("신발", "shoes"),
    ("악기", "a musical instrument"), ("기타", "a guitar"), ("피아노", "a piano"), ("게임", "playing video games"),
    ("크리스마스", "christmas decorations"), ("불꽃놀이", "fireworks"), ("축제", "a festival"),
    ("스크린샷", "a screenshot of a phone screen"), ("문서", "a document with text"), ("영수증", "a receipt"),
    ("그림", "a drawing or painting"), ("밤", "at night"), ("아침", "a morning scene"),
]


def device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _normalize(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return m / n


def load_image(path: str | Path):
    from PIL import Image, ImageOps

    from .ingest import photos  # noqa: F401  HEIC 열기 등록

    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).convert("RGB")


class TextEmbedder:
    name = TEXT_MODEL

    def __init__(self, model: str = TEXT_MODEL):
        self.name = model
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.name, device=device())
            self._model.max_seq_length = 256
        return self._model

    def encode(self, texts: Sequence[str], batch_size: int = 16) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1024), dtype=np.float32)
        m = self._load()
        return _normalize(m.encode(list(texts), batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False))


class VisionEmbedder:
    name = VISION_MODEL

    def __init__(self, model: str = VISION_MODEL):
        self.name = model
        self._model = None
        self._proc = None

    def _load(self):
        if self._model is None:
            import torch
            from transformers import AutoModel, AutoProcessor

            dtype = torch.float16 if device() != "cpu" else torch.float32
            self._model = AutoModel.from_pretrained(self.name, dtype=dtype).to(device()).eval()
            self._proc = AutoProcessor.from_pretrained(self.name)
        return self._model, self._proc

    @staticmethod
    def _out(o):
        return o.pooler_output if hasattr(o, "pooler_output") else o

    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        import torch

        model, proc = self._load()
        out = []
        with torch.no_grad():
            for i in range(0, len(texts), 64):
                inp = proc(text=list(texts[i : i + 64]), padding="max_length", max_length=64, truncation=True,
                           return_tensors="pt").to(device())
                out.append(self._out(model.get_text_features(**inp)).float().cpu().numpy())
        return _normalize(np.concatenate(out)) if out else np.zeros((0, 768), dtype=np.float32)

    def encode_images(self, paths: Sequence[str], batch_size: int = 16) -> np.ndarray:
        import torch

        model, proc = self._load()
        out = []
        with torch.no_grad():
            for i in range(0, len(paths), batch_size):
                imgs = [load_image(p) for p in paths[i : i + batch_size]]
                inp = proc(images=imgs, return_tensors="pt").to(device())
                inp = {k: (v.to(model.dtype) if v.is_floating_point() else v) for k, v in inp.items()}
                out.append(self._out(model.get_image_features(**inp)).float().cpu().numpy())
        return _normalize(np.concatenate(out)) if out else np.zeros((0, 768), dtype=np.float32)

    def logit_params(self) -> tuple[float, float]:
        model, _ = self._load()
        return float(model.logit_scale.exp().item()), float(model.logit_bias.item())


class HashEmbedder:
    """테스트용 결정론적 임베더. 글자 2-gram 을 해시해서 벡터로 만든다. 모델을 내려받지 않는다."""

    def __init__(self, name: str = "hash-2gram", dim: int = 256):
        self.name = name
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        s = f" {text} "
        for i in range(len(s) - 1):
            h = int(hashlib.md5(s[i : i + 2].encode("utf-8")).hexdigest()[:8], 16)
            v[h % self.dim] += 1.0
        return v

    def encode(self, texts: Sequence[str], batch_size: int = 16) -> np.ndarray:
        return _normalize(np.stack([self._vec(t) for t in texts])) if texts else np.zeros((0, self.dim), np.float32)

    # 비전 인터페이스 흉내: 이미지 경로의 파일 이름을 텍스트처럼 쓴다
    def encode_texts(self, texts: Sequence[str]) -> np.ndarray:
        return self.encode(texts)

    def encode_images(self, paths: Sequence[str], batch_size: int = 16) -> np.ndarray:
        return self.encode([Path(p).stem.replace("_", " ") for p in paths])

    def logit_params(self) -> tuple[float, float]:
        return 10.0, -5.0


def zero_shot_tags(
    vision: VisionEmbedder | HashEmbedder, img_vecs: np.ndarray, top_k: int = 4, min_prob: float = 0.08
) -> list[list[tuple[str, float]]]:
    """사진마다 PHOTO_VOCAB 에서 가장 맞는 태그를 고른다. 확률은 어휘 안 softmax (SigLIP 로짓 기준)."""
    if len(img_vecs) == 0:
        return []
    prompts = [f"a photo of {en}" for _, en in PHOTO_VOCAB]
    tv = vision.encode_texts(prompts)
    scale, _ = vision.logit_params()
    logits = img_vecs @ tv.T * scale
    logits -= logits.max(axis=1, keepdims=True)
    probs = np.exp(logits)
    probs /= probs.sum(axis=1, keepdims=True)
    out = []
    for row in probs:
        idx = np.argsort(-row)[:top_k]
        tags = [(PHOTO_VOCAB[i][0], float(row[i])) for i in idx if row[i] >= min_prob]
        if not tags:
            tags = [(PHOTO_VOCAB[int(idx[0])][0], float(row[idx[0]]))]
        out.append(tags)
    return out


def sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1 / (1 + np.exp(-x)) if isinstance(x, np.ndarray) else 1 / (1 + math.exp(-x))
