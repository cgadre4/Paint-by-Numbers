# Agent Guide

## Working rules

- Read this file and the latest entry in `status_log.md` before editing. Inspect relevant source and tests; older logs and `mission_briefing.md` describe historical behavior or goals, not necessarily the shipped app.
- Work toward the user's requested scope. Keep reliability work separate from algorithm experiments and visual redesigns. Do not add new cloud dependencies or external image uploads without a request for that capability.
- Inspect Git status before editing. Preserve unrelated user changes. Make small, reviewable changes and update documentation when behavior or commands change.
- Keep the app local by default and bind to loopback. Preserve the Python backend and vanilla frontend unless an architectural change is requested and justified.
- Add regression tests for real defects and meaningful behavioral changes. Do not treat syntax checks as runtime verification or claim that untested features work.
- Record completed implementation milestones with `[DONE]` in `status_log.md` and append changes to `updates.md`. Distinguish observed results from proposed improvements.
- Publish, push, deploy, or change repository visibility only within the user's authorization. Before a public push, inspect the staged files; exclude virtual environments, caches, uploaded images, test artifacts, credentials, and machine-specific paths.

## Product and architecture

Paint by #s converts a still image into a quantized preview, a numbered outline template, and a palette key. It runs at `http://127.0.0.1:8000`.

| File | Responsibility |
| --- | --- |
| `server.py` | Root FastAPI lifespan, mounted API, upload-body cap, parameter validation, downloads, static serving, launch entry point. |
| `jobs.py` | One server process owns a `JobManager`, spawned workers, a small shared progress dictionary, private temporary directories, timeout/retention, and cleanup. |
| `pipeline.py` | Image validation/normalization, scaling, optional OpenCL bilateral filtering, OpenCV K-means, region cleanup, outlines, interior label placement. |
| `exports.py` | Template/palette PNG and A4 PDF generation independent of frontend view state. |
| `static/index.html` | Controls, stable DOM IDs, upload zone, three canvases, clean print image. |
| `static/app.js` | UI state, revision ownership, sequential job polling, stale-response guards, rendering, downloads, highlighting, pan/zoom/reset. |
| `static/styles.css` | Existing theme, responsive layout, palette wrapping, print-ready and print styles. |
| `tests/test_core.py` | API, image validation, output, geometry, and real spawned-worker lifecycle tests. |
| `tests/test_browser.py` | Real Chromium/Chrome workflows, exports, delayed responses, network errors, reset/cancellation, and mobile layout. |
| `pyproject.toml`, `uv.lock`, `.python-version` | Dependencies, locked resolutions, and Python 3.12 selection. |
| `run.bat`, `test.ps1` | Windows launch and verification helpers using a local cached environment. |
| `.github/workflows/tests.yml` | Linux/Windows CI test matrix. |

The root app mounts `/api` before the catch-all static mount. Keep that order. Tests must enter the root app's lifespan; testing the API sub-app alone will not initialize jobs. Use one Uvicorn worker; the in-memory job registry is not shared across multiple server instances.

## Setup and commands

Install uv. From the project root:

```sh
uv sync --locked
uv run --locked python server.py
uv run --locked python -m pytest tests/test_core.py -q
uv run --locked python -m playwright install chromium
uv run --locked python -m pytest -q
```

For a development server without automatic browser opening:

```sh
uv run --locked python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Windows: `run.bat` and `test.ps1` default `UV_PROJECT_ENVIRONMENT` to `%LOCALAPPDATA%\PaintByNumbers\venv` when it is unset. This avoids executing a virtual environment from a synced/network drive. For direct PowerShell commands:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:LOCALAPPDATA 'PaintByNumbers\venv'
```

Use `python -m pytest` and `python -m uvicorn` through uv rather than executable console wrappers; some Windows Application Control configurations block those wrappers. Never disable system security to run tests. Prefer the installed trusted runtime and a local environment. If Playwright's bundled Node runtime is blocked, `PLAYWRIGHT_NODEJS_PATH` can point to an installed permitted Node executable. Browser tests automatically use installed Chrome at the standard Windows path, otherwise installed Playwright Chromium; `PBN_BROWSER_CHANNEL` overrides the channel.

For Linux CI, install browser/system dependencies with `uv run --locked python -m playwright install --with-deps chromium`. Node/npm is not an application dependency. If Node is available, `node --check static/app.js` is an additional syntax check.

Keep `uv.lock` synchronized with intentional dependency changes. Do not reintroduce scikit-learn/SciPy merely to run K-means: the implementation uses OpenCV, and this machine previously encountered Application Control restrictions on SciPy DLLs.

## Contracts and invariants

- Multipart start fields: `file`, `k`, `resolution_factor`, `bilateral_diameter`, `sigma_color`, `area_min`, `stroke_width`, and `font_scale`. The server enforces the ranges represented in the HTML controls. Update validation, UI defaults, and tests together.
- `POST /api/process/start` returns HTTP 202 and `{job_id}`. Progress comes from `GET /api/process/status/{job_id}`. `POST /api/process/stop/{job_id}` cancels a running job; repeated stops are safe and completed results are preserved.
- A completed snapshot has `status: "Complete"`, `progress: 100`, and `result` with `processed_image`, `template_image`, `label_map`, `palette`, `image_dimensions`, `parameters`, `label_placements`, and `warnings`.
- Palette entries are `{id, hex, rgb}`. IDs and displayed numbers start at one; grayscale label-map pixels start at zero. Never use `color.index`.
- Result images are PNG data URLs. `label_map` is an exact grayscale class-index image; never infer region identity by comparing approximate RGB colors.
- `GET /api/process/export/{job_id}/{template|palette}.{png|pdf}` generates downloads. Unknown/expired jobs return 404; unfinished/failed jobs return 409. Export sources must be unaffected by zoom, pan, selection, or highlighted canvases.
- OpenCV pixels/centers are BGR; palette values are RGB. Preserve explicit conversion at boundaries. K-means is randomized: use property-based expectations or fixed synthetic shapes instead of exact clustering snapshots.
- Label placement must keep the entire text bounding box within the corresponding region. Preserve hole boundaries. Report regions that cannot fit a number; never silently claim every region is numbered.

## Input and job lifecycle rules

- Accept still PNG, JPEG, WebP, and BMP by actual decoded format. Normalize EXIF orientation and composite transparency onto white. MIME types and file extensions alone are not validation.
- Current limits: 20 MiB file size; multipart body allowance of an additional 256 KiB; 40 million source pixels; 4 million processing pixels. Both declared and streamed oversized request bodies must be bounded before multipart parsing.
- Validate finite parameter values, scaled dimensions, and pixel count relative to K before spawning a worker. Close upload handles on every path.
- Run at most two workers. Timeout after five minutes. Keep at most eight finished jobs for up to 15 minutes. The periodic reaper works even when no client polls.
- Shared multiprocessing state contains progress only. Large results are written atomically to job-local files. Only the parent owns job-directory deletion; worker input cleanup is also attempted on normal exit.
- Join and close processes on completion/error/cancellation, remove temporary input on forced termination, handle unexpected worker exits, and clean every owned job before shutting down the multiprocessing manager.
- Keep deletion confined to the manager's own temporary root. Do not delete user uploads or source images.
- Test spawn failures, worker crashes, timeout, cancellation, expiry, retention caps, and shutdown when changing job ownership.

## Frontend state rules

- Every asynchronous upload/job operation belongs to a revision. Check ownership after requests and image decoding before committing UI state.
- Use sequential polling with bounded retries. Stop/reset/new upload must invalidate old responses. If start completes after cancellation, stop the returned job ID.
- Reset UI values and submitted state from the same defaults. New images and changed processing parameters invalidate old exports. Keep Reset accessible while processing.
- Keep the original `File` for multipart upload. Do not upload an `HTMLImageElement`. Handle decode errors and empty selections without leaving the UI stuck.
- Preserve canvas intrinsic image dimensions. Use transforms only for view controls. Print the clean template image, not a highlighted or transformed canvas.
- Maintain keyboard access, visible focus, status announcements, touch/pointer cancellation, and responsive layout when touching controls.

## Verification and release

Run relevant focused tests while working, then the full suite before publishing a reliability change. A passing backend suite is not a substitute for the browser tests. Inspect generated PDF and PNG output visually after export/layout changes. Include all 32 palette colors, a portrait/landscape template, and label containment in relevant checks.

Test artifacts live under ignored `.local/test-artifacts/`; never publish them or `.venv/`. Use synthetic images in tests. Run `git diff --check`, inspect staged content, and keep README commands accurate. Report exact tests run and any remaining limitations. CI is configured separately from local verification; do not claim remote CI passed until its result is known.

Current scope is reliability. Perceptual color clustering, superpixel/graph segmentation, new GPU backends, vector template output, physical canvas sizing, and manual region editing require separate implementation and evaluation. Do not describe them as shipped. PDF templates currently embed a raster image fitted to A4 with 10 mm margins.
