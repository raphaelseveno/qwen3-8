"""OpenAI-compatible, wake-on-demand proxy for a SkyPilot model cluster.

Run this on the Mac (or a small always-on CPU VM) where `sky check` has already
been completed. It deliberately does not contain cloud credentials.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger("qwen_wake_gateway")

def load_dotenv() -> None:
    """Load local configuration without logging its secret values."""
    path = Path(os.getenv("GATEWAY_ENV_FILE", ".env"))
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


load_dotenv()

# Read configuration after .env has been loaded so the documented overrides work.
CLUSTER = os.getenv("SKY_CLUSTER", "qwen-coding")
TASK_FILE = Path(os.getenv("SKY_TASK_FILE", "skypilot/model-server.yaml"))
MODEL_ID = os.getenv("MODEL_ID", "qwen3.8-27b-obliterated-q8")
IDLE_MINUTES = int(os.getenv("IDLE_MINUTES", "10"))
PORT = os.getenv("MODEL_PORT", "8080")
# Optional public/private registry image that already contains CUDA llama.cpp.
# A blank value uses the portable (but slower) compile-on-first-GPU task.
MODEL_SERVER_IMAGE = os.getenv("MODEL_SERVER_IMAGE", "").strip()

_wake_lock = asyncio.Lock()
_active_requests = 0
_idle_task: asyncio.Task | None = None


async def sky(*args: str, allow_failure: bool = False) -> str:
    """Run the local SkyPilot CLI without exposing credentials to OpenHands."""
    proc = await asyncio.create_subprocess_exec(
        "sky", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    output, _ = await proc.communicate()
    text = output.decode(errors="replace")
    if proc.returncode and not allow_failure:
        raise RuntimeError(text[-2000:])
    return text


async def endpoint() -> str | None:
    output = await sky("status", "--endpoint", PORT, CLUSTER, allow_failure=True)
    # SkyPilot may append a human-readable status message after the endpoint.
    # Stop at any whitespace rather than treating it as part of the URL.
    urls = re.findall(r'''https?://[^\s'"]+''', output)
    if urls:
        return urls[-1].rstrip("/.,)")
    # RunPod endpoints are commonly printed as a bare public IP and port.
    addresses = re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}:\d+(?!\d)", output)
    return f"http://{addresses[-1]}" if addresses else None


async def ensure_model_is_ready() -> str:
    async with _wake_lock:
        live_endpoint = await endpoint()
        if live_endpoint:
            return live_endpoint
        if not TASK_FILE.exists():
            raise RuntimeError(f"SkyPilot task file is missing: {TASK_FILE}")
        def launch_task() -> None:
            import sky
            task = sky.Task.from_yaml(str(TASK_FILE))
            if MODEL_SERVER_IMAGE:
                image = MODEL_SERVER_IMAGE
                if not image.startswith("docker:"):
                    image = f"docker:{image}"
                task.set_resources_override({"image_id": image})
            sky.launch(task, cluster_name=CLUSTER)

        await asyncio.to_thread(launch_task)
        for _ in range(90):
            await asyncio.sleep(10)
            live_endpoint = await endpoint()
            if live_endpoint:
                return live_endpoint
        raise RuntimeError("Model cluster did not become reachable within 15 minutes")


def reset_idle_timer() -> None:
    global _idle_task
    if _idle_task:
        _idle_task.cancel()
    _idle_task = asyncio.create_task(stop_after_idle())


async def stop_after_idle() -> None:
    try:
        await asyncio.sleep(IDLE_MINUTES * 60)
        if _active_requests == 0:
            # RunPod has no SkyPilot stop/resume operation.  Down releases
            # the billed GPU; without a network volume the local cache is
            # intentionally discarded and re-downloaded next wake.
            await sky("down", "--yes", CLUSTER, allow_failure=True)
    except asyncio.CancelledError:
        return


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not TASK_FILE.exists():
        raise RuntimeError(f"Set SKY_TASK_FILE to an existing file (got {TASK_FILE})")
    yield


app = FastAPI(title="SkyPilot wake gateway", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str | int]:
    return {"status": "ok", "cluster": CLUSTER, "idle_minutes": IDLE_MINUTES}


@app.get("/v1/models")
async def models() -> dict:
    # Do not wake the GPU merely because OpenHands refreshes its model picker.
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    global _active_requests
    if path == "models":
        return await models()
    _active_requests += 1
    try:
        base_url = await ensure_model_is_ready()
        body = await request.body()
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() not in {"host", "content-length"}}
        async with httpx.AsyncClient(timeout=None) as client:
            upstream = await client.send(
                client.build_request(request.method, f"{base_url}/v1/{path}",
                                     content=body, headers=headers), stream=True)
            response_headers = {key: value for key, value in upstream.headers.items()
                                if key.lower() not in {"content-length", "connection"}}
            if "text/event-stream" in upstream.headers.get("content-type", ""):
                async def stream():
                    try:
                        async for chunk in upstream.aiter_raw():
                            yield chunk
                    finally:
                        await upstream.aclose()
                        reset_idle_timer()
                return StreamingResponse(stream(), status_code=upstream.status_code,
                                         headers=response_headers, media_type="text/event-stream")
            content = await upstream.aread()
            reset_idle_timer()
            return Response(content=content, status_code=upstream.status_code,
                            headers=response_headers)
    except Exception as exc:
        # Keep the response actionable while retaining the traceback in the
        # gateway terminal. This includes SkyPilot API validation failures.
        logger.exception("Model wake/proxy request failed")
        raise HTTPException(status_code=503, detail=f"Model unavailable: {exc}") from exc
    finally:
        _active_requests -= 1
