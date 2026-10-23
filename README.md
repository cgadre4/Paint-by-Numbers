# Paint by #s

A local web app that turns a still image into a limited-color preview, a numbered painting template, and a printable palette key. Image processing runs on your computer; the app does not upload your images to a cloud service.

## Run on Windows

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then double-click `run.bat`. It installs the locked dependencies, starts the local server, and opens `http://127.0.0.1:8000`. Close the server with Ctrl+C so active jobs and temporary files are cleaned up.

The launcher stores its environment in `%LOCALAPPDATA%\PaintByNumbers\venv`, keeping executable dependencies off synced/network project drives. Set `UV_PROJECT_ENVIRONMENT` before launching to choose another location.

## Run from a terminal

Python 3.12 is selected by `.python-version` and managed by uv:

```sh
uv sync --locked
uv run --locked python server.py
```

On Windows, when working from a synced drive, set this first in PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:LOCALAPPDATA 'PaintByNumbers\venv'
```

For development without opening a browser:

```sh
uv run --locked python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Use one server worker. Jobs are owned by that process; multi-worker deployment is not supported.

## Workflow

1. Browse or drop a PNG, JPEG, WebP, or BMP still image.
2. Choose the number of paints, resolution, smoothing, region size, and label settings. **Perceptual (Lab)** color matching and **Protect detected human eyes** are enabled by default; **Classic (RGB)** remains available.
3. Select **Process Image** (or Ctrl/Cmd+Enter). **Stop** cancels processing. Reset and new uploads invalidate earlier results.
4. Click a palette number to highlight its regions; click again to clear it. Wheel/slider zoom and drag-to-pan affect all three canvases.
5. Download template and palette as PNG or PDF. **Print Ready View** isolates the template; browser printing uses the clean template without highlighting or viewport transforms.

For portraits, start with 16-32 colors. Eye protection runs locally using bundled frontal-face/eye detectors. It uses gentler smoothing in detected eyes, preserves their palette regions during cleanup, draws thin eye outlines, and keeps numbers outside the eye boxes. Tiny eye details may need painting from the color preview without numbers. Detection can miss small, angled, or obscured faces and does not target animal eyes; review the output and detection notice.

Changing processing settings invalidates the current result. Processing is explicit; changing a slider does not automatically launch a job.

## Output and limits

- Maximum upload: **20 MiB**; source: **40 million pixels**; processed image: **4 million pixels**. Lower Resolution Factor for large source images.
- Animated and unsupported images receive a clear validation error. EXIF orientation is normalized and transparency is composited onto white.
- Two jobs can run concurrently. Processing times out after five minutes.
- Finished jobs expire after 15 minutes; at most eight finished jobs are retained. Download results before they expire. Temporary data is removed on expiry and normal shutdown.
- Template PDF fits a raster template on one A4 page (landscape for wide images), with 10 mm margins. It is not a vector PDF. Palette PDF includes all colors, numbers, hex values, and RGB values.
- Except for protected eye regions, small regions merge only into edge-adjacent regions, using color similarity and shared boundary length. Small unnumberable regions receive additional conservative cleanup; stronger contrasts and large thin features are preserved.
- Numbers are placed only when their complete bounding box fits inside a region. If some regions cannot fit a number, the app reports how many. Physical canvas sizing, advanced simplification, and manual region editing are future work.
- Color matching uses OpenCV K-means in float CIELab by default, or RGB when selected. K-means is randomized. Exact palette order and pixel output can vary between runs.
- OpenCL is attempted for bilateral filtering with CPU fallback. K-means and region work currently use the CPU.

## Tests

```sh
uv sync --locked
uv run --locked python -m playwright install chromium
uv run --locked python -m pytest -q
```

On Linux, install Chromium system dependencies with `python -m playwright install --with-deps chromium` instead. On Windows, the browser tests use installed Chrome when available; `PBN_BROWSER_CHANNEL` can select another installed Playwright channel.

`./test.ps1` runs the suite on Windows using the local cached environment. Backend-only checks:

```sh
uv run --locked python -m pytest tests/test_core.py -q
```

Tests cover processing and data contracts; image/parameter limits; decoding, orientation, and transparency; label containment; PNG/PDF output; crashes, cancellation, expiry, retention, and shutdown; and real browser workflows including delayed responses and network failure. Generated exports and screenshots are ignored under `.local/test-artifacts/`.

## Image benchmark

```sh
uv run --locked python -m benchmarks.run
uv run --locked python -m benchmarks.run --colors 32 --output .local/benchmarks-32
uv run --locked python -m benchmarks.photos --download
uv run --locked python -m benchmarks.faces
```

The benchmark compares the former cleanup with the current algorithm on six seeded synthetic images, with identical K-means input for both methods. It records region/label counts, color distortion, stage timings, and comparison PNGs under ignored `.local/benchmarks/`. Add your own photographs with `--images path/to/photo.jpg`; they remain local. See [benchmark methodology and options](benchmarks/README.md). The photo benchmark optionally downloads six credited, checksum-pinned public-domain/CC0 photographs and compares the current cleanup/renderer against the previous shipped version at 8, 16, and 32 colors. Downloads and generated reports stay under ignored `.local/`. See [real-photo results and tradeoffs](benchmarks/PHOTO_RESULTS.md); this small corpus does not establish quality for every photograph.

The historical cleanup benchmarks retain RGB quantization for comparable results. The facial benchmark compares RGB/Lab with and without eye protection against a common source-image reference, across two seeds and three palette sizes. See [facial-detail results and limitations](benchmarks/FACIAL_RESULTS.md).

## API options

Alongside the existing numeric fields, `POST /api/process/start` accepts multipart `color_space` (`lab` or `rgb`, default `lab`) and `protect_eyes` (`true` or `false`, default `true`). Results include these settings and `eye_protection` with `enabled`, `detected_faces`, and `boxes` as `[x, y, width, height]` in processed-image pixels. Detection metadata describes locations, not identities.

## Code map

- `server.py`: FastAPI routes, bounded multipart input, lifecycle, static serving.
- `jobs.py`: spawned workers, progress snapshots, timeouts, retention, and cleanup.
- `pipeline.py`: input normalization, OpenCV quantization, and numbered template rendering.
- `facial.py`: bounded local human-eye detection and gentle eye filtering.
- `regions.py`: edge-adjacent, color-aware region merging and shared label-fit geometry.
- `benchmarks/`: synthetic and downloaded-photo benchmarks, pinned source credits, paired measurements, and visual comparisons.
- `exports.py`: clean template/palette downloads and A4 PDF layout.
- `static/`: vanilla HTML/CSS/JavaScript interface, with no npm build step.
- `tests/`: pytest API/lifecycle tests and Playwright browser tests.
- `AGENTS.md`: contributor/agent working rules.

`mission_briefing.md` describes the original goals. Earlier sections of `status_log.md` and `updates.md` are historical and may describe behavior that has since changed. This README and the current source describe the shipped implementation.
