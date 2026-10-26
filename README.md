# Paint by #s

A local web app that turns a still image into a limited-color preview, a numbered painting template, and a printable palette key. Image processing runs on your computer; the app does not upload your images to a cloud service.

## Run on Windows

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then double-click `run.bat`. It installs the locked dependencies, starts the local server, and opens `http://127.0.0.1:8000`. Close the server with Ctrl+C so active jobs and temporary files are cleaned up.

The launcher stores its environment in `%LOCALAPPDATA%\PaintByNumbers\venv-1.3`, keeping executable dependencies off synced/network project drives. Set `UV_PROJECT_ENVIRONMENT` before launching to choose another location.

## Run from a terminal

Python 3.12 is selected by `.python-version` and managed by uv:

```sh
uv sync --locked
uv run --locked python server.py
```

On Windows, when working from a synced drive, set this first in PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:LOCALAPPDATA 'PaintByNumbers\venv-1.3'
```

For development without opening a browser:

```sh
uv run --locked python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Use one server worker. Jobs are owned by that process; multi-worker deployment is not supported.

## Workflow

1. Browse or drop a PNG, JPEG, WebP, or BMP still image.
2. Choose **Simple** (8 paints, stronger simplification) or **Detailed** (24 paints, gentler smoothing), then optionally set canvas width in centimetres or inches. Height follows the image proportions. Open **Advanced settings** to adjust individual controls. **Perceptual (Lab)** color matching and **Protect detected human eyes** are enabled by default; **Classic (RGB)** remains available.
3. Select **Process Image** (or Ctrl/Cmd+Enter). **Stop** cancels processing. Reset and new uploads invalidate earlier results.
4. Click a palette number to highlight its regions; click again to clear it. Wheel/slider zoom and drag-to-pan affect all three canvases. Arrow buttons or focused-canvas arrow keys pan; +/- zoom and Home resets. Use **Show** to switch views and enable the protected-eye or unnumbered-region overlays for review.
5. Download template and palette as PNG or PDF. **Print Ready View** isolates the template; browser printing uses the clean template without highlighting or viewport transforms.

For portraits, start with 16-32 colors. Eye protection runs locally using bundled frontal-face/eye detectors. It uses gentler smoothing in detected eyes, preserves their palette regions during cleanup, draws thin eye outlines, and keeps numbers outside the eye boxes. Tiny eye details may need painting from the color preview without numbers. Detection can miss small, angled, or obscured faces and does not target animal eyes; review the output and detection notice.

Changing processing settings invalidates the current result. Processing is explicit; changing a slider does not automatically launch a job.

## Output and limits

- Maximum upload: **20 MiB**; source: **40 million pixels**; processed image: **4 million pixels**. Lower Resolution Factor for large source images.
- Animated and unsupported images receive a clear validation error. EXIF orientation is normalized, embedded ICC profiles are converted to sRGB, and transparency is composited onto white. Invalid profiles are rejected with an actionable message. Decoding runs in the owned worker; its errors appear in job status.
- Two bounded uploads and two jobs can run concurrently. Uploads have a 30-second body deadline. Processing times out after five minutes.
- Finished jobs expire after 15 minutes; at most eight finished jobs are retained. Download results before they expire. Temporary data is removed on expiry and normal shutdown.
- With canvas width blank, template PDF fits a raster template on the selected A4 or US Letter page (landscape for wide images), with 10 mm margins. A width of 5-200 cm produces a PDF page at that exact canvas width, with proportional height (maximum 200 cm) and no added margins. Print custom PDFs at **100% / actual size**; printer Fit-to-page changes their dimensions. Use **Tiled PDF** for a larger canvas on ordinary A4/Letter sheets: orientation is selected to minimize sheet count, with 10 mm margins, 5 mm overlap, matching alignment crosses, trim marks and a 50 mm calibration ruler. Print at 100%, measure the ruler, trim margins and align overlapping strips. Exports are capped at 100 sheets. PNG exports retain processed pixel dimensions; browser printing fits the selected paper and image orientation, while exact custom dimensions require the PDF. It is not a vector PDF. Palette PDF includes all colors, numbers, hex values, and RGB values.
- Except for protected eye regions, small regions merge only into edge-adjacent regions, using color similarity and shared boundary length. Small unnumberable regions receive additional conservative cleanup; stronger contrasts and large thin features are preserved.
- Numbers are placed only when their complete bounding box fits inside a region. If some regions cannot fit a number, the app reports how many. **Physical sizing** (default) uses canvas dimensions for number-fit cleanup and rendering: number ink is at least 2 mm tall, outlines target 0.3 mm (at least one pixel), and protected eyes retain one-pixel contours. The explicit noise-removal area cutoff remains in processing pixels; additional fit merges remain bounded and preserve strong boundaries. **Pixels** mode retains the advanced font/outline controls and previous geometry. Changing canvas size reruns cleanup/rendering while reusing valid retained color analysis. Enlargement cannot recover lost detail. The app reports effective pixel density, outline width and minimum number height; numbers under 2 mm or custom output below 150 ppi trigger review warnings. These are advisory thresholds, not a guarantee of paintability. Vector export, advanced simplification and manual region editing remain future work. Very small canvases may have more unnumbered regions because readable numbers take space; review warnings and the color reference.
- Color matching uses OpenCV K-means in float CIELab by default, or RGB when selected. K-means is randomized. Exact palette order and pixel output can vary between runs.
- The application uses portable CPU processing, with at most two OpenCV threads per worker. An optional OpenCL helper remains tested for experiments but is not enabled by the application. Immutable color analysis is reused within retained jobs when only cleanup, label, outline or canvas settings change. Exports are cached for the job lifetime. Color caches are shape/type/range validated, read without pickle, bounded in expanded size and rebuilt after corruption; publication replaces the owned cache atomically.

## Tests

```sh
uv sync --locked
uv run --locked python -m playwright install chromium
uv run --locked python -m pytest -q
```

On Linux, install Chromium system dependencies with `python -m playwright install --with-deps chromium` instead. On Windows, the browser tests use installed Chrome when available; `PBN_BROWSER_CHANNEL` can select another installed Playwright channel.

`./test.ps1` uses an installed Node executable for Playwright when available and runs the suite on Windows using the local cached environment. Backend-only checks:

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

The browser-only API is now schema version 2. `GET /api/settings` supplies presets, defaults and ranges. Start accepts an optional UUID `request_id`; repeating a retained request ID returns that job, and `POST /api/process/cancel/{request_id}` safely cancels even before start arrives. Cancellation tombstones are bounded and expire after 15 minutes. Invalid numeric fields return 422; valid uploads return 202 before image decoding, and invalid images subsequently produce an Error snapshot with `error_code`. Unknown options are rejected. Hosts must be loopback, and browser mutations must come from the same origin. Alongside the existing numeric fields, `POST /api/process/start` accepts multipart `color_space` (`lab` or `rgb`, default `lab`) and `protect_eyes` (`true` or `false`, default `true`). Results include these settings and `eye_protection` with `enabled`, `detected_faces`, and `boxes` as `[x, y, width, height]` in processed-image pixels. Detection metadata describes locations, not identities. Optional `canvas_width_cm` is 0 for A4 fit or 5-200 for custom size. Results add `canvas`, `missing_regions` (count, pixel coverage and exact mask), stage timings and CPU/cache diagnostics. Color analysis and export caches remain private and expire with their jobs.

## Code map

- `server.py`: FastAPI routes, bounded multipart input, lifecycle, static serving.
- `jobs.py`: spawned workers, progress snapshots, timeouts, retention, and cleanup.
- `pipeline.py`: input normalization, OpenCV quantization, and numbered template rendering.
- `facial.py`: bounded local human-eye detection and gentle eye filtering.
- `regions.py`: edge-adjacent, color-aware region merging and shared label-fit geometry.
- `benchmarks/`: synthetic and downloaded-photo benchmarks, pinned source credits, paired measurements, and visual comparisons.
- `exports.py`, `canvas_size.py`: clean template/palette downloads, A4 and physical canvas geometry.
- `settings.py`: shared presets, defaults and numeric ranges.
- `static/`: vanilla HTML/CSS/JavaScript interface, with no npm build step.
- `tests/`: pytest API/lifecycle tests and Playwright browser tests.
- `AGENTS.md`: contributor/agent working rules.

`mission_briefing.md` describes the original goals. Earlier sections of `status_log.md` and `updates.md` are historical and may describe behavior that has since changed. This README and the current source describe the shipped implementation.

## Troubleshooting and release validation

Keep Windows Application Control enabled. A corrupted or blocked old environment is not repaired by copying or renaming executables. Version 1.2 launchers use a fresh local `venv-1.3` directory; an explicit `UV_PROJECT_ENVIRONMENT` override is still respected. If startup fails, inspect the reported error and use an installed, permitted Python/Node runtime. Never weaken operating-system security. The prior environment is left untouched.

CI tests Python 3.12 and 3.13 on Linux and Windows, runs scoped Ruff checks and audits the locked runtime/development dependencies. macOS and non-Chromium browsers are not yet verified release targets. No frontend build step or cloud service is required.

Job latency and sampled process-tree memory can be measured with `uv run --locked python -m benchmarks.jobs` (cached photos required). These are diagnostic measurements, not CI timing thresholds. The sampled-clustering experiment is benchmark-only: `uv run --locked python -m benchmarks.cpu`. It compares a 65,536-pixel sample with full Lab K-means using identical source/filter/protection settings and three timing repeats. See [1.2 release evaluation](benchmarks/RELEASE_1_2_RESULTS.md) for the selection decision and limitations. Existing historical benchmark references remain frozen.

## Release 1.3 print-quality evaluation

```sh
uv run --locked python -m benchmarks.painting --download
uv run --locked python -m benchmarks.painting --size 768 --seed 42 --widths 5 40 --output .local/painting-small
```

This pairs the prior pixel geometry with physical geometry on identical Lab clusters, across both presets and canvas sizes. The four additional credited portraits are pinned in `benchmarks/portrait_sources.json`, with evaluation-only eye/nose/mouth annotations. Photos and rendered reports stay in ignored local storage; CI uses synthetic fixtures and never downloads photos. See `benchmarks/RELEASE_1_3_RESULTS.md` for measured results and remaining limitations.
