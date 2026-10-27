"""Local FastAPI service. Image work and process ownership live in separate modules."""
import asyncio
import logging
import math
import json
import tempfile
from contextlib import asynccontextmanager, suppress
from multiprocessing import freeze_support
from pathlib import Path
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, StrictInt

from jobs import JobCapacityError, JobManager, JobNotFound
from pipeline import MAX_UPLOAD_BYTES
from settings import DEFAULTS, RANGES, PRESETS
from editing import rectangles
from project_io import MAX_BUNDLE
from projects import ProjectManager, RevisionConflict

logger = logging.getLogger(__name__)


class LocalRequestMiddleware:
    """Reject non-loopback hosts and cross-origin mutations before reading bodies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin1")
        try:
            parsed = urlsplit("http://" + host)
            valid = parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.username is None
            origin = headers.get(b"origin")
            if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                valid = valid and headers.get(b"sec-fetch-site", b"") != b"cross-site"
                if origin:
                    valid = valid and origin.decode("latin1") == scope.get("scheme", "http") + "://" + host
        except ValueError:
            valid = False
        if not valid:
            return await Response('{"detail":"Only same-origin local requests are allowed."}', status_code=403,
                                  media_type="application/json")(scope, receive, send)
        await self.app(scope, receive, send)


class UploadLimitMiddleware:
    """Bound admission, duration and bytes before multipart parsing."""
    def __init__(self, app, max_uploads=2, upload_timeout=30):
        self.app, self.max_uploads, self.upload_timeout = app, max_uploads, upload_timeout
        self.active = 0

    async def __call__(self, scope, receive, send):
        path = scope.get('path','')
        limited = path == '/api/process/start' or path == '/api/projects/open' or (path.startswith('/api/projects') and scope.get('method') == 'POST')
        if scope["type"] != "http" or not limited:
            return await self.app(scope, receive, send)
        async def reject(status, detail):
            await Response('{"detail":"' + detail + '"}', status_code=status,
                           media_type="application/json")(scope, receive, send)
        limit = MAX_BUNDLE + 256 * 1024 if path == '/api/projects/open' else (
            MAX_UPLOAD_BYTES + 256 * 1024 if path == '/api/process/start' else 64 * 1024)
        try:
            declared = int(dict(scope.get("headers", [])).get(b"content-length", b"0"))
        except ValueError:
            return await reject(400, "Invalid Content-Length")
        if declared < 0:
            return await reject(400, "Invalid Content-Length")
        if declared > limit:
            return await reject(413, "Request exceeds the upload limit.")
        if self.active >= self.max_uploads:
            return await reject(429, "Upload slots are busy. Try again shortly.")
        self.active += 1  # No await between admission and increment.
        try:
            with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
                size = 0
                try:
                    async with asyncio.timeout(self.upload_timeout):
                        while True:
                            message = await receive()
                            if message["type"] == "http.disconnect":
                                return
                            chunk = message.get("body", b"")
                            size += len(chunk)
                            if size > limit:
                                return await reject(413, "Request exceeds the upload limit.")
                            await run_in_threadpool(spool.write, chunk)
                            if not message.get("more_body", False):
                                break
                except TimeoutError:
                    return await reject(408, "Upload timed out. Try again.")
                await run_in_threadpool(spool.seek, 0)
                remaining = size
                replayed = False
                async def bounded_receive():
                    nonlocal remaining, replayed
                    if remaining <= 0:
                        if not replayed:
                            replayed = True
                            return {"type": "http.request", "body": b"", "more_body": False}
                        return await receive()
                    replayed = True
                    chunk = await run_in_threadpool(spool.read, 65536)
                    remaining -= len(chunk)
                    return {"type": "http.request", "body": chunk, "more_body": remaining > 0}
                await self.app(scope, bounded_receive, send)
        finally:
            self.active -= 1


@asynccontextmanager
async def lifespan(application):
    jobs = JobManager()
    application.state.jobs = jobs
    api_app.state.jobs = jobs
    try:
        projects = ProjectManager(jobs)
    except Exception:
        await run_in_threadpool(jobs.close)
        raise
    api_app.state.projects = application.state.projects = projects
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
        try:
            await run_in_threadpool(projects.close)
        finally:
            await run_in_threadpool(jobs.close)


api_app = FastAPI()
app = FastAPI(title="Paint-by-Numbers App", lifespan=lifespan)
app.add_middleware(UploadLimitMiddleware)
app.add_middleware(LocalRequestMiddleware)


@api_app.get("/settings")
def settings():
    return {"schema_version": 2, "defaults": DEFAULTS, "ranges": RANGES, "presets": PRESETS}


@api_app.get("/hardware-status")
def hardware_status():
    return {"has_hardware_acceleration": False,
            "message": "Portable CPU processing, up to two threads per job."}


@api_app.post("/process/start", status_code=202)
async def start_processing(
    request: Request,
    file: UploadFile = File(...),
    k: int = Form(..., ge=RANGES["k"][0], le=RANGES["k"][1]),
    resolution_factor: float = Form(1.0, ge=RANGES["resolution_factor"][0], le=RANGES["resolution_factor"][1]),
    bilateral_diameter: int = Form(..., ge=RANGES["bilateral_diameter"][0], le=RANGES["bilateral_diameter"][1]),
    sigma_color: int = Form(..., ge=RANGES["sigma_color"][0], le=RANGES["sigma_color"][1]),
    area_min: int = Form(..., ge=RANGES["area_min"][0], le=RANGES["area_min"][1]),
    stroke_width: int = Form(..., ge=RANGES["stroke_width"][0], le=RANGES["stroke_width"][1]),
    font_scale: float = Form(..., ge=RANGES["font_scale"][0], le=RANGES["font_scale"][1]),
    color_space: Literal["rgb", "lab"] = Form("lab"),
    # A bool Form treats an absent checkbox as False; preserve our API default.
    protect_eyes: Literal["true", "false"] = Form("true"),
    canvas_width_cm: float = Form(0, ge=RANGES["canvas_width_cm"][0], le=RANGES["canvas_width_cm"][1]),
    sizing_mode: Literal["physical", "pixels"] = Form("physical"),
    paper_size: Literal["a4", "letter"] = Form("a4"),
    request_id: UUID | None = Form(None),
    manual_protection: str = Form('[]'),
    eye_detail: Literal['fine','balanced'] = Form('fine'),
):
    params = dict(k=k, resolution_factor=resolution_factor, bilateral_diameter=bilateral_diameter,
                  sigma_color=sigma_color, area_min=area_min, stroke_width=stroke_width, font_scale=font_scale)
    if not all(math.isfinite(value) for value in params.values()):
        raise HTTPException(422, "Parameters must be finite numbers.")
    if not math.isfinite(canvas_width_cm) or 0 < canvas_width_cm < 5:
        raise HTTPException(422, "Canvas width must be 0 (fit selected paper) or 5-200 cm.")
    form = await request.form()
    allowed = set(params) | {"file", "color_space", "protect_eyes", "canvas_width_cm", "request_id", "sizing_mode", "paper_size", "manual_protection", "eye_detail"}
    if set(form) - allowed:
        raise HTTPException(422, "Unknown processing option.")
    params.update(color_space=color_space, protect_eyes=(protect_eyes == "true"), canvas_width_cm=canvas_width_cm, sizing_mode=sizing_mode, paper_size=paper_size, eye_detail=eye_detail)
    try:
        manual = rectangles(json.loads(manual_protection))
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if manual:
        params['manual_protection'] = manual
    try:
        async with api_app.state.upload_lock:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Images must be 20 MB or smaller.")
            job_id = await run_in_threadpool(api_app.state.jobs.start, data, params, str(request_id) if request_id else None)
        return {"job_id": job_id}
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


@api_app.post("/process/cancel/{request_id}")
def cancel_request(request_id: UUID):
    try:
        return api_app.state.jobs.cancel_request(str(request_id))
    except JobCapacityError as exc:
        raise HTTPException(429, str(exc)) from exc


@api_app.get("/process/export/{job_id}/{kind}.{format_}")
def export_result(job_id: UUID, kind: Literal["template", "palette", "tiles"], format_: Literal["png", "pdf"]):
    try:
        data = api_app.state.jobs.export(str(job_id), kind, format_)
    except JobNotFound as exc:
        raise HTTPException(404, "Job not found or expired. Process the image again.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(data, media_type="application/pdf" if format_ == "pdf" else "image/png",
                    headers={"Content-Disposition": f'attachment; filename="{kind}.{format_}"',
                             "Cache-Control": "no-store"})


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: UUID
    request_id: UUID


class ProjectCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: StrictInt
    request_id: UUID
    command: dict


def project_call(function, *args):
    try:
        return function(*args)
    except JobNotFound as exc:
        raise HTTPException(404, 'Project or revision not found. Reopen your saved project.') from exc
    except RevisionConflict as exc:
        raise HTTPException(409,str(exc)) from exc
    except JobCapacityError as exc:
        raise HTTPException(429,str(exc),headers={'Retry-After':'2'}) from exc
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc


@api_app.post('/projects',status_code=202)
def create_project(body: ProjectCreate):
    return project_call(api_app.state.projects.from_job,str(body.job_id),str(body.request_id))


@api_app.get('/projects')
def list_project_sessions():
    return api_app.state.projects.sessions()


@api_app.post('/projects/open',status_code=202)
async def open_project(file: UploadFile = File(...), request_id: UUID = Form(...)):
    try:
        data = await file.read(MAX_BUNDLE+1)
        if len(data) > MAX_BUNDLE:
            raise HTTPException(413,'Project files must be 128 MiB or smaller.')
        return await run_in_threadpool(project_call,api_app.state.projects.create,data,str(request_id))
    finally:
        await file.close()


@api_app.get('/projects/{project_id}')
def get_project(project_id: UUID):
    return project_call(api_app.state.projects.snapshot,str(project_id))


@api_app.post('/projects/{project_id}/commands',status_code=202)
def project_command(project_id: UUID,body: ProjectCommand):
    return project_call(api_app.state.projects.command,str(project_id),body.expected_revision,str(body.request_id),body.command)


@api_app.post('/projects/{project_id}/cancel')
def cancel_project_operation(project_id: UUID):
    return project_call(api_app.state.projects.cancel,str(project_id))


@api_app.delete('/projects/{project_id}')
def close_project(project_id: UUID):
    project_call(api_app.state.projects.close_project,str(project_id))
    return {'status':'Closed'}


@api_app.post('/projects/{project_id}/close')
def close_project_beacon(project_id: UUID):
    return close_project(project_id)


@api_app.get('/projects/{project_id}/revisions/{revision}/assets/{name}.png')
def project_asset(project_id: UUID,revision: int,name: Literal['source','preview','template','labels','missing']):
    def read():
        with api_app.state.projects.lease(str(project_id),revision) as path:
            return (path/(name+'.png')).read_bytes()
    return Response(project_call(read),media_type='image/png',headers={'Cache-Control':'no-store'})


@api_app.get('/projects/{project_id}/revisions/{revision}/export/{kind}.{format_}')
def project_export(project_id: UUID,revision: int,kind: Literal['template','palette','tiles','project'],
                   format_: Literal['png','pdf','pbn'],include_original: bool = False):
    def read():
        from exports import make_export
        with api_app.state.projects.lease(str(project_id),revision) as path:
            if kind == 'project' and format_ == 'pbn':
                return (path/('document.pbn' if include_original else 'portable.pbn')).read_bytes()
            if kind == 'project' or format_ == 'pbn' or kind == 'tiles' and format_ != 'pdf':
                raise ValueError('Unsupported export.')
            target = path/f'{kind}.{format_}'
            if target.exists():
                return target.read_bytes()
            result = json.loads((path/'result.json').read_text(encoding='utf-8'))
            data = make_export(result,kind,format_)
            api_app.state.projects.cache_export(str(project_id),target,data)
            return data
    data = project_call(read)
    media = {'png':'image/png','pdf':'application/pdf','pbn':'application/octet-stream'}[format_]
    return Response(data,media_type=media,headers={'Content-Disposition':f'attachment; filename="{kind}.{format_}"','Cache-Control':'no-store'})


@api_app.get('/projects/{project_id}/revisions/{revision}/regions')
def project_regions(project_id: UUID,revision: int,region: int | None = None,x: int | None = None,y: int | None = None,
                    offset: int = 0,missing: bool = False):
    def read():
        import numpy as np
        from project_io import png_bytes
        from facial import eye_mask
        from base64 import b64encode
        with api_app.state.projects.lease(str(project_id),revision) as path:
            stats = np.load(path/'stats.npy',allow_pickle=False)
            colors = np.load(path/'colors.npy',allow_pickle=False)
            numbered = np.load(path/'numbered.npy',allow_pickle=False)
            if region is None and x is None and y is None:
                indices = np.flatnonzero(~numbered) if missing else np.arange(len(stats))
                if not 0 <= offset <= len(indices):
                    raise ValueError('Invalid region list offset.')
                return {'total':len(indices),'items':[{'region':int(i),'color':int(colors[i])+1,'area':int(stats[i,4]),'numbered':bool(numbered[i])}
                                                     for i in indices[offset:offset+100]]}
            regions = np.load(path/'regions.npy',allow_pickle=False)
            if region is None:
                if x is None or y is None or not 0 <= x < regions.shape[1] or not 0 <= y < regions.shape[0]:
                    raise ValueError('Select a point inside the image.')
                selected = int(regions[y,x])
            else:
                if not 0 <= region < len(stats):
                    raise ValueError('Invalid region selection.')
                selected = region
            left,top,w,h,area = map(int,stats[selected])
            selected_mask = regions[top:top+h,left:left+w] == selected
            neighborhood = set()
            ys0,xs0 = np.nonzero(selected_mask)
            # Read a one-pixel halo, using four-connected contact only.
            for dx,dy in ((-1,0),(1,0),(0,-1),(0,1)):
                xs,ys = xs0+left+dx,ys0+top+dy
                valid = (xs>=0)&(ys>=0)&(xs<regions.shape[1])&(ys<regions.shape[0])
                neighborhood.update(np.unique(regions[ys[valid],xs[valid]]).tolist())
            neighborhood.discard(selected)
            metadata = json.loads((path/'metadata.json').read_text(encoding='utf-8'))
            mask = eye_mask(regions.shape,metadata['eye_protection']['boxes'])
            overlay = np.zeros((h,w,4),np.uint8)
            overlay[selected_mask] = [255,170,60,130]
            return {'region':selected,'color':int(colors[selected])+1,'area':area,'box':[left,top,w,h],
                    'numbered':bool(numbered[selected]),'protected':bool(mask[top:top+h,left:left+w][selected_mask].any()),
                    'neighbors':[{'region':int(i),'color':int(colors[i])+1,'area':int(stats[i,4])} for i in sorted(neighborhood)],
                    'mask':'data:image/png;base64,'+b64encode(png_bytes(overlay)).decode()}
    return project_call(read)


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
