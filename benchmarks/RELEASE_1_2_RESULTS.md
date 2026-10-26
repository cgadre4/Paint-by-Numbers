# Release 1.2 evaluation — 2026-10-26

Portable CPU release; full Lab K-means and local eye protection remain the application defaults. Sampled clustering was evaluated and was not selected. All photos remain checksum-pinned in `photo_sources.json`; binaries and generated artifacts are ignored under `.local/release-benchmarks/` and `.local/test-artifacts/`. Frozen benchmark reference files are unchanged.

## Verification

- `uv run --locked python -m pytest -q`: **113 passed** (38.32 s in the final full run), including 11 real-browser workflows and the new completion/capacity race regression.
- Additional cached-photo browser checks passed for both human portraits and the landscape: Detailed preset, custom 40 cm width, eye/missing overlays, mobile layout, all four exports. These three local photo workflows are not fetched or run by offline CI.
- Scoped Ruff and JavaScript syntax checks passed. A locked runtime/development `pip-audit` scan found no known vulnerabilities after the Pillow 13 upgrade; advisory data is time-dependent.
- Runtime-only `uv sync --locked --no-dev` environment: imports, documented Uvicorn launch, real upload, custom 40 × 50 cm canvas and four export downloads passed. An initial auxiliary threaded smoke harness did not start its server; validation was repeated successfully through the documented launch command. Graceful cleanup is covered by the full lifecycle/browser suite.
- Inspected desktop/mobile views; 32-color PNG/PDF palette; portrait/landscape A4 templates; custom-size real-photo PDFs; and enlarged portrait eyes. Exact physical page dimensions and complete number containment have automated checks.
- Remote CI is verified against the pushed main SHA separately; local success alone does not imply remote success.

## Image quality regression checks

Commands: `python -m benchmarks.run`, `python -m benchmarks.photos`, `python -m benchmarks.faces`, and `python -m benchmarks.faces --colors 16 --seeds 2026 --size 768`, all through the locked environment with cached photos.

| Check | Frozen reference | Current | Interpretation |
| --- | ---: | ---: | --- |
| Six synthetic images: unnumbered regions | 247 | 49 | Historical cleanup comparison |
| 18 RGB photo cases: unnumbered regions | 2,534 | 1,716 | Existing cleanup behavior retained |
| Photo mean unnumbered pixel area | 20.644% | 15.710% | Count alone is insufficient |
| Photo mean strong-edge recall | 82.008% | 83.615% | Same quantization inputs per pair |
| Photo mean RGB error | 10.284 | 10.753 | Cleanup retains the known distortion tradeoff |
| 36 facial cases: mean source DE76 | 7.325 | 6.859 | RGB reference vs Lab + eyes |
| Human eye DE76 | 16.137 | 9.599 | Lower is better |
| Human eye-edge F1 | 75.625% | 92.459% | Proxy metric, not semantic accuracy |
| 36 facial cases: unnumbered regions | 3,517 | 4,360 | Protected details can remain unnumbered |
| Protected eye pixels recolored by cleanup | — | 0 | Both main and second size/seed checks |

These reproduce the previous release’s quality findings; they are not new algorithm gains attributed to 1.2. At the second 768-pixel/seed-2026 check, human eye DE76 was 17.023 → 10.057 and F1 76.216% → 90.049%. The cat is reported separately by the facial runner because the production detector targets frontal human eyes. Two human subjects do not establish broad portrait quality.

## Sampled clustering decision

The experiment fits 65,536 uniformly sampled pixels (seeded standard-library sampling), then assigns every pixel with chunked native nearest-center computation. Both methods use Lab, identical resized source/filter/protection settings, the same palette budget, cleanup and renderer. Three quantization timings per method alternate order; reported speedups concern quantization only. The 512-pixel run has 18 cases; the 1024-pixel/seed-2026 run has six cases at K=16. Native small photos are not enlarged.

| Measurement | Full fit | Sampled fit |
| --- | ---: | ---: |
| Main mean source DE76 | 6.8177 | 6.8439 |
| Main human-eye DE76 | 9.4201 | 9.5295 |
| Main human-eye F1 | 92.237% | 93.310% |
| Main unnumbered regions | 2,245 | 2,085 |
| Second check mean source DE76 | 6.3102 | 6.4492 |
| Second check human-eye DE76 | 9.6573 | 10.1820 |
| Cat K=16 eye F1, main seed | 56.556% | 49.838% |
| Cat K=16 eye F1, second seed | 56.235% | 49.232% |

Median per-case quantization speedup was **1.95×** in the main run and **6.81×** in the second check. Aggregate F1/label counts conceal individual regressions: the cat loses eye/eyelid structure in the inspected preview, and some human-eye/color scores worsen. Full fitting remains the default; this experiment is benchmark-only. A held-out corpus and a quality-aware fitting strategy are the next algorithm work.

## Job latency and memory baseline

`uv run --locked python -m benchmarks.jobs` uses the pinned full-size portrait source with processing dimensions below. It measures one cold batch and one subsequent style-only change per configuration. Color analysis is reused for that style change; cleanup and rendering still run. Timings include worker startup, decoding and result reads, but not server startup, HTTP transfer or browser work. All work uses the CPU, at most two OpenCV threads per worker. This Windows/Python 3.12.13 host reports 16 logical CPUs.

RSS is the sum of sampled parent/child resident sets every 50 ms. Shared pages may be counted repeatedly and brief peaks missed. These single samples are a diagnostic baseline, not p95, maximum-memory bounds, a comparison with the old whole pipeline, or a cross-machine speed guarantee.

| Processed pixels | Colors | Concurrent cold jobs | Batch seconds | Subsequent style seconds | Sampled tree RSS MiB |
| --- | ---: | ---: | ---: | ---: | ---: |
| 410 × 512 | 8 | 1 | 1.427 | 0.800 | 184.4 |
| 410 × 512 | 8 | 2 | 1.714 | 0.882 | 351.8 |
| 410 × 512 | 16 | 1 | 1.902 | 0.885 | 226.8 |
| 410 × 512 | 16 | 2 | 2.171 | 0.924 | 345.2 |
| 410 × 512 | 32 | 1 | 2.873 | 1.053 | 225.3 |
| 410 × 512 | 32 | 2 | 3.971 | 0.989 | 282.1 |
| 894 × 1117 | 8 | 1 | 3.751 | 0.954 | 234.8 |
| 894 × 1117 | 8 | 2 | 4.344 | 0.990 | 284.9 |
| 894 × 1117 | 16 | 1 | 5.975 | 1.159 | 195.3 |
| 894 × 1117 | 16 | 2 | 6.922 | 1.134 | 296.0 |
| 894 × 1117 | 32 | 1 | 10.827 | 1.378 | 229.1 |
| 894 × 1117 | 32 | 2 | 12.047 | 1.601 | 295.5 |
| 1789 × 2234 | 8 | 1 | 11.266 | 1.525 | 294.3 |
| 1789 × 2234 | 8 | 2 | 13.966 | 1.549 | 485.7 |
| 1789 × 2234 | 16 | 1 | 20.309 | 2.029 | 301.0 |
| 1789 × 2234 | 16 | 2 | 23.902 | 2.051 | 486.7 |
| 1789 × 2234 | 32 | 1 | 39.246 | 2.960 | 306.3 |
| 1789 × 2234 | 32 | 2 | 45.281 | 2.974 | 505.9 |

The load benchmark exposed a terminal-worker capacity race; it was fixed, regression-tested, and the entire 18-case run was repeated successfully. Peak sampled tree RSS across these runs was 505.9 MiB. Four-megapixel/32-color cold work remains expensive (39.246 s for one job; 45.281 s for a two-job batch). Reusing color analysis reduced the subsequent style change to about 3 s. Large images should still use lower resolution or fewer paints when speed matters.

## Physical canvas limitations

Custom PDFs use exact width and proportional height with no added margins; A4 remains the default when width is blank. Geometry tests cover both orientations. Canvas size changes export scale and readability warnings, not color clustering or cleanup thresholds. Pixels-per-inch below 150 or number ink below 2 mm trigger review warnings; these are advisory, not print-quality guarantees. Fine eye regions remain protected and can require the color reference. PNG and browser A4 printing do not promise custom physical size. Automatic tiling, vector paths, manual edits and physical-unit merging are deferred.
