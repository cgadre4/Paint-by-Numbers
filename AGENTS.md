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
| `pipeline.py` | Image validation/normalization, scaling, optional OpenCL bilateral filtering, OpenCV K-means, outlines, interior label placement. |
| `facial.py` | Bundled, local frontal-face/eye detection, processing-coordinate masks, and gentle eye filtering. |
| `tests/test_facial.py` | RGB/Lab conversion, protected-region invariants, thin eye outlines, detector bounds, worker results, and offline facial benchmark checks. |
| `regions.py` | Four-connected region graph, color/boundary-aware merging, and shared label-fit geometry. |
| `benchmarks/` | Seeded synthetic/custom-image comparisons; legacy cleanup reference is benchmark-only. |
| `tests/test_regions.py` | Region adjacency, detail preservation, label geometry, and benchmark regressions. |
| `exports.py` | Template/palette PNG and A4 PDF generation independent of frontend view state. |
| `static/index.html` | Controls, stable DOM IDs, upload zone, three canvases, clean print image. |
| `static/app.js` | UI state, revision ownership, sequential job polling, stale-response guards, rendering, downloads, highlighting, pan/zoom/reset. |
| `editing.py`, `projects.py`, `project_io.py` | Pure region/number/protection operations, bounded project sessions and immutable revisions, versioned portable bundles. |
| `static/editor.js`, `tests/test_editor_browser.py` | Finishing tools, protection gestures, history, project downloads/reopen and real-browser editing regressions. |
| `tests/test_editing.py`, `tests/test_project_lifecycle.py` | Bundle validation, edit invariants, exact undo, independent project lifetime, quotas, cancellation and storage faults. |
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

Windows: `run.bat` and `test.ps1` default `UV_PROJECT_ENVIRONMENT` to `%LOCALAPPDATA%\PaintByNumbers\venv-1.4` when it is unset. This avoids executing a virtual environment from a synced/network drive. For direct PowerShell commands:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:LOCALAPPDATA 'PaintByNumbers\venv-1.4'
```

Use `python -m pytest` and `python -m uvicorn` through uv rather than executable console wrappers; some Windows Application Control configurations block those wrappers. Never disable system security to run tests. Prefer the installed trusted runtime and a local environment. If Playwright's bundled Node runtime is blocked, `PLAYWRIGHT_NODEJS_PATH` can point to an installed permitted Node executable. Browser tests automatically use installed Chrome at the standard Windows path, otherwise installed Playwright Chromium; `PBN_BROWSER_CHANNEL` overrides the channel.

For Linux CI, install browser/system dependencies with `uv run --locked python -m playwright install --with-deps chromium`. Node/npm is not an application dependency. If Node is available, `node --check static/app.js` is an additional syntax check.

Keep `uv.lock` synchronized with intentional dependency changes. Do not reintroduce scikit-learn/SciPy merely to run K-means: the implementation uses OpenCV, and this machine previously encountered Application Control restrictions on SciPy DLLs.

## Contracts and invariants

- Multipart start fields: `file`, `k`, `resolution_factor`, `bilateral_diameter`, `sigma_color`, `area_min`, `stroke_width`, `font_scale`, `color_space`, `protect_eyes`, `canvas_width_cm`, and optional UUID `request_id`. Color space is `lab` (default) or `rgb`; eye protection accepts literal multipart strings `true` (default) or `false`, converted to a boolean in result parameters. Preserve omitted-field defaults and reject unknown options. Shared defaults, numeric ranges and Simple/Detailed presets live in `settings.py` and are served by `/api/settings`. Update validation, defaults and tests together.
- `POST /api/process/start` returns HTTP 202 and `{job_id}` before image decoding (schema version 2). Invalid images fail in job status with `error_code`; invalid parameters still return 422. Client-generated request IDs provide idempotency and `/api/process/cancel/{request_id}` cancels pending starts using bounded expiring tombstones. Progress comes from `GET /api/process/status/{job_id}`. `POST /api/process/stop/{job_id}` cancels a running job; repeated stops are safe and completed results are preserved.
- A completed snapshot has `status: "Complete"`, `progress: 100`, and `result` with `processed_image`, `template_image`, `label_map`, `palette`, `image_dimensions`, `parameters`, `label_placements`, `eye_protection`, `warnings`, `canvas`, `missing_regions`, `diagnostics`, and `schema_version`. Eye metadata contains `enabled`, `detected_faces`, and `[x, y, width, height]` boxes in processed-image pixels.
- Palette entries are `{id, hex, rgb}`. IDs and displayed numbers start at one; grayscale label-map pixels start at zero. Never use `color.index`.
- Result images are PNG data URLs. `label_map` is an exact grayscale class-index image; never infer region identity by comparing approximate RGB colors.
- `GET /api/process/export/{job_id}/{template|palette}.{png|pdf}` generates downloads. Unknown/expired jobs return 404; unfinished/failed jobs return 409. Export sources must be unaffected by zoom, pan, selection, or highlighted canvases.
- OpenCV pixels/centers are BGR; palette values are RGB. Preserve explicit conversion at boundaries. K-means is randomized: use property-based expectations or fixed synthetic shapes instead of exact clustering snapshots.
- Lab clustering must use float CIELab after normalizing BGR to 0-1, not OpenCV's rescaled 8-bit Lab representation. Convert centers back to BGR before rendering and palette construction. Keep the historical benchmark helper default RGB; the application explicitly selects Lab.
- Eye protection uses bundled local OpenCV cascades, with detector input bounded to a 960-pixel side. It is best-effort for frontal human faces, not identity recognition. Never feed benchmark annotations into production detection. Preserve every protected pixel's quantized palette ID through cleanup, including same-color coalescing; use one-pixel contours inside eye masks and exclude full number boxes from them. Report missed detection and unnumbered regions. Do not erase eyes to improve label counts.
- Cleanup and numbering use four-connected regions; diagonal contact alone must not combine paintable regions. Merge only across a shared edge, preserve palette IDs, and coalesce newly connected regions of the same color.
- Keep the extra label-fit merging conservative: honor the area bound and color-distance guard in `regions.py`. Preserve existing strong boundaries to third regions during extra fit-based merging. Share tightly cropped antialiased number bitmaps and padded geometry with the renderer; do not change the minimum font scale to hide missing labels. Larger or contrasting unnumberable details may remain and must still generate warnings.
- Label placement must keep the entire text bounding box within the corresponding region. Preserve hole boundaries. Report regions that cannot fit a number; never silently claim every region is numbered.

## Input and job lifecycle rules

- Accept still PNG, JPEG, WebP, and BMP by actual decoded format. Normalize EXIF orientation and composite transparency onto white. MIME types and file extensions alone are not validation.
- Current limits: 20 MiB file size; multipart body allowance of an additional 256 KiB; 40 million source pixels; 4 million processing pixels. Both declared and streamed oversized request bodies must be bounded before multipart parsing. Admit at most two upload bodies with a 30-second deadline; spool I/O must not block the event loop. Enforce local Host and same-origin mutation checks before reading bodies.
- Validate finite parameter values before spawning. Decode only allowed formats, normalize ICC to sRGB, and validate dimensions/pixel count relative to K inside the owned worker. Scale positive dimensions with floor(value + 0.5), matching browser Math.round. Decoding must remain inside the five-minute timeout/cancellation boundary. Close upload handles on every path.
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
- Preserve output canvas intrinsic image dimensions; source previews are bounded to a 1600-pixel side while the original File and original dimensions remain available. Release canvas buffers on reset/new image and retain compact one-byte label/missing masks. Yield during large highlights and invalidate stale render work. Use transforms only for view controls. Print the clean template image, not a highlighted or transformed canvas.
- Maintain keyboard access, visible focus, status announcements, touch/pointer cancellation, and responsive layout when touching controls.

## Verification and release

The user has given standing authorization to commit and push completed project changes to the existing GitHub repository after the full test suite and relevant workflow/visual checks pass. Do this by default without asking again, unless the user explicitly requests local-only work. Commit only the intended task changes; preserve unrelated work. Inspect the staged diff for public-safe contents and run `git diff --cached --check` before committing, then push the current work branch using a normal, non-force push. This authorization does not cover deployments, repository visibility changes, or rewriting remote history.

After each push, identify the GitHub Actions run for the exact pushed commit SHA and wait for every required job, including both Linux and Windows tests, to finish successfully. Investigate failures, fix issues within scope, rerun relevant local checks, and push follow-up fixes until CI passes. Never treat a previous commit's green run or a queued/in-progress run as verification of the current commit. Report the commit and CI link with the observed result; if access or infrastructure prevents verification, state the blocker rather than claiming success.

Run relevant focused tests while working, then the full suite before publishing a reliability change. A passing backend suite is not a substitute for the browser tests. Inspect generated PDF and PNG output visually after export/layout changes. Include all 32 palette colors, a portrait/landscape template, and label containment in relevant checks.

Test artifacts live under ignored `.local/test-artifacts/`; never publish them or `.venv/`. Use synthetic images in tests. Run `git diff --check`, inspect staged content, and keep README commands accurate. Report exact tests run and any remaining limitations. CI is configured separately from local verification; do not claim remote CI passed until its result is known.

Reliability work and the first region-cleanup/image-benchmark iteration are implemented. Run `uv run --locked python -m benchmarks.run` after changing cleanup; compare label counts, distortion, timing, and images, and distinguish synthetic results from photographic evaluation. Use identical quantization inputs for paired comparisons. For photographic changes, also run `uv run --locked python -m benchmarks.photos --download` (omit `--download` after caching) and inspect `benchmarks/PHOTO_RESULTS.md` for the method and limitations. Keep photo URLs, credits, licenses, and SHA-256 hashes in `benchmarks/photo_sources.json`; never silently accept changed downloads or add photo binaries to Git. Compare unnumbered counts and pixel coverage, strong-edge recall, color distortion, and visual output; fewer missing numbers alone is not sufficient. The photo reference freezes both cleanup and rendering at `f8cd6b0`; the synthetic reference is older and shares the current renderer. Offline CI must not fetch photos. Benchmark-only legacy code must not become the application path. Lab/RGB comparison and local eye protection are implemented. For color/facial changes, run `uv run --locked python -m benchmarks.faces` after caching the photos, plus a second size/seed check. The frozen `facial_baseline.py` reference is commit `6b657ba`; manual regions in `face_annotations.json` are evaluation-only. Compare all four RGB/Lab x protection variants against the same resized source, report human and animal cases separately, and inspect eyes in previews and templates. See `benchmarks/FACIAL_RESULTS.md`; lower color error or missing-label count alone does not establish better faces. Superpixel/graph segmentation, new GPU backends, vector template output, manual region editing, require separate implementation and evaluation. Do not describe them as shipped. PDF templates embed raster images. Blank canvas width fits the selected A4/Letter sheet with 10 mm margins; custom width uses an exact-size page. The optional tiled PDF uses exact scale, 5 mm overlap, trim/alignment marks and a 50 mm ruler, with a 100-sheet cap. Browser print fits the selected paper/orientation; custom dimensions require PDF. PNG remains at processing resolution.

## Release 1.2 additions

- Portable CPU is the application default; cap workers at two OpenCV threads (one on small hosts). Report the actual selected backend. New GPU paths and sampled clustering are experiments until quality gates pass.
- Retained jobs may share immutable `clusters.npz` through parent-created hard links, keyed by source bytes and every upstream parameter. Publish caches atomically and load with `allow_pickle=False`. Style changes must rerun cleanup/rendering, since label geometry affects merging. Never reuse a cache across different source, palette, smoothing, color-space or protection options.
- Parse result JSON and build exports outside the registry lock, using read leases so expiry cannot remove in-use files. Export caches and intermediate arrays belong to the same private job lifetime. Attempt cleanup of every job even when one path fails; always shut down the multiprocessing manager.
- Maintain the missing-region mask and coverage diagnostics using the same components as numbering. Review overlays are never part of clean exports.
- Run `uv run --locked python -m ruff check .` and the full tests. Ruff deliberately excludes frozen benchmark references; do not reformat or rewrite those baselines. Audit the locked dependency export with `python -m pip_audit --no-deps --disable-pip -r <requirements-file>`.
- CI now validates both Python 3.12/3.13 on Linux/Windows and the security job. Verify all five jobs for the exact pushed SHA. Update pinned action SHAs from official releases only.


## Release 1.3 rules

- `sizing_mode` is `physical` (default) or `pixels`; `paper_size` is `a4` (default) or `letter`. Canvas width is canonical centimetres in the API; the browser may display inches. Preserve unit round trips and invalidate results when physical settings change. Presets preserve paper and canvas width.
- Physical mode shares its computed minimum font scale between cleanup and rendering. Keep actual glyph ink at least 2 mm tall; target 0.3 mm contours with a one-pixel raster floor, and keep protected eye contours one pixel. Bound computed glyph/stroke sizes before allocation. Do not silently shrink unreadable numbers to improve coverage.
- The explicit noise area cutoff remains in pixels. Only extra label-fit merging adapts to physical geometry. Do not turn every tiny speck into protected detail: that experiment produced thousands of missing labels. Preserve the existing color-distance/strong-boundary guards for extra merges and every protected eye palette ID.
- Validate optional color caches (expanded size, dimensions, numeric types/ranges, finite centers, eye boxes/mask agreement); rebuild invalid caches from source. Replace owned files atomically so rebuilding a hard-linked cache cannot modify another job's file.
- `tiles.pdf` is a PDF-only export under the existing job export route and ownership/lease rules. Check page count, physical image matrices, overlap, crop/registration marks and calibration ruler; visually inspect actual rendered portrait/landscape exports. Never call a PDF vector output when it embeds a raster.
- Run `python -m benchmarks.painting` through uv for physical-geometry changes, including a second size/seed with `--size 768 --seed 42 --widths 5 40`. Additional portraits are pinned in `benchmarks/portrait_sources.json`; `portrait_annotations.json` is evaluation-only. Preserve the original six-photo comparisons. Report human/animal cases separately, eye/nose/mouth metrics and print readability; the small mostly frontal corpus is not evidence of general face robustness.

## Release 1.4 rules

- Multipart `manual_protection` is optional JSON containing at most 128 normalized `[x,y,width,height]` rectangles on the oriented source. Omission preserves the previous API defaults. Manual masks work independently of automatic `protect_eyes`. Include them in cache provenance; do not reuse an incompatible automatic-only analysis. Effective eye boxes include manual areas; `enabled` still records the automatic-detection setting.
- Editable projects have separate owned temporary directories, four-session capacity, a 512 MiB per-session and 1 GiB total commit/export-cache budget. Active sessions do not inherit finished-job expiry. They last until explicit close or server shutdown; final pagehide is best-effort, while back/forward-cached pages retain their session. Do not evict unsaved active sessions. The session list enables explicit abandoned-session cleanup. Crash recovery/autosave is not implemented.
- Original processing retains a portable base document (normalized processing-resolution source, raw clusters, current map and original upload) inside the private job lifetime. Input handles and the temporary `input.png` are still removed. Keep this data local and out of Git. Project files omit the original upload by default; explicit inclusion preserves its original bytes/metadata.
- Commands require a UUID request ID and expected monotonic project revision. One pending command per project; duplicate requests are idempotent, conflicting/stale requests return 409. Project opening, bundle parsing, regeneration and editing/rendering use the existing two spawned workers and timeout/cancellation boundary. Never decode untrusted project assets in API/event-loop code.
- Region IDs identify four-connected components within one revision, independently of palette indices. Recolor only one component; merge only across a shared edge, then coalesce same-color connections. Finishing edits never rerun automatic cleanup. Reject any edit changing protected palette IDs. Keep manual numbers in untouched components, and disclose reset manual positions in changed components through history.
- Protection drafts do not alter the committed design. Regeneration reapplies filtering/clustering/cleanup, may change the palette and resets finishing edits; retain the previous complete state for undo. Do not feed benchmark annotations into detection or processing.
- Undo/redo uses stored, content-deduplicated checkpoints, not randomized replay. Retain at most 100 actions and 64 MiB of unique historical pixel arrays; report oldest-history pruning. A new edit discards redo. Guard every async request, decode, selection and commit by document/revision ownership. Keep the committed design during pending work and failed import/edit. Save/print/export only a committed revision.
- `.pbn` bundle format and renderer are version 1, separate from process API schema 2. Bound uploads before parsing (128 MiB), expanded members (256 MiB), decoded unique images (96 MiB), manifest (2 MiB), entry count, history, image modes/shapes, palette indices, finite centers/settings, boxes and complete number geometry. Reject unexpected/duplicate/path/link/encrypted members and unsupported versions. Never extract arbitrary archive paths or load pickle. Reopen stored maps without detection/K-means; retain bounded undo/redo history and optional original.
- Project assets are binary PNG endpoints; legacy job snapshots still contain data URLs. Metadata polling excludes base64 image payloads. Keep leases through revision reads and atomic exports; never delete in-use revisions. Preserve the direct renderer's two-field placement contract unless geometry metadata is explicitly requested.
- Maintain keyboard alternatives for region selection and protection/number coordinates, visible focus, native text undo, pointer cancellation, mobile template focus, dirty-state warnings and clean print output. Run the editor API/lifecycle and real-browser tests as well as the full suite before publication.


## Release 1.4.1 eye-detail and review controls

- Multipart `eye_detail` is `fine` (default, historical d3/sigma25 eye filter) or opt-in `balanced` (d5/sigma40). Include it in upstream cache identity. Filter only protected pixels before quantization; preserve every resulting protected palette ID during cleanup. Do not promote stronger filtering to the default based only on missing-label counts.
- Protection regeneration accepts optional `eye_detail` and commits it with the complete undo checkpoint. Format-v1 projects missing this additive parameter reopen as Fine without rewriting stored arrays. Other unexpected fields remain invalid.
- Physical font/outline sliders show Auto with an explicit manual Pixels shortcut; the 40 cm shortcut preserves unit conversion. Stronger cleanup is an explicit area_min=200 choice, not a preset/default change. Warn users to mark missed eyes first.
- Missing diagnostics retain total count/coverage/mask and add protected/unprotected count/area splits. A component touching the mask is classified as touching protected eyes; this does not establish why it is unnumberable. Keep complete label boxes, physical minimums and clean exports unchanged.
- Facial/painting benchmark CLIs accept `--eye-detail balanced`; default Fine preserves historical comparisons. See benchmarks/RELEASE_1_4_1_RESULTS.md for the optional mode's measured tradeoffs. Private image tests remain ignored and local; never publish their paths, filenames, images or saved projects.
