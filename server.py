"""Local FastAPI service. Image work and process ownership live in separate modules."""
import asyncio
import logging
import math
import tempfile
from contextlib import asynccontextmanager, suppress
from multiprocessing import freeze_support
from pathlib import Path
from typing import Literal
from uuid import UUID

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from exports import make_export
from jobs import JobCapacityError, JobManager, JobNotFound
from pipeline import InvalidImage, MAX_UPLOAD_BYTES, normalize_image

logger = logging.getLogger(__name__)


class UploadLimitMiddleware:
    """Bound the multipart request before Starlette can spool an unlimited body."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/process/start":
            return await self.app(scope, receive, send)
        limit = MAX_UPLOAD_BYTES + 256 * 1024  # Allow multipart framing and parameter fields.
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await Response("Invalid Content-Length", status_code=400)(scope, receive, send)
        if declared > limit:
            return await Response(' {"detail":"Images must be 20 MB or smaller."}',
                                  status_code=413, media_type="application/json")(scope, receive, send)
        # Buffer a bounded request before multipart parsing. The context closes the
        # spool on disconnect/oversize too, including requests without Content-Length.
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    return await Response('{"detail":"Images must be 20 MB or smaller."}',
                        status_code=413, media_type="application/json")(scope, receive, send)
                spool.write(chunk)
                if not message.get("more_body", False):
                    break
            spool.seek(0)
            remaining = size
            async def bounded_receive():
                nonlocal remaining
                if remaining <= 0:
                    return {"type": "http.request", "body": b"", "more_body": False}
                chunk = spool.read(65536)
                remaining -= len(chunk)
                return {"type": "http.request", "body": chunk, "more_body": remaining > 0}
            await self.app(scope, bounded_receive, send)


@asynccontextmanager
async def lifespan(application):
    jobs = JobManager()
    application.state.jobs = jobs
    api_app.state.jobs = jobs
    # Bound concurrent decodes as well as worker processes.
    api_app.state.upload_lock = asyncio.Semaphore(2)
    async def reaper():
        while True:
            await asyncio.sleep(0.5)
            try:
                await run_in_threadpool(jobs.reap)
            except Exception:
                logger.exception("Job cleanup failed; will retry")
    cleanup = asyncio.create_task(reaper())
    try:
        yield
    finally:
        cleanup.cancel()
        with suppress(asyncio.CancelledError):
            await cleanup
        await run_in_threadpool(jobs.close)


api_app = FastAPI()
app = FastAPI(title="Paint-by-Numbers App", lifespan=lifespan)
app.add_middleware(UploadLimitMiddleware)


@api_app.get("/hardware-status")
def hardware_status():
    cv2.ocl.setUseOpenCL(True)
    enabled = cv2.ocl.useOpenCL()
    return {"has_hardware_acceleration": enabled,
            "message": f"Bilateral filter OpenCL: {'available' if enabled else 'unavailable (CPU fallback)'}. Other processing uses CPU."}


@api_app.post("/process/start", status_code=202)
async def start_processing(
    file: UploadFile = File(...),
    k: int = Form(..., ge=3, le=32),
    resolution_factor: float = Form(1.0, ge=0.1, le=1.0),
    bilateral_diameter: int = Form(..., ge=5, le=15),
    sigma_color: int = Form(..., ge=25, le=150),
    area_min: int = Form(..., ge=10, le=500),
    stroke_width: int = Form(..., ge=1, le=4),
    font_scale: float = Form(..., ge=0.5, le=2.0),
    color_space: Literal["rgb", "lab"] = Form("lab"),
    # A bool Form treats an absent checkbox as False; preserve our API default.
    protect_eyes: Literal["true", "false"] = Form("true"),
):
    params = dict(k=k, resolution_factor=resolution_factor, bilateral_diameter=bilateral_diameter,
                  sigma_color=sigma_color, area_min=area_min, stroke_width=stroke_width, font_scale=font_scale)
    if not all(math.isfinite(value) for value in params.values()):
        raise HTTPException(422, "Parameters must be finite numbers.")
    params.update(color_space=color_space, protect_eyes=(protect_eyes == "true"))
    try:
        async with api_app.state.upload_lock:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Images must be 20 MB or smaller.")
            normalized = await run_in_threadpool(normalize_image, data, params)
            job_id = await run_in_threadpool(api_app.state.jobs.start, normalized, params)
        return {"job_id": job_id}
    except InvalidImage as exc:
        raise HTTPException(422, str(exc)) from exc
    except JobCapacityError as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "2"}) from exc
    finally:
        await file.close()


def snapshot(job_id):
    try:
        return api_app.state.jobs.snapshot(str(job_id))
    except JobNotFound as exc:
        raise HTTPException(404, "Job not found or expired. Process the image again.") from exc


@api_app.get("/process/status/{job_id}")
def get_status(job_id: UUID):
    return snapshot(job_id)


@api_app.post("/process/stop/{job_id}")
def stop_processing(job_id: UUID):
    try:
        return api_app.state.jobs.stop(str(job_id))
    except JobNotFound as exc:
        raise HTTPException(404, "Job not found or expired.") from exc


@api_app.get("/process/export/{job_id}/{kind}.{format_}")
def export_result(job_id: UUID, kind: Literal["template", "palette"], format_: Literal["png", "pdf"]):
    state = snapshot(job_id)
    if state["status"] != "Complete":
        raise HTTPException(409, "This job has no completed result to export.")
    data = make_export(state["result"], kind, format_)
    return Response(data, media_type="application/pdf" if format_ == "pdf" else "image/png",
                    headers={"Content-Disposition": f'attachment; filename="{kind}.{format_}"',
                             "Cache-Control": "no-store"})


app.mount("/api", api_app)
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True))


if __name__ == "__main__":
    freeze_support()
    import threading
    import webbrowser
    import uvicorn
    # Delay opening the browser briefly to allow Uvicorn to bind its socket.
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    uvicorn.run("server:app", host="127.0.0.1", port=8000)
