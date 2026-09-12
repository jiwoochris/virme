import asyncio
import base64
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response

from latent_control import ModelRequest
from stylegan_runtime import StyleGanRuntime


MAX_BATCH = 64

runtime = StyleGanRuntime()
load_error: str | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global load_error
    try:
        await asyncio.to_thread(runtime.load)
    except Exception as error:
        load_error = str(error)
    yield


app = FastAPI(title="Virtueme Mac StyleGAN", lifespan=lifespan)


@app.get("/health")
def health():
    if load_error:
        return {"status": "error", "detail": load_error, **runtime.metadata}
    return runtime.metadata


@app.post("/generate")
async def generate(payload: dict):
    if load_error:
        raise HTTPException(status_code=503, detail=load_error)
    try:
        image, metadata = await asyncio.to_thread(runtime.generate_jpeg, ModelRequest.from_dict(payload))
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={
            "X-StyleGAN-Device": str(metadata["device"]),
            "X-StyleGAN-Precision": str(metadata["precision"]),
            "X-StyleGAN-Model": str(metadata["model"]),
            "X-StyleGAN-Seconds": str(metadata["generation_seconds"]),
            "X-StyleGAN-Render-Mode": str(metadata["render_mode"]),
        },
    )


@app.post("/generate/batch")
async def generate_batch(payload: dict):
    # One trip through the generator for several frames. A GPU fills a batch at close to
    # the cost of a single frame, so pre-rendering a loop is where the rented box pays off.
    if load_error:
        raise HTTPException(status_code=503, detail=load_error)
    frames = payload.get("frames")
    if not isinstance(frames, list) or not frames:
        raise HTTPException(status_code=400, detail="frames must be a non-empty list of generate payloads")
    if len(frames) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"frames must hold at most {MAX_BATCH} items")
    requests = [ModelRequest.from_dict(frame) for frame in frames]
    try:
        images, metadata = await asyncio.to_thread(runtime.generate_many, requests)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {
        "frames": [
            {"jpeg_base64": base64.b64encode(image).decode("ascii"), **entry}
            for image, entry in zip(images, metadata)
        ]
    }
