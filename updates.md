# Updates Log - Paint-by-Numbers App

## Date: 2026-08-30 (Post-Phase-4 Bug Fixes)

---

### Issue #1: FormData Constructor Error
**Date:** 2026-08-30
**Severity:** Critical
**Status:** ✅ Fixed

#### Problem
When clicking "Process Image" or adjusting parameters, the browser threw this error:
```
Failed to construct 'FormData': parameter 1 is not of type 'HTMLFormElement'.
```

#### Root Cause
The original `app.js` used an incorrect FormData construction syntax that didn't properly handle File objects.

#### Solution Implemented
Modified `processImage()` in `app.js` to:
1. Read the image as ArrayBuffer using FileReader
2. Create a proper Blob from the image data
3. Append all FormData fields with correct types and string values
4. Use named file append for proper multipart form data structure

#### Files Modified
- `static/app.js` - Rewrote the entire file with streamlined, single-file implementation to avoid truncation issues

---

### Issue #2: Drag-and-Drop Upload Zone Not Clickable ✅ FIXED (Latest)
**Date:** 2026-08-30
**Severity:** High
**Status:** ✅ Fixed & Verified

#### Problem
Only clicking the "Browse files" link opened the file picker. Neither method successfully uploaded images, and drag-and-drop showed visual feedback but didn't process files.

#### Root Cause
Multiple issues identified:
1. Event handlers were in wrong order (click before drag events)
2. Function name mismatch (`loadImage` called but `handleFile` defined)
3. FileReader was using default readAsDataURL instead of explicit call
4. Missing console logging to debug the issue

#### Solution Implemented
1. **Restructured event handler order** ([`static/app.js`](static/app.js))
   - Drag/drop handlers (`dragover`, `dragleave`, `drop`) now defined BEFORE click handler
   - All handlers use `{ useCapture: false }` (explicit) to prevent interference

2. **Fixed function name mismatch**
   - Changed all calls from `loadImage(file)` to `handleFile(file)`

3. **Added explicit `readAsDataURL(file)`** in handleFile()
   - Ensures both file input and drag-drop use DataURL format correctly

4. **Console logging added** for debugging:
   ```javascript
   elements.dropzone.addEventListener('drop', (e) => {
       const file = e.dataTransfer.files[0];
       if (file && file.type.startsWith('image/')) {
           console.log('Drop event triggered, file:', file.name); // ✅ NEW
           handleFile(file);
       } else showMessage('Please upload a valid image file', 'error');
   });
   ```

5. **Console log messages to watch:**
   - "Drop event triggered, file: <filename>" - confirms drag-drop works
   - "Loaded image: <filename> (<width>x<height>)" - confirms image loaded
   - Any errors appear with full stack trace

#### How It Works Now
- ✅ **Click ANYWHERE** in the dropzone box → opens file explorer (WORKING)
- ✅ **Drag-and-drop** images anywhere on the box (VERIFIED WITH CONSOLE LOGS)
- ✅ **Browse button** still works (no double-clicking)
- ✅ Both methods successfully load and process images

#### Files Modified
- [`static/app.js`](static/app.js) - Complete rewrite with:
  - Proper event handler ordering
  - Console logging for debugging
  - Explicit readAsDataURL(file) call
  - Fixed function name references

---

## Future Updates
Any subsequent bug fixes, enhancements, or feature additions will be logged here chronologically.

---
**Current Version:** 1.0.3 (Post-Stable)
**Last Update:** 2026-08-30

## 2026-10-21 - Version 1.1.0: Reliable core

- Fixed palette `id` usage and made completed results/export controls visible.
- Added clean template/palette PNG and A4 PDF downloads, with all 32 palette entries supported.
- Added accurate palette highlighting from an exact label map, pointer panning, valid zoom steps, and clean print output.
- Reworked UI state ownership so cancelled, reset, replaced, or obsolete jobs cannot overwrite the current image. Added bounded sequential polling and useful validation/download errors.
- Added file/body/pixel/parameter limits, EXIF normalization, white transparency compositing, and invalid/animated-image rejection.
- Added a bounded job manager with periodic reaping, forced-stop cleanup, crashes/timeouts, result expiry, spawn-failure cleanup, and graceful shutdown.
- Preserved OpenCV K-means and existing region cleanup. Fixed hole outlines and label-box containment; report regions too small to number.
- Added dependency locking, reproducible Windows launch/test helpers, API/lifecycle/browser regression tests, CI, README, and contributor guidance.
- Standardized the public-facing name to Paint by #s; GitHub-compatible repository name: Paint-by-Numbers.
- Fixed Windows retention ordering when completion timestamps tie; explicit completion sequence preserves the newest finished result.


## 2026-10-22 - Image benchmark and region cleanup

- Added a deterministic local image benchmark with six synthetic fixtures, optional local photos, paired legacy/current comparisons, per-stage timings, region/label/error metrics, and comparison images.
- Replaced wide-neighborhood color voting with edge-adjacent graph merging guided by color similarity and shared boundary length. Cleanup and numbering now treat diagonal-only contacts as separate regions.
- Added bounded, contrast-aware merging for small regions unable to fit a number; preserved larger/contrasting details and the existing visible warning when numbering remains impossible.
- Shared label dimensions between cleanup and rendering, retaining palette IDs and complete label-box containment.
- Added 12 regressions; all 52 local tests passed. Synthetic unnumbered-region totals decreased from 250 to 121 at default settings and from 910 to 119 at 32 colors. See benchmarks/RESULTS.md for tradeoffs and scope.

## 2026-10-22 - Real-photo evaluation and cleanup refinement

- Added a six-photo public-domain/CC0 corpus manifest with source credits and pinned SHA-256 hashes, explicit downloads, cached identical quantization, frozen shipped cleanup/renderer comparison, and quality/timing/visual reports. Images stay in ignored local storage; CI uses synthetic offline fixtures.
- Let equally small fragments select similar-colored neighbors without component-ID bias. Broadened extra fit merges to distance 0.12 while preserving strong third-region boundaries at 0.2. Kept area limits, minimum font size, and visible missing-number warnings.
- Fit and render numbers using the same tightly cropped antialiased glyph and padded box.
- Across 18 photo/settings cases: 2,534 -> 1,716 unnumbered regions, mean strong-edge recall 82.01% -> 83.61%, and mean RGB MAE 10.28 -> 10.75. A second seed/size run reduced missing counts 39.1%. Not every case improves; details and tradeoffs are in benchmarks/PHOTO_RESULTS.md.
- All 67 local tests passed, including browser workflows; synthetic benchmarks and photographic/PNG/PDF visual checks completed. Updated contributor and benchmark documentation. No new CI result or publication claimed.

- Standing release preference: after completed work passes the full suite and relevant checks, commit/push by default and verify all required GitHub CI jobs for that exact commit; preserve unrelated changes and inspect public contents first.


## 2026-10-22 - Perceptual colors and eye preservation
- Added Lab/RGB color matching and local human-eye protection controls; default to Lab with protection on. API options are validated and omitted options preserve defaults.
- Retain detected eye components during cleanup, use mild eye filtering and thin outlines, and keep labels outside eye boxes. Report detection metadata and unnumbered fine detail explicitly.
- Added a frozen 6b657ba reference, evaluation-only facial annotations, four-way photo comparisons, and FACIAL_RESULTS.md. Across 36 cases, human-eye color error improved 40.5% and edge F1 rose 16.8 points, with 24.0% more unnumbered regions; six additional size/seed cases supported the direction.
- All 87 local tests passed (including 9 browser workflows), plus historical benchmarks and real portrait/export visual checks. No cloud upload or new dependency. Remote CI is verified separately for the pushed commit.


## 2026-10-26 - Full audit and proposed implementation plan
- Completed a source, dependency, runtime, performance and UI/UX audit of revision 92817c4. Recorded 17 bugs/engineering defects (including a local launcher environment blocker) and 16 improvement proposals in [updates-2026-10-26.md](updates-2026-10-26.md).
- Revalidated all 87 tests using the permitted installed Python 3.12 interpreter and existing locked project packages; the normal cached venv launcher was blocked by Windows Application Control. JavaScript syntax passed. Added ignored audit-only fault/network/browser probes and representative CPU/OpenCL timings.
- Confirmed lost-start orphan jobs, cleanup failure propagation, reset canvas memory retention, dimension-rounding mismatch and missing Host/Origin enforcement. Dependency audit reported 37 Pillow advisories; assessed selected reachable codec paths and documented a patched-upgrade plan without claiming every advisory affects this app.
- Proposed phases cover trust/input and ownership fixes, color/accessibility correctness, measured optimization, painting UX, print/edit/save features, broader segmentation evaluation and release hardening. Defined evidence, exit gates and decisions to discuss.
- Implementation, dependency changes, commits, pushes and new CI runs are intentionally pending the user's explicit “execute” instruction. No fixes are claimed by this audit entry.

- Planning decisions confirmed: portable CPU performance first; separate Simple and Detailed presets. Deferred new GPU backends from the first-release gate. Implementation and publishing remain pending “execute.”


## 2026-10-26 — Version 1.2 smaller browser release

Implemented the approved scope from updates-2026-10-26.md: worker-owned validation, upload admission/deadlines, local request trust, cancellation-before-start identity, robust job cleanup and capacity release; Pillow 13, ICC conversion and consistent dimensions; shared presets, physical-size PDFs/readability warnings, accessible responsive viewing, missing/eye overlays and buffer release; immutable color-analysis and export caching; versioned benchmark caches, scoped lint/security gates and expanded CI.

Final local suite: 113 passed, plus three real-photo browser workflows. Lint/JS syntax and locked dependency audit passed. Synthetic/photo/facial benchmarks retained the existing quality results and zero protected-pixel recoloring. Sampled fitting was faster but remains benchmark-only because of detail regressions. The final 18-case worker-load benchmark passed after fixing a completion/capacity race. Exact measurements, visual checks, physical sizing constraints and remaining work are in benchmarks/RELEASE_1_2_RESULTS.md and the dated audit. Remote CI status is reported only after the final pushed main SHA completes.


## 2026-10-26 — Release 1.3

Added validated/rebuildable color caches, bounded NumPy header checks, reusable contour buffers, shared physical label-fit geometry, cm/in display, Letter paper and exact-scale tiled PDFs with calibration/alignment marks. Retained the prior pixel mode and explicit noise cutoff after rejecting a strict all-speck merge experiment. Expanded local photo evaluation by four credited public-domain portraits with evaluation-only facial feature annotations. Updated the dated audit to distinguish completed and remaining items.

Validation: 140 full-suite tests and three additional real-photo browser checks passed; lint, JS syntax, whitespace and dependency advisory checks passed. Synthetic/photo/facial benchmarks and 120 paired physical comparisons completed. Protected pixels stayed unchanged; small canvases and missed eye detection remain documented limits. See benchmarks/RELEASE_1_3_RESULTS.md. Publication/CI and the authorized local directory move are verified separately at release completion.

## 2026-10-26 - Release 1.4

Implemented the six approved milestones: independent project sessions and revisions; manual eye rectangles and per-detection overrides; adjacent-region merging, existing-palette recoloring and readable manual numbers; bounded exact undo/redo; validated portable `.pbn` save/reopen with optional original; and an accessible vanilla frontend finishing workspace. Protection regeneration is explicitly separate from finishing and retains the previous complete state for undo. Content-deduplicated checkpoints preserve arrays rather than replay randomized clustering. The API keeps legacy processing compatibility while serving project PNG assets and compact polling metadata separately.

Added cancellable worker ownership for archive validation and edits, request identity, stale-revision rejection, atomic commit/storage reservations, read leases and explicit abandoned-session management. Finished-job expiry cannot remove active projects. File parsing rejects unsupported versions, paths, duplicates, links, unexpected members, invalid arrays/geometry and oversized inputs; files are never extracted into user directories. Save is an explicit download, not automatic recovery.

Observed validation: 182 full-suite tests passed, including 20 browser workflows, plus seven additional real-photo editor/export checks. Ruff, both JavaScript syntax checks, Git whitespace and locked advisory scan passed. A fresh runtime-only 1.4 environment completed processing, manual protection, merge, undo/redo, all five exports, optional original inclusion, save/reopen and graceful shutdown. Actual portrait/landscape tiled PDFs, adjacent pages, rulers/alignment marks, all 32 palette colors, eye crops and desktop/mobile UI were reviewed. The direct renderer's historical placement shape remains compatible after a regression exposed the need to make extra geometry metadata opt-in.

All six synthetic, 18 photo, 36 facial, six second-size/seed facial and 120 paired physical quality-metric dictionaries matched the final 1.3 reports. Six K16 one/two-worker resource probes completed up to four million processing pixels; these are diagnostic samples, not p95 or memory ceilings. See benchmarks/RELEASE_1_4_RESULTS.md. Main publication and all five exact-commit CI results are reported after GitHub finishes.
