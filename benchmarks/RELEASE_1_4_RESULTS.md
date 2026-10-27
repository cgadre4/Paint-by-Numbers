# Release 1.4 validation - 2026-10-26

## Implemented workflow

Local manual eye rectangles (including an automatic-detection override per box), adjacent-component merge, existing-palette recolor, valid manual number placement, bounded exact undo/redo, and portable `.pbn` save/reopen. Protection regeneration is a whole-design checkpoint and can change the palette. Other finishing edits do not rerun cleanup. Protected palette IDs, four-connected topology, complete number-box containment, physical readability and clean revision exports remain enforced.

Projects use private temporary ownership independent of job expiry, immutable revisions, reader leases, request identity, stale-revision checks, storage reservations, cancellation and explicit abandoned-session cleanup. Project assets are binary PNGs and polling metadata excludes base64 images. The legacy processing status API remains schema 2 with data URLs. Bundles use format/renderer version 1 and content-deduplicated checkpoints, avoiding randomized replay and repeated storage of unchanged source/raw maps. No new runtime dependency, cloud service, segmentation method or GPU path was introduced.

## Local verification

- **182 tests passed in 92.76 s**, including 20 real Chrome workflows. The suite includes project restoration after a server restart, undo/redo history, palette/protection/number geometry, malformed archives, body bounds, lost/stale responses, cancellation/timeout, leases, history limits, disk/quota faults, independent job expiry, shutdown and abandoned-session handling.
- Seven additional cached real-photo editor workflows passed: astronaut, Yellen, Jemison, Ginsburg, Powell, cat and 32-color landscape. Each exercises project creation, applicable manual protection, clean exports and project download/validation. Scripted user rectangles do not use benchmark annotations. These are functional checks, not a blinded quality study.
- Ruff, both JavaScript syntax checks and Git whitespace checks passed. The locked runtime/development advisory scan reported no known vulnerabilities. The dependency lock changed only the application version.
- A fresh **runtime-only** 1.4 environment passed startup, upload, manual protection regeneration, merge, undo/redo, all five image/PDF exports, optional original inclusion, save/reopen and graceful shutdown. The final smoke server runs Uvicorn on its main thread; early test-harness startup/shutdown attempts were corrected, rather than counted as passing application checks.
- Visually reviewed actual portrait/landscape PDF tiles and adjacent overlap pages, trim/alignment marks, 50 mm rulers, the complete 32-color palette, portrait eye crops and desktop/mobile finishing UI. The PDF renderer emitted missing optional font-map warnings; the rendered Helvetica text and geometry were inspected. No physical printer was used.

Remote CI must be checked separately for the exact published main commit. Local results do not imply remote success.

## Historical quality regressions

All quality-metric dictionaries below exactly match the final release 1.3 local reports after excluding timing fields. The main physical comparison uses the final `painting-final` reference, not the rejected all-speck experiment. Image comparisons and frozen references are unchanged. Historical old-versus-current gains are not gains introduced by 1.4.

| Runner | Cases | Observed result |
| --- | ---: | --- |
| Synthetic | 6 | All quality metrics unchanged; legacy missing 247 -> current 49. |
| RGB photo cleanup | 18 | All quality metrics unchanged, including missing counts/area, strong-edge recall and distortion. |
| Facial four-variant comparison | 36 | All quality metrics unchanged. Human and cat results retain their separate interpretation. |
| Facial second size/seed | 6 | All quality metrics unchanged, maximum side 768, K16, seed 2026. |
| Physical, 512 / seed 12345 / 20 and 40 cm | 40 pairs | Metrics unchanged; total missing 6,386 -> 6,312 across repeated widths. |
| Physical, 768 / seed 42 / 20 and 40 cm | 40 pairs | Metrics unchanged; total missing 10,346 -> 10,182. |
| Physical stress, 768 / seed 42 / 5 and 40 cm | 40 pairs | Metrics unchanged; total missing 10,346 -> 10,486. |

Zero protected pixels changed palette ID in the physical pairs. At 5 cm, the existing readability-versus-coverage tradeoff remains: human eyes/nose/mouth and animal eyes must still be reviewed separately. The ten-photo corpus (six human subjects) is small and mostly frontal; no broader face robustness or semantic improvement is claimed. See [1.3 results](RELEASE_1_3_RESULTS.md) for the unchanged eye/nose/mouth metrics and limitations.

The editor changes what users can correct and resume. It does not make the automatic detector generally more accurate. Regenerating with user protection changes upstream filtering and may change randomized palette fitting. Undo and reopen restore stored arrays instead of trying to reproduce that fitting.

## Resource diagnostic

Six K16 cold-job cases cover one/two workers at approximately 0.2, 1 and 4 MP on the current Windows host. Cold batches ranged from 1.418 to 16.630 s; retained style reuse ranged from 0.643 to 2.060 s. The largest sampled process-tree RSS was 489.9 MiB. Timings include worker startup, normalization and retained project-base creation, but exclude HTTP/browser and server startup. RSS sums parent/children every 50 ms, may double-count shared pages and miss short peaks. These are single diagnostic samples, not p95, worst-case memory limits or a cross-host speed claim. Import/finishing worst-case profiling remains future work.

## Reproduction

```sh
uv run --locked python -m pytest -q
uv run --locked python -m ruff check .
uv run --locked python -m benchmarks.run --output .local/release-1.4/synthetic
uv run --locked python -m benchmarks.photos --output .local/release-1.4/photos
uv run --locked python -m benchmarks.faces --output .local/release-1.4/faces
uv run --locked python -m benchmarks.faces --size 768 --colors 16 --seeds 2026 --output .local/release-1.4/faces-768
uv run --locked python -m benchmarks.painting --output .local/release-1.4/painting
uv run --locked python -m benchmarks.painting --size 768 --seed 42 --widths 20 40 --output .local/release-1.4/painting-768
uv run --locked python -m benchmarks.painting --size 768 --seed 42 --widths 5 40 --output .local/release-1.4/painting-small
uv run --locked python -m benchmarks.jobs --sides 512 1117 2234 --colors 16 --output .local/release-1.4/jobs
```

Cache the pinned photos with `benchmarks.photos --download` first when needed. Offline CI uses synthetic fixtures and never fetches photos. Raw reports, comparison images, saved smoke bundles, screenshots and rendered PDFs stay in ignored `.local/` storage.

## Remaining limits

Save is an explicit browser download, not autosave or crash recovery. History is capped; old actions may be pruned with a notice. Unsupported bundle versions are rejected rather than silently migrated. Source/resolution replacement starts a new design. Freehand splitting, arbitrary boundary drawing, new palette creation, external leader labels, vector PDF and broad accessibility/cross-device/p95 evaluation remain future work. Keep OS security enabled and retain the Python/vanilla frontend/local-only architecture.
