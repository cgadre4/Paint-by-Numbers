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
